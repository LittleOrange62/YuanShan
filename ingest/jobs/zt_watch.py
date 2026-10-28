# -*- coding: utf-8 -*-
"""
Z1 · 盘中涨跌停池事件差 —— 单次一针（外部 `intraday_zt/zt_watcher.py` 的整体迁入）。

跑法：
    python -m ingest.jobs.zt_watch --once                # 取一针、判事件、**只落库**
    python -m ingest.jobs.zt_watch --once --dry-run      # 连库都不写，只打印
    python -m ingest.jobs.zt_watch --once --send         # 落库 + 发渠道（默认不发）

**默认不发送**：外部那套每次触发都往飞书盯盘群推。迁过来后发送要显式 `--send`，
理由是"发到群里"是别人看得见的动作，不该由一次调试运行顺带做掉；
节奏也交给调度（下面），不由这个脚本自己循环。

---- 为什么是"单次短跑"而不是常驻线程（用户 2026-10-26 定：全部搬进来）----

外部 10-23(二) 事故登记：watcher 常驻 loop 在 09:29 建完基线后**静默死亡**
（无 traceback、无日志、任务计划程序还显示"运行中"），盘中 8 轮全瞎，中午才发现
状态文件停在 5 家。修法是弃常驻、改**每日触发器 + 每 5 分钟重复的单次 `--once`**
—— 调度器本身就是看门狗，卡死由执行时限兜底，最坏瞎一轮。
所以这一层搬进来的是**判据与落库**，执行形态保持"到点起一个短进程"，
不在 uvicorn 里开常驻线程（那等于把人家踩过的坑再踩一遍）。

---- 与库里另一张表的关系 ----
`analytics.limit_up_pool_daily`（DDL 53）＝一场一行的**日级母体**，家数以本地日线为准；
本作业写 `analytics.intraday_pool_event`（DDL 54）＝一日多行的**盘中时序**，
判据是相邻两针东财池之差，出处只能是源站。两边的数**允许不一致**，行上带 `basis`，
读侧与界面不许把它们混成一个数。
"""
from __future__ import annotations

import argparse
import datetime as dt
import importlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))
try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')      # type: ignore[attr-defined]
except Exception:  # noqa: BLE001
    pass

from app.db import engine                                    # noqa: E402
from app.intraday import store                               # noqa: E402
from app.intraday.pool_events import (KIND_LABELS, KIND_ORDER, SPEC_VERSION,   # noqa: E402
                                      digest, diff_events, fmt_rows, normalize)
from duanxian.live_emotion import POOL_SORT                  # noqa: E402  排序键只有一份定义

#: 基线状态。语义与外部一致：换日清空、盘前空池不建"0 家"假基线、跌停轨独立判首轮。
STATE_FILE = ROOT / 'var' / 'intraday_watch_state.json'
#: 文案原样沿用外部那份（42 个键，用户已定版）—— **改文案只动这个文件**，不碰代码。
#: 放在这里而不是并进 `notifyconfig`：那套的档内容是"现读接口自己就接受的参数"
#: （limit/min_energy/signal_type…），而播报正文是渲染成品，两者契约不同，
#: 硬塞会让面板能勾、执行体读不到（那是本项目明令禁止的半截功能）。
TPL_FILE = ROOT / 'app' / 'intraday' / 'templates.json'
CHANNEL_DEFAULT = 'feishu-main'
_TPL_CACHE: dict = {}


def tpl() -> dict:
    """读文案模板。读不出来就**抛** —— 用一套悄悄编的默认措辞去播，等于把口径改掉。"""
    if not _TPL_CACHE:
        _TPL_CACHE.update(json.loads(TPL_FILE.read_text(encoding='utf-8')))
    return _TPL_CACHE


def _pool_fetcher():
    """取原始池的出口：常驻进程里用已加载的 `astock`，独立作业里现 import `vr.astock`。

    ⚠️ 不另写一份 HTTP 取数 —— 同一段接口逻辑出现两份，迟早一份修了另一份没修
    （跌停池的 `fund:asc` 就是这么在 10-26 被我们这边漏掉的）。
    """
    mod = sys.modules.get('astock')
    if mod is None:
        try:
            mod = importlib.import_module('vr.astock')
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(f'取不到 astock 出口：{exc}') from None
    fetch = getattr(mod, '_pool_unpinned', None) or getattr(mod, 'em_zt_topic_pool', None)
    if fetch is None:
        raise RuntimeError('astock 里没有 em_zt_topic_pool / _pool_unpinned 出口')
    return fetch


def fetch_pools(now: dt.datetime) -> tuple[dict, dict, dict]:
    """→ (涨停 {code:row}, 炸板 {code:row}, 跌停 {code:row})。取不到**抛**，不返回空当"0 家"。"""
    fetch = _pool_fetcher()
    ymd = f'{now:%Y%m%d}'
    out = {}
    for endpoint, alias in (('getTopicZTPool', 'zt'), ('getTopicZBPool', 'zb'),
                            ('getTopicDTPool', 'dt')):
        rows = fetch(endpoint, ymd, POOL_SORT[endpoint])
        if rows is None:
            raise RuntimeError(f'{endpoint} 取数失败（返回 None）')
        out[alias] = {r['code']: r for r in (normalize(x) for x in rows) if r['code']}
    return out['zt'], out['zb'], out['dt']


def load_state(now: dt.datetime) -> dict:
    state = {}
    if STATE_FILE.is_file():
        try:
            state = json.loads(STATE_FILE.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            state = {}                      # 状态坏=重建基线，不拦今天的针；但下面会留一行日志
    if state.get('date') and state.get('date') != f'{now:%Y-%m-%d}':
        state = {'date': f'{now:%Y-%m-%d}'}     # 换日清基线
    return state


def save_state(state: dict) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE_FILE.with_suffix('.json.tmp')
    tmp.write_text(json.dumps(state, ensure_ascii=False), encoding='utf-8')
    tmp.replace(STATE_FILE)


def is_trade_day(now: dt.datetime) -> bool | None:
    """PG 交易日历（列名 `calendar_date`，不是 `cal_date`）。查不到 → None，调用方按周末兜底。"""
    try:
        return engine.scalar(
            'SELECT is_open FROM reference_data.trade_calendar WHERE calendar_date = %s',
            (now.date(),), role='reader')
    except Exception:  # noqa: BLE001  库挂了不拦盘中采集，退化成工作日判断
        return None


#: 排列与 emoji 都按外部定版那份（`zt_watcher.py:133-136`），只是中文标签改从 `KIND_LABELS` 取
KIND_EMOJI = {'unseal': '🔴', 'dt_open': '🟠', 'dt_seal_new': '🟣',
              'dt_reseal': '🟣', 'dt_reseal_after_open': '🟣'}


def render(kind_groups: dict, now: dt.datetime, pool_n: int, dt_n: int) -> str:
    """按 `templates.json` 那三个键渲染，**措辞一个字不改**。"""
    t = f'{now:%H:%M:%S}'
    T = tpl()
    lines = [T['zt_header'].format(t=t)]
    for kind in KIND_ORDER:
        rows = kind_groups.get(kind)
        if not rows:
            continue
        label = KIND_LABELS[kind]
        lines.append(T['zt_line'].format(
            emoji=KIND_EMOJI.get(kind, '🟢'), kind=label, n=len(rows),
            rows=fmt_rows(rows, 6, board='连跌停' if '跌停' in label else '连板')))
    lines.append(T['zt_footer'].format(pool_n=pool_n, dt_n=dt_n, t=t))
    return '\n'.join(lines)


#: 两针之间隔过这么久就**不做差**：差出来的是"这几小时里累计的所有变化"，
#: 逐条标成「新封/新跌停」是把长期累计冒充盘中事件（10-26 19:31 手动补发就踩过：
#: 上一针停在 13:08，收盘后一键下去，群里收到"新跌停 12 只"）。
STALE_GAP_MIN = 12


def in_session(now: dt.datetime) -> bool:
    """交易时段（含 9:24 起与 11:32/12:58 前后各留一分钟缓冲，同外部定版）。"""
    hm = now.hour * 100 + now.minute
    return (924 <= hm <= 1132) or (1258 <= hm <= 1503)


#: 每多这么多只涨停或跌停才播一次（用户 10-26 定：不是每 5 分钟无脑播一遍）。
#: 只影响**播报**——每一针的事件仍照常落 `analytics.intraday_pool_event`，入库不省。
REPORT_STEP_N = 5
#: 概念榜取前几（用户定：涨停前三、跌停前三）。泛标签门槛与竞价/热度同一份。
CONCEPT_TOP = 3
CFG_MAX_MEMBERS = 1500


def report_text(now: dt.datetime, cur: dict, cur_dt: dict, prev: dict, prev_dt: dict,
                *, baseline: bool) -> str:
    """
    新格式（旧的页脚"—东财涨跌停池 · 涨停xx/跌停xx @时间"那行按用户要求**去掉**）：

        14:05:10 · 涨停58/跌停21
        新增涨停：A、B、C
        新增跌停：D
        涨停减少：E、F
        跌停减少：G
        涨停概念前三：机器人概念×9、AI×6、华为概念×4
        跌停概念前三：光伏×3、芯片×2

    首轮（9:25 那一针）只报家数与概念前三——那时无比对照，不硬编"变化"。
    缺的那一格不念（`pool_events.fmt_rows`）；概念计数与竞价/热度**同一份 SQL**。
    """
    lines = [f'{now:%H:%M:%S} · 涨停{len(cur)}/跌停{len(cur_dt)}']
    if not baseline:
        for label, now_pool, was in (('涨停', cur, prev), ('跌停', cur_dt, prev_dt)):
            added = {k: v for k, v in now_pool.items() if k not in was}
            gone = {k: v for k, v in was.items() if k not in now_pool}
            if added:
                lines.append(f'新增{label}：{fmt_rows(list(added.values()), 12)}')
            if gone:
                lines.append(f'{label}减少：{fmt_rows(list(gone.values()), 12)}')
    from app.intraday import sources
    for label, pool in (('涨停', cur), ('跌停', cur_dt)):
        hits, _un = sources.sector_counts(sorted(pool), max_members=CFG_MAX_MEMBERS,
                                          sector_top=CONCEPT_TOP, min_hits=1)
        lines.append(f'{label}概念前三：' +
                     ('、'.join(f"{h['name']}×{h['n']}" for h in hits) or '无命中'))
    return '\n'.join(lines)


def one_round(*, dry_run: bool, send: bool, channel: str, quiet_first: bool) -> int:
    now = dt.datetime.now()
    open_day = is_trade_day(now)
    if open_day is False or (open_day is None and now.weekday() > 4):
        print(f'[{now:%H:%M:%S}] 非交易日，静默退出（不发不写）')
        return 0
    state = load_state(now)
    # 收盘后手工跑（或调度器被 StartWhenAvailable 补跑）：只取数、不判事件、不发送。
    # 判"要不要出声"的根据是**这一针与上一针的间隔**，不是"我现在是不是被调度起来的"。
    gap_min = None
    prev_at = state.get('at')
    if prev_at:
        try:
            gap_min = (now - dt.datetime.fromisoformat(prev_at)).total_seconds() / 60.0
        except ValueError:
            gap_min = None
    if not in_session(now) or (gap_min is not None and gap_min > STALE_GAP_MIN):
        why = ('不在交易时段' if not in_session(now)
               else f'与上一针隔了 {gap_min:.0f} 分钟（> {STALE_GAP_MIN}）')
        print(f'[{now:%H:%M:%S}] {why} —— 只刷新基线，不判事件、不发送：'
              '长空档做差会把几小时的累计变化冒充成"新封/新跌停"')
        if not dry_run:
            save_state({'date': f'{now:%Y-%m-%d}', 'at': now.isoformat(),
                        'round_seq': int(state.get('round_seq') or 0),
                        'seen': sorted(set(state.get('seen') or [])), 'pool': {},
                        'seen_dt': sorted(set(state.get('seen_dt') or [])), 'dt_pool': {}})
            store.write_broadcast(now, task='watcher', what='skipped_offsession',
                                  err=why, counts={'gap_min': round(gap_min or 0, 1)})
        return 0
    try:
        cur, zb, cur_dt = fetch_pools(now)
    except Exception as exc:  # noqa: BLE001  宁缺毋假：不拿上一针顶
        print(f'[{now:%H:%M:%S}] 池子取不到：{exc}', file=sys.stderr)
        if not dry_run:
            store.write_broadcast(now, task='watcher', what='poll_fail', err=str(exc)[:300])
        return 1

    prev = state.get('pool') or {}
    prev_dt = state.get('dt_pool') or {}
    seen = set(state.get('seen') or [])
    seen_dt = set(state.get('seen_dt') or [])
    first_round = not prev
    if first_round and not cur:
        print(f'[{now:%H:%M:%S}] 盘前池为空，不建基线（避免"0 家"假基线）')
        return 0
    dt_ready = 'dt_pool' in state

    events = [] if first_round else diff_events(prev, cur, zb_codes=set(zb), seen=seen, side='zt')
    if dt_ready:
        events += diff_events(prev_dt, cur_dt, seen=seen_dt, side='dt')
    elif cur_dt:
        print(f'[{now:%H:%M:%S}] 跌停轨首轮：静默建基线（不把既存量报成"新跌停"）')

    round_seq = int(state.get('round_seq') or 0) + 1
    pool_n = {'zt': len(cur), 'zb': len(zb), 'dt': len(cur_dt) if dt_ready else None}

    # ---- 播报门槛：从上一次播报的家数算起，涨停或跌停**任一**多够 REPORT_STEP_N 只才播一次。
    #      首轮（9:25 那一针）一定播——那是基准，之后每一次都跟它比。
    rep_zt = int(state.get('reported_zt') or 0)
    rep_dt = int(state.get('reported_dt') or 0)
    baseline = first_round or not state.get('reported_at')
    d_zt, d_dt = len(cur) - rep_zt, len(cur_dt) - rep_dt
    due = baseline or d_zt >= REPORT_STEP_N or d_dt >= REPORT_STEP_N
    text = report_text(now, cur, cur_dt, prev, prev_dt, baseline=baseline)
    if due:
        rep_zt, rep_dt = len(cur), len(cur_dt)
    else:
        print(f'[{now:%H:%M:%S}] 事件 {len(events)} 条已落库；距上次播报 '
              f'涨停{d_zt:+d}/跌停{d_dt:+d}，没到 {REPORT_STEP_N} 只的门槛 —— 不发')

    if not dry_run:
        written = store.write_events(now, events, pool_n=pool_n, round_seq=round_seq,
                                     is_baseline=baseline)
        store.write_broadcast(now, task='watcher', what='digest', body=text if due else '',
                              counts={'events': len(events), 'inserted': written,
                                      'pool_zt': len(cur), 'pool_dt': len(cur_dt),
                                      'round_seq': round_seq, 'announced': due},
                              source_detail={'spec_version': SPEC_VERSION})
    save_state({'date': f'{now:%Y-%m-%d}', 'round_seq': round_seq, 'at': now.isoformat(),
                'reported_at': (state.get('reported_at') if not due else now.isoformat()),
                'reported_zt': rep_zt, 'reported_dt': rep_dt,
                'seen': sorted(seen | set(cur)), 'pool': cur,
                'seen_dt': sorted(seen_dt | set(cur_dt)), 'dt_pool': cur_dt})

    print(f'[{now:%H:%M:%S}] 第 {round_seq} 针 · 事件 {len(events)} 条 · '
          f'涨停池 {len(cur)} 家 · 炸板 {len(zb)} · 跌停 {len(cur_dt)}'
          + ('（dry-run 未落库）' if dry_run else ''))
    if due:
        print(text)

    if send and due and not dry_run:
        from duanxian import notify
        res = notify.send_to([channel], '涨停动态', text)
        store.write_broadcast(now, task='watcher', what='sent', body=text,
                              ok=bool(res.get('ok')), delivered=bool(res.get('ok')),
                              channel_key=channel,
                              err=str(res.get('detail'))[:300] if not res.get('ok') else None)
        print(f'  发送 → {channel}：{"已确认" if res.get("ok") else "未确认：" + str(res.get("detail"))[:160]}'
              f'（判据是渠道业务码，不是 HTTP 200）')
    elif send and dry_run:
        print('  （dry-run：没有发送）')
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description='Z1 · 盘中涨跌停池事件差（单次一针）')
    ap.add_argument('--once', action='store_true', help='只跑一针（调度器就这么调）')
    ap.add_argument('--dry-run', action='store_true', help='不落库、不发送，只打印')
    ap.add_argument('--send', action='store_true', help='落库后推渠道（默认不推）')
    ap.add_argument('--channel', default=CHANNEL_DEFAULT)
    ap.add_argument('--announce-first', action='store_true',
                    help='首轮也播一条基线摘要（默认静默建基线）')
    args = ap.parse_args(argv)
    if not args.once:
        ap.print_help()
        return 2
    return one_round(dry_run=args.dry_run, send=args.send, channel=args.channel,
                     quiet_first=not args.announce_first)


if __name__ == '__main__':
    raise SystemExit(main())
