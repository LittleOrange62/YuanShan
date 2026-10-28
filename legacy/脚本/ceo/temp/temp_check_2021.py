import sqlite3
import os

D = r'D:\finance\data'

# 检查各年份数据库大小和行数
for year in ['2021', '2022', '2023', '2024', '2025', '2026']:
    db_path = os.path.join(D, f'tq_daily_{year}.db')
    if not os.path.exists(db_path):
        print(f'{year}: DB not exists')
        continue
    
    size = os.path.getsize(db_path) / 1024 / 1024
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    
    # 股票日线行数
    cur.execute('SELECT COUNT(*) FROM stock_daily')
    stock_cnt = cur.fetchone()[0]
    
    # 指数日线行数
    cur.execute('SELECT COUNT(*) FROM index_daily')
    index_cnt = cur.fetchone()[0]
    
    # 日期范围
    cur.execute('SELECT MIN(trade_date), MAX(trade_date) FROM stock_daily')
    date_range = cur.fetchone()
    
    # 股票数量
    cur.execute('SELECT COUNT(DISTINCT ts_code) FROM stock_daily')
    stock_count = cur.fetchone()[0]
    
    conn.close()
    
    print(f'{year}: {size:.1f}MB, stocks={stock_count}, stock_daily={stock_cnt}, index_daily={index_cnt}, dates={date_range}')

# 抽查 2021 年数据
print('\n=== 2021 Sample Check ===')
conn = sqlite3.connect(os.path.join(D, 'tq_daily_2021.db'))
cur = conn.cursor()

# 检查某只股票的完整数据
cur.execute('''
    SELECT ts_code, COUNT(*) as cnt, MIN(trade_date) as min_d, MAX(trade_date) as max_d
    FROM stock_daily
    GROUP BY ts_code
    ORDER BY cnt DESC
    LIMIT 5
''')
print('Top 5 stocks by count in 2021:')
for row in cur.fetchall():
    print(f'  {row[0]}: {row[1]} days, {row[2]} ~ {row[3]}')

# 检查指数数据
cur.execute('SELECT ts_code, COUNT(*) as cnt FROM index_daily GROUP BY ts_code')
print('\nIndex data in 2021:')
for row in cur.fetchall():
    print(f'  {row[0]}: {row[1]} days')

# 检查特定股票的数据连续性
cur.execute('''
    SELECT trade_date, close, pct_change
    FROM stock_daily
    WHERE ts_code = '600519.SH'
    ORDER BY trade_date
    LIMIT 10
''')
print('\n600519.SH sample (first 10):')
for row in cur.fetchall():
    print(f'  {row[0]}: close={row[1]}, pct_change={row[2]}')

conn.close()
