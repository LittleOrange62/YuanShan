# -*- coding: utf-8 -*-
import sqlite3

conn = sqlite3.connect(r'D:\finance\data\tq_minline_2026.db')
cur = conn.cursor()
cur.execute("SELECT ts_code, period, COUNT(*), MIN(trade_datetime), MAX(trade_datetime), source "
            "FROM stock_minline WHERE ts_code='600367.SH' GROUP BY ts_code, period")
for r in cur.fetchall():
    print('600367:', r)
cur.execute("SELECT trade_datetime, open, high, low, close, vol, amount FROM stock_minline "
            "WHERE ts_code='600367.SH' AND period='1m' AND trade_datetime LIKE '20260825%' "
            "ORDER BY trade_datetime LIMIT 5")
print('08-25 前5分钟:')
for r in cur.fetchall():
    print(' ', r)
cur.execute("SELECT trade_datetime, open, high, low, close, vol, amount FROM stock_minline "
            "WHERE ts_code='600367.SH' AND period='1m' AND trade_datetime LIKE '20260825%' "
            "ORDER BY trade_datetime DESC LIMIT 3")
print('08-25 尾3分钟:')
for r in cur.fetchall():
    print(' ', r)
conn.close()
