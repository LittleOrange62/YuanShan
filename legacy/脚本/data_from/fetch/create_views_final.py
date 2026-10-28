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

# v_stock_daily_stock
c.execute("DROP VIEW IF EXISTS v_stock_daily_stock")
c.execute(f"CREATE VIEW v_stock_daily_stock AS SELECT * FROM stock_daily WHERE {STOCK_RULE}")

# v_stock_daily_index
c.execute("DROP VIEW IF EXISTS v_stock_daily_index")
c.execute(f"CREATE VIEW v_stock_daily_index AS SELECT * FROM stock_daily WHERE {INDEX_RULE}")

# v_security_daily
c.execute("DROP VIEW IF EXISTS v_security_daily")
c.execute("CREATE VIEW v_security_daily AS SELECT * FROM stock_daily")

c.commit()

# 最终验证
print("=== views ===")
for v in ["v_stock_daily_stock", "v_stock_daily_index", "v_security_daily"]:
    n = c.execute(f"SELECT COUNT(*) FROM {v}").fetchone()[0]
    d = c.execute(f"SELECT COUNT(DISTINCT ts_code) FROM {v}").fetchone()[0]
    print(f"{v}: rows={n}, codes={d}")

# index_daily 重复情况
print("index_daily rows:", c.execute("SELECT COUNT(*) FROM index_daily").fetchone()[0],
      "codes:", c.execute("SELECT COUNT(DISTINCT ts_code) FROM index_daily").fetchone()[0])
print("v_stock_daily_index overlap with index_daily (codes):",
      c.execute("""SELECT COUNT(DISTINCT v.ts_code) FROM v_stock_daily_index v
                   WHERE EXISTS (SELECT 1 FROM index_daily d WHERE d.ts_code=v.ts_code)""").fetchone()[0])

# 视图定义确认
print("=== sqlite_master ===")
for r in c.execute("SELECT name, type, sql FROM sqlite_master WHERE type='view' ORDER BY name"):
    print(r[0], "|", (r[2] or "")[:120].replace("\n", " "), "...")
c.close()
