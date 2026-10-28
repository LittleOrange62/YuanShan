import sqlite3

db = r'D:\finance\data\tq_core.db'
conn = sqlite3.connect(db)
cur = conn.cursor()

# Get schema
cur.execute("PRAGMA table_info(stock_info)")
cols = cur.fetchall()
print('stock_info columns:')
for c in cols:
    print(f'  {c}')

conn.close()
