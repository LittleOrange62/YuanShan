# -*- coding: utf-8 -*-
"""只读：拉取 plan3 08-12 真实评分卡数据（决策依据）"""
import sqlite3

con = sqlite3.connect(r'D:\finance\data\fin_factor.db')
cur = con.cursor()

# 查看 score_results 最新 plan3 数据样例
cur.execute("""
SELECT symbol, trade_date, x_score, y_score, oper_score, total_score
FROM score_results
WHERE plan_id=3 AND trade_date='2026-08-12'
ORDER BY total_score DESC
LIMIT 30
""")
rows = cur.fetchall()
print('=== plan3 08-12 Top30 (原始) ===')
for r in rows:
    print(r)

con.close()
