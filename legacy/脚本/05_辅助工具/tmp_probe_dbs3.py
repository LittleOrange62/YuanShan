# -*- coding: utf-8 -*-
"""摸底3: source 分布 + 2026库混入量 + 分钟线现状 + .day 起始"""
import sqlite3
import struct
import os

print('=== 各年份日线库 source 分布 ===')
for y in ['2021', '2022', '2023', '2024', '2025', '2026']:
    conn = sqlite3.connect(rf'D:\finance\data\tq_daily_{y}.db')
    cur = conn.cursor()
    try:
        cur.execute('SELECT source, COUNT(*) FROM stock_daily GROUP BY source')
        print(f'tq_daily_{y}.db:', dict(cur.fetchall()))
    except Exception as e:
        print(f'tq_daily_{y}.db: 查询失败 {e}')
    conn.close()

print()
print('=== 2026 库中 2021-2025 混入量 ===')
conn = sqlite3.connect(r'D:\finance\data\tq_daily_2026.db')
cur = conn.cursor()
for y in ['2021', '2022', '2023', '2024', '2025']:
    cur.execute(f"SELECT COUNT(*) FROM stock_daily WHERE trade_date LIKE '{y}%'")
    print(f'{y}年: {cur.fetchone()[0]} 条')
cur.execute("SELECT COUNT(*) FROM stock_daily WHERE trade_date LIKE '2026%'")
print(f'2026年: {cur.fetchone()[0]} 条')
conn.close()

print()
print('=== 分钟线库现状 ===')
conn = sqlite3.connect(r'D:\finance\data\tq_minline_2026.db')
cur = conn.cursor()
cur.execute('SELECT period, COUNT(*), COUNT(DISTINCT ts_code) FROM stock_minline GROUP BY period')
for r in cur.fetchall():
    print(f'period={r[0]}: {r[1]} 条, {r[2]} 代码')
cur.execute('SELECT MIN(trade_datetime), MAX(trade_datetime) FROM stock_minline')
print('日期范围:', cur.fetchone())
cur.execute('SELECT source, COUNT(*) FROM stock_minline GROUP BY source')
print('source:', dict(cur.fetchall()))
conn.close()

print()
print('=== vipdoc .day 起始日期 ===')
for f in [r'D:\new_tdx64\vipdoc\sh\lday\sh600367.day', r'D:\new_tdx64\vipdoc\sh\lday\sh000001.day', r'D:\new_tdx64\vipdoc\sz\lday\sz000001.day']:
    if os.path.exists(f):
        with open(f, 'rb') as fp:
            rec = fp.read(32)
        d = struct.unpack('<I', rec[:4])[0]
        fp2 = open(f, 'rb')
        fp2.seek(os.path.getsize(f) - 32)
        rec2 = fp2.read(32)
        d2 = struct.unpack('<I', rec2[:4])[0]
        n = os.path.getsize(f) // 32
        fp2.close()
        print(f'{os.path.basename(f)}: {n} 条 [{d}~{d2}]')
