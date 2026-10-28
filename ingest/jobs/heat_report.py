# -*- coding: utf-8 -*-
"""
H3 · 概念热度（10:05 / 10:55 / 14:18 三针）——外部 `intraday_zt/heat_report.py` 迁入。

取**当时**的涨停池+跌停池（不是竞价口径），各自做概念计数（同一份 SQL、同一个
成员>1500 泛标签剔除门槛，见 `app/intraday/sources.sector_counts`），
只保留出现次数 ≥ `heat_min_n`（默认 3）的概念，降序；两组分开播，组内无命中播"无命中"。
池子取不到 ⇒ 记 `未就绪` 一行并退出，**不播、不拿上一针顶**。

    python -m ingest.jobs.heat_report --dry-run        # 取数+算+打印，不落库不发送
    python -m ingest.jobs.heat_report                  # 落库（analytics.intraday_concept_count）
    python -m ingest.jobs.heat_report --send           # 再加发送
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))
try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')     # type: ignore[attr-defined]
except Exception:  # noqa: BLE001
    pass

from app.intraday import sources, store                            # noqa: E402
from app.intraday.pool_events import SPEC_VERSION                  # noqa: E402
from ingest.jobs import zt_watch                                   # noqa: E402  池子出口只有一份

CFG = json.loads((ROOT / 'app' / 'intraday' / 'broadcast_config.json').read_text(encoding='utf-8'))
TPL = json.loads((ROOT / 'app' / 'intraday' / 'templates.json').read_text(encoding='utf-8'))


def one_round(*, dry_run: bool, send: bool, channel: str, force: bool = False) -> int:
    now = dt.datetime.now()
    if not force and not zt_watch.in_session(now):
        print(f'[{now:%H:%M:%S}] 不在交易时段（12:10 那类午休时点属正常）—— 不播不写')
        return 0
    try:
        cur, _zb, cur_dt = zt_watch.fetch_pools(now)
    except Exception as exc:  # noqa: BLE001
        print(f'[{now:%H:%M:%S}] 池子取不到：{exc} —— 按未就绪处理，不播')
        if not dry_run:
            store.write_broadcast(now, task='heat', what='heat_fail', err=str(exc)[:300])
        return 1
    min_n, top = int(CFG.get('heat_min_n', 3)), int(CFG.get('heat_top', 25))
    groups = {'heat_zt': sorted(cur), 'heat_dt': sorted(cur_dt)}
    rows, counts_by_bucket = [], {}
    for bucket, codes in groups.items():
        hits, unmapped = sources.sector_counts(codes, max_members=int(CFG['max_members']),
                                               sector_top=top, min_hits=min_n)
        pcts = sources.sector_pcts([h['name'] for h in hits])
        counts_by_bucket[bucket] = (hits, unmapped)
        for i, h in enumerate(hits, 1):
            got = pcts.get(h['name'])
            rows.append((now.date(), now, bucket, h['name'], SPEC_VERSION,
                         h.get('sector_code'), False, h['n'], i,
                         None, None, None, None,
                         got[0] if got else None, got[1] if got else None, unmapped))
    lines = [TPL['heat_header'].format(t=f'{now:%H:%M:%S}', zt_n=len(cur), dt_n=len(cur_dt))]
    for label, bucket in (('heat_up', 'heat_zt'), ('heat_down', 'heat_dt')):
        hits, unmapped = counts_by_bucket[bucket]
        if not hits:
            lines.append(TPL[('heat_up_none' if bucket == 'heat_zt' else 'heat_down_none')]
                         .format(min_n=min_n, dt_n=len(cur_dt)))
            continue
        lines.append(TPL[label].format(rows='、'.join(
            sources.concept_rows(hits, pcts, tpl_row=TPL['auction_sector_row'],
                                 tpl_pct_row=TPL['auction_sector_pct_row']))))
    lines.append(TPL['heat_footer'].format(t=f'{now:%H:%M:%S}'))
    text = '\n'.join(lines)
    print(text)
    if dry_run:
        print(f'（dry-run：概念行 {len(rows)} 条未落库）')
        return 0
    if rows:
        store.upsert_concept_counts(rows)
    store.write_broadcast(now, task='heat', what='heat', body=text,
                          counts={'zt_n': len(cur), 'dt_n': len(cur_dt),
                                  'concept_rows': len(rows), 'min_n': min_n})
    if send:
        _send(now, text, channel)
    return 0


def _send(now, text, channel):
    from duanxian import notify
    res = notify.send_to([channel], '概念热度', text)
    store.write_broadcast(now, task='heat', what='sent', body=text,
                          ok=bool(res.get('ok')), delivered=bool(res.get('ok')),
                          channel_key=channel,
                          err=None if res.get('ok') else str(res.get('detail'))[:300])
    print(f'  发送 → {channel}：{"已确认" if res.get("ok") else "未确认"}')


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description='H3 · 概念热度（单次一针）')
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--send', action='store_true')
    ap.add_argument('--channel', default=CFG.get('channel', 'feishu-main'))
    ap.add_argument('--force', action='store_true', help='非交易时段手工看数（收盘后取的是当日终态池）')
    a = ap.parse_args(argv)
    return one_round(dry_run=a.dry_run, send=a.send, channel=a.channel, force=a.force)


if __name__ == '__main__':
    raise SystemExit(main())
