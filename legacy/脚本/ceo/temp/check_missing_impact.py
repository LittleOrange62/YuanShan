# -*- coding: utf-8 -*-
"""只读诊断：检查 08-14 缺失的 7 只真实股票是否影响自选股/板块"""
import sqlite3

conn = sqlite3.connect(r'D:\finance\data\fin_data_2026.db')
cur = conn.cursor()

missing_real = ['SH603468', 'SH688825', 'SH688828', 'SZ001232', 'SZ301677', 'SZ301707', 'SZ301717']

# 1. 找自选股表
cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
tables = [r[0] for r in cur.fetchall()]
print('数据库表:', tables)

# 2. 检查自选股表（常见表名）
for t in tables:
    if 'watch' in t.lower() or 'select' in t.lower() or 'stock' in t.lower() or 'basic' in t.lower():
        try:
            cur.execute(f"SELECT COUNT(*) FROM {t}")
            cnt = cur.fetchone()[0]
            print(f'  表 {t}: {cnt} 行')
        except Exception as e:
            print(f'  表 {t}: {e}')

conn.close()
