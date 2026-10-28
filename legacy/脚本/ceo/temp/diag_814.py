# -*- coding: utf-8 -*-
"""只读诊断：8-14 数据同步状态 + 板块热度断供范围"""
import sqlite3

# 1. daily_kline 是否已有 08-14
con = sqlite3.connect(r'D:\finance\data\fin_data_2026.db')
cur = con.cursor()
cur.execute("SELECT trade_date, COUNT(*) FROM daily_kline GROUP BY trade_date ORDER BY trade_date DESC LIMIT 6")
print('=== daily_kline 最近交易日 ===')
for r in cur.fetchall():
    print(' ', r)
con.close()

# 2. sector_heat 断供范围
con = sqlite3.connect(r'D:\finance\data\fin_data.db')
cur = con.cursor()
cur.execute("SELECT MAX(date), MIN(date), COUNT(*) FROM sector_heat")
print('\n=== sector_heat ===')
print('最新/最早/总数:', cur.fetchone())
cur.execute("SELECT date, COUNT(*) FROM sector_heat GROUP BY date ORDER BY date DESC LIMIT 8")
print('最近日期分布:')
for r in cur.fetchall():
    print(' ', r)
con.close()

# 3. 远程 API 健康检查
import requests
try:
    r = requests.get('http://192.168.176.68:8765/api/sql/health', timeout=10)
    print('\n=== 远程 API health ===')
    print(r.status_code, r.text[:300])
except Exception as e:
    print('\n远程 API 不可达:', e)

# 4. exec 开关状态
try:
    r = requests.get('http://192.168.176.68:8765/api/exec/enabled', timeout=10)
    print('\n=== exec enabled ===')
    print(r.status_code, r.text[:300])
except Exception as e:
    print('exec 检查失败:', e)

# 5. 任务列表
try:
    r = requests.get('http://192.168.176.68:8765/api/task/list?limit=10', timeout=10)
    print('\n=== 最近任务 ===')
    print(r.status_code, r.text[:800])
except Exception as e:
    print('task list 失败:', e)
