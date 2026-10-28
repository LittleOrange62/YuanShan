import os
import sqlite3
import pandas as pd
from mootdx.reader import Reader
from datetime import datetime
import time

r = Reader.factory(market='std', tdxdir='D:/new_tdx64')
D = r'D:\finance\data'

# 获取所有股票代码
symbols = []
for ex in ['sh', 'sz', 'bj']:
    lday_dir = f'D:/new_tdx64/vipdoc/{ex}/lday'
    if not os.path.exists(lday_dir):
        continue
    for f in os.listdir(lday_dir):
        if f.endswith('.day'):
            code = f.replace('.day', '')
            if code.startswith(ex):
                pure_code = code[2:]
                if pure_code.isdigit() and len(pure_code) == 6:
                    ts_code = f'{pure_code}.{ex.upper()}'
                    symbols.append((ts_code, pure_code, ex.upper(), code))

print(f'Total symbols to fetch minute data: {len(symbols)}')

BATCH_SIZE = 100
start_time = time.time()
errors = []


def get_year_db(year):
    """根据年份获取数据库连接，文件不存在则自动创建并初始化表结构"""
    db_path = os.path.join(D, f'tq_minline_{year}.db')
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    # 确保表结构存在
    cur.execute('''
        CREATE TABLE IF NOT EXISTS stock_minline (
            ts_code TEXT NOT NULL,
            trade_datetime TEXT NOT NULL,
            period TEXT NOT NULL DEFAULT '1m',
            open REAL,
            high REAL,
            low REAL,
            close REAL,
            vol REAL,
            amount REAL,
            source TEXT DEFAULT 'tq',
            updated_at TEXT DEFAULT (datetime('now','localtime')),
            PRIMARY KEY (ts_code, trade_datetime, period)
        )
    ''')
    cur.execute('''
        CREATE TABLE IF NOT EXISTS meta_tables (
            table_name TEXT PRIMARY KEY,
            table_comment TEXT,
            column_comment TEXT,
            created_at TEXT DEFAULT (datetime('now','localtime')),
            updated_at TEXT DEFAULT (datetime('now','localtime'))
        )
    ''')
    cur.execute('''
        CREATE INDEX IF NOT EXISTS idx_stock_minline_tsdt 
        ON stock_minline(ts_code, trade_datetime)
    ''')
    # 初始化 meta_tables（如果不存在）
    cur.execute('''
        INSERT OR IGNORE INTO meta_tables (table_name, table_comment, column_comment)
        VALUES ('stock_minline', '分钟线数据表（按年拆分）', 
                'ts_code,trade_datetime,period,open,high,low,close,vol,amount,source,updated_at')
    ''')
    conn.commit()
    return conn


for idx, (ts_code, pure_code, market, tdx_code) in enumerate(symbols):
    try:
        # 获取分钟线数据（1分钟线）
        df = r.minute(tdx_code)
        if df is None or df.empty:
            continue
        
        # 准备数据
        df = df.reset_index()
        df['trade_datetime'] = df['date'].dt.strftime('%Y%m%d %H:%M')
        df['ts_code'] = ts_code
        df['period'] = '1m'
        df['year'] = df['trade_datetime'].str[:4]  # 提取年份
        
        # 按年份分组写入不同的数据库文件
        year_groups = df.groupby('year')
        for year, group in year_groups:
            conn = get_year_db(year)
            cur = conn.cursor()
            
            for _, row in group.iterrows():
                cur.execute('''
                    INSERT OR REPLACE INTO stock_minline 
                    (ts_code, trade_datetime, period, open, high, low, close, vol, amount, source, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''', (
                    row['ts_code'], row['trade_datetime'], '1m',
                    row.get('open'), row.get('high'), row.get('low'), row.get('close'),
                    row.get('volume'), row.get('amount'),
                    'tq', datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                ))
            
            conn.commit()
            conn.close()
        
        # 每批提交进度
        if (idx + 1) % BATCH_SIZE == 0:
            elapsed = time.time() - start_time
            print(f'Progress: {idx+1}/{len(symbols)} ({elapsed:.1f}s, {(idx+1)/elapsed:.1f} stocks/s)')
    
    except Exception as e:
        errors.append((ts_code, str(e)))
        if len(errors) <= 5:
            print(f'Error fetching minute data for {ts_code}: {e}')

print(f'Done. Total: {len(symbols)}, Errors: {len(errors)}')
if errors:
    print('First 5 errors:')
    for e in errors[:5]:
        print(e)
