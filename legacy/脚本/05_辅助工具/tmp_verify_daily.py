# -*- coding: utf-8 -*-
"""验证: 600367/指数/基金 各年覆盖 + 2026库缺失的3代码"""
import sqlite3

print('=== 600367.SH 各年 ===')
for y in ['2021', '2022', '2023', '2024', '2025', '2026']:
    conn = sqlite3.connect(rf'D:\finance\data\tq_daily_{y}.db')
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*), MIN(trade_date), MAX(trade_date) FROM stock_daily WHERE ts_code='600367.SH'")
    cnt, mn, mx = cur.fetchone()
    cur.execute("SELECT close FROM stock_daily WHERE ts_code='600367.SH' ORDER BY trade_date DESC LIMIT 1")
    last_close = cur.fetchone()[0]
    print(f'  {y}: {cnt} 条 [{mn}~{mx}] 年末收盘={last_close}')
    conn.close()

print()
print('=== 指数/基金 2021 库覆盖 ===')
conn = sqlite3.connect(r'D:\finance\data\tq_daily_2021.db')
cur = conn.cursor()
for code in ['000001.SH', '399001.SZ', '399006.SZ', '000300.SH', '000905.SH']:
    cur.execute("SELECT COUNT(*), MIN(trade_date), MAX(trade_date) FROM stock_daily WHERE ts_code=?", (code,))
    cnt, mn, mx = cur.fetchone()
    print(f'  {code}: {cnt} 条 [{mn}~{mx}]')
conn.close()

print()
print('=== 2026 库 9135 vs 9138 差异 ===')
conn = sqlite3.connect(r'D:\finance\data\tq_daily_2026.db')
cur = conn.cursor()
cur.execute('SELECT DISTINCT ts_code FROM stock_daily')
in2026 = set(r[0] for r in cur.fetchall())
conn.close()
# vipdoc 全部代码
import os
all_codes = set()
for m in ['sh', 'sz']:
    d = rf'D:\new_tdx64\vipdoc\{m}\lday'
    for fn in os.listdir(d):
        if fn.endswith('.day'):
            all_codes.add(f'{fn[2:8]}.{"SH" if m=="sh" else "SZ"}')
missing = sorted(all_codes - in2026)
print(f'vipdoc 代码数: {len(all_codes)}, 2026库: {len(in2026)}, 缺失: {missing}')
