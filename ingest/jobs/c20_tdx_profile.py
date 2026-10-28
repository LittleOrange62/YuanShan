# -*- coding: utf-8 -*-
"""
C20 · 通达信个股档案入库（reference_data.stock_profile），走 TQ 接口。

**取什么**：`tq.get_more_info(code)` 返回 80+ 字段的当日盘面档案 ——
主营业务、涨跌停价、历史高低价、IPO 价、Beta、员工数、换手率、动态/静态 PE、
PB 等。其中大部分（主营/涨跌停/历史高低/IPO/员工数…）是
`market_data.stock_snapshot`（c15 收盘插件）里没有的，互补。
全量字段另存 `raw` JSONB，将来要哪个再挑，不为没查过的字段预建列。

**为什么走 TQ 不解析 profile.dat**：profile.dat 是 GBK 私有二进制无文档；
插件自己就用 get_more_info。

**快照性质**：get_more_info 只给当前值（HqDate=当日），一行 = 一票一天。
`ON CONFLICT DO UPDATE`，同日重跑刷新。

**前置**：通达信已启动并登录。

用法：
    python scripts\\ingest\\c20_tdx_profile.py --dry-run
    python scripts\\ingest\\c20_tdx_profile.py --limit 30
    python scripts\\ingest\\c20_tdx_profile.py
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
sys.stdout.reconfigure(encoding='utf-8')

from app import config  # noqa: E402
from app.db import engine  # noqa: E402
from app.db.repo import ops as ops_repo  # noqa: E402
from app.tdx_client import tq_bridge  # noqa: E402

NDJSON = 'tdx_profile.ndjson'

# 挑出来建列的字段（TQ 键 → 表列）。其余全进 raw。
SNIPPET = '''
import json
codes = {codes!r}
n = 0
with open(OUT_DIR + r"/{ndjson}", "w", encoding="utf-8") as f:
    for code in codes:
        try:
            m = tq.get_more_info(stock_code=code)
        except Exception:
            m = None
        if not m or not m.get("HqDate"):
            continue
        f.write(json.dumps({{"ts_code": code, "raw": m}}, ensure_ascii=False) + "\\n")
        n += 1
emit({{"rows": n, "stocks": len(codes)}})
'''

# TQ 键 → (列名, 转换)。'' / '0' 之类的空值统一转 None。
FIELDS = {
    'ZTPrice': ('limit_up', float),
    'DTPrice': ('limit_down', float),
    'fHSL': ('turnover_pct', float),
    'DynaPE': ('pe_dyn', float),
    'StaticPE_TTM': ('pe_static_ttm', float),
    'PB_MRQ': ('pb_mrq', float),
    'BetaValue': ('beta', float),
    'IPO_Price': ('ipo_price', float),
    'HisHigh': ('his_high', float),
    'HisLow': ('his_low', float),
    'StaffNum': ('staff_num', int),
}


def _num(val, cast):
    try:
        s = str(val).strip()
        if not s or s in ('-', '--'):
            return None
        v = cast(float(s))
        return v
    except (ValueError, TypeError):
        return None


def all_codes(limit: int | None) -> list[str]:
    rows = engine.query(
        'SELECT security_code FROM reference_data.security ORDER BY security_code')
    codes = [r['security_code'] for r in rows]
    return codes[:limit] if limit else codes


def to_row(ts_code: str, raw: dict, batch_id: int) -> tuple | None:
    hd = str(raw.get('HqDate', '')).strip()
    try:
        hq_date = datetime.strptime(hd, '%Y%m%d').date()
    except ValueError:
        return None
    cols = {c: None for c, _ in FIELDS.values()}
    for key, (col, cast) in FIELDS.items():
        cols[col] = _num(raw.get(key), cast)
    return (ts_code, hq_date, raw.get('MainBusiness') or None,
            cols['limit_up'], cols['limit_down'], cols['turnover_pct'],
            cols['pe_dyn'], cols['pe_static_ttm'], cols['pb_mrq'], cols['beta'],
            cols['ipo_price'], cols['his_high'], cols['his_low'],
            cols['staff_num'], json.dumps(raw, ensure_ascii=False), batch_id)


UPSERT = """
INSERT INTO reference_data.stock_profile
    (security_code, hq_date, main_business, limit_up, limit_down, turnover_pct,
     pe_dyn, pe_static_ttm, pb_mrq, beta, ipo_price, his_high, his_low,
     staff_num, raw, source_batch_id)
VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s)
ON CONFLICT (security_code, hq_date) DO UPDATE SET
    main_business=EXCLUDED.main_business, limit_up=EXCLUDED.limit_up,
    limit_down=EXCLUDED.limit_down, turnover_pct=EXCLUDED.turnover_pct,
    pe_dyn=EXCLUDED.pe_dyn, pe_static_ttm=EXCLUDED.pe_static_ttm,
    pb_mrq=EXCLUDED.pb_mrq, beta=EXCLUDED.beta, ipo_price=EXCLUDED.ipo_price,
    his_high=EXCLUDED.his_high, his_low=EXCLUDED.his_low,
    staff_num=EXCLUDED.staff_num, raw=EXCLUDED.raw,
    source_batch_id=EXCLUDED.source_batch_id, updated_at=now()
"""


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
    print('=== 通达信个股档案（TQ get_more_info）===')
    print('  标的 %s 只' % len(codes))
    if args.dry_run:
        print('  样例：%s' % codes[:5])
        print('\n--dry-run，未调用接口、未写库')
        return 0

    batch_id = ops_repo.begin_batch('tdx_tq_api', 'reference_data.stock_profile',
                                    source_path='tq:get_more_info')
    try:
        r = tq_bridge.run_snippet(SNIPPET.format(codes=codes, ndjson=NDJSON),
                                  timeout=3600, stream=True)
        if not r.get('ok'):
            raise RuntimeError('TQ 片段失败：%s' % r.get('error'))
        print('  接口回显：%s 只票，取到 %s 条档案' % (r['stocks'], r['rows']))

        rows = []
        with (config.VAR_DIR / NDJSON).open(encoding='utf-8') as f:
            for line in f:
                rec = json.loads(line)
                row = to_row(rec['ts_code'], rec['raw'], batch_id)
                if row:
                    rows.append(row)
        engine.executemany(UPSERT, rows, role='etl')
        ops_repo.end_batch(batch_id, status='ok', row_count=len(rows))
    except Exception as exc:
        ops_repo.end_batch(batch_id, status='failed', message=str(exc)[:2000])
        raise

    print('=== 结果 ===')
    print('  入库 %s 行；stock_profile 现有 %s 行 / %s 只'
          % (len(rows),
             engine.scalar('SELECT count(*) FROM reference_data.stock_profile'),
             engine.scalar('SELECT count(DISTINCT security_code) FROM reference_data.stock_profile')))
    print('  快照日 %s；有主营业务 %s 条'
          % (engine.scalar('SELECT max(hq_date) FROM reference_data.stock_profile'),
             engine.scalar('SELECT count(*) FROM reference_data.stock_profile '
                           'WHERE main_business IS NOT NULL')))
    engine.close_all()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
