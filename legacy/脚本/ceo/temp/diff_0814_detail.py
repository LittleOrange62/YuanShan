# -*- coding: utf-8 -*-
"""只读诊断：分析 08-14 缺失股票中真实股票的数量"""
import sqlite3

conn = sqlite3.connect(r'D:\finance\data\fin_data_2026.db')
cur = conn.cursor()

cur.execute('''
SELECT symbol FROM (
  SELECT symbol FROM daily_kline WHERE trade_date='2026-08-13'
  EXCEPT
  SELECT symbol FROM daily_kline WHERE trade_date='2026-08-14'
)
''')
missing = [r[0] for r in cur.fetchall()]

# 真实股票前缀：SH600/601/603/605/688/689 + SZ000/001/002/003/300/301
real_stocks = []
others = []
for sym in missing:
    if sym.startswith('SH6') or sym.startswith('SH68') or sym.startswith('SZ0') or sym.startswith('SZ3'):
        # 排除转债 110/111/113/118/132 和 ETF 508/512/515/518/520/560/561/563/588/589
        code = sym[2:]
        if code.startswith(('600','601','603','605','688','689','000','001','002','003','300','301')):
            real_stocks.append(sym)
        else:
            others.append(sym)
    else:
        others.append(sym)

print(f'缺失总数: {len(missing)}')
print(f'其中真实股票: {len(real_stocks)} 只')
print('真实股票样例:', real_stocks[:50])
print(f'其他(转债/ETF/基金等): {len(others)} 只')

# 看缺失真实股票的前缀分布
from collections import Counter
prefix = Counter(s[:5] for s in real_stocks)
print('真实股票前缀分布:', dict(prefix))

conn.close()
