# -*- coding: utf-8 -*-
import sqlite3

DB_DAILY = r"D:\finance\data\tq_daily_2026.db"
DB_CORE = r"D:\finance\data\tq_core.db"

STOCK_RULE = """ts_code LIKE '600%.SH' OR ts_code LIKE '601%.SH' OR ts_code LIKE '603%.SH'
 OR ts_code LIKE '605%.SH' OR ts_code LIKE '688%.SH' OR ts_code LIKE '689%.SH'
 OR ts_code LIKE '204%.SH'
 OR ts_code LIKE '000%.SZ' OR ts_code LIKE '001%.SZ' OR ts_code LIKE '002%.SZ'
 OR ts_code LIKE '003%.SZ' OR ts_code LIKE '300%.SZ' OR ts_code LIKE '301%.SZ'"""
INDEX_RULE = """ts_code LIKE '000%.SH' OR ts_code LIKE '399%.SZ'"""

c = sqlite3.connect(DB_DAILY)
c.execute("ATTACH DATABASE ? AS core", (DB_CORE,))

# 规则计数 vs security_info JOIN 计数
print("stock  rule:", c.execute(f"SELECT COUNT(*) FROM stock_daily WHERE {STOCK_RULE}").fetchone()[0],
      "| join:", c.execute("SELECT COUNT(*) FROM stock_daily s JOIN core.security_info i ON i.ts_code=s.ts_code WHERE i.asset_type='stock'").fetchone()[0])
print("index  rule:", c.execute(f"SELECT COUNT(*) FROM stock_daily WHERE {INDEX_RULE}").fetchone()[0],
      "| join:", c.execute("SELECT COUNT(*) FROM stock_daily s JOIN core.security_info i ON i.ts_code=s.ts_code WHERE i.asset_type='index'").fetchone()[0])

# 规则与 JOIN 的差集检查（rule 里有没有 join 没有的，join 里有没有 rule 没有的）
print("stock rule-only codes:", c.execute(f"""SELECT COUNT(DISTINCT ts_code) FROM stock_daily WHERE {STOCK_RULE}
  AND ts_code NOT IN (SELECT ts_code FROM core.security_info WHERE asset_type='stock')""").fetchone()[0])
print("stock join-only codes:", c.execute(f"""SELECT COUNT(DISTINCT s.ts_code) FROM stock_daily s
  JOIN core.security_info i ON i.ts_code=s.ts_code AND i.asset_type='stock'
  WHERE NOT ({STOCK_RULE})""").fetchone()[0])
print("index rule-only codes:", c.execute(f"""SELECT COUNT(DISTINCT ts_code) FROM stock_daily WHERE {INDEX_RULE}
  AND ts_code NOT IN (SELECT ts_code FROM core.security_info WHERE asset_type='index')""").fetchone()[0])
print("index join-only codes:", c.execute(f"""SELECT COUNT(DISTINCT s.ts_code) FROM stock_daily s
  JOIN core.security_info i ON i.ts_code=s.ts_code AND i.asset_type='index'
  WHERE NOT ({INDEX_RULE})""").fetchone()[0])
