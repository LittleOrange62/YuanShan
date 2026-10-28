#!/usr/bin/env python3
"""Generate SQL import scripts for MCP - using actual etf.db schema."""
import sqlite3, os, sys

BASE = "/app/working/workspaces/data_in/本地历史数据"

def get_tables(conn):
    return [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]

# 1. Sector heat (17,592 rows)
for db_name in sorted([f for f in os.listdir(BASE) if f.startswith("sector_heat_") and f.endswith(".db")]):
    year = db_name.split("_")[2].split(".")[0]
    db_path = os.path.join(BASE, db_name)
    conn = sqlite3.connect(db_path)
    tables = get_tables(conn)
    if 'heat' not in tables:
        conn.close()
        continue
    rows = conn.execute("SELECT date, group_id, group_name, heat, score, calc_params FROM heat").fetchall()
    conn.close()
    out = open(os.path.join(BASE, f"_import_heat_{year}.sql"), "w", encoding="utf-8")
    for r in rows:
        date, gid, gname, heat, score, params = r
        gname = (gname or "").replace("'", "''")
        p = (params or "").replace("'", "''")
        h = "NULL" if heat is None else str(heat)
        s = "NULL" if score is None else str(score)
        out.write(f"INSERT OR IGNORE INTO sector_heat VALUES ('{date}',{gid},'{gname}',{h},{s},'{p}',datetime('now'));\n")
    out.close()
    print(f"sector_heat_{year}: {len(rows)} rows")

# 2. ETF - use actual column names
conn = sqlite3.connect(os.path.join(BASE, "etf.db"))

# 2a. etf_info
rows = conn.execute("SELECT etf_code, etf_name, sector, fund_scale, list_date, update_date FROM etf_info").fetchall()
out = open(os.path.join(BASE, "_import_etf_info.sql"), "w", encoding="utf-8")
for r in rows:
    code, name, sector, scale, list_date, update_date = r
    code = (code or "").replace("'", "''")
    name = (name or "").replace("'", "''")
    sector = (sector or "").replace("'", "''")
    list_d = (list_date or "").replace("'", "''")
    update_d = (update_date or "").replace("'", "''")
    s = "NULL" if scale is None else str(scale)
    out.write(f"INSERT OR IGNORE INTO etf_info VALUES ('{code}','{name}','{sector}',{s},'{list_d}','{update_d}');\n")
out.close()
print(f"etf_info: {len(rows)} rows")

# 2b. etf_daily (99K rows)
rows = conn.execute("SELECT etf_code,trade_date,change_pct,amount,etf_name,open,high,low,close,volume FROM etf_daily").fetchall()
out = open(os.path.join(BASE, "_import_etf_daily.sql"), "w", encoding="utf-8")
batch_size = 500
for i in range(0, len(rows), batch_size):
    batch = rows[i:i+batch_size]
    vals_list = []
    for r in batch:
        vs = [f"'{r[0]}'", f"'{r[1]}'"]
        for v in r[2:]:
            vs.append("NULL" if v is None else str(v))
        vals_list.append(f"({','.join(vs)})")
    out.write(f"INSERT OR IGNORE INTO etf_daily (etf_code,trade_date,change_pct,amount,etf_name,open,high,low,close,volume) VALUES {','.join(vals_list)};\n")
out.close()
print(f"etf_daily: {len(rows)} rows")

# 2c. sector_momentum
rows = conn.execute("SELECT trade_date,sector_name,weighted_return,return_5d,return_20d,return_60d,total_amount,etf_count FROM sector_momentum").fetchall()
out = open(os.path.join(BASE, "_import_sector_momentum.sql"), "w", encoding="utf-8")
for i in range(0, len(rows), batch_size):
    batch = rows[i:i+batch_size]
    vals_list = []
    for r in batch:
        vs = [f"'{r[0]}'", f"'{r[1]}'"]
        for v in r[2:]:
            vs.append("NULL" if v is None else str(v))
        vals_list.append(f"({','.join(vs)})")
    out.write(f"INSERT OR IGNORE INTO sector_momentum (trade_date,sector_name,weighted_return,return_5d,return_20d,return_60d,total_amount,etf_count) VALUES {','.join(vals_list)};\n")
out.close()
conn.close()
print(f"sector_momentum: {len(rows)} rows")

print("\nDone!")
