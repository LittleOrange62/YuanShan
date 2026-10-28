# -*- coding: utf-8 -*-
"""查看 daily_close_data.db 表结构"""
import sqlite3

DB = r'D:\new_tdx64\daily_close_data.db'
conn = sqlite3.connect(DB)
c = conn.cursor()

c.execute("PRAGMA table_info(day_end_20260824)")
cols = [r[1] for r in c.fetchall()]
print(f'列名 ({len(cols)}):')
for i, col in enumerate(cols):
    print(f'  {i}: {col}')

# 查看一行数据
print('\n=== 前3行数据 ===')
c.execute("SELECT * FROM day_end_20260824 LIMIT 3")
rows = c.fetchall()
for row in rows:
    print('---')
    for col, val in zip(cols, row):
        print(f'  {col}: {val}')

conn.close()
