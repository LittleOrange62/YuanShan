#!/usr/bin/env python3
"""Sync pct_chg -> pct_change for 20260701 data"""
import sqlite3

data_dir = '/app/working/workspaces/data_in/data'
src = sqlite3.connect(f'{data_dir}/a_stock_data.db')
dst = sqlite3.connect(f'{data_dir}/quant_views.db')

# Sync pct_chg -> pct_change
rows = src.execute("SELECT ts_code, trade_date, pct_chg FROM daily_kline WHERE trade_date='20260701'").fetchall()
print(f"Found {len(rows)} rows with pct_chg data")

updated = 0
for ts_code, trade_date, pct_chg in rows:
    dst.execute("UPDATE daily_kline_all SET pct_change=? WHERE ts_code=? AND trade_date=?",
                (pct_chg, ts_code, trade_date))
    updated += 1
dst.commit()
print(f"Updated {updated} rows with pct_change")

# Verify
cnt = dst.execute("SELECT COUNT(*) FROM daily_kline_all WHERE trade_date='20260701' AND pct_change IS NOT NULL").fetchone()[0]
print(f"20260701 rows with pct_change: {cnt}")

# Also check what other columns might be missing
dst_cols = [c[1] for c in dst.execute("PRAGMA table_info(daily_kline_all)").fetchall()]
src_cols = [c[1] for c in src.execute("PRAGMA table_info(daily_kline)").fetchall()]
print(f"\nSource columns: {src_cols}")
print(f"Dest columns: {dst_cols}")
missing_in_src = [c for c in dst_cols if c not in src_cols and c not in ['ts_code', 'trade_date']]
print(f"Columns in dest but not in source (default NULL): {missing_in_src}")

src.close()
dst.close()
print("\n✅ pct_change sync complete")
