# -*- coding: utf-8 -*-
"""只读诊断：7 只缺失股票是否在自选股/重点股池"""
import sqlite3

conn = sqlite3.connect(r'D:\finance\data\fin_watchlist.db')
cur = conn.cursor()

missing = ['603468', '688825', '688828', '001232', '301677', '301707', '301717']

# watchlist_stocks 的 symbol 是纯数字
cur.execute("SELECT symbol, name FROM watchlist_stocks WHERE symbol IN ({})".format(
    ','.join('?' * len(missing))), missing)
print('在自选股中的:', cur.fetchall())

cur.execute("SELECT symbol, name FROM focus_stocks WHERE symbol IN ({})".format(
    ','.join('?' * len(missing))), missing)
print('在重点股中的:', cur.fetchall())

# 自选股总数
cur.execute("SELECT COUNT(DISTINCT symbol) FROM watchlist_stocks")
print('自选股总去重数:', cur.fetchone()[0])

conn.close()
