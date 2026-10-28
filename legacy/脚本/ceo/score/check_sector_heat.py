#!/usr/bin/env python3
"""检查板块热度表里都有些什么板块"""
import sqlite3
conn = sqlite3.connect('/app/working/workspaces/data_in/data/sector_heat_master.db')
print("heat表列:", [r[1] for r in conn.execute("PRAGMA table_info(heat)").fetchall()])
rows = conn.execute("SELECT * FROM heat LIMIT 20").fetchall()
print("前20条:")
for r in rows:
    print(" ", r)
print(f"\n总条数: {conn.execute('SELECT COUNT(*) FROM heat').fetchone()[0]}")
print(f"不同板块: {conn.execute('SELECT COUNT(DISTINCT sector_name) FROM heat').fetchone()[0]}")
conn.close()
