# -*- coding: utf-8 -*-
"""摸底: 各年份日线库 + 分钟线库"""
import sqlite3
import os

print('=== 各年份日线库 ===')
for y in ['2021', '2022', '2023', '2024', '2025', '2026']:
    p = rf'D:\finance\data\tq_daily_{y}.db'
    if os.path.exists(p):
        sz = os.path.getsize(p)
        try:
            conn = sqlite3.connect(p)
            cur = conn.cursor()
            cur.execute('SELECT COUNT(*), COUNT(DISTINCT ts_code) FROM stock_daily')
            cnt, codes = cur.fetchone()
            cur.execute('SELECT MIN(trade_date), MAX(trade_date) FROM stock_daily')
            mn, mx = cur.fetchone()
            print(f'tq_daily_{y}.db: {sz/1024/1024:.1f}MB 记录={cnt} 代码={codes} 日期[{mn}~{mx}]')
            conn.close()
        except Exception as e:
            print(f'tq_daily_{y}.db: {sz/1024/1024:.1f}MB 读取失败: {e}')
    else:
        print(f'tq_daily_{y}.db: 不存在')

print()
print('=== 分钟线库 ===')
for p in [r'D:\finance\data\tq_minline_2026.db', r'D:\finance\data\tq_minline.db',
          r'D:\finance\data\tq_minline_2021.db', r'D:\finance\data\tq_minline_2025.db']:
    if os.path.exists(p):
        sz = os.path.getsize(p)
        try:
            conn = sqlite3.connect(p)
            cur = conn.cursor()
            cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
            tabs = [r[0] for r in cur.fetchall()]
            print(f'{os.path.basename(p)}: {sz/1024/1024:.1f}MB 表={tabs}')
            for t in tabs:
                try:
                    cur.execute(f'SELECT COUNT(*), COUNT(DISTINCT ts_code) FROM {t}')
                    cnt, codes = cur.fetchone()
                    cur.execute(f'SELECT MIN(trade_date), MAX(trade_date) FROM {t}')
                    mn, mx = cur.fetchone()
                    print(f'  表 {t}: 记录={cnt} 代码={codes} 日期[{mn}~{mx}]')
                except Exception as e:
                    print(f'  表 {t}: 查询失败 {e}')
            conn.close()
        except Exception as e:
            print(f'{p}: 读取失败: {e}')
    else:
        print(f'{os.path.basename(p)}: 不存在')
