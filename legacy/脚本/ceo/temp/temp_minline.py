import sqlite3
import os
import pandas as pd
from datetime import datetime

D = r'D:\finance\data'
SRC_DB = os.path.join(D, 'tq_daily_2026.db')
DST_DB = os.path.join(D, 'tq_minline_2026.db')

print('Loading data from tq_daily_2026.db...')
src_conn = sqlite3.connect(SRC_DB)
src_cur = src_conn.cursor()

# 获取所有股票代码
src_cur.execute('SELECT DISTINCT ts_code FROM stock_daily')
all_codes = [row[0] for row in src_cur.fetchall()]
print(f'Total stocks: {len(all_codes)}')

# 连接目标数据库
dst_conn = sqlite3.connect(DST_DB)
dst_cur = dst_conn.cursor()

# 创建表（如果不存在）
dst_cur.execute('''
    CREATE TABLE IF NOT EXISTS minline (
        ts_code TEXT NOT NULL,
        trade_date TEXT NOT NULL,
        minute_time TEXT NOT NULL,
        open REAL,
        high REAL,
        low REAL,
        close REAL,
        vol REAL,
        amt REAL,
        created_at TEXT DEFAULT (datetime('now','localtime')),
        PRIMARY KEY (ts_code, trade_date, minute_time)
    )
''')

# 表结构说明
dst_cur.execute('''
    INSERT OR REPLACE INTO meta_tables 
    (table_name, table_desc, columns, created_at)
    VALUES 
    ('minline', '分钟线数据表（按年拆分）', 
     'ts_code,trade_date,minute_time,open,high,low,close,vol,amt,created_at',
     datetime('now','localtime'))
''')

BATCH_SIZE = 20
success = 0
errors = []

for idx, code in enumerate(all_codes):
    try:
        # 读取该股票的日线数据
        df = pd.read_sql(
            f"SELECT * FROM stock_daily WHERE ts_code = ? ORDER BY trade_date",
            src_conn,
            params=(code,)
        )
        
        if len(df) < 1:
            continue
        
        # 简化版：只取第一天的分钟数据（后续可扩展为实际分钟数据）
        row = df.iloc[0]
        trade_date = row['trade_date']
        
        # 模拟分钟数据（实际需替换为 mootdx 读取 lc1 数据）
        minline_data = [
            (code, trade_date, f"{trade_date} 09:30", 0, 0, 0, 0, 0, 0, datetime.now().strftime('%Y-%m-%d %H:%M:%S')),
            (code, trade_date, f"{trade_date} 11:30", 0, 0, 0, 0, 0, 0, datetime.now().strftime('%Y-%m-%d %H:%M:%S')),
            (code, trade_date, f"{trade_date} 13:00", 0, 0, 0, 0, 0, 0, datetime.now().strftime('%Y-%m-%d %H:%M:%S')),
            (code, trade_date, f"{trade_date} 15:00", 0, 0, 0, 0, 0, 0, datetime.now().strftime('%Y-%m-%d %H:%M:%S')),
        ]
        
        dst_cur.executemany('''
            INSERT OR REPLACE INTO minline 
            (ts_code, trade_date, minute_time, open, high, low, close, vol, amt, created_at)
            VALUES (?,?,?,?,?,?,?,?,?,?)
        ''', minline_data)
        
        success += 1
        
        if (idx + 1) % BATCH_SIZE == 0:
            dst_conn.commit()
            print(f'Progress: {idx+1}/{len(all_codes)} ({success} processed)')
    
    except Exception as e:
        errors.append((code, str(e)))
        if len(errors) <= 5:
            print(f'Error processing {code}: {e}')

dst_conn.commit()
dst_conn.close()
src_conn.close()

print(f'Done. Processed: {success}, Errors: {len(errors)}')
if errors:
    print('First 5 errors:')
    for e in errors[:5]:
        print(e)
