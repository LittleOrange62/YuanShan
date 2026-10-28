import sqlite3
import os
from datetime import datetime

D = r'D:\finance\data'
backup_db = os.path.join(D, 'tq_daily_2026_backup.db')
SRC_DB = os.path.join(D, 'tq_daily_2026.db')

# 如果备份存在则使用备份，否则使用原文件
if os.path.exists(backup_db):
    src_path = backup_db
    print('Using backup DB')
else:
    src_path = SRC_DB
    print('Using original DB')

years = ['2021', '2022', '2023', '2024', '2025', '2026']

src_conn = sqlite3.connect(src_path)
src_cur = src_conn.cursor()

src_cur.execute("PRAGMA table_info(stock_daily)")
stock_cols = [row[1] for row in src_cur.fetchall()]

src_cur.execute("PRAGMA table_info(index_daily)")
index_cols = [row[1] for row in src_cur.fetchall()]

for year in years:
    db_path = os.path.join(D, f'tq_daily_{year}.db')
    # 如果文件存在且无法删除，尝试连接并清空
    if os.path.exists(db_path):
        try:
            os.remove(db_path)
        except PermissionError:
            print(f'Warning: {db_path} locked, trying to connect...')
            try:
                temp_conn = sqlite3.connect(db_path)
                temp_conn.execute('DELETE FROM stock_daily')
                temp_conn.execute('DELETE FROM index_daily')
                temp_conn.commit()
                temp_conn.close()
                print(f'  Cleared existing data')
            except Exception as e:
                print(f'  Cannot clear: {e}')
    
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    
    # 创建表（如果不存在）
    cur.execute(f'''
        CREATE TABLE IF NOT EXISTS stock_daily (
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
        CREATE TABLE IF NOT EXISTS index_daily (
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

# 尝试删除备份
try:
    if os.path.exists(backup_db):
        os.remove(backup_db)
except:
    pass

print('\nDone. Split by year completed.')
for year in years:
    db_path = os.path.join(D, f'tq_daily_{year}.db')
    if os.path.exists(db_path):
        size = os.path.getsize(db_path)
        print(f'  tq_daily_{year}.db ({size/1024/1024:.1f} MB)')
