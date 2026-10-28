# -*- coding: utf-8 -*-
import sqlite3

conn = sqlite3.connect(r'D:\finance\data\tq_minline_2026.db')
cur = conn.cursor()
cur.execute("SELECT ts_code, period, COUNT(*), MIN(trade_datetime), MAX(trade_datetime), source "
            "FROM stock_minline WHERE ts_code LIKE '00000%.SH' AND period='1m' GROUP BY ts_code LIMIT 5")
print('上证指数系列 1m:')
for r in cur.fetchall():
    print(' ', r)
# 检查 08-25 当天 600367 前的验证用 000001.SH
cur.execute("SELECT trade_datetime, open, high, low, close, vol, amount FROM stock_minline "
            "WHERE ts_code='000001.SH' AND period='1m' AND trade_datetime LIKE '20260825%' "
            "ORDER BY trade_datetime LIMIT 3")
print('000001.SH 08-25 前3分钟:')
for r in cur.fetchall():
    print(' ', r)
conn.close()
