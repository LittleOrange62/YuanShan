# -*- coding: utf-8 -*-
"""轮询任务状态"""
import requests, json, sys, time

BASE = 'http://192.168.176.68:8765'
H = {'X-API-Key': 'fin'}
tid = sys.argv[1]
rounds = int(sys.argv[2]) if len(sys.argv) > 2 else 6

for i in range(rounds):
    r = requests.get(f'{BASE}/api/task/status?task_id={tid}', headers=H, timeout=15)
    j = r.json()
    print(i, j.get('status'), j.get('progress'), str(j.get('message', ''))[:150])
    if j.get('status') in ('done', 'failed', 'cancelled'):
        print('RESULT:', json.dumps(j.get('result'), ensure_ascii=False)[:800])
        break
    time.sleep(10)
