# -*- coding: utf-8 -*-
"""监控 daily_kline 08-14 写入进度"""
import sqlite3
con = sqlite3.connect(r'D:\finance\data\fin_data_2026.db')
cur = con.cursor()
cur.execute("SELECT COUNT(*) FROM daily_kline WHERE trade_date='2026-08-14'")
print('08-14 已写入:', cur.fetchone()[0])
cur.execute("SELECT COUNT(*) FROM daily_kline WHERE trade_date='2026-08-13'")
print('08-13 行数:', cur.fetchone()[0])
con.close()
