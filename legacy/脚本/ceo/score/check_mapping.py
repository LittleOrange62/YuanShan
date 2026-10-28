#!/usr/bin/env python3
"""检查 stock_basics.db 中可用的板块映射数据"""
import sqlite3

conn = sqlite3.connect('/app/working/workspaces/data_in/data/stock_basics.db')

# 看 stock_concept 的表结构
print("=== stock_concept schema ===")
cols = conn.execute("PRAGMA table_info(stock_concept)").fetchall()
for c in cols:
    print(" ", c)

# 看有哪些数据
print("\n=== stock_concept 前10条 ===")
for r in conn.execute("SELECT * FROM stock_concept LIMIT 10").fetchall():
    print(" ", r)

# 概念数量
n_concepts = conn.execute("SELECT COUNT(DISTINCT concept_name) FROM stock_concept").fetchone()[0]
n_stocks = conn.execute("SELECT COUNT(DISTINCT ts_code) FROM stock_concept").fetchone()[0]
print(f"\n概念数: {n_concepts}, 股票数: {n_stocks}")

# 另外查 concept_board
print("\n=== concept_board ===")
cols2 = conn.execute("PRAGMA table_info(concept_board)").fetchall()
for c in cols2:
    print(" ", c)
n_cb = conn.execute("SELECT COUNT(*) FROM concept_board").fetchone()[0]
print("行数:", n_cb)
if n_cb > 0:
    print("前5条:", conn.execute("SELECT * FROM concept_board LIMIT 5").fetchall())

# industry_classification
print("\n=== industry_classification ===")
cols3 = conn.execute("PRAGMA table_info(industry_classification)").fetchall()
for c in cols3:
    print(" ", c)
n_ic = conn.execute("SELECT COUNT(*) FROM industry_classification").fetchone()[0]
print("行数:", n_ic)
if n_ic > 0:
    print("前5条:", conn.execute("SELECT * FROM industry_classification LIMIT 5").fetchall())

# sw_industry
print("\n=== sw_industry ===")
cols4 = conn.execute("PRAGMA table_info(sw_industry)").fetchall()
for c in cols4:
    print(" ", c)
n_sw = conn.execute("SELECT COUNT(*) FROM sw_industry").fetchone()[0]
print("行数:", n_sw)
if n_sw > 0:
    print("全量:", conn.execute("SELECT * FROM sw_industry").fetchall())

conn.close()
