import sqlite3
import os

DB_PATH = r'D:\finance\data\tq_daily_2026.db'
if not os.path.exists(DB_PATH):
    print('DB not exists')
    exit(1)

conn = sqlite3.connect(DB_PATH)
cur = conn.cursor()

# 检查 stock_daily 表中的年份分布
print('=== stock_daily year distribution ===')
cur.execute('''
    SELECT substr(trade_date, 1, 4) as year, COUNT(*) as cnt, MIN(trade_date) as min_date, MAX(trade_date) as max_date
    FROM stock_daily
    GROUP BY year
    ORDER BY year
''')
for row in cur.fetchall():
    print(row)

# 检查 index_daily 表中的年份分布
print('\n=== index_daily year distribution ===')
cur.execute('''
    SELECT substr(trade_date, 1, 4) as year, COUNT(*) as cnt, MIN(trade_date) as min_date, MAX(trade_date) as max_date
    FROM index_daily
    GROUP BY year
    ORDER BY year
''')
for row in cur.fetchall():
    print(row)

# 检查总行数
cur.execute('SELECT COUNT(*) FROM stock_daily')
print(f'\nTotal stock_daily rows: {cur.fetchone()[0]}')
cur.execute('SELECT COUNT(*) FROM index_daily')
print(f'Total index_daily rows: {cur.fetchone()[0]}')

conn.close()
