import sqlite3
import os
import shutil
from datetime import datetime

D = r'D:\finance\data'
SRC_DB = os.path.join(D, 'tq_daily_2026.db')

# 先复制一份避免锁定
backup_db = os.path.join(D, 'tq_daily_2026_backup.db')
if os.path.exists(backup_db):
    os.remove(backup_db)
shutil.copy2(SRC_DB, backup_db)
print(f'Backed up to {backup_db}')

years = ['2021', '2022', '2023', '2024', '2025', '2026']

# 使用备份数据库作为源
src_conn = sqlite3.connect(backup_db)
src_cur = src_conn.cursor()

src_cur.execute("PRAGMA table_info(stock_daily)")
stock_cols = [row[1] for row in src_cur.fetchall()]

src_cur.execute("PRAGMA table_info(index_daily)")
index_cols = [row[1] for row in src_cur.fetchall()]

for year in years:
    db_path = os.path.join(D, f'tq_daily_{year}.db')
    if os.path.exists(db_path):
        os.remove(db_path)
    
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    
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
    
    print(f'Processing {year}...')
    
    src_cur.execute('SELECT * FROM stock_daily WHERE trade_date LIKE ?', (f'{year}%',))
    rows = src_cur.fetchall()
    if rows:
        placeholders = ','.join(['?' for _ in stock_cols])
        cur.executemany(f'INSERT OR REPLACE INTO stock_daily VALUES ({placeholders})', rows)
        print(f'  stock_daily: {len(rows)} rows')
    
    src_cur.execute('SELECT * FROM index_daily WHERE trade_date LIKE ?', (f'{year}%',))
    rows = src_cur.fetchall()
    if rows:
        placeholders = ','.join(['?' for _ in index_cols])
        cur.executemany(f'INSERT OR REPLACE INTO index_daily VALUES ({placeholders})', rows)
        print(f'  index_daily: {len(rows)} rows')
    
    conn.commit()
    conn.close()

src_conn.close()

# 删除备份
if os.path.exists(backup_db):
    os.remove(backup_db)
    for ext in ['-wal', '-shm']:
        wal_path = backup_db + ext
        if os.path.exists(wal_path):
            os.remove(wal_path)

print('\nDone. Split by year completed.')
for year in years:
    db_path = os.path.join(D, f'tq_daily_{year}.db')
    if os.path.exists(db_path):
        size = os.path.getsize(db_path)
        print(f'  tq_daily_{year}.db ({size/1024/1024:.1f} MB)')
