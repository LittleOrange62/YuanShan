#!/usr/bin/env python3
"""Analyze group stocks coverage"""
import sqlite3

# Connect to quant_views via boss_groups
conn = sqlite3.connect('/app/working/workspaces/data_in/data/quant_views.db')
cur = conn.cursor()

# Get all group stocks
gconn = sqlite3.connect('/app/working/workspaces/data_in/data/boss_groups.db')
gcur = gconn.cursor()
gcur.execute("SELECT DISTINCT ts_code FROM boss_group_stocks")
all_groups = [row[0] for row in gcur.fetchall()]
gconn.close()

print(f"Total unique group stocks: {len(all_groups)}")

# Check coverage in daily_kline_all
cur.execute("SELECT DISTINCT k.ts_code FROM daily_kline_all k WHERE k.trade_date='20260629'")
in_kline = set(row[0] for row in cur.fetchall())

covered = [s for s in all_groups if s in in_kline]
missing = [s for s in all_groups if s not in in_kline]
print(f"Covered in kline: {len(covered)}")
print(f"Missing from kline: {len(missing)}")
if missing:
    for s in missing[:20]:
        print(f"  {s}")

# Check watchlist new stocks
new_watchlist = ['001208.SZ', '000600.SZ', '920808.BJ', '688820.SH', '688008.SH', 
                 '603893.SH', '000988.SZ', '300666.SZ', '002409.SZ', '000021.SZ', '002138.SZ']
print("\n--- New watchlist coverage ---")
for s in new_watchlist:
    in_k = s in in_kline
    in_g = s in all_groups
    print(f"{s}: in_kline={in_k}, in_groups={in_g}")

conn.close()
