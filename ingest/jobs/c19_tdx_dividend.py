# -*- coding: utf-8 -*-
"""
C19 · 通达信除权除息入库（reference_data.dividend），走 TQ 接口。

**为什么走 TQ 不解析 gbbq**：`gbbq` 是 XOR 编码的私有二进制、无官方文档，
逆向易错；通达信插件自己用 `tq.get_divid_factors(code)` 取除权。实测它返回
DataFrame（列 Type/Bonus/ShareBonus/Allotment/AllotPrice，索引 Date）。

**字段口径已实证对齐**（2026-09-22，拿 600519/600036 与东财同日比对）：
TQ 是「每 10 股」口径，`reference_data.dividend` 是「每股」——
    600519 2021-06-25  TQ Bonus=192.93  ÷10 = 东财 cash_per_share 19.293  ✓
    600036 2021-07-13  TQ Bonus=12.53   ÷10 = 东财 cash_per_share 1.253   ✓
所以 cash=Bonus/10、bonus_ratio=ShareBonus/10、rights_ratio=Allotment/10、
rights_price=AllotPrice（配股价本就是每股）。

**价值**：东财的 reference_data.dividend 只回溯到 2021，TQ 有 2002 年起的完整历史。
本脚本 `ON CONFLICT DO NOTHING`，只补东财没有的早年除权行，不动已有数据。

**前置**：通达信已启动并登录。

用法：
    python scripts\\ingest\\c19_tdx_dividend.py --dry-run
    python scripts\\ingest\\c19_tdx_dividend.py --limit 50
    python scripts\\ingest\\c19_tdx_dividend.py
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
sys.stdout.reconfigure(encoding='utf-8')

from app import config  # noqa: E402
from app.db import engine  # noqa: E402
from app.db.repo import ops as ops_repo  # noqa: E402
from app.tdx_client import tq_bridge  # noqa: E402

NDJSON = 'tdx_dividend.ndjson'

SNIPPET = '''
import json
codes = {codes!r}
n = 0
with open(OUT_DIR + r"/{ndjson}", "w", encoding="utf-8") as f:
    for code in codes:
        try:
            d = tq.get_divid_factors(stock_code=code)
        except Exception:
            continue
        if d is None or not len(getattr(d, "index", [])):
            continue
        for r in d.reset_index().to_dict("records"):
            date = str(r.get("Date", ""))[:10]
            if not date:
                continue
            f.write(json.dumps({{
                "security_code": code, "ex_date": date,
                "cash_per_share": round(float(r.get("Bonus") or 0) / 10, 6),
                "bonus_ratio": round(float(r.get("ShareBonus") or 0) / 10, 6),
                "rights_ratio": round(float(r.get("Allotment") or 0) / 10, 6),
                "rights_price": (float(r.get("AllotPrice"))
                                 if r.get("AllotPrice") else None),
            }}, ensure_ascii=False) + "\\n")
            n += 1
emit({{"rows": n, "stocks": len(codes)}})
'''


def all_codes(limit: int | None) -> list[str]:
    rows = engine.query(
        "SELECT security_code FROM reference_data.security "
        "WHERE asset_type='股票' ORDER BY security_code")
    codes = [r['security_code'] for r in rows] or \
            [r['security_code'] for r in engine.query('SELECT security_code FROM reference_data.security ORDER BY security_code')]
    return codes[:limit] if limit else codes


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--limit', type=int, help='只处理前 N 只（调试用）')
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args()

    ok, why = tq_bridge.available()
    if not ok:
        print('TQ 不可用：%s' % why)
        return 2

    codes = all_codes(args.limit)
    print('=== 通达信除权除息（TQ）===')
    print('  标的 %s 只' % len(codes))
    if args.dry_run:
        print('  样例：%s' % codes[:5])
        print('\n--dry-run，未调用接口、未写库')
        return 0

    batch_id = ops_repo.begin_batch('tdx_tq_api', 'reference_data.dividend',
                                    source_path='tq:get_divid_factors')
    try:
        r = tq_bridge.run_snippet(SNIPPET.format(codes=codes, ndjson=NDJSON),
                                  timeout=3600, stream=True)
        if not r.get('ok'):
            raise RuntimeError('TQ 片段失败：%s' % r.get('error'))
        print('  接口回显：%s 只票，共 %s 条除权记录' % (r['stocks'], r['rows']))

        rows = []
        with (config.VAR_DIR / NDJSON).open(encoding='utf-8') as f:
            for line in f:
                d = json.loads(line)
                rows.append((d['security_code'], d['ex_date'], d['cash_per_share'],
                             d['bonus_ratio'], d['rights_ratio'], d['rights_price']))
        before = engine.scalar('SELECT count(*) FROM reference_data.dividend')
        engine.executemany(
            """
            INSERT INTO reference_data.dividend
                (security_code, ex_date, cash_per_share, bonus_ratio, rights_ratio, rights_price)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (security_code, ex_date) DO NOTHING
            """, rows, role='etl')
        after = engine.scalar('SELECT count(*) FROM reference_data.dividend')
        ops_repo.end_batch(batch_id, status='ok', row_count=after - before)
    except Exception as exc:
        ops_repo.end_batch(batch_id, status='failed', message=str(exc)[:2000])
        raise

    print('=== 结果 ===')
    print('  解析 %s 条，新增 %s 行（其余为东财已有同日记录，跳过）'
          % (len(rows), after - before))
    print('  reference_data.dividend 现覆盖最早除权日 %s'
          % engine.scalar('SELECT min(ex_date) FROM reference_data.dividend'))
    engine.close_all()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
