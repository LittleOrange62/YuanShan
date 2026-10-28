import sqlite3
conn = sqlite3.connect(r'D:\finance\data\tq_core.db')
cur = conn.cursor()
cur.execute('SELECT * FROM meta_tables')
for row in cur.fetchall():
    print(row)
conn.close()
