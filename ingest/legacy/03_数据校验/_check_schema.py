import sqlite3

conn = sqlite3.connect(r'D:\finance\data\tq_daily_2026.db')
cur = conn.cursor()

# 查看 stock_daily 表结构
print('=== stock_daily 表结构 ===')
cur.execute('PRAGMA table_info(stock_daily)')
for row in cur.fetchall():
    print(row)

# 查看 sector_daily 表结构
print('\n=== sector_daily 表结构 ===')
cur.execute('PRAGMA table_info(sector_daily)')
for row in cur.fetchall():
    print(row)

conn.close()
