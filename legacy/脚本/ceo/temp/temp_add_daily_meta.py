import sqlite3
import os
from datetime import datetime

D = r'D:\finance\data'

# 为 tq_daily_YYYY.db 系列添加 meta_tables
for year in ['2021', '2022', '2023', '2024', '2025', '2026']:
    db_path = os.path.join(D, f'tq_daily_{year}.db')
    if not os.path.exists(db_path):
        continue
    
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    
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
    
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    
    # stock_daily 元数据
    stock_meta = {
        'comment': f'个股日线行情表（{year}年数据）',
        'columns': {
            'ts_code': '股票代码（联合主键）',
            'trade_date': '交易日期 YYYYMMDD（联合主键）',
            'open': '开盘价（元）',
            'high': '最高价（元）',
            'low': '最低价（元）',
            'close': '收盘价（元）',
            'pre_close': '昨收价（元）',
            'change': '涨跌额（元）',
            'pct_change': '涨跌幅（%）',
            'vol': '成交量（手）',
            'amount': '成交额（千元）',
            'turnover_rate': '换手率（%）',
            'total_mv': '总市值（万元）',
            'float_mv': '流通市值（万元）',
            'total_share': '总股本（股）',
            'float_share': '流通股本（股）',
            'adj_factor': '复权因子（默认1.0）',
            'limit_up': '涨停价（元）',
            'limit_down': '跌停价（元）',
            'source': '数据来源（tq）',
            'updated_at': '写入时间'
        }
    }
    
    # index_daily 元数据
    index_meta = {
        'comment': f'指数日线行情表（{year}年数据）',
        'columns': {
            'ts_code': '指数代码（联合主键）',
            'trade_date': '交易日期 YYYYMMDD（联合主键）',
            'name': '指数名称',
            'open': '开盘价',
            'high': '最高价',
            'low': '最低价',
            'close': '收盘价',
            'pre_close': '昨收价',
            'change': '涨跌额',
            'pct_change': '涨跌幅（%）',
            'vol': '成交量',
            'amount': '成交额',
            'source': '数据来源（tq）',
            'updated_at': '写入时间'
        }
    }
    
    import json
    for table_name, meta in [('stock_daily', stock_meta), ('index_daily', index_meta)]:
        cur.execute('''
            INSERT OR REPLACE INTO meta_tables (table_name, table_comment, column_comment, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?)
        ''', (table_name, meta['comment'], json.dumps(meta['columns'], ensure_ascii=False), now, now))
    
    conn.commit()
    conn.close()
    print(f'tq_daily_{year}.db: meta_tables updated')

print('\nDone.')
