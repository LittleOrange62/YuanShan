# -*- coding: utf-8 -*-
"""只读诊断：带 API Key 查任务状态 + 数据同步情况"""
import requests, json

BASE = "http://192.168.176.68:8765"
H = {"X-API-Key": "fin", "Content-Type": "application/json"}

# 1. 最近任务列表
try:
    r = requests.get(f"{BASE}/api/task/list?limit=15", headers=H, timeout=10)
    print('=== 最近任务 ===')
    print(r.status_code)
    print(json.dumps(r.json(), ensure_ascii=False, indent=1)[:2000])
except Exception as e:
    print('task list 失败:', e)

# 2. databases 可读列表
try:
    r = requests.get(f"{BASE}/api/sql/databases", headers=H, timeout=10)
    print('\n=== databases ===')
    print(r.status_code, r.text[:400])
except Exception as e:
    print('databases 失败:', e)
