# -*- coding: utf-8 -*-
import sqlite3

DB = r'D:\finance\data\fin_data_2026.db'
con = sqlite3.connect(DB, timeout=30)
cur = con.cursor()
cur.execute("PRAGMA busy_timeout=30000")
cur.execute("PRAGMA journal_mode=WAL")

cur.execute("SELECT COUNT(*) FROM daily_kline WHERE trade_date='2026-08-14'")
before = cur.fetchone()[0]
print('Before delete count:', before)

cur.execute("DELETE FROM daily_kline WHERE trade_date='2026-08-14'")
con.commit()

cur.execute("SELECT COUNT(*) FROM daily_kline WHERE trade_date='2026-08-14'")
after = cur.fetchone()[0]
print('After delete count:', after)

con.close()
print('DELETE OK, removed', before - after, 'rows')
