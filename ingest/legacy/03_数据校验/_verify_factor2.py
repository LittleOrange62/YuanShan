#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import sqlite3
db = r"D:\finance\data\fin_factor.db"
conn = sqlite3.connect(db)
c = conn.cursor()
print("最新 trade_date 及各计划条数:")
for r in c.execute("""SELECT plan_id, trade_date, COUNT(*) as cnt
                      FROM score_results
                      WHERE trade_date=(SELECT MAX(trade_date) FROM score_results)
                      GROUP BY plan_id, trade_date""").fetchall():
    print(r)
print("\n最近5个 trade_date 的总条数:")
for r in c.execute("""SELECT trade_date, COUNT(*) FROM score_results
                      GROUP BY trade_date ORDER BY trade_date DESC LIMIT 5""").fetchall():
    print(r)
conn.close()
