# -*- coding: utf-8 -*-
"""检查 08-14 已写入数据样例"""
import sqlite3
con = sqlite3.connect(r'D:\finance\data\fin_data_2026.db')
cur = con.cursor()
cur.execute("SELECT symbol, trade_date, open, high, low, close, volume, amount, turnover, asset_type FROM daily_kline WHERE trade_date='2026-08-14' LIMIT 5")
print('=== 08-14 样例 ===')
for r in cur.fetchall():
    print(r)
cur.execute("SELECT COUNT(*) FROM daily_kline WHERE trade_date='2026-08-14'")
print('总数:', cur.fetchone()[0])
con.close()
