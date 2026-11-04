# -*- coding: utf-8 -*-
"""D5b · 盘中「交易体系榜」推送（14:40:30 那一炉）—— **只读库，一次源站都不碰**。

分工（用户 11-03 定：取数落库与发送分两层，互不影响）：判据与快照归 `ingest/jobs/push/t06_sysentry_fill.py`
（14:39 那一针，落 `analytics.intraday_sysentry`），本脚本只做"读那张表 → 排版 → 发渠道"。

所以这里一次网络请求都不发（除了发渠道那一下）。好处是实打实的：
14:40 那条没发出去、或文案改了想重发，任何时候重跑本脚本，榜上的数与当时一字不差；
而老写法"边取边发"一旦快照源翻页或时点错位，重跑就会发出另一场数，那种差别没人能察觉。

    python -m ingest.jobs.push.t07_sysentry_report --dry-run                 # 只读库排版，不发
    python -m ingest.jobs.push.t07_sysentry_report                           # 排版 + 落留痕（不发）
    python -m ingest.jobs.push.t07_sysentry_report --send                    # 真发（渠道读系统配置）
    python -m ingest.jobs.push.t07_sysentry_report --at 14:39:05 --send      # 补发某一针的结果

库里今天一行都没有 ⇒ **不发榜**，播一句"行情未就绪"并写清为什么（A 侧那一针的失败原因），
记 `sysentry_no_data`。绝不拿昨天的行顶成"今天的榜"，也不发一条空榜冒充"看过了"。
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
import time
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

TPL_FILE = ROOT / 'app' / 'intraday' / 'templates.json'
TASK = 'sysentry'
#: 等 A 那一针的上限（`sysentry_wait_s`）与回看间隔：TQ 一次要跑几十只票，慢的时候真会跨过半分钟
POLL_S = 20
DRY_RUN = False


def _log(now, what, **kw):
    if DRY_RUN:
        return 0
    try:
        store.write_broadcast(now, task=TASK, what=what, **kw)
    except Exception:  # noqa: BLE001
        pass


def tpl() -> dict:
    """读文案模板。读不出来就**抛** —— 用一套编出来的措辞去播，等于把口径改掉。"""
    return json.loads(TPL_FILE.read_text(encoding='utf-8'))


def latest_run(day: dt.date, wait_s: int) -> dict | None:
    """今天最后一针落库的结果；没有就最多回看 `wait_s` 秒（等 A 那一针跑完）。"""
    deadline = time.monotonic() + max(0, int(wait_s))
    while True:
        run = store.sysentry_latest_run(day)
        if run and run.get('captured_at'):
            return run
        if time.monotonic() >= deadline:
            return None
        time.sleep(min(POLL_S, max(1, int(deadline - time.monotonic()))))


def last_fill_reason(day: dt.date) -> str:
    """A 侧今天最后那次尝试怎么收场的（只读留痕，用来回答"为什么没有榜"）。"""
    row = engine.query_one(
        "SELECT what, err FROM analytics.intraday_broadcast"
        " WHERE trade_date = %s AND task = %s AND what IN ('sysentry_fill_fail', 'sysentry_thin')"
        ' ORDER BY at_ts DESC LIMIT 1', (day, TASK + '_fill'), role='reader')
    if not row:
        return '今天没有 14:39 那一针的记录（任务没跑，或没到点）'
    return f"{row['what']}：{str(row.get('err') or '')[:120]}"


def fmt_stock(r: dict, i: int, t: dict) -> str:
    """一行票。缺的那一格显式写"缺数"，不填空、不拿 0 顶。"""
    af = S._f(r.get('adjustment_factor') or 0) or 1.0
    stop = (S._f(r['stop_ref_hfq']) / af) if r.get('stop_ref_hfq') else None
    close, pre = S._f(r.get('close')), S._f(r.get('pre_close'))
    pct = S._f(r.get('pct')) if r.get('pct') is not None else None
    stop_dist = (100.0 * (S._f(r['stop_ref_hfq']) / (close * af) - 1.0)
                 if stop and close else None)
    gap_date = r.get('gap_date')
    return t['sysentry_row'].format(
        i=i, name=r.get('security_name') or '', code=r['security_code'],
        price=f'{close:.2f}', pct=f'{pct:+.2f}%' if pct is not None else '涨幅缺数',
        gap_date=f'{gap_date:%m-%d}' if gap_date else '缺数',
        gap_pct=f"{S._f(r.get('gap_pct')):.2f}"
        if r.get('gap_pct') is not None else '缺数',
        unfilled=r.get('gap_unfilled') if r.get('gap_unfilled') is not None else '缺数',
        pb_ratio=(f"{S._f(r['pullback_max_vol_ratio']):.3f}"
                  if r.get('pullback_max_vol_ratio') is not None else '缺数'),
        pb_down=r.get('pullback_down_cnt') if r.get('pullback_down_cnt') is not None else '缺数',
        stop=f'{stop:.2f}' if stop else '缺数',
        stop_dist=(f'{stop_dist:+.1f}' if stop_dist is not None else '缺数'))


def build_message(rows: dict, summary: dict | None, run: dict, now: dt.datetime,
                  cfg: dict, *, at_all: bool | None = None) -> str:
    T, s = tpl(), (summary or {})
    entry, watch = rows.get('entry') or [], rows.get('watch') or []
    stamp = f'{now:%H:%M:%S}'
    lines = []
    if (cfg.get('sysentry_at_all') if at_all is None else at_all):
        lines.append(T['sysentry_atall'])
    lines.append(T['sysentry_header'].format(t=stamp, n=len(entry), m=len(watch)))
    if entry:
        for i, r in enumerate(entry[:int(cfg.get('sysentry_max_rows') or 30)], 1):
            lines.append(fmt_stock(r, i, T))
        if len(entry) > int(cfg.get('sysentry_max_rows') or 30):
            lines.append(f"…其余{len(entry) - int(cfg.get('sysentry_max_rows') or 30)}只未列")
    else:
        lines.append(T['sysentry_none'].format(m=len(watch)))
    extra = []
    for label, key in (('停牌/无报价未判', 'no_quote'), ('除权基不符未判', 'exright'),
                       ('涨停价与东财池不一致', 'mismatch')):
        n = s.get(key)
        if n:                                           # 0 与 None 都不念：没发生的事不占一行
            extra.append(f'{label}{n}只' + ('（见留痕）' if key == 'mismatch' else ''))
    if cfg.get('sysentry_watch_list') and watch:
        extra.append('观察池：' + '、'.join(r['security_code'] for r in watch[:40]))
    if not summary:
        extra.append('摘要缺数：A 那一针的留痕行没读到，未判只数不能当成 0')
    if extra:
        lines.append('；'.join(extra))
    note = ('离线：今日那根取的是库里已落的日线，不是实时快照'
            if '库内日线' in str(run.get('quote_source') or '') else '盘中未收盘，收盘数以15:59收盘链为准')
    lines.append(T['sysentry_footer'].format(
        spec=S.SPEC_VERSION, last_day=s.get('last_day') or str(run.get('quote_day') or '缺数'),
        src=run.get('quote_source') or '缺数',
        proven=s.get('proven') or s.get('last_day') or '缺数', t=stamp, note=note))
    return '\n'.join(lines)


def one_round(*, dry_run: bool, send: bool, force: bool, channel: str | None,
              at: str | None) -> int:
    global DRY_RUN
    DRY_RUN = dry_run
    now = dt.datetime.now()
    day = now.date()
    cfg, _warns = tasks.load_cfg()
    if at:
        # 补发：点名要今天哪一针的结果（`--at 14:39` 或 `--at 14:39:05`）。只读库，不重算、不重新取数。
        h, m, sec = ([int(x) for x in at.split(':')] + [0, 0])[:3]
        want = dt.datetime.combine(day, dt.time(h, m, sec)).astimezone()
        run = store.sysentry_run_at(day, want)
        if not run:
            why = f'今天 {at} 前后两分钟内没有落库的那一针'
            print(f'[{now:%H:%M:%S}] {why} —— 不发榜（不拿别的时针冒充）')
            _log(now, 'sysentry_no_data', ok=False, err=why)
            return 1
        print(f"[{now:%H:%M:%S}] 按 {at} 找到 {run['captured_at']:%H:%M:%S} 那一针"
              f"（差 {float(run['drift_s']):.3f} 秒）")
    else:
        if not force:
            if not zt_watch.is_trading_day(now):
                print(f'[{now:%H:%M:%S}] 非交易日 —— 不推')
                return 0
            if not zt_watch.in_session(now):
                print(f'[{now:%H:%M:%S}] 不在交易时段 —— 不推（收盘后要看请带 --force）')
                return 0
        run = latest_run(day, int(cfg.get('sysentry_wait_s') or 0))
    if not run:
        why = last_fill_reason(day)
        print(f'[{now:%H:%M:%S}] 库里今天还没有这一场的数 —— 不发榜（{why}）')
        _log(now, 'sysentry_no_data', ok=False, err=why[:300])
        if send and not dry_run:
            keys = tasks.send_channels(channel)
            text = tpl()['sysentry_stale'].format(t=f'{now:%H:%M:%S}', why=why)
            _send(now, text, keys, counts={'reason': why})
        return 1
    rows = store.sysentry_rows(day, run['captured_at'])
    summary = store.sysentry_fill_summary(day, run['captured_at'])
    text = build_message(rows, summary, run, now, cfg)
    asof = f"{run['captured_at']:%H:%M:%S}"
    print(f'[{now:%H:%M:%S}] 榜取 {asof} 那一针 · '
          f"三条全过 {len(rows.get('entry') or [])} · 观察池 {len(rows.get('watch') or [])}")
    print(text)
    counts = {'asof': asof, 'entry_n': len(rows.get('entry') or []),
              'watch_n': len(rows.get('watch') or []), 'src': run.get('quote_source'),
              'summary_found': bool(summary)}
    if dry_run:
        print('（dry-run：未写账、未发送）')
        return 0
    _log(now, 'sysentry', body=text, counts=counts)
    if send:
        keys = tasks.send_channels(channel)
        _send(now, text, keys, counts=counts)
    return 0


def _send(now, text, keys, *, counts):
    """发渠道。**只发一次**：没确认就记失败并写清下一步，不自动降级重发。

    带 @所有人 的那一版如果被群拒收，自动去@重发会在"其实已经收到"的情况下撞出两条榜
    （外部那份就是这么做的，代价是偶发双发）。这里改成留一句人话让人决定：
    把 `sysentry_at_all` 关掉再手工补发（`--send --force`），判定权交回给人。
    """
    from duanxian import notify
    res = notify.send_to(keys, '盘中交易体系榜', text)
    ok = bool(res.get('ok'))
    err = None if ok else str(res.get('detail'))[:300]
    if not ok:
        err += '（带@所有人发的这一条没确认；若群没开@所有人，请把 sysentry_at_all 关掉后手工 ' \
               '`python -m ingest.jobs.push.t07_sysentry_report --send --force` 补发）'
    store.write_broadcast(now, task=TASK, what='sent', body=text, ok=ok, delivered=ok,
                          channel_key=','.join(keys), counts=counts, err=err)
    verdict = '已确认' if ok else f'未确认：{str(res.get("detail"))[:160]}（判据是渠道业务码，不是 HTTP 200）'
    print(f'  发送 → {",".join(keys)}：{verdict}')


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description='D5b · 盘中交易体系榜推送（只读库）')
    ap.add_argument('--dry-run', action='store_true', help='只排版，不写账不发送')
    ap.add_argument('--send', action='store_true', help='发渠道（不给就只落留痕）')
    ap.add_argument('--channel', default=None,
                    help='逗号分隔的渠道串；不给就读系统配置「盘中播报」那一格')
    ap.add_argument('--force', action='store_true', help='跳过交易日与时段判断（手工补发）')
    ap.add_argument('--at', default=None, help='补发某一针：HH:MM:SS（默认取今天最后一针）')
    a = ap.parse_args(argv)
    return one_round(dry_run=a.dry_run, send=a.send, force=a.force, channel=a.channel,
                     at=a.at)


if __name__ == '__main__':
    raise SystemExit(main())
