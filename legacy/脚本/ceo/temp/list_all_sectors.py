#!/usr/bin/env python3
"""查看所有热力板块的名称，以便精确映射"""
import sqlite3
conn = sqlite3.connect('/app/working/workspaces/data_in/data/sector_heat_master.db')
rows = conn.execute("SELECT sector_name FROM heat ORDER BY sector_name").fetchall()
print(f"共有 {len(rows)} 个板块:")
for r in rows:
    print(f"  '{r[0]}'")
conn.close()
