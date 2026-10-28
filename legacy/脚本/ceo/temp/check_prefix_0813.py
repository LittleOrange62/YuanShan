# -*- coding: utf-8 -*-
"""统计 08-13 全部 symbol 前缀分布（确认正确的股票池格式）"""
import sqlite3
con = sqlite3.connect(r'D:\finance\data\fin_data_2026.db')
cur = con.cursor()
cur.execute("SELECT substr(symbol,1,2) AS prefix, COUNT(*) FROM daily_kline WHERE trade_date='2026-08-13' GROUP BY prefix ORDER BY prefix")
print('08-13 symbol 前缀分布:')
for r in cur.fetchall():
    print(' ', r)
cur.execute("SELECT COUNT(*) FROM daily_kline WHERE trade_date='2026-08-13'")
print('总数:', cur.fetchone()[0])
con.close()
