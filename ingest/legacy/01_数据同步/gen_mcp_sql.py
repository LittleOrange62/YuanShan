#!/usr/bin/env python3
"""Generate SQL files adapted to MCP table structures."""
import sqlite3, os, json

BASE = "/app/working/workspaces/data_in/本地历史数据"
os.chdir(BASE)

conn = sqlite3.connect("etf.db")

# === 1. etf_info (11 cols) ===
# MCP: symbol PK, name, fund_scale, management_fee, custodian_fee, tracking_index,
#      fund_establishment_date, fund_manager, stock_selection, category, current_amount
# Local: etf_code, etf_name, sector, fund_scale, list_date, update_date
rows = conn.execute("SELECT etf_code, etf_name, sector, fund_scale, list_date, update_date FROM etf_info").fetchall()
out = open("_import_etf_info_mcp.sql", "w", encoding="utf-8")
batch_size = 200
for i in range(0, len(rows), batch_size):
    batch = rows[i:i+batch_size]
    vals = []
    for r in batch:
        code = (r[0] or "").replace("'", "''")
        name = (r[1] or "").replace("'", "''")
        sector = (r[2] or "").replace("'", "''")
        scale = "NULL" if r[3] is None else str(r[3])
        list_date = (r[4] or "").replace("'", "''")
        # Map: symbol=code, name=etf_name, fund_scale=scale, category=sector, others NULL
        vals.append(f"('{code}','{name}',{scale},NULL,NULL,NULL,'{list_date}',NULL,NULL,'{sector}',NULL)")
    out.write(f"INSERT OR IGNORE INTO etf_info (symbol,name,fund_scale,management_fee,custodian_fee,tracking_index,fund_establishment_date,fund_manager,stock_selection,category,current_amount) VALUES {','.join(vals)};\n")
out.close()
print(f"etf_info: {len(rows)} rows")

# === 2. etf_daily (8 cols) ===
# MCP: symbol PK, trade_date PK, open, high, low, close, volume, amount
# Local: etf_code, trade_date, change_pct, amount, etf_name, open, high, low, close, volume
rows = conn.execute("SELECT etf_code,trade_date,open,high,low,close,volume,amount FROM etf_daily").fetchall()
out = open("_import_etf_daily_mcp.sql", "w", encoding="utf-8")
for i in range(0, len(rows), batch_size):
    batch = rows[i:i+batch_size]
    vals = []
    for r in batch:
        code = (r[0] or "").replace("'", "''")
        vals.append(f"('{code}','{r[1]}',{r[2] or 'NULL'},{r[3] or 'NULL'},{r[4] or 'NULL'},{r[5] or 'NULL'},{r[6] or 'NULL'},{r[7] or 'NULL'})")
    out.write(f"INSERT OR IGNORE INTO etf_daily (symbol,trade_date,open,high,low,close,volume,amount) VALUES {','.join(vals)};\n")
out.close()
print(f"etf_daily: {len(rows)} rows")

# === 3. sector_momentum (9 cols) ===
# MCP: sector_code, sector_name, trade_date, daily_return, momentum_5d, momentum_10d, momentum_20d, momentum_60d, volume_ma5
# Local: trade_date, sector_name, weighted_return, return_5d, return_20d, return_60d, total_amount, etf_count
rows = conn.execute("SELECT trade_date,sector_name,weighted_return,return_5d,return_20d,return_60d,total_amount,etf_count FROM sector_momentum").fetchall()
out = open("_import_sector_momentum_mcp.sql", "w", encoding="utf-8")
for i in range(0, len(rows), batch_size):
    batch = rows[i:i+batch_size]
    vals = []
    for r in batch:
        sname = (r[1] or "").replace("'", "''")
        vals.append(f"('{sname}','{sname}','{r[0]}',{r[2] or 'NULL'},{r[3] or 'NULL'},NULL,{r[4] or 'NULL'},{r[5] or 'NULL'},NULL)")
    out.write(f"INSERT OR IGNORE INTO sector_momentum (sector_code,sector_name,trade_date,daily_return,momentum_5d,momentum_10d,momentum_20d,momentum_60d,volume_ma5) VALUES {','.join(vals)};\n")
out.close()
print(f"sector_momentum: {len(rows)} rows")

conn.close()
print("\nDone!")
