# -*- coding: utf-8 -*-
"""查看表完整结构并搜索600367"""
import sqlite3

DB = r'D:\new_tdx64\daily_close_data.db'
conn = sqlite3.connect(DB)
c = conn.cursor()

c.execute("PRAGMA table_info(day_end_20260824)")
cols = [r[1] for r in c.fetchall()]
print(f'=== 全部列名 ({len(cols)}) ===')
for i in range(0, len(cols), 8):
    print(', '.join(f'{j}:{cols[j]}' for j in range(i, min(i+8, len(cols)))))

# 查询第一行数据，看哪个列是股票代码
print('\n=== 第一行 ===')
c.execute("SELECT * FROM day_end_20260824 LIMIT 1")
row = c.fetchone()
for col, val in zip(cols, row):
    sval = str(val)
    if len(sval) > 80:
        sval = sval[:80] + '...'
    print(f'  {col}: {sval}')

# 全表扫描找600367
print('\n=== 搜索600367 ===')
for col in cols:
    try:
        c.execute(f"SELECT COUNT(*) FROM day_end_20260824 WHERE [{col}] LIKE '%600367%'")
        cnt = c.fetchone()[0]
        if cnt > 0:
            print(f'列 {col} 包含600367: {cnt} 条')
    except Exception as e:
        pass

conn.close()
