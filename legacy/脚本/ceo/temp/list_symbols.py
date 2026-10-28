import sqlite3
db = r'D:\finance\data\fin_data_2026.db'
conn = sqlite3.connect(db)
rows = conn.execute('SELECT symbol FROM daily_kline LIMIT 20').fetchall()
print('sample symbols', rows)
conn.close()
