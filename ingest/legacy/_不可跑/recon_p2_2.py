# -*- coding: utf-8 -*-
"""P2.2 对账：YuanShan 的板块分类解析 vs FinanceTool c17，再与生产表比（只读）。"""
import os
import sys

FT_ROOT = os.environ.get('FT_ROOT', '')   # 一次性对账脚本，只在参考仓库还在时能跑
if not FT_ROOT:
    raise SystemExit('需要 set FT_ROOT=<参考仓库路径>：这是对账用的历史脚本，不在产品链路上')
sys.path.insert(0, FT_ROOT)
sys.stdout.reconfigure(encoding='utf-8')
from ingest import tdx_sector_cfg as NEW
c17 = importlib.import_module('scripts.ingest.c17_tdx_sector')
a, b = c17.load_sectors(), NEW.load_sectors()
print('解析结果：FinanceTool %d 个板块 / YuanShan %d 个 / 两份完全相同 %s'
      % (len(a), len(b), a == b))
if a != b:
    print('  仅老有', list(set(a) - set(b))[:5], '仅新有', list(set(b) - set(a))[:5])
    print('  值不同', [k for k in set(a) & set(b) if a[k] != b[k]][:5])
rows = NEW.build_rows(a)
from collections import Counter
print('build_rows %d 行；类型分布 %s；level 为空 %d 行'
      % (len(rows), Counter(r[2] for r in rows).most_common(), sum(1 for r in rows if r[3] is None)))
from app.db import engine
db = {r['sector_code']: (r['name'], r['sector_type'], r['level']) for r in engine.query(
    "select sector_code, name, sector_type, level from reference_data.sector_info "
    "where left(sector_code, 4) = 'TDX_'")}
codes = {r[0] for r in rows}
full = sum(1 for c, n, t, l in rows if db.get(c) == (n, t, l))
print('\n与生产表 reference_data.sector_info 比对：解析 %d / 库里 TDX_ %d / 三列全等 %d'
      % (len(rows), len(db), full))
print('  解析有而库里没有：%d（%s）' % (sum(1 for r in rows if r[0] not in db),
      [r[0] for r in rows if r[0] not in db][:6]))
print('  库里有而解析没有：%d（%s）' % (sum(1 for c in db if c not in codes),
      [c for c in db if c not in codes][:6]))
print('  同名但值不同：%d（%s）' % (sum(1 for c, n, t, l in rows if c in db and db[c] != (n, t, l)),
      [(c, db[c], (n, t, l)) for c, n, t, l in rows if c in db and db[c] != (n, t, l)][:3]))
engine.close_all()
