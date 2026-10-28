# -*- coding: utf-8 -*-
import sqlite3

conn = sqlite3.connect(r'D:\finance\data\tq_minline_2026.db')
cur = conn.cursor()
# 旧 tq 来源 vol 量级
cur.execute("SELECT trade_datetime, open, close, vol, amount, source FROM stock_minline "
            "WHERE source='tq' AND period='1m' AND ts_code='600367.SH' ORDER BY trade_datetime DESC LIMIT 5")
print('旧 tq 来源 600367 1m (末尾5条):')
for r in cur.fetchall():
    print(' ', r)
# 对照日线 600367 08-20 vol
cur2 = sqlite3.connect(r'D:\finance\data\tq_daily_2026.db')
c2 = cur2.cursor()
c2.execute("SELECT trade_date, close, vol, amount FROM stock_daily WHERE ts_code='600367.SH' AND trade_date='20260820'")
print('日线 600367 08-20:', c2.fetchone())
conn.close()
cur2.close()
