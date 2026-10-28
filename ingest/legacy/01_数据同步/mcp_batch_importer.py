#!/usr/bin/env python3
"""Batch import SQL data into MCP via db_execute tool calls.
This script is invoked by data_in and reads pre-generated SQL files.

Strategy: Use multi-row INSERT (500 values per statement) to minimize round trips.
"""
import json, sys, os, re, math

BASE = "/app/working/workspaces/data_in/本地历史数据"
# This script just prints SQL statements in the format expected by the calling agent

def batch_values(sql_file, table, columns, batch_size=500):
    """Read a file of single-row INSERTs and group into multi-row INSERTs."""
    with open(sql_file, 'r', encoding='utf-8') as f:
        lines = f.readlines()
    
    # Parse INSERT OR IGNORE INTO tablename VALUES (val1,val2,...);
    values = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        # Extract the parenthesized values
        start = line.find('VALUES (')
        if start < 0:
            continue
        vals_part = line[start+7:]  # after 'VALUES ('
        # Match parenthesis
        depth = 0
        paren_start = start + 7
        paren_end = None
        for i in range(paren_start, len(line)):
            if line[i] == '(':
                depth += 1
            elif line[i] == ')':
                depth -= 1
                if depth == 0:
                    paren_end = i
                    break
        if paren_end:
            val_str = line[paren_start+1:paren_end]
            values.append(val_str)
    
    total = len(values)
    batches = []
    for i in range(0, total, batch_size):
        batch_vals = values[i:i+batch_size]
        rows_str = ','.join([f'({v})' for v in batch_vals])
        stmt = f'INSERT OR IGNORE INTO {table} VALUES {rows_str};'
        batches.append(stmt)
    
    print(f"{sql_file}: {total} rows -> {len(batches)} batches", file=sys.stderr)
    return batches, total

all_batches = []

# 1. Sector heat (4 years combined)
for year in ['2023', '2024', '2025', '2026']:
    f = os.path.join(BASE, f'_import_heat_{year}.sql')
    if os.path.exists(f):
        batches, total = batch_values(f, 'sector_heat', '', 500)
        all_batches.extend(batches)
        print(f"Heat {year}: {total} rows -> {len(batches)} statements", file=sys.stderr)

# 2. ETF info
f = os.path.join(BASE, '_import_etf_info.sql')
if os.path.exists(f):
    batches, total = batch_values(f, 'etf_info', '', 500)
    all_batches.extend(batches)
    print(f"ETF info: {total} rows -> {len(batches)} statements", file=sys.stderr)

# 3. ETF daily
f = os.path.join(BASE, '_import_etf_daily.sql')
if os.path.exists(f):
    batches, total = batch_values(f, 'etf_daily', '', 500)
    all_batches.extend(batches)
    print(f"ETF daily: {total} rows -> {len(batches)} statements", file=sys.stderr)

# 4. Sector momentum
f = os.path.join(BASE, '_import_sector_momentum.sql')
if os.path.exists(f):
    batches, total = batch_values(f, 'sector_momentum', '', 500)
    all_batches.extend(batches)
    print(f"Sector momentum: {total} rows -> {len(batches)} statements", file=sys.stderr)

# Print all statements as JSON for the calling agent
result = []
for i, stmt in enumerate(all_batches):
    result.append(stmt)

print(f"\nTOTAL: {len(result)} SQL statements to execute", file=sys.stderr)

# Save to file for the calling agent
out = os.path.join(BASE, '_mcp_batch_import.json')
with open(out, 'w', encoding='utf-8') as f:
    json.dump(result, f, ensure_ascii=False)
print(f"Saved to {out}", file=sys.stderr)
print(f"Total: {len(result)} statements", file=sys.stderr)
