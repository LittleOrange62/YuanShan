# -*- coding: utf-8 -*-
"""摸底2: 表结构 + 600367 各库覆盖情况"""
import sqlite3

print('=== tq_minline_2026.db 表结构 ===')
conn = sqlite3.connect(r'D:\finance\data\tq_minline_2026.db')
cur = conn.cursor()
cur.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='stock_minline'")
print(cur.fetchone()[0])
cur.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='meta_tables'")
print(cur.fetchone()[0])
conn.close()

print()
print('=== 各年份日线库 表结构 ===')
for y in ['2021', '2022', '2023', '2024', '2025', '2026']:
    conn = sqlite3.connect(rf'D:\finance\data\tq_daily_{y}.db')
    cur = conn.cursor()
    cur.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='stock_daily'")
    sql = cur.fetchone()
    print(f'tq_daily_{y}.db: {sql[0][:200] if sql else "无 stock_daily 表"}')
    # 600367 是否存在
    cur.execute("SELECT COUNT(*) FROM stock_daily WHERE ts_code='600367.SH'")
    n600 = cur.fetchone()[0]
    if n600:
        cur.execute("SELECT MIN(trade_date), MAX(trade_date) FROM stock_daily WHERE ts_code='600367.SH'")
        mn, mx = cur.fetchone()
        print(f'  600367.SH: {n600} 条 [{mn}~{mx}]')
    else:
        print(f'  600367.SH: 0 条')
    conn.close()
