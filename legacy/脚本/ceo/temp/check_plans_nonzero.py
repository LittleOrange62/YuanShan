# -*- coding: utf-8 -*-
"""只读：检查哪些 plan 有非0评分 + 找 stocks_master.db"""
import sqlite3, os, glob

# 1. 各 plan 08-12 非0统计
con = sqlite3.connect(r'D:\finance\data\fin_factor.db')
cur = con.cursor()
for plan in [1,3,4,5,6,7,8,9]:
    cur.execute("""
    SELECT COUNT(*), SUM(CASE WHEN total_score<>0 THEN 1 ELSE 0 END),
           SUM(CASE WHEN x_score<>0 THEN 1 ELSE 0 END),
           SUM(CASE WHEN y_score<>0 THEN 1 ELSE 0 END)
    FROM score_results WHERE plan_id=? AND trade_date='2026-08-12'
    """, (plan,))
    r = cur.fetchone()
    print(f'plan {plan}: 总{r[0]} 非0total:{r[1]} 非0x:{r[2]} 非0y:{r[3]}')
con.close()

# 2. 找 stocks_master.db
print('\n=== 找 stocks_master.db ===')
for root in [r'C:\Users\Administrator\.qwenpaw\workspaces', r'D:\finance']:
    for f in glob.glob(os.path.join(root, '**', 'stocks_master.db'), recursive=True):
        print(f, os.path.getsize(f), os.path.getmtime(f))
    for f in glob.glob(os.path.join(root, '**', 'daily_score_update.py'), recursive=True):
        print('脚本:', f)
