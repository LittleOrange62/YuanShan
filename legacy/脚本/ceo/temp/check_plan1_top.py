# -*- coding: utf-8 -*-
"""只读：从 plan1 08-12 拉真实个股评分，关联名称，过滤指数/转债/基金"""
import sqlite3

con = sqlite3.connect(r'D:\finance\data\fin_factor.db')
cur = con.cursor()

# 拉 plan1 08-12 全部非0分，过滤明显非个股（指数 SH000xxx/880xxx、转债 SH11/12/13、基金 SH5/15/16、LOF等）
cur.execute("""
SELECT symbol, trade_date, x_score, y_score, oper_score, total_score
FROM score_results
WHERE plan_id=1 AND trade_date='2026-08-12' AND total_score<>0
ORDER BY total_score DESC
LIMIT 200
""")
rows = cur.fetchall()
print('=== plan1 08-12 非0 Top200（原始，未过滤） ===')
for r in rows[:40]:
    print(r)

# 名称映射：从 fin_data.db stock_basic
con2 = sqlite3.connect(r'D:\finance\data\fin_data.db')
cur2 = con2.cursor()
cur2.execute("SELECT * FROM stock_basic LIMIT 3")
cols2 = [d[0] for d in cur2.description]
print('\nstock_basic 字段:', cols2)
cur2.execute("SELECT COUNT(*) FROM stock_basic")
print('stock_basic 行数:', cur2.fetchone()[0])
# 样例
cur2.execute("SELECT * FROM stock_basic LIMIT 3")
for r in cur2.fetchall():
    print(r)
con2.close()
con.close()
