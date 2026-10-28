# -*- coding: utf-8 -*-
"""只读检查：fin_data_2026.db 最新 day_end 表与评分数据（决策依据，不改库）"""
import sqlite3

db = r'D:\finance\data\fin_data_2026.db'
con = sqlite3.connect(db)
cur = con.cursor()

# 1. 最近 day_end 表
cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'day_end%' ORDER BY name DESC LIMIT 15")
tables = [r[0] for r in cur.fetchall()]
print('=== 最近 day_end 表 ===')
print(tables)

# 2. 最新表行数
if tables:
    latest = tables[0]
    cur.execute(f'SELECT COUNT(*) FROM "{latest}"')
    print(f'{latest} 行数:', cur.fetchone()[0])
    # 抽样看字段
    cur.execute(f'SELECT * FROM "{latest}" LIMIT 3')
    cols = [d[0] for d in cur.description]
    print('字段:', cols)
    rows = cur.fetchall()
    for r in rows[:2]:
        print(r)

con.close()
