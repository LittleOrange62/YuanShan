#!/usr/bin/env python3
import sqlite3
conn = sqlite3.connect('/app/working/workspaces/data_in/data/stocks_master.db')
print("=== x_y (前3条) ===")
for r in conn.execute("SELECT * FROM x_y LIMIT 3").fetchall():
    print("  ", r)
print("\n=== factors (前2条) ===")
for r in conn.execute("SELECT * FROM factors LIMIT 2").fetchall():
    print("  ", r)
print("\n=== score (前3条) ===")
for r in conn.execute("SELECT * FROM score LIMIT 3").fetchall():
    print("  ", r)
total_xy = conn.execute('SELECT COUNT(*) FROM x_y').fetchone()[0]
total_score = conn.execute('SELECT COUNT(*) FROM score').fetchone()[0]
print(f"\n总记录数: x_y={total_xy}, score={total_score}")
conn.close()
