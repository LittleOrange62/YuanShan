import sqlite3, sys
db = r'D:/finance/data/fin_data.db'
con = sqlite3.connect(db)
cur = con.cursor()
cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'day_end_%' ORDER BY name")
rows = cur.fetchall()
for r in rows:
    print(r[0])
