#!/usr/bin/env python3
import sqlite3

conn = sqlite3.connect('/app/working/workspaces/data_in/data/kline_2026.db')
c = conn.execute("PRAGMA table_info(daily_kline)")
print("=== kline_2026.daily_kline ===")
cols = c.fetchall()
for col in cols:
    print(f"  {col}")

print(f"\n总行数: {conn.execute('SELECT COUNT(*) FROM daily_kline').fetchone()[0]}")
print(f"日期范围: {conn.execute('SELECT MIN(trade_date), MAX(trade_date) FROM daily_kline').fetchone()}")
print(f"股票数量: {conn.execute('SELECT COUNT(DISTINCT ts_code) FROM daily_kline').fetchone()[0]}")
print(f"最近5个交易日: {[r[0] for r in conn.execute('SELECT DISTINCT trade_date FROM daily_kline ORDER BY trade_date DESC LIMIT 5').fetchall()]}")
conn.close()
