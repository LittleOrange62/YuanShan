# -*- coding: utf-8 -*-
"""查询 daily_close_data.db 中 600367 的最新快照数据"""
import sqlite3
import sys

DB = r'D:\new_tdx64\daily_close_data.db'
TABLES = ['day_end_20260824', 'day_end_20260821', 'day_end_20260819']

conn = sqlite3.connect(DB)
c = conn.cursor()

for table in TABLES:
    # 检查表是否存在
    c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,))
    if not c.fetchone():
        print(f'表 {table} 不存在')
        continue
    # 查看表结构
    c.execute(f"PRAGMA table_info({table})")
    cols = [r[1] for r in c.fetchall()]
    # 找代码列
    code_col = None
    for col in cols:
        if col.lower() in ('code', '股票代码', '代码', 'itemnum'):
            code_col = col
            break
    print(f'=== {table} 列数: {len(cols)} ===')
    if code_col:
        print(f'代码列: {code_col}')
        # 查询600367
        c.execute(f"SELECT * FROM {table} WHERE {code_col} LIKE '%600367%'")
        rows = c.fetchall()
        print(f'600367 记录数: {len(rows)}')
        for row in rows:
            for col, val in zip(cols, row):
                print(f'  {col}: {val}')
    else:
        print('未找到代码列，前3列示例:')
        c.execute(f"SELECT * FROM {table} LIMIT 1")
        row = c.fetchone()
        if row:
            for col, val in zip(cols[:10], row[:10]):
                print(f'  {col}: {val}')

conn.close()
