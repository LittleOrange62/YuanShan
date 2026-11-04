# -*- coding: utf-8 -*-
"""D5a · 盘中「交易体系榜」**取数与判据落库**（14:39 那一针）—— 只算、只落库，一个字都不发。

与 `ingest/jobs/push/t07_sysentry_report.py` 的分工（用户 11-03 定：判断与取数属于数据加工环节，是取数落地的，
跟推送到飞书的那些行为分开，不要互相影响）：

    本脚本（A）   14:39:00：PG 历史日线 → ②预筛 → 实时快照拼今天这一根 → `sys-1` 判据
                  → 逐票写 `analytics.intraday_sysentry`，摘要写 `analytics.intraday_broadcast`
    sysentry_report（B） 14:40:30：只读那两张表 → 渲染 → 发渠道

搬进来的原由（外部那份 `intraday_sysentry/sysentry_intraday.py` 是边取边发的 875 行）：
发失败之后重发一次，快照已经变了 ⇒ 群里两条不是同一场数；判据的中间结果只活在一条消息里，
事后问"那天 14:40 榜上都有谁、为什么它没上"答不上来。拆开后 B 侧随便重跑，数与当时一字不差。

    python -m ingest.jobs.push.t06_sysentry_fill                      # 真跑：落库，不发
    python -m ingest.jobs.push.t06_sysentry_fill --dry-run            # 只算不写库
    python -m ingest.jobs.push.t06_sysentry_fill --offline --asof 2026-10-21   # 拿库里那根当"今天"复盘
    python -m ingest.jobs.push.t06_sysentry_fill --check-days 10      # 与 analytics.system_entry 逐票对账
    python -m ingest.jobs.push.t06_sysentry_fill --tq-check           # 只验本地通达信链路，不写不发

非交易日静默退（`--force` 跳过）。A 侧失败**不往群里发任何东西** —— 那是 B 那一炉的事，
它到点读不到今天的行就播"行情未就绪"那句（两条都发就会撞出两行不同的解释）。
"""
from __future__ import annotations

import argparse
import datetime as dt
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]      # push/ → jobs/ → ingest/ → 仓库根（挪目录时这一格最容易数错）
sys.path.insert(0, str(ROOT))
try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')      # type: ignore[attr-defined]
except Exception:  # noqa: BLE001
    pass

from app.db import engine                                           # noqa: E402
from app.intraday import store, sysentry as S, tasks                # noqa: E402
from ingest.jobs.push import t01_zt_watch as zt_watch                                    # noqa: E402

TASK = 'sysentry_fill'
DRY_RUN = False


def _log(now, what, **kw):
    if DRY_RUN:
        return 0
    try:
        store.write_broadcast(now, task=TASK, what=what, **kw)
    except Exception:  # noqa: BLE001  留痕写不进去不能把今天这一针带走
        pass


def zt_pool_codes(now: dt.datetime) -> set[str]:
    """今天东财涨停池的代码（**只用于对账**：我按档位算出的涨停价与池子不一致就计数）。

    池子挂了不拦这一针 —— 判据不依赖它，拦下来反而会让榜整场消失。
    """
    S.no_test_net('eastmoney_zt_pool')
    zt, _zb, _dt = zt_watch.fetch_pools(now)
    return set(zt.keys())


def one_round(*, dry_run: bool, offline: bool, asof: dt.date | None, force: bool,
              tq_check: bool) -> int:
    global DRY_RUN
    DRY_RUN = dry_run
    now = dt.datetime.now()
    day = asof or now.date()
    if not force and not offline:
        if not zt_watch.is_trading_day(now):
            print(f'[{now:%H:%M:%S}] 非交易日 —— 不落库')
            return 0
    cfg = S.cfg_from(tasks.load_cfg()[0])
    zt: set[str] = set()
    if not offline:
        try:
            zt = zt_pool_codes(now)
        except Exception as exc:  # noqa: BLE001
            print(f'  涨停池对账用不了（{type(exc).__name__}: {str(exc)[:120]}）—— 这一针少一项对账')
            _log(now, 'sysentry_zt_pool_fail', err=str(exc)[:200])
    try:
        got = S.collect(now=now, day=day, offline=offline, cfg=cfg, zt_pool=zt,
                        note=lambda d: _log(now, str(d.get('what') or 'note'),
                                           err=str(d.get('err') or '')[:200] or None,
                                           counts={k: v for k, v in d.items() if k != 'what'}))
    except Exception as exc:  # noqa: BLE001  A 侧失败不发东西，只留痕（B 侧到点会播"未就绪"）
        reason = f'{type(exc).__name__}: {str(exc)[:200]}'
        print(f'[{now:%H:%M:%S}] 取数/判据失败 —— {reason}')
        _log(now, 'sysentry_fill_fail', ok=False, err=reason)
        return 1
    s = got['summary']
    # 离线模式下 `cand_n` 是"库里今天有行情的票"（母体），不是②预筛的候选数 —— 同一格两种含义，
    # 必须当场说清是哪一种，否则屏上那个 5569 会被读成"②预筛出 5569 只"
    cand_label = '母体（离线：库里今天有行情的票）' if offline else '②候选'
    print(f"历史票 {s['hist_n']} · {cand_label} {s['cand_n']} · 源 {got['src']}（{got['proven']}） · "
          f"判过 {s['judged']} · 三条全过 {s['entry_n']} · 观察池 {s['watch_n']} · "
          f"未判 {s['no_quote']}（除权 {s['exright']}） · 档价不一致 {s['mismatch']}")
    if tq_check:
        for r in got['rows'][:10]:
            print('  样本', r[3], r[4], '现价', r[7], '缺口', r[10], r[11])
        print('[tq-check] 只验链路，没有落库')
        return 0
    if dry_run:
        print('（dry-run：一行都没写）')
        return 0
    if not got['rows'] and s['cand_n'] and s['judged'] == 0:
        # 有候选却一票没判过：先怀疑快照，不把"今天 0 只"当成读数落下去
        print('  候选全没判过 —— 先记失败，不发榜（多半是快照非今日/停牌）')
        _log(now, 'sysentry_fill_thin', ok=False, err=f"候选 {s['cand_n']} 判过 0",
             counts=s, source_detail={'src': got['src'], 'proven': got['proven']})
        return 1
    n = store.upsert_sysentry_rows(got['rows'])
    counts = {**s, 'last_day': got['last_day'], 'proven': got['proven'],
              'rows_written': n, 'offline': offline}
    _log(now, 'digest', counts=counts,
         source_detail={'src': got['src'], 'proven': got['proven'],
                        'captured_at': f"{got['captured_at']:%Y-%m-%d %H:%M:%S}"})
    print(f"  落库 {n} 行 → analytics.intraday_sysentry（captured_at {got['captured_at']:%H:%M:%S}）")
    return 0


def check_days(n: int) -> int:
    """连续 N 场：拿库里那根当"今天"重算 → 与 `analytics.system_entry` 逐票对账。

    顺带验证「② 预筛 ⊇ 表里那批」—— 线上只拉候选，这条保证它不漏票（漏了没人会看见）。
    """
    days = [r['trade_date'] for r in engine.query(
        'select distinct trade_date from analytics.system_entry where spec_version = %s '
        'order by 1 desc limit %s', (S.SPEC_VERSION, n), role='reader') or []]
    bad = 0
    for d in sorted(days):
        got = S.collect(day=d, now=dt.datetime.combine(d, dt.time(15, 5)), offline=True,
                        cfg=S.cfg_from(tasks.DEFAULTS))
        res = got.get('res') or {'entry': [], 'watch': []}
        mine = {r['security_code'] for r in res['entry']}
        mine_all = mine | {r['security_code'] for r in res['watch']}
        tab_entry = {r['security_code'] for r in engine.query(
            'select security_code from analytics.system_entry where trade_date = %s '
            'and spec_version = %s and entry_ok', (d, S.SPEC_VERSION), role='reader') or []}
        tab_all = {r['security_code'] for r in engine.query(
            'select security_code from analytics.system_entry where trade_date = %s '
            'and spec_version = %s', (d, S.SPEC_VERSION), role='reader') or []}
        hist = S.history_bars(S.last_daily_day(d) or d)
        pre = set(S.candidates(hist))
        ok = mine == tab_entry and mine_all == tab_all
        cover = tab_all <= pre
        bad += 0 if (ok and cover) else 1
        print(f'{d} 全过 {len(mine)}/表{len(tab_entry)} 母体 {len(mine_all)}/表{len(tab_all)} '
              f'②预筛 {len(pre)} 只'
              + ('⊇表 ✓' if cover else f'漏票 ✗ {sorted(tab_all - pre)}')
              + (' → 一致' if ok else f' → 不一致 多:{sorted(mine - tab_entry)} 少:{sorted(tab_entry - mine)}'))
    print('对账结论：' + ('全部一致（判据 + 预筛无漏票）' if not bad else f'{bad} 场有问题'))
    return 0 if not bad else 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description='D5a · 盘中交易体系榜取数落库（不发）')
    ap.add_argument('--dry-run', action='store_true', help='只算，一行都不写')
    ap.add_argument('--offline', action='store_true', help='用库里已落的日线当"今天"（复盘/对账）')
    ap.add_argument('--asof', default='', help='YYYY-MM-DD，指定"今天"')
    ap.add_argument('--force', action='store_true', help='跳过交易日判断')
    ap.add_argument('--check-days', type=int, default=0, help='与 analytics.system_entry 逐票对账 N 场')
    ap.add_argument('--tq-check', action='store_true', help='只验本地通达信链路，不落库不发送')
    a = ap.parse_args(argv)
    if a.check_days:
        return check_days(a.check_days)
    return one_round(dry_run=a.dry_run, offline=a.offline,
                     asof=dt.date.fromisoformat(a.asof) if a.asof else None,
                     force=a.force, tq_check=a.tq_check)


if __name__ == '__main__':
    raise SystemExit(main())
