import sqlite3

conn = sqlite3.connect(r'D:\finance\data\tq_daily_2026.db')
cur = conn.cursor()
cur.execute('pragma table_info(stock_daily)')
print('=== stock_daily 列结构 ===')
for r in cur.fetchall():
    print(r)

print()
cur.execute("select ts_code, trade_date, open, high, low, close, vol, amount from stock_daily where ts_code='600519.SH' limit 3")
print('=== 600519.SH 样例 ===')
for r in cur.fetchall():
    print(r)

conn.close()
