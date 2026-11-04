# -*- coding: utf-8 -*-
"""D1 · 盘中涨跌停变化量（六个固定时点推送）—— 只读库里的事件差，不碰源站。

与 `zt_watch.py` 的分工（用户 11-03 定：「判断与取数属于数据加工环节，跟推送分开，不要互相影响」）：

    `zt_watch --once`      每 5 分钟一针：取东财三池 → 算事件差 → **只落库**（不再发送）
    `zt_delta`（本脚本）    固定六点：读 `analytics.intraday_pool_event` → 汇总窗口变化量 → 发送

所以这个脚本**一次网络请求都不发**：它的全部输入是已经落库的那些行。
好处是实打实的 —— 六点里任何一次都可以事后重跑（补发、改文案后重发），拿到的数与当时一致；
而老写法"边取边发"一旦源站翻页或时点错位，重跑就会发出不一样的内容，那种差别没人能察觉。

    python -m ingest.jobs.push.t03_zt_delta --dry-run               # 只算不发（不写账）
    python -m ingest.jobs.push.t03_zt_delta                         # 落留痕 + 发送
    python -m ingest.jobs.push.t03_zt_delta --force                 # 跳过交易日判断
    python -m ingest.jobs.push.t03_zt_delta --since 10:50           # 手工指定窗口起点（补发用）

窗口起点 = 今天上一次本路发送的时刻（`intraday_broadcast` 里 `task='delta' AND what='sent' AND ok`），
第一针之前没有发送记录 ⇒ 从上午开盘那一针算起。宁缺毋假：库里今天一行都没有 ⇒ 报"未落库"，
不拿昨天凑、也不发一条"无变化"冒充"看过了"。
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]      # push/ → jobs/ → ingest/ → 仓库根（挪目录时这一格最容易数错）
sys.path.insert(0, str(ROOT))
try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')      # type: ignore[attr-defined]
except Exception:  # noqa: BLE001
    pass

from app.db import engine                                           # noqa: E402
from app.intraday import store, tasks                               # noqa: E402
from app.intraday.pool_events import (KIND_LABELS, KIND_ORDER,      # noqa: E402
                                      SPEC_VERSION, fmt_rows)
from ingest.jobs.push import t01_zt_watch as zt_watch                                    # noqa: E402  时段与排列口径共用一份

TPL_FILE = ROOT / 'app' / 'intraday' / 'templates.json'
#: 每个窗口最多列几只（六点比每 5 分钟那一版密，正文要压得住；剩下的只报只数）
LIST_TOP = 8
TASK = 'delta'
DRY_RUN = False


def _log(now, what, **kw):
    if DRY_RUN:
        return 0
    try:
        store.write_broadcast(now, task=TASK, what=what, **kw)
    except Exception:  # noqa: BLE001
        pass


def window_start(now: dt.datetime, day: dt.date, override: str | None) -> dt.datetime:
    """这一炉的窗口起点：今天上一次推送的时刻；没有就从今天第一针算起。

    手工 `--since` 只给补发用；它同样不许跨越"换日"（昨天的事件不会混进今天这条）。
    """
    if override:
        h, m = (int(x) for x in override.split(':')[:2])
        return dt.datetime.combine(day, dt.time(h, m), tzinfo=now.tzinfo)
    last = engine.scalar(
        "SELECT max(at_ts) FROM analytics.intraday_broadcast"
        " WHERE trade_date = %s AND task = %s AND what = 'sent' AND ok IS TRUE AND spec_version = %s",
        (day, TASK, SPEC_VERSION), role='reader')
    if last:
        return last
    first = engine.scalar(
        "SELECT min(captured_at) FROM analytics.intraday_pool_event"
        " WHERE trade_date = %s AND spec_version = %s", (day, SPEC_VERSION), role='reader')
    return first or now.replace(hour=9, minute=24, second=0, microsecond=0)


def window_events(day: dt.date, lo: dt.datetime, hi: dt.datetime) -> tuple[dict, dict]:
    """→ (按事件类分组的窗口内明细, 摘要)。同一只票在一个窗口里反复出现只留**最后一次**。"""
    rows = engine.query(
        "SELECT kind, kind_label, security_code, security_name, pct, board_count, open_count,"
        "       first_seal_time, pool_zt_n, pool_zb_n, pool_dt_n, captured_at"
        "  FROM analytics.intraday_pool_event"
        " WHERE trade_date = %s AND spec_version = %s AND captured_at > %s AND captured_at <= %s"
        " ORDER BY captured_at, event_key", (day, SPEC_VERSION, lo, hi), role='reader') or []
    groups: dict[str, dict[str, dict]] = {}
    pool_now, pool_asof = None, None
    for r in rows:
        groups.setdefault(r['kind'], {})[r['security_code']] = {
            'code': r['security_code'], 'name': r['security_name'], 'pct': r['pct'],
            'board_count': r['board_count'], 'open_count': r['open_count'],
            'first_seal_time': r['first_seal_time']}
        if r['pool_zt_n'] is not None:
            pool_now = {'zt': r['pool_zt_n'], 'zb': r['pool_zb_n'], 'dt': r['pool_dt_n']}
            pool_asof = r['captured_at']
    kind_groups = {k: list(v.values()) for k, v in groups.items()}
    summary = {'n_rows': len(rows), 'n_distinct': sum(len(v) for v in kind_groups.values()),
               'pool': pool_now, 'pool_asof': pool_asof}
    return kind_groups, summary


def latest_pool(day: dt.date, hi: dt.datetime) -> tuple[dict | None, dt.datetime | None]:
    """窗口里一条事件都没有时，退到"最近一针的整池家数"——那是当时真取到的数，不是编的。"""
    row = engine.query_one(
        "SELECT pool_zt_n, pool_zb_n, pool_dt_n, captured_at AS at"
        "  FROM analytics.intraday_pool_event"
        " WHERE trade_date = %s AND spec_version = %s AND captured_at <= %s"
        "   AND pool_zt_n IS NOT NULL"
        " ORDER BY captured_at DESC LIMIT 1", (day, SPEC_VERSION, hi), role='reader')
    if not row or row.get('at') is None:
        return None, None
    return ({'zt': row['pool_zt_n'], 'zb': row['pool_zb_n'], 'dt': row['pool_dt_n']}, row['at'])


def render(kind_groups: dict, summary: dict, now: dt.datetime, lo: dt.datetime,
           pool: dict | None, pool_asof) -> str:
    T = json.loads(TPL_FILE.read_text(encoding='utf-8'))
    t = f'{now:%H:%M:%S}'
    head = f'{lo:%H:%M}'
    lines = [T['delta_header'].format(t=t, since=head)]
    for kind in KIND_ORDER:
        rows = kind_groups.get(kind)
        if not rows:
            continue
        label = KIND_LABELS[kind]
        order = '连跌停' if '跌停' in label else '连板'
        rows = sorted(rows, key=lambda r: (-(r.get('board_count') or 0), -(r.get('pct') or 0)))
        names = '、'.join(f"{r['name'] or r['code']}{'' if r.get('board_count') is None else str(r['board_count']) + '板'}"
                          for r in rows[:LIST_TOP])
        more = f' 等{len(rows)}只' if len(rows) > LIST_TOP else ''
        lines.append(T['delta_line'].format(kind=label, n=len(rows), names=names, more=more))
    if len(lines) == 1:
        lines.append(T['delta_none'])
    if pool:
        stamp = f'（截至 {pool_asof:%H:%M:%S} 那一针）' if pool_asof and \
            f'{pool_asof:%H%M%S}' != f'{now:%H%M%S}' else ''
        lines.append(T['delta_pool'].format(zt=pool['zt'], zb=pool['zb'], dt=pool['dt'], stamp=stamp))
    else:
        lines.append(T['delta_pool_none'])
    return '\n'.join(lines)


def one_round(*, dry_run: bool, send: bool, force: bool, since: str | None,
              channel: str | None) -> int:
    global DRY_RUN
    DRY_RUN = dry_run
    now = dt.datetime.now()
    if not force:
        if not zt_watch.is_trading_day(now):
            print(f'[{now:%H:%M:%S}] 非交易日 —— 不推')
            return 0
        if not zt_watch.in_session(now):
            print(f'[{now:%H:%M:%S}] 不在交易时段 —— 不推（收盘后要看请带 --force）')
            return 0
    day = now.date()
    lo = window_start(now, day, since)
    kind_groups, summary = window_events(day, lo, now)
    pool, pool_asof = summary.get('pool'), summary.get('pool_asof')
    if not pool:
        pool, pool_asof = latest_pool(day, now)
    if pool is None and not summary['n_rows']:
        # 库里今天连一行都没有 ⇒ 是采集没跑，不是"没变化"。这两种必须分开说。
        print(f'[{now:%H:%M:%S}] 库里今天还没有事件行 —— ①那一炉没落库，这一炉不发假数')
        _log(now, 'delta_no_data', err='analytics.intraday_pool_event 当天零行')
        return 1
    text = render(kind_groups, summary, now, lo, pool, pool_asof)
    print(text)
    if dry_run:
        print('（dry-run：未写账、未发送）')
        return 0
    counts = {'since': f'{lo:%H:%M:%S}', 'rows': summary['n_rows'],
              'kinds': {KIND_LABELS.get(k, k): len(v) for k, v in kind_groups.items()}}
    _log(now, 'delta', body=text, counts=counts)
    if send:
        _send(now, text, channel, counts)
    return 0


def _send(now, text, channel, counts):
    from duanxian import notify
    keys = tasks.send_channels(channel)
    res = notify.send_to(keys, '盘中涨跌停变化量', text)
    store.write_broadcast(now, task=TASK, what='sent', body=text,
                          ok=bool(res.get('ok')), delivered=bool(res.get('ok')),
                          channel_key=','.join(keys), counts=counts,
                          err=None if res.get('ok') else str(res.get('detail'))[:300])
    verdict = '已确认' if res.get('ok') else f'未确认：{str(res.get("detail"))[:160]}（判据是渠道业务码，不是 HTTP 200）'
    print(f'  发送 → {",".join(keys)}：{verdict}')


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description='D1 · 盘中涨跌停变化量（固定时点、只读库）')
    ap.add_argument('--dry-run', action='store_true', help='只算不发，也不写账')
    ap.add_argument('--send', action='store_true', help='发渠道（不给就只落留痕）')
    ap.add_argument('--channel', default=None,
                    help='逗号分隔的渠道串；不给就读系统配置「盘中播报」那一格')
    ap.add_argument('--force', action='store_true', help='跳过交易日与时段判断（手工补发）')
    ap.add_argument('--since', default=None, help='窗口起点 HH:MM（补发用，默认接上次推送）')
    a = ap.parse_args(argv)
    return one_round(dry_run=a.dry_run, send=a.send, force=a.force,
                     since=a.since, channel=a.channel)


if __name__ == '__main__':
    raise SystemExit(main())
