# -*- coding: utf-8 -*-
"""
A2 · 竞价速览（09:25:30 一针）——外部 `intraday_zt/auction_report.py` 迁入。

三块：涨幅 >min_pct% 降序榜、跌幅 <-min_pct% 升序榜（**两侧独立取数**：一侧挂了不影响
另一侧出榜，挂的那侧落一行 `unavailable=True` 并写明原因——否则库里只能看出"那天没有跌幅榜"，
与"没取到"两种情况事后分不开），再加「竞价一字」块（首封恰好 09:25＝开盘即封死）。
每侧后面跟一行概念计数（与热度、午间**同一份 SQL、同一个泛标签剔除门槛**），
并附该板块**上一交易日收盘**涨幅（板块指数在竞价时段没有有效价，当日竞价板块涨幅不存在 ⇒ 不造）。

市场级新鲜判据：全 A 榜里 |涨幅| ≥ 0.05% 的家数 ≥ `fresh_min_movers`(30) 才认为竞价价已刷新；
不足重拉 `retry`(3) 次、间隔 `retry_wait_s`(15) 秒；仍不过 ⇒ 记 `auction_unready` 并按需发
"未就绪"占位 —— **绝不拿昨收盘顶**。

    python -m ingest.jobs.auction_report --dry-run     # 取数+渲染，不落库不发送
    python -m ingest.jobs.auction_report --force       # 非 9 点档手工跑（文案自标"样机演示"）
    python -m ingest.jobs.auction_report               # 落库
    python -m ingest.jobs.auction_report --send        # 落库 + 发送
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))
try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')      # type: ignore[attr-defined]
except Exception:  # noqa: BLE001
    pass

from app.intraday import sources, store                             # noqa: E402
from app.intraday.pool_events import SPEC_VERSION, money_text       # noqa: E402
from ingest.jobs import zt_watch                                    # noqa: E402  池子出口只有一份

CFG = {**{'min_pct': 5.0, 'top_n': 60, 'sector_top': 10, 'max_members': 1500,
          'channel': 'feishu-main', 'page_size': 100, 'max_pages': 3,
          'fresh_min_movers': 30, 'retry': 3, 'retry_wait_s': 15},
       **json.loads((ROOT / 'app' / 'intraday' / 'broadcast_config.json').read_text(encoding='utf-8'))}
TPL = json.loads((ROOT / 'app' / 'intraday' / 'templates.json').read_text(encoding='utf-8'))


def _log(now, what, **kw):
    """写库失败不许把播报带崩：这是留痕，不是主流程。"""
    try:
        store.write_broadcast(now, task='auction', what=what, **kw)
    except Exception:  # noqa: BLE001
        pass


def boards(cfg: dict, now):
    """两榜 + 新鲜度达标。返回 `(up, down, src_up, src_down, 未就绪原因)`；拿不到榜 ⇒ up=None。"""
    for attempt in range(1, cfg['retry'] + 1):
        try:
            up, src_up = sources.board(cfg, asc=False,
                                       note=lambda w, x: _log(now, w, counts=x))
            try:
                down, src_down = sources.board(cfg, asc=True,
                                               note=lambda w, x: _log(now, w, counts=x))
            except Exception as exc:  # noqa: BLE001  跌幅侧挂了不影响涨侧出榜
                down, src_down = [], None
                _log(now, 'auction_down_unavailable', err=f'{type(exc).__name__}: {exc}'[:300])
            if sources.movers(up) + sources.movers(down) >= cfg['fresh_min_movers']:
                return up, down, src_up, src_down, ''
            _log(now, 'auction_stale', counts={'attempt': attempt,
                                               'movers': sources.movers(up) + sources.movers(down),
                                               'need': cfg['fresh_min_movers']})
        except Exception as exc:  # noqa: BLE001
            _log(now, 'auction_poll_fail', err=f'{type(exc).__name__}: {exc}'[:300],
                 counts={'attempt': attempt})
        if attempt < cfg['retry']:
            time.sleep(cfg['retry_wait_s'])
    return None, None, None, None, f'{cfg["retry"]} 次都没过新鲜判据（竞价价可能还没刷新）'


def _side_block(board_key, rows, cfg, now, board_rows, concept_rows):
    """渲染一侧 + 攒它的落库行。返回要拼进正文的几行。"""
    out = []
    shown = rows[:cfg['top_n']]
    if board_key == 'up':
        out.append(TPL['auction_header'].format(t=f'{now:%H:%M:%S}', min_pct=cfg['min_pct'],
                                                n=len(rows)))
    else:
        out.append((TPL['auction_down_header'] if rows else TPL['auction_down_none'])
                   .format(min_pct=cfg['min_pct'], n=len(rows)))
    if rows:
        out.append(TPL['auction_stocks'].format(rows='、'.join(
            TPL['auction_stock_row'].format(name=r['name'], pct=f"{r['pct']:+.2f}%")
            for r in shown)))
    hits, unmapped = sources.sector_counts([r['code'] for r in rows],
                                           max_members=cfg['max_members'],
                                           sector_top=cfg['sector_top'])
    pcts = sources.sector_pcts([h['name'] for h in hits])
    if hits:
        out.append(TPL['auction_sectors'].format(rows='、'.join(
            sources.concept_rows(hits, pcts, tpl_row=TPL['auction_sector_row'],
                                 tpl_pct_row=TPL['auction_sector_pct_row']))))
    for i, h in enumerate(hits, 1):
        got = pcts.get(h['name'])
        concept_rows.append((now.date(), now, f'auction_{board_key}', h['name'], SPEC_VERSION,
                             h.get('sector_code'), False, h['n'], i, None, None, None, None,
                             got[0] if got else None, got[1] if got else None, unmapped))
    src = None
    for i, r in enumerate(shown, 1):
        board_rows.append((now.date(), board_key, i, SPEC_VERSION, r['code'], r['name'],
                           r['pct'], r['amount'], None, None, None, None,
                           len(rows), max(0, len(rows) - len(shown)), src or 'unknown',
                           False, None, now))
    if not rows and board_key == 'down':
        board_rows.append((now.date(), board_key, 0, SPEC_VERSION, None, None, None, None,
                           None, None, None, None, 0, 0, src or 'unknown', True,
                           '跌幅侧本轮没取到', now))
    return out


def one_round(*, dry_run: bool, send: bool, force: bool, channel: str) -> int:
    now = dt.datetime.now()
    if now.hour != 9 and not force:
        print(f'[{now:%H:%M:%S}] 不在 9 点档（竞价价还没产生）—— 手工看数请加 --force')
        return 0
    open_day = zt_watch.is_trade_day(now)
    if open_day is False or (open_day is None and now.weekday() > 4):
        print(f'[{now:%H:%M:%S}] 非交易日，静默退出')
        return 0
    up, down, _su, _sd, why = boards(CFG, now)
    if up is None:
        text = f"【竞价速览 {now:%H:%M:%S}】未就绪（{why}），本轮不播。"
        print(text)
        if not dry_run:
            _log(now, 'auction_unready', body=text, err=why)
            if send:
                _send(now, text, channel)
        return 1
    # 非 9 点手工跑拿到的是当日累计涨幅，不是竞价价 —— 明标在正文里，不装作是竞价榜
    demo = '' if now.hour == 9 else '（样机演示：非竞价时点取的数，不是竞价价）'
    board_rows: list[tuple] = []
    concept_rows: list[tuple] = []
    lines: list[str] = []
    lines += _side_block('up', up, CFG, now, board_rows, concept_rows)
    lines += _side_block('down', down, CFG, now, board_rows, concept_rows)
    yizi_n = 0
    try:
        cur_pool, _zb, _dt = zt_watch.fetch_pools(now)
        hits = sources.yizi_from_pool(list(cur_pool.values()))
        yizi_n = len(hits)
        if hits:
            lines.append(TPL['yizi_header'].format(n=yizi_n))
            for i, r in enumerate(hits, 1):
                lines.append(TPL['yizi_row'].format(
                    name=r['name'], code=r['code'],
                    lbc=(f"{r['board_count']}板" if r['board_count'] is not None else '板数缺数'),
                    pct=f"{r['pct']:+.2f}%" if r['pct'] is not None else '涨幅缺数',
                    fund=money_text(r['seal_fund_yuan'])))
                board_rows.append((now.date(), 'yizi', i, SPEC_VERSION, r['code'], r['name'],
                                   r['pct'], None, r['board_count'], r['seal_fund_yuan'],
                                   r['first_seal_time'], r['industry'], yizi_n, 0,
                                   'eastmoney_push2ex', False, None, now))
        else:
            lines.append(TPL['yizi_none'])
    except Exception as exc:  # noqa: BLE001
        lines.append(TPL['yizi_fail'].format(why=type(exc).__name__))
    lines.append(TPL['auction_footer'].format(t=f'{now:%H:%M:%S}'))
    text = '\n'.join(lines) + demo
    print(text)
    if dry_run:
        print(f"（dry-run：榜行 {len(board_rows)}、概念行 {len(concept_rows)} 未落库）")
        return 0
    store.upsert_auction_rows(board_rows)
    store.upsert_concept_counts(concept_rows)
    _log(now, 'auction', body=text, counts={'up_n': len(up), 'down_n': len(down),
                                            'yizi_n': yizi_n})
    if send:
        _send(now, text, channel)
    return 0


def _send(now, text, channel):
    from duanxian import notify
    res = notify.send_to([channel], '竞价速览', text)
    store.write_broadcast(now, task='auction', what='sent', body=text,
                          ok=bool(res.get('ok')), delivered=bool(res.get('ok')),
                          channel_key=channel,
                          err=None if res.get('ok') else str(res.get('detail'))[:300])
    print(f'  发送 → {channel}：{"已确认" if res.get("ok") else "未确认"}')


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description='A2 · 竞价速览（09:25:30 单次）')
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--send', action='store_true')
    ap.add_argument('--force', action='store_true', help='非 9 点档手工跑（文案标样机演示）')
    ap.add_argument('--wake-at', default='', help='睡到该时刻再取数，如 09:25:30')
    ap.add_argument('--channel', default=CFG['channel'])
    a = ap.parse_args(argv)
    if a.wake_at:
        target = dt.datetime.combine(dt.date.today(),
                                     dt.datetime.strptime(a.wake_at, '%H:%M:%S').time())
        while dt.datetime.now() < target:
            time.sleep(max(0.1, min(5.0, (target - dt.datetime.now()).total_seconds())))
    return one_round(dry_run=a.dry_run, send=a.send, force=a.force, channel=a.channel)


if __name__ == '__main__':
    raise SystemExit(main())
