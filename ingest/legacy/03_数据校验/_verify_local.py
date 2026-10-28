#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import sqlite3, os
db = r"D:\finance\data\fin_data_2026.db"
print("=== 检查文件 ===")
print("文件存在:", os.path.exists(db), "大小:", os.path.getsize(db) if os.path.exists(db) else "N/A")

conn = sqlite3.connect(db)
c = conn.cursor()

print("\n=== daily_kline 表结构 ===")
rows = c.execute("PRAGMA table_info(daily_kline)").fetchall()
for r in rows:
    print(r)

print("\n=== 总条数 ===")
try:
    n = c.execute("SELECT COUNT(*) FROM daily_kline").fetchone()
    print("总条数:", n[0])
except Exception as e:
    print("错误:", e)

print("\n=== 最新 trade_date ===")
try:
    d = c.execute("SELECT MAX(trade_date) FROM daily_kline").fetchone()
    print("MAX(trade_date):", d[0])
except Exception as e:
    print("错误:", e)

print("\n=== 2026-08-14 当天条数 ===")
try:
    n2 = c.execute("SELECT COUNT(*) FROM daily_kline WHERE trade_date='2026-08-14'").fetchone()
    print("2026-08-14 条数:", n2[0])
except Exception as e:
    print("错误:", e)

print("\n=== 最近5个 trade_date 分布 ===")
try:
    for r in c.execute("SELECT trade_date, COUNT(*) FROM daily_kline GROUP BY trade_date ORDER BY trade_date DESC LIMIT 5").fetchall():
        print(r)
except Exception as e:
    print("错误:", e)

print("\n=== 索引列表 ===")
for r in c.execute("PRAGMA index_list(daily_kline)").fetchall():
    print(r)

conn.close()
