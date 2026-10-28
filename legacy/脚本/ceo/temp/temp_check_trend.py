import sqlite3
conn = sqlite3.connect(r'D:\finance\data\trend_research.db')
cur = conn.cursor()
cur.execute('SELECT COUNT(*) FROM size_divergence')
print('size_divergence rows:', cur.fetchone()[0])
cur.execute('SELECT * FROM size_divergence ORDER BY trade_date DESC LIMIT 5')
print('Recent 5 rows:')
for row in cur.fetchall():
    print(row)
conn.close()
