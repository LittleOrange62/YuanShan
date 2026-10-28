# -*- coding: utf-8 -*-
"""对比 08-13 vs 08-14 symbol 格式"""
import sqlite3
con = sqlite3.connect(r'D:\finance\data\fin_data_2026.db')
cur = con.cursor()
cur.execute("SELECT symbol, trade_date FROM daily_kline WHERE trade_date='2026-08-13' LIMIT 3")
print('08-13 样例:')
for r in cur.fetchall():
    print(' ', r)
cur.execute("SELECT symbol, trade_date FROM daily_kline WHERE trade_date='2026-08-14' LIMIT 3")
print('08-14 样例:')
for r in cur.fetchall():
    print(' ', r)
# 统计 08-14 的 symbol 格式分布
cur.execute("SELECT substr(symbol,1,2) AS prefix, COUNT(*) FROM daily_kline WHERE trade_date='2026-08-14' GROUP BY prefix")
print('\n08-14 symbol 前缀分布:')
for r in cur.fetchall():
    print(' ', r)
con.close()
