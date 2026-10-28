# -*- coding: utf-8 -*-
import sqlite3

DB_DAILY = r"D:\finance\data\tq_daily_2026.db"
DB_CORE = r"D:\finance\data\tq_core.db"

c = sqlite3.connect(DB_DAILY)
c.execute("ATTACH DATABASE ? AS core", (DB_CORE,))

print("asset_type dist (core.security_info):", list(c.execute("SELECT asset_type, COUNT(*) FROM core.security_info GROUP BY asset_type")))
print("stock_daily total:", c.execute("SELECT COUNT(*) FROM stock_daily").fetchone())
print("sample codes:", [r[0] for r in c.execute("SELECT DISTINCT ts_code FROM stock_daily LIMIT 15")])
print("daily distinct codes:", c.execute("SELECT COUNT(DISTINCT ts_code) FROM stock_daily").fetchone())
print("daily codes NOT in security_info:", c.execute("SELECT COUNT(DISTINCT ts_code) FROM stock_daily s WHERE NOT EXISTS (SELECT 1 FROM core.security_info i WHERE i.ts_code=s.ts_code)").fetchone())
print("asset_type dist in stock_daily join:")
for r in c.execute("""SELECT i.asset_type, COUNT(*) FROM stock_daily s JOIN core.security_info i ON i.ts_code=s.ts_code GROUP BY i.asset_type"""):
    print("  ", r)
