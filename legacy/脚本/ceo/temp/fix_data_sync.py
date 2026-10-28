#!/usr/bin/env python3
"""Sync 20260701 data from a_stock_data.db to quant_views.db"""
import sqlite3
from datetime import datetime

data_dir = '/app/working/workspaces/data_in/data'

src = sqlite3.connect(f'{data_dir}/a_stock_data.db')
dst = sqlite3.connect(f'{data_dir}/quant_views.db')

# 1. Get column info properly
src_cols_info = src.execute("PRAGMA table_info(daily_kline)").fetchall()
dst_cols_info = dst.execute("PRAGMA table_info(daily_kline_all)").fetchall()

src_col_names = [c[1] for c in src_cols_info]
dst_col_names = [c[1] for c in dst_cols_info]

print(f"[{datetime.now().isoformat()}] Source columns: {src_col_names}")
print(f"[{datetime.now().isoformat()}] Dest columns: {dst_col_names}")

common_cols = [c for c in src_col_names if c in dst_col_names]
print(f"[{datetime.now().isoformat()}] Common columns: {common_cols}")

# 2. Get data
rows = src.execute("SELECT * FROM daily_kline WHERE trade_date='20260701'").fetchall()
print(f"[{datetime.now().isoformat()}] Found {len(rows)} kline rows for 20260701")

if rows:
    existing = dst.execute("SELECT COUNT(*) FROM daily_kline_all WHERE trade_date='20260701'").fetchone()[0]
    print(f"  Already in quant_views: {existing} rows")
    
    if existing == 0:
        col_list = ', '.join(common_cols)
        placeholders = ', '.join(['?'] * len(common_cols))
        insert_sql = f"INSERT OR IGNORE INTO daily_kline_all ({col_list}) VALUES ({placeholders})"
        print(f"  SQL: {insert_sql}")
        
        src_indices = {c: i for i, c in enumerate(src_col_names)}
        indices = [src_indices[c] for c in common_cols]
        batch = [tuple(r[i] for i in indices) for r in rows]
        
        dst.executemany(insert_sql, batch)
        dst.commit()
        
        new_cnt = dst.execute("SELECT COUNT(*) FROM daily_kline_all WHERE trade_date='20260701'").fetchone()[0]
        print(f"  After sync: {new_cnt} rows in daily_kline_all")
    else:
        print(f"  Data already exists, no need to sync")

# 3. Verify
maxd = dst.execute("SELECT MAX(trade_date) FROM daily_kline_all").fetchone()[0]
print(f"\n[{datetime.now().isoformat()}] ✅ quant_views.daily_kline_all latest: {maxd}")
cnt = dst.execute("SELECT COUNT(*) FROM daily_kline_all WHERE trade_date='20260701'").fetchone()[0]
print(f"  20260701 rows: {cnt}")

# 4. Also check stock names - do we have a name source?
try:
    basics = sqlite3.connect(f'{data_dir}/stock_basics.db')
    tbls = basics.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    print(f"\nstock_basics tables: {[t[0] for t in tbls]}")
    for t in tbls:
        cols = basics.execute(f"PRAGMA table_info({t[0]})").fetchall()
        print(f"  {t[0]}: {[c[1] for c in cols]}")
        # Check if has name and ts_code
        if 'name' in [c[1] for c in cols] and 'ts_code' in [c[1] for c in cols]:
            sample = basics.execute(f"SELECT ts_code, name FROM {t[0]} LIMIT 5").fetchall()
            print(f"    sample: {sample}")
    basics.close()
except Exception as e:
    print(f"\nstock_basics error: {e}")

src.close()
dst.close()
