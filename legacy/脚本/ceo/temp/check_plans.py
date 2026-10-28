# -*- coding: utf-8 -*-
"""只读：确认 scoring_plans 口径 + 拉取 08-12 真实评分（决策依据）"""
import sqlite3

con = sqlite3.connect(r'D:\finance\data\fin_factor.db')
cur = con.cursor()

# 1. scoring_plans 口径
cur.execute("SELECT * FROM scoring_plans")
cols = [d[0] for d in cur.description]
print('=== scoring_plans 字段:', cols)
for r in cur.fetchall():
    print(r)

con.close()
