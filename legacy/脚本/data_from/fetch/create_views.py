# -*- coding: utf-8 -*-
import sqlite3

DB_DAILY = r"D:\finance\data\tq_daily_2026.db"
DB_CORE = r"D:\finance\data\tq_core.db"

c = sqlite3.connect(DB_DAILY)
c.execute("ATTACH DATABASE ? AS core", (DB_CORE,))

# 1) 个股日线：只含 asset_type='stock'
c.execute("DROP VIEW IF EXISTS v_stock_daily_stock")
c.execute("""
CREATE VIEW v_stock_daily_stock AS
SELECT s.* FROM stock_daily s
WHERE EXISTS (SELECT 1 FROM core.security_info i
              WHERE i.ts_code = s.ts_code AND i.asset_type = 'stock')
""")

# 2) 指数日线：只含 asset_type='index'
c.execute("DROP VIEW IF EXISTS v_stock_daily_index")
c.execute("""
CREATE VIEW v_stock_daily_index AS
SELECT s.* FROM stock_daily s
WHERE EXISTS (SELECT 1 FROM core.security_info i
              WHERE i.ts_code = s.ts_code AND i.asset_type = 'index')
""")

# 3) 全量证券日线：保留原貌
c.execute("DROP VIEW IF EXISTS v_security_daily")
c.execute("CREATE VIEW v_security_daily AS SELECT * FROM stock_daily")

c.commit()

# 验证
for v in ["v_stock_daily_stock", "v_stock_daily_index", "v_security_daily"]:
    n = c.execute(f"SELECT COUNT(*) FROM {v}").fetchone()[0]
    print(v, n)

# 对照 index_daily 看是否重复
print("index_daily rows:", c.execute("SELECT COUNT(*) FROM index_daily").fetchone()[0])
print("index codes in index_daily:", c.execute("SELECT COUNT(DISTINCT ts_code) FROM index_daily").fetchone()[0])
print("v_stock_daily_index codes:", c.execute("SELECT COUNT(DISTINCT ts_code) FROM v_stock_daily_index").fetchone()[0])
c.close()
