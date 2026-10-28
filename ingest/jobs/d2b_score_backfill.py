# -*- coding: utf-8 -*-
"""评分历史回算（可续跑 ＋ 避让）。为什么要有这个驱动，而不是多开几个 `d2_scores`：

2026-10-19 的现场：`analytics.score` 里带 rank/象限的 `oper_score` 只有 47 场是非 stale
（2026-07-29 起），于是「启动前 30 场四维象限轨迹」满窗的首板事件只剩 867 个 —— 统计强度被这一格卡死。
用户裁定「补 218 场够满窗，但先把旧读数归档再重算」（归档已做：`analytics.score_legacy_archive`
批次 `pre_recompute_20261019`，主表 349,418 行 / 明细 121,433 行）。

剩下的事实决定了不能"我坐在键盘前掐点"：一场 ≈195 秒（`.local/fill_0924_*.log` 实测：
2026-09-24 单场 5,557 只、回溯 262 天、195.3 秒），220 场串行就是 12 小时。所以：

1. **可续跑**：每算一场前先问"这一场已经有非 stale 的 `oper_score` 行没有"，有就跳过。
   ⇒ 中途被杀/断电/机器休眠都不需要人记得"从第 31 场继续"。
   （为什么不靠台账：台账只留最近 30 条流水，而这一批要跑几百场。）
2. **分片**：`--shard i/n` 按下标取模分日期，各片互不相交、并集等于全集；
   并行安全的前提是 `scoring._write` 的中间表已经改成**每张进程一张**（`stg_score_<pid>`，
   2026-10-14 `indicators.py:137-143` 那起互拆事故的同一课）。
3. **避让**：15:30~20:30 一律不动手（收盘链 15:59 起、每日备份 18:40 起），到点就睡过去。
   为什么用时间窗而不是"看链在不在跑"：`jobs.stage_busy()` 读的是**进程内**的 `_claims`，
   本驱动是另一个进程，看不见链的占用 —— 拿"看不见"当"没有"就是假绿灯。
4. **每场都留账**：`jobs.record('score_backfill', …)`，页面上的作业面板能看到跑到哪一天、
   为什么这一场没跑（跳过/让路/失败），失败原因原文带出来。

用法：

    python -m ingest.jobs.d2b_score_backfill --from 2025-08-28 --to 2026-07-28 --shard 0/3
    python -m ingest.jobs.d2b_score_backfill --status          # 只看还剩多少场
"""
from __future__ import annotations

import argparse
import datetime as dt
import time
from typing import Callable, Iterable

from app.compute import scoring
from app.db import engine
from app.db.repo import security as sec
from duanxian import jobs as jstore

JOB_KEY = 'score_backfill'
#: 不动手的时间窗（本地钟点，含起点不含终点）。15:59 收盘链 + 18:40 每日备份都在这段里。
BUSY_WINDOW = ((15, 30), (20, 30))
#: 每场之间歇一下：让 8 核机器上别的那 7 个进程有机会插进来，也让本进程的连接池喘口气
PACING_SEC = 2.0


def _sql_done(d_from: dt.date, d_to: dt.date) -> set:
    """这段区间里**已经有新算法读数**的交易日（有非 stale 的 oper_score 行）。"""
    rows = engine.query(
        "select distinct trade_date from analytics.score "
        "where capability_key = 'oper_score' and is_stale = false "
        "  and trade_date >= %s and trade_date < %s",
        (d_from, d_to + dt.timedelta(days=1)), role='reader') or []
    return {r['trade_date'] for r in rows}


def pending_dates(d_from: dt.date, d_to: dt.date, *, shard: int = 0, shards: int = 1,
                  sessions: Iterable[dt.date] | None = None,
                  done: set | None = None) -> list[dt.date]:
    """本片还该跑哪几天：交易日历 ∩ 区间，减掉已完成的，再按下标取模分片。

    ⚠️ 取模是**按当下这份待跑清单**算的：两片先后启动时可能撞同一天 —— 那只是白算一遍
    （写侧是按主键 upsert，同一场重算结果一致），不会漏、也不会把数写坏。
    """
    days = [d for d in (sessions if sessions is not None
                        else sec.recent_trade_dates(10_000, d_to))
            if d_from <= d <= d_to]
    left = [d for d in sorted(set(days)) if d not in (done or set())]
    if shards > 1:
        left = [d for i, d in enumerate(left) if i % shards == shard]
    return left


def in_busy_window(now: dt.datetime) -> bool:
    (h1, m1), (h2, m2) = BUSY_WINDOW
    mins = now.hour * 60 + now.minute
    return h1 * 60 + m1 <= mins < h2 * 60 + m2


def run(d_from: dt.date, d_to: dt.date, *, shard: int = 0, shards: int = 1,
        sleeper: Callable[[float], None] = time.sleep,
        clock: Callable[[], dt.datetime] = dt.datetime.now,
        compute: Callable[[dt.date], dict] | None = None,
        sessions: Iterable[dt.date] | None = None,
        done: set | None = None) -> dict:
    """按日期正序跑（老场先补上，象限轨迹那种"往前看 30 场"的窗口才尽快满）。

    `sessions` / `done` 是给单测留的缝（不传就现查交易日历与库里的完成场）；
    `compute` 默认是 `scoring.compute_date` —— 单测里换掉它，才不会真去算一场 5,500 只的评分。
    """
    compute = compute or (lambda d: scoring.compute_date(d))
    out = {'shard': f'{shard}/{shards}', 'ran': 0, 'skipped': 0, 'yielded': 0,
           'failed': [], 'days': 0, 'seconds': 0.0}
    dates = pending_dates(d_from, d_to, shard=shard, shards=shards,
                          sessions=sessions, done=_sql_done(d_from, d_to) if done is None else done)
    out['days'] = len(dates)
    print(f'[{out["shard"]}] 本片待跑 {len(dates)} 场 '
          f'{dates[0] if dates else "—"} .. {dates[-1] if dates else "—"}')
    for d in dates:
        if in_busy_window(clock()):
            out['yielded'] += 1
            print(f'[{out["shard"]}] {d} 让路：现在 {clock().strftime("%H:%M")} 在收盘链/备份的窗口里 '
                  f'{BUSY_WINDOW[0][0]:02d}:{BUSY_WINDOW[0][1]:02d}~'
                  f'{BUSY_WINDOW[1][0]:02d}:{BUSY_WINDOW[1][1]:02d}，等到窗口外再继续')
            while in_busy_window(clock()):
                sleeper(300.0)
        t0 = time.time()
        try:
            st = compute(d)
        except Exception as exc:                       # noqa: BLE001  一场坏了不许带崩整批
            out['failed'].append({'date': str(d), 'error': f'{type(exc).__name__}: {str(exc)[:160]}'})
            jstore.record(JOB_KEY, ok=False,
                          detail={'date': str(d), 'shard': out['shard'],
                                  'reason': f'{type(exc).__name__}: {str(exc)[:160]}'})
            print(f'[{out["shard"]}] {d} 失败：{type(exc).__name__}: {str(exc)[:160]}')
            continue
        secs = time.time() - t0
        out['seconds'] += secs
        bad = isinstance(st, dict) and 'error' in st
        out['ran'] += 0 if bad else 1
        out['skipped'] += 1 if bad else 0
        jstore.record(JOB_KEY, ok=not bad, detail={
            'date': str(d), 'shard': out['shard'], 'seconds': round(secs, 1),
            'score_rows': (st or {}).get('score_rows'),
            'reason': (st or {}).get('error') or f'跑到 {d}（本片还剩 '
                                                 f'{len(dates) - out["ran"] - out["skipped"] - out["yielded"]} 场）'})
        print(f'[{out["shard"]}] {d} '
              + (f"跳过：{st['error']}" if bad else
                 f"{st.get('codes', 0):,} 只 评分 {st.get('score_rows', 0):,} 行  {secs:.0f}s"))
        sleeper(PACING_SEC)
    return out


def status(d_from: dt.date, d_to: dt.date) -> dict:
    """还差几场：区间里的交易日历数 − 已有新算法读数的场数。"""
    days = [d for d in sec.recent_trade_dates(10_000, d_to) if d >= d_from]
    done = _sql_done(d_from, d_to)
    return {'range': f'{d_from}..{d_to}', 'sessions': len(days), 'done': len(done),
            'pending': len([d for d in days if d not in done])}


def main() -> int:
    ap = argparse.ArgumentParser(description='评分历史回算（可续跑、会避让）')
    ap.add_argument('--from', dest='d_from', default=None, help='起始交易日 YYYY-MM-DD')
    ap.add_argument('--to', dest='d_to', default=None, help='结束交易日 YYYY-MM-DD（默认=库里最新一场）')
    ap.add_argument('--shard', default='0/1', help='第几片/共几片，如 0/3')
    ap.add_argument('--status', action='store_true', help='只报还差几场，不动手')
    args = ap.parse_args()

    d_to = (dt.date.fromisoformat(args.d_to) if args.d_to else sec.latest_trade_date())
    d_from = dt.date.fromisoformat(args.d_from) if args.d_from else None
    if args.status:
        if not d_from:
            print('--status 要配 --from'); return 2
        s = status(d_from, d_to)
        print(f"区间 {s['range']}：交易日历 {s['sessions']} 场，已有新算法读数 {s['done']} 场，"
              f"还差 {s['pending']} 场（按 195 秒/场估 ≈{s['pending'] * 195 / 3600:.1f} 小时单线程）")
        return 0
    if not d_from:
        print('要 --from 才动手（避免手一抖就把全史重算一遍）'); return 2

    shard_s, shards_s = args.shard.split('/')
    shard, shards = int(shard_s), int(shards_s)
    t0 = time.time()
    out = run(d_from, d_to, shard=shard, shards=shards)
    print(f"[{out['shard']}] 完：跑成 {out['ran']} 场、跳过 {out['skipped']} 场、让路 {out['yielded']} 场、"
          f"失败 {len(out['failed'])} 场，用时 {(time.time() - t0) / 3600:.2f} 小时")
    for f in out['failed'][:5]:
        print(f"  {f['date']}  {f['error']}")
    return 0 if not out['failed'] else 1


if __name__ == '__main__':
    import sys
    sys.exit(main())
