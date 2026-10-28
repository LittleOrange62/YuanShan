# -*- coding: utf-8 -*-
"""只读诊断：检查 stocks_master.db / fin_factor.db 08-14 评分数据状态"""
import sqlite3, os

# 找数据库文件
for root in [r'D:\finance\data']:
    if os.path.exists(root):
        for f in os.listdir(root):
            if f.endswith('.db'):
                print(f'{root}\\{f}  ({os.path.getsize(os.path.join(root, f))//1024} KB)')

print()

# 检查 stocks_master.db
for db in [r'D:\finance\data\stocks_master.db', r'D:\finance\data\fin_factor.db']:
    if os.path.exists(db):
        print(f'=== {db} ===')
        conn = sqlite3.connect(db)
        cur = conn.cursor()
        cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = [r[0] for r in cur.fetchall()]
        print('表:', tables)
        for t in tables:
            try:
                cur.execute(f"SELECT COUNT(*) FROM {t}")
                print(f'  {t}: {cur.fetchone()[0]} 行')
            except:
                pass
        conn.close()
