#!/usr/bin/env python3
"""重建 stocks_master.db 的鑫鑫表结构"""
import sqlite3, os

db = '/app/working/workspaces/data_in/data/stocks_master.db'
conn = sqlite3.connect(db, timeout=30)
c = conn.cursor()

# 删除旧表（老板说乱七八糟的数据删掉）
for tbl in ['x_y', 'factors', 'score', 'signals', 'runs']:
    c.execute(f'DROP TABLE IF EXISTS "{tbl}"')

# 重建鑫鑫标准结构
c.execute("""
    CREATE TABLE x_y(
        date TEXT, symbol TEXT, x REAL, y REAL, quadrant TEXT, created_at TEXT
    )
""")
c.execute("""
    CREATE TABLE factors(
        date TEXT, symbol TEXT, xmorph REAL, xtrend REAL, xpos REAL,
        xchip REAL, vhealth REAL, y_from_sector REAL, subj REAL, created_at TEXT
    )
""")
c.execute("""
    CREATE TABLE score(
        date TEXT, symbol TEXT, x REAL, y REAL, final REAL, oper REAL, created_at TEXT
    )
""")
c.execute("""
    CREATE TABLE signals(
        date TEXT, symbol TEXT, tag TEXT, note TEXT, created_at TEXT
    )
""")
c.execute("""
    CREATE TABLE runs(
        run_id TEXT, date TEXT, symbol TEXT, status TEXT,
        source TEXT, notes TEXT, created_at TEXT
    )
""")
conn.commit()
conn.close()
print("✅ stocks_master.db 表结构已重建（鑫鑫标准）")

# 重建 sector_heat_master.db 的结构（同样统一）
db2 = '/app/working/workspaces/data_in/data/sector_heat_master.db'
conn2 = sqlite3.connect(db2, timeout=30)
c2 = conn2.cursor()
c2.execute('DROP TABLE IF EXISTS heat')
c2.execute('DROP TABLE IF EXISTS factors')
c2.execute('DROP TABLE IF EXISTS runs')
c2.execute("""
    CREATE TABLE heat(
        date TEXT, sector_name TEXT, heat_score REAL, created_at TEXT
    )
""")
c2.execute("""
    CREATE TABLE factors(
        date TEXT, sector_name TEXT, factor_name TEXT, factor_value REAL, created_at TEXT
    )
""")
c2.execute("""
    CREATE TABLE runs(
        run_id TEXT, date TEXT, status TEXT, source TEXT, notes TEXT, created_at TEXT
    )
""")
conn2.commit()
conn2.close()
print("✅ sector_heat_master.db 表结构已重建")
