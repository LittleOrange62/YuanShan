# -*- coding: utf-8 -*-
"""只读诊断：对比 08-13 与 08-14 daily_kline 差异"""
import sqlite3

conn = sqlite3.connect(r'D:\finance\data\fin_data_2026.db')
cur = conn.cursor()

cur.execute("SELECT COUNT(*) FROM daily_kline WHERE trade_date='2026-08-13'")
c13 = cur.fetchone()[0]
cur.execute("SELECT COUNT(*) FROM daily_kline WHERE trade_date='2026-08-14'")
c14 = cur.fetchone()[0]
print(f'08-13: {c13} 条, 08-14: {c14} 条')

# 08-13 有但 08-14 无
cur.execute('''
SELECT symbol FROM (
  SELECT symbol FROM daily_kline WHERE trade_date='2026-08-13'
  EXCEPT
  SELECT symbol FROM daily_kline WHERE trade_date='2026-08-14'
)
''')
missing = [r[0] for r in cur.fetchall()]
print(f'08-13 有但 08-14 无: {len(missing)} 只')
print('缺失样例:', missing[:30])

# 08-14 有但 08-13 无（可能是新增）
cur.execute('''
SELECT symbol FROM (
  SELECT symbol FROM daily_kline WHERE trade_date='2026-08-14'
  EXCEPT
  SELECT symbol FROM daily_kline WHERE trade_date='2026-08-13'
)
''')
extra = [r[0] for r in cur.fetchall()]
print(f'08-14 有但 08-13 无: {len(extra)} 只')
print('新增样例:', extra[:10])

conn.close()
