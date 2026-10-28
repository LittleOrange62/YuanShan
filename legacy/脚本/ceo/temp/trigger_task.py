# -*- coding: utf-8 -*-
"""触发远程任务：sync_data + run_factor"""
import requests, json, sys

BASE = 'http://192.168.176.68:8765'
H = {'X-API-Key': 'fin', 'Content-Type': 'application/json'}

action = sys.argv[1] if len(sys.argv) > 1 else 'sync'

if action == 'sync':
    r = requests.post(f'{BASE}/api/task/sync_data', json={'sync_type': 'incremental'}, headers=H, timeout=15)
    print('sync_data:', r.status_code, r.text[:600])
elif action == 'factor':
    plan_id = int(sys.argv[2]) if len(sys.argv) > 2 else 1
    days = int(sys.argv[3]) if len(sys.argv) > 3 else 10
    r = requests.post(f'{BASE}/api/task/run_factor', json={'plan_id': plan_id, 'backfill_days': days}, headers=H, timeout=15)
    print(f'run_factor plan={plan_id} days={days}:', r.status_code, r.text[:600])
elif action == 'list':
    r = requests.get(f'{BASE}/api/task/list?limit=5', headers=H, timeout=15)
    print('task list:', r.status_code, r.text[:1500])
