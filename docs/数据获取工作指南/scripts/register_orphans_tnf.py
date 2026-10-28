# -*- coding: utf-8 -*-
"""从通达信 .tnf 名称表补登记主档缺票。体检 E2/E3 报孤儿时用。
用法（在 D:\\FinanceTool 下）：python 本文件.py            # 找孤儿并打印
                           python 本文件.py --register  # 注册可查到大名的
"""
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[3]   # 本文件在 docs/<指南>/scripts/ 下，上溯三级即项目根

sys.path.insert(0, str(ROOT))                # 用项目根，绝不指外部仓库
sys.stdout.reconfigure(encoding='utf-8')
from app.db import engine  # noqa: E402
from app.db.repo import security as sec  # noqa: E402
from app.ingest import tdx_tnf  # noqa: E402

SQL = """
select distinct t.security_code from (
  select security_code from market_data.stock_snapshot
  union select security_code from market_data.stock_daily_2026
) t where not exists (select 1 from reference_data.security s where s.security_code=t.security_code)
"""

def main() -> int:
    orphans = {r['security_code'] for r in engine.query(SQL, role='reader')}
    if not orphans:
        print('无孤儿代码')
        return 0
    print('孤儿:', sorted(orphans))
    if '--register' not in sys.argv:
        return 0
    nm = tdx_tnf.name_map()
    rows = []
    for c in sorted(orphans):
        s = nm.get(c)
        if not s:
            print('  .tnf 查不到，留待网络源:', c)
            continue
        sym, mkt = c.split('.')
        rows.append((c, sym, mkt, s.name, None, None,
                     sec.guess_board(sym, mkt), sec.guess_asset_type(sym, mkt),
                     False, None, None, None, True))
    if rows:
        print('登记 %d 只' % sec.upsert_securities(rows, role='admin'))
    return 0

if __name__ == '__main__':
    sys.exit(main())
