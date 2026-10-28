import sqlite3
import os
from datetime import datetime

D = r'D:\finance\data'
SRC_DB = os.path.join(D, 'tq_daily_2026.db')

# 需要拆分的年份
years = ['2021', '2022', '2023', '2024', '2025', '2026']

# 连接源数据库
src_conn = sqlite3.connect(SRC_DB)
src_cur = src_conn.cursor()

# 获取 stock_daily 表结构
src_cur.execute("PRAGMA table_info(stock_daily)")
stock_cols = [row[1] for row in src_cur.fetchall()]
print(f'stock_daily columns: {stock_cols}')

# 获取 index_daily 表结构
src_cur.execute("PRAGMA table_info(index_daily)")
index_cols = [row[1] for row in src_cur.fetchall()]
print(f'index_daily columns: {index_cols}')

for year in years:
    # 创建年度数据库
    db_path = os.path.join(D, f'tq_daily_{year}.db')
    if os.path.exists(db_path):
        os.remove(db_path)
    
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    
    # 创建 stock_daily 表
    cur.execute(f'''
        CREATE TABLE stock_daily (
            ts_code TEXT NOT NULL,
            trade_date TEXT NOT NULL,
            open REAL,
            high REAL,
            low REAL,
            close REAL,
            pre_close REAL,
            change REAL,
            pct_change REAL,
            vol REAL,
            amount REAL,
            turnover_rate REAL,
            total_mv REAL,
            float_mv REAL,
            total_share REAL,
            float_share REAL,
            adj_factor REAL DEFAULT 1.0,
            limit_up REAL,
            limit_down REAL,
            source TEXT DEFAULT 'tq',
            updated_at TEXT DEFAULT (datetime('now','localtime')),
            PRIMARY KEY (ts_code, trade_date)
        )
    ''')
    
    # 创建 index_daily 表
    cur.execute(f'''
        CREATE TABLE index_daily (
            ts_code TEXT NOT NULL,
            trade_date TEXT NOT NULL,
            name TEXT,
            open REAL,
            high REAL,
            low REAL,
            close REAL,
            pre_close REAL,
            change REAL,
            pct_change REAL,
            vol REAL,
            amount REAL,
            source TEXT DEFAULT 'tq',
            updated_at TEXT DEFAULT (datetime('now','localtime')),
            PRIMARY KEY (ts_code, trade_date)
        )
    ''')
    
    # 从源数据库复制该年份的数据
    print(f'Processing {year}...')
    
    # 复制 stock_daily
    src_cur.execute('SELECT * FROM stock_daily WHERE trade_date LIKE ?', (f'{year}%',))
    rows = src_cur.fetchall()
    if rows:
        placeholders = ','.join(['?' for _ in stock_cols])
        cur.executemany(f'INSERT OR REPLACE INTO stock_daily VALUES ({placeholders})', rows)
        print(f'  stock_daily: {len(rows)} rows')
    
    # 复制 index_daily
    src_cur.execute('SELECT * FROM index_daily WHERE trade_date LIKE ?', (f'{year}%',))
    rows = src_cur.fetchall()
    if rows:
        placeholders = ','.join(['?' for _ in index_cols])
        cur.executemany(f'INSERT OR REPLACE INTO index_daily VALUES ({placeholders})', rows)
        print(f'  index_daily: {len(rows)} rows')
    
    conn.commit()
    conn.close()

src_conn.close()

# 删除旧的混合数据库
os.remove(SRC_DB)
# 删除 WAL 和 SHM 文件
for ext in ['-wal', '-shm']:
    wal_path = SRC_DB + ext
    if os.path.exists(wal_path):
        os.remove(wal_path)

print('\nDone. Split by year completed.')
print('Files created:')
for year in years:
    db_path = os.path.join(D, f'tq_daily_{year}.db')
    if os.path.exists(db_path):
        size = os.path.getsize(db_path)
        print(f'  tq_daily_{year}.db ({size/1024/1024:.1f} MB)')
