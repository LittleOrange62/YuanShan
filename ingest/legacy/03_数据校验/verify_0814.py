import sqlite3
db_path = r'D:\finance\data\fin_data_2026.db'
conn = sqlite3.connect(db_path)
cur = conn.cursor()
cur.execute("SELECT COUNT(*) FROM daily_kline WHERE trade_date='2026-08-14'")
print('Total rows:', cur.fetchone()[0])
cur.execute("SELECT substr(symbol,1,2) AS p, COUNT(*) FROM daily_kline WHERE trade_date='2026-08-14' GROUP BY p")
rows = cur.fetchall()
for p, cnt in rows:
    print(p, cnt)
conn.close()
