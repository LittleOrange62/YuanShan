# validation script
import sqlite3
DB = r'D:\finance\data\fin_data_2026.db'
con = sqlite3.connect(DB)
cur = con.cursor()
# total count
cur.execute("SELECT COUNT(*) FROM daily_kline WHERE trade_date='2026-08-14'")
total = cur.fetchone()[0]
print('Total rows for 2026-08-14:', total)
# distribution
cur.execute("SELECT substr(symbol,1,2) AS p, COUNT(*) FROM daily_kline WHERE trade_date='2026-08-14' GROUP BY p")
rows = cur.fetchall()
for p,cnt in rows:
    print(p, cnt)
con.close()
