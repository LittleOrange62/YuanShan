import sqlite3, json, sys
db_path = r'D:\finance\data\fin_watchlist.db'
conn = sqlite3.connect(db_path)
cur = conn.cursor()
# list tables
tables = [t[0] for t in cur.execute('SELECT name FROM sqlite_master WHERE type="table"')]
print('tables', tables)
for t in tables:
    rows = cur.execute(f'SELECT * FROM {t} LIMIT 10').fetchall()
    print('---', t, '---')
    for r in rows:
        print(r)
conn.close()
