# -*- coding: utf-8 -*-
import sqlite3, collections

DB_DAILY = r"D:\finance\data\tq_daily_2026.db"
DB_CORE = r"D:\finance\data\tq_core.db"

c = sqlite3.connect(DB_DAILY)
c.execute("ATTACH DATABASE ? AS core", (DB_CORE,))

# 每个 asset_type 的 ts_code 形态：前缀3位 + 后缀
for at in ["stock", "index", "sector", "fund", "bond"]:
    rows = c.execute("""
        SELECT DISTINCT s.ts_code FROM stock_daily s
        JOIN core.security_info i ON i.ts_code = s.ts_code
        WHERE i.asset_type = ?
    """, (at,)).fetchall()
    codes = [r[0] for r in rows]
    # 前缀（前6位数字）和后缀分布
    pre = collections.Counter(cd[:3] for cd in codes)
    suf = collections.Counter(cd.split(".")[-1] for cd in codes)
    print(f"== {at}: {len(codes)} codes")
    print("   prefix3:", dict(sorted(pre.items())))
    print("   suffix :", dict(suf))
    print("   sample :", sorted(codes)[:12])
