#!/usr/bin/env python3
import sqlite3, os
db = r"D:\\finance\\data\\fin_watchlist.db"
conn = sqlite3.connect(db)
c = conn.cursor()
print('Tables:', c.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall())
print('watchlist_stocks count:', c.execute("SELECT COUNT(*) FROM watchlist_stocks").fetchone())
print('Sample rows:')
for row in c.execute("SELECT id, symbol, name FROM watchlist_stocks LIMIT 10").fetchall():
    print(row)
conn.close()
