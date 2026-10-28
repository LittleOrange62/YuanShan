# -*- coding: utf-8 -*-
"""只读检查：确认各库最新数据日期（决策依据）"""
import sqlite3

# 1. fin_data_2026.db daily_kline 最新日期
con = sqlite3.connect(r'D:\finance\data\fin_data_2026.db')
cur = con.cursor()
cur.execute("SELECT MAX(trade_date), MIN(trade_date), COUNT(DISTINCT symbol) FROM daily_kline")
print('daily_kline 最新/最早/股票数:', cur.fetchone())
cur.execute("SELECT trade_date, COUNT(*) FROM daily_kline GROUP BY trade_date ORDER BY trade_date DESC LIMIT 5")
print('最近5个交易日行数:')
for r in cur.fetchall():
    print('  ', r)
con.close()

# 2. fin_factor.db score_results 最新日期
con = sqlite3.connect(r'D:\finance\data\fin_factor.db')
cur = con.cursor()
cur.execute("SELECT MAX(trade_date), MIN(trade_date), COUNT(DISTINCT plan_id) FROM score_results")
print('\nscore_results 最新/最早/plan数:', cur.fetchone())
cur.execute("SELECT plan_id, trade_date, COUNT(*) FROM score_results GROUP BY plan_id, trade_date ORDER BY trade_date DESC, plan_id LIMIT 15")
print('最近评分记录 (plan_id, trade_date, 数量):')
for r in cur.fetchall():
    print('  ', r)
con.close()

# 3. fin_data.db sector_heat 最新日期
con = sqlite3.connect(r'D:\finance\data\fin_data.db')
cur = con.cursor()
cur.execute("SELECT MAX(date), COUNT(*) FROM sector_heat")
print('\nsector_heat 最新/总数:', cur.fetchone())
con.close()

# 4. scoring.db xy_score_results 是否为空
con = sqlite3.connect(r'D:\finance\data\scoring.db')
cur = con.cursor()
cur.execute("SELECT COUNT(*) FROM xy_score_results")
print('\nscoring.db xy_score_results 行数:', cur.fetchone()[0])
cur.execute("SELECT COUNT(*) FROM sector_heat_daily")
print('scoring.db sector_heat_daily 行数:', cur.fetchone()[0])
con.close()
