#!/usr/bin/env python3
"""Import local data to MCP databases via db_execute tool."""

import json, sqlite3, os, sys, time

def call_tool(name, args):
    """Call this via QwenPaw finmcp__* tools - this script generates SQL."""
    pass

BASE = "/app/working/workspaces/data_in/本地历史数据"

def gen_sector_heat_sql():
    """Generate INSERT SQL for sector_heat data."""
    lines = []
    db_files = sorted([f for f in os.listdir(BASE) if f.startswith("sector_heat_") and f.endswith(".db")])
    for db_name in db_files:
        db_path = os.path.join(BASE, db_name)
        conn = sqlite3.connect(db_path)
        rows = conn.execute("SELECT date, group_id, group_name, heat, score, calc_params FROM heat").fetchall()
        for r in rows:
            date, gid, gname, heat, score, params = r
            if gname:
                gname = gname.replace("'", "''")
            p = (params or "").replace("'", "''")
            h = "NULL" if heat is None else str(heat)
            s = "NULL" if score is None else str(score)
            lines.append(f"INSERT OR IGNORE INTO sector_heat VALUES ('{date}',{gid},'{gname}',{h},{s},'{p}',datetime('now'));")
        conn.close()
        print(f"  {db_name}: {len(rows)} rows", file=sys.stderr)
    return lines

def gen_etf_info_sql():
    """Generate INSERT SQL for etf_info."""
    conn = sqlite3.connect(os.path.join(BASE, "etf.db"))
    rows = conn.execute("SELECT * FROM etf_info").fetchall()
    cols = [d[0] for d in conn.execute("PRAGMA table_info(etf_info)").fetchall()]
    conn.close()
    
    lines = []
    for r in rows:
        vals = []
        for i, v in enumerate(r):
            if v is None:
                vals.append("NULL")
            elif isinstance(v, (int, float)):
                vals.append(str(v))
            else:
                vals.append(f"'{str(v).replace(chr(39), chr(39)+chr(39))}'")
        cols_str = ",".join(cols)
        vals_str = ",".join(vals)
        lines.append(f"INSERT OR IGNORE INTO etf_info ({cols_str}) VALUES ({vals_str});")
    print(f"  etf_info: {len(rows)} rows", file=sys.stderr)
    return lines

def gen_etf_daily_sql():
    conn = sqlite3.connect(os.path.join(BASE, "etf.db"))
    rows = conn.execute("SELECT symbol,trade_date,open,high,low,close,volume,amount FROM etf_daily").fetchall()
    conn.close()
    lines = []
    for r in rows:
        vals = [f"'{r[0]}'", f"'{r[1]}'"]
        for v in r[2:]:
            vals.append("NULL" if v is None else str(v))
        lines.append(f"INSERT OR IGNORE INTO etf_daily VALUES ({','.join(vals)});")
    print(f"  etf_daily: {len(rows)} rows", file=sys.stderr)
    return lines

def gen_sector_momentum_sql():
    conn = sqlite3.connect(os.path.join(BASE, "etf.db"))
    rows = conn.execute("SELECT sector_code,sector_name,trade_date,daily_return,momentum_5d,momentum_10d,momentum_20d,momentum_60d,volume_ma5 FROM sector_momentum").fetchall()
    conn.close()
    lines = []
    for r in rows:
        vals = [f"'{r[0]}'", f"'{r[1]}'", f"'{r[2]}'"]
        for v in r[3:]:
            vals.append("NULL" if v is None else str(v))
        lines.append(f"INSERT OR IGNORE INTO sector_momentum VALUES ({','.join(vals)});")
    print(f"  sector_momentum: {len(rows)} rows", file=sys.stderr)
    return lines

if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "all"
    
    if mode in ("all", "sector_heat"):
        print("Generating sector_heat SQL...", file=sys.stderr)
        sql = gen_sector_heat_sql()
        with open(os.path.join(BASE, "_import_sector_heat.sql"), "w") as f:
            f.write("BEGIN TRANSACTION;\n")
            f.write("\n".join(sql))
            f.write("\nCOMMIT;\n")
        print(f"  -> {len(sql)} statements", file=sys.stderr)
    
    if mode in ("all", "etf"):
        print("Generating ETF SQL...", file=sys.stderr)
        for name, gen in [("etf_info", gen_etf_info_sql), 
                           ("etf_daily", gen_etf_daily_sql),
                           ("sector_momentum", gen_sector_momentum_sql)]:
            sql = gen()
            with open(os.path.join(BASE, f"_import_{name}.sql"), "w") as f:
                f.write("\n".join(sql))
            print(f"  {name}: {len(sql)} statements", file=sys.stderr)
    
    print("Done!", file=sys.stderr)
