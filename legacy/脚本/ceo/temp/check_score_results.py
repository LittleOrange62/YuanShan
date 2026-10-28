# -*- coding: utf-8 -*-
"""只读诊断：检查 score_results 08-14 状态 + 找 stocks_master.db"""
import sqlite3, os

# 找 stocks_master.db
for root in [r'D:\finance', r'D:\finance\data', r'C:\Users\Administrator\.qwenpaw\workspaces']:
    if os.path.exists(root):
        for dirpath, dirnames, filenames in os.walk(root):
            for f in filenames:
                if 'stocks_master' in f.lower():
                    print(f'找到: {os.path.join(dirpath, f)}')
            if dirpath.count(os.sep) > root.count(os.sep) + 3:
                dirnames[:] = []

print()
conn = sqlite3.connect(r'D:\finance\data\fin_factor.db')
cur = conn.cursor()
# score_results 结构
cur.execute("PRAGMA table_info(score_results)")
cols = [r[1] for r in cur.fetchall()]
print('score_results 字段:', cols)
# 最近的评分日期
cur.execute("SELECT MAX(trade_date), MIN(trade_date) FROM score_results")
print('score_results 日期范围:', cur.fetchone())
# 08-14 数据量
try:
    cur.execute("SELECT COUNT(*) FROM score_results WHERE trade_date LIKE '2026-08-14%'")
    print('08-14 评分条数:', cur.fetchone()[0])
except Exception as e:
    print('08-14 查询失败:', e)
# 最近5个日期分布
cur.execute("SELECT trade_date, COUNT(*) FROM score_results GROUP BY trade_date ORDER BY trade_date DESC LIMIT 5")
for r in cur.fetchall():
    print(' ', r)
conn.close()
