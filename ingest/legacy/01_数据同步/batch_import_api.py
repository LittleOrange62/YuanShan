#!/usr/bin/env python3
"""Batch import data to MCP via SQL HTTP API."""
import requests, json, os, sys

api = 'http://192.168.176.68:8765/api/sql'
headers = {'X-API-Key': 'fin', 'Content-Type': 'application/json'}
BASE = os.path.join(os.path.dirname(os.path.abspath(__file__)), '本地历史数据')

def send(sql, label=''):
    try:
        r = requests.post(f'{api}/execute', json={'db': 'fin_data.db', 'sql': sql}, headers=headers, timeout=120)
        j = r.json()
        if j.get('success'):
            return j['affected_rows']
        else:
            print(f'  FAIL [{label}]: {j}', file=sys.stderr)
            return 0
    except Exception as e:
        print(f'  ERROR [{label}]: {e}', file=sys.stderr)
        return 0

# 1. SECTOR HEAT (17592 rows)
print('Importing sector_heat...', flush=True)
for year in ['2023','2024','2025','2026']:
    f = os.path.join(BASE, f'_import_heat_{year}_small.sql')
    with open(f, 'r') as fh:
        lines = fh.readlines()
    ok = 0
    for i, line in enumerate(lines):
        line = line.strip()
        if not line:
            continue
        rows = send(line, f'heat_{year}_{i}')
        if rows:
            ok += 1
    print(f'  {year}: {ok}/{len(lines)} statements OK', flush=True)

# 2. ETF INFO
print('Importing etf_info...', flush=True)
f = os.path.join(BASE, '_import_etf_info_small.sql')
with open(f, 'r') as fh:
    sql = fh.read().strip()
if sql:
    rows = send(sql, 'etf_info')
    print(f'  {rows} rows', flush=True)

# 3. ETF DAILY (99295 rows)
print('Importing etf_daily (99295 rows)...', flush=True)
f = os.path.join(BASE, '_import_etf_daily_small.sql')
with open(f, 'r') as fh:
    lines = fh.readlines()
ok = 0
for i, line in enumerate(lines):
    line = line.strip()
    if not line:
        continue
    rows = send(line, f'daily_{i}')
    if rows:
        ok += 1
    if (i+1) % 200 == 0:
        print(f'  progress: {i+1}/{len(lines)}', flush=True)
print(f'  {ok}/{len(lines)} statements OK', flush=True)

# 4. SECTOR MOMENTUM (42159 rows)
print('Importing sector_momentum (42159 rows)...', flush=True)
f = os.path.join(BASE, '_import_sector_momentum_small.sql')
with open(f, 'r') as fh:
    lines = fh.readlines()
ok = 0
for i, line in enumerate(lines):
    line = line.strip()
    if not line:
        continue
    rows = send(line, f'mom_{i}')
    if rows:
        ok += 1
    if (i+1) % 100 == 0:
        print(f'  progress: {i+1}/{len(lines)}', flush=True)
print(f'  {ok}/{len(lines)} statements OK', flush=True)

print('\nALL DONE!', flush=True)
