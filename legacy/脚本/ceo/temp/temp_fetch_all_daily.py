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

print(f'Total symbols to fetch: {len(symbols)}')

# 主要指数代码
INDEX_CODES = {
    '000001.SH': '上证指数',
    '000300.SH': '沪深300',
    '000852.SH': '中证1000',
    '000905.SH': '中证500',
    '399001.SZ': '深证成指',
    '399006.SZ': '创业板指',
    '899050.BJ': '北证50'
}

BATCH_SIZE = 500
start_time = time.time()
errors = []

# 缓存各年份数据库连接，避免频繁开关
db_conns = {}

def get_year_db(year):
    """根据年份获取数据库连接，文件不存在则自动创建并初始化表结构"""
    if year not in db_conns:
        db_path = os.path.join(D, f'tq_daily_{year}.db')
        conn = sqlite3.connect(db_path)
        cur = conn.cursor()
        # 确保 stock_daily 表存在
        cur.execute('''
            CREATE TABLE IF NOT EXISTS stock_daily (
                ts_code TEXT,
                trade_date TEXT,
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
        # 确保 index_daily 表存在
        cur.execute('''
            CREATE TABLE IF NOT EXISTS index_daily (
                ts_code TEXT,
                trade_date TEXT,
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
        # 确保 meta_tables 表存在
        cur.execute('''
            CREATE TABLE IF NOT EXISTS meta_tables (
                table_name TEXT PRIMARY KEY,
                table_comment TEXT,
                column_comment TEXT,
                created_at TEXT DEFAULT (datetime('now','localtime')),
                updated_at TEXT DEFAULT (datetime('now','localtime'))
            )
        ''')
        conn.commit()
        db_conns[year] = conn
    return db_conns[year]

for idx, (ts_code, pure_code, market, tdx_code) in enumerate(symbols):
    try:
        # 使用 mootdx 读取日线
        df = r.daily(tdx_code)
        if df is None or df.empty:
            continue
        
        # 准备数据
        df = df.reset_index()
        df['trade_date'] = df['date'].dt.strftime('%Y%m%d')
        df['ts_code'] = ts_code
        df['year'] = df['trade_date'].str[:4]
        
        # 确定资产类型
        asset_type = 'index' if ts_code in INDEX_CODES else 'stock'
        
        # 按年份分组写入不同的数据库文件
        year_groups = df.groupby('year')
        for year, group in year_groups:
            conn = get_year_db(year)
            cur = conn.cursor()
            
            # 插入 stock_daily
            for _, row in group.iterrows():
                cur.execute('''
                    INSERT OR REPLACE INTO stock_daily 
                    (ts_code, trade_date, open, high, low, close, pre_close, change, pct_change, vol, amount, turnover_rate, total_mv, float_mv, total_share, float_share, adj_factor, limit_up, limit_down, source, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''', (
                    row['ts_code'], row['trade_date'], row.get('open'), row.get('high'), row.get('low'), row.get('close'),
                    row.get('pre_close'), row.get('change'), row.get('pct_change'), row.get('volume'), row.get('amount'),
                    None, None, None, None, None, 1.0, None, None, 'tq', datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                ))
            
            # 如果是主要指数，也插入 index_daily
            if ts_code in INDEX_CODES:
                for _, row in group.iterrows():
                    cur.execute('''
                        INSERT OR REPLACE INTO index_daily 
                        (ts_code, trade_date, name, open, high, low, close, pre_close, change, pct_change, vol, amount, source, updated_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ''', (
                        row['ts_code'], row['trade_date'], INDEX_CODES[ts_code], row.get('open'), row.get('high'), row.get('low'), row.get('close'),
                        row.get('pre_close'), row.get('change'), row.get('pct_change'), row.get('volume'), row.get('amount'),
                        'tq', datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                    ))
            
            conn.commit()
        
        # 每批提交进度
        if (idx + 1) % BATCH_SIZE == 0:
            elapsed = time.time() - start_time
            print(f'Progress: {idx+1}/{len(symbols)} ({elapsed:.1f}s, {(idx+1)/elapsed:.1f} stocks/s)')
    
    except Exception as e:
        errors.append((ts_code, str(e)))
        if len(errors) <= 5:
            print(f'Error fetching {ts_code}: {e}')

# 关闭所有数据库连接
for conn in db_conns.values():
    conn.commit()
    conn.close()

print(f'Done. Total: {len(symbols)}, Errors: {len(errors)}')
if errors:
    print('First 5 errors:')
    for e in errors[:5]:
        print(e)
