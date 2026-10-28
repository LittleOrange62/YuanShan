import sqlite3
db = r'D:\finance\data\fin_data_2026.db'
conn = sqlite3.connect(db)
symbol='300398'
rows = conn.execute(f"SELECT trade_date, close FROM daily_kline WHERE symbol=? ORDER BY trade_date DESC LIMIT 10", (symbol,)).fetchall()
print('rows for',symbol, rows)
conn.close()
