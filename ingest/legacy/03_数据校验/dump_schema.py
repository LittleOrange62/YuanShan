import sqlite3, json, sys
db='D:/finance/data/fin_data.db'
con=sqlite3.connect(db)
cur=con.cursor()
cur.execute("SELECT name, sql FROM sqlite_master WHERE type='table' ORDER BY name")
rows=cur.fetchall()
print(json.dumps(rows, ensure_ascii=False, indent=2))
