#!/usr/bin/env python3
import sqlite3, os
db = r"D:\\finance\\data\\fin_factor.db"
conn = sqlite3.connect(db)
c = conn.cursor()
print('Rows for plan_id 1:')
rows = c.execute("SELECT trade_date, COUNT(*) FROM score_results WHERE plan_id=1 GROUP BY trade_date ORDER BY trade_date DESC LIMIT 5").fetchall()
print(rows)
if rows:
    latest = rows[0][0]
    print('Latest date', latest)
    top = c.execute("SELECT symbol, total_score FROM score_results WHERE plan_id=1 AND trade_date=? ORDER BY total_score DESC LIMIT 20", (latest,)).fetchall()
    print('Top rows', top[:5])
conn.close()
