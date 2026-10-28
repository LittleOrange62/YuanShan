#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import sqlite3, os, sys, datetime, json

DATA_DIR = r'D:\finance\data'

print("=" * 60)
print("本地直操作模式端到端验证 - 连通性 + 数据完整性")
print("=" * 60)

# 1. fin_data.db stock_basic
try:
    conn = sqlite3.connect(os.path.join(DATA_DIR, 'fin_data.db'))
    r = conn.execute("SELECT COUNT(*) FROM stock_basic").fetchone()[0]
    print(f"[fin_data.db] stock_basic 行数: {r}")
    wl = conn.execute("SELECT COUNT(*) FROM watchlist_stocks").fetchone()[0]
    wl_groups = conn.execute("SELECT COUNT(*) FROM watchlist_groups").fetchone()[0]
    print(f"[fin_data.db] watchlist_stocks 行数: {wl}, watchlist_groups: {wl_groups}")
    print("[fin_data.db] 表清单:", end=" ")
    tbls = conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name").fetchall()
    print(", ".join(t[0] for t in tbls))
    conn.close()
    TEST1 = True
except Exception as e:
    print(f"[fin_data.db] ERROR: {e}")
    TEST1 = False

# 2. fin_watchlist.db
try:
    conn = sqlite3.connect(os.path.join(DATA_DIR, 'fin_watchlist.db'))
    tbls = conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name").fetchall()
    print(f"[fin_watchlist.db] 表: {', '.join(t[0] for t in tbls)}")
    try:
        wl = conn.execute("SELECT COUNT(*) FROM watchlist_stocks").fetchone()[0]
        wl_g = conn.execute("SELECT COUNT(*) FROM watchlist_groups").fetchone()[0]
        print(f"[fin_watchlist.db] watchlist_stocks: {wl}, watchlist_groups: {wl_g}")
    except:
        print("[fin_watchlist.db] watchlist 表不存在或不适用")
    conn.close()
    TEST2 = True
except Exception as e:
    print(f"[fin_watchlist.db] ERROR: {e}")
    TEST2 = False

# 3. fin_factor.db (评分/因子)
try:
    conn = sqlite3.connect(os.path.join(DATA_DIR, 'fin_factor.db'))
    tbls = conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name").fetchall()
    print(f"[fin_factor.db] 表: {', '.join(t[0] for t in tbls)}")
    conn.close()
    TEST3 = True
except Exception as e:
    print(f"[fin_factor.db] ERROR: {e}")
    TEST3 = False

print("\n验证项 | 结果 | 数据/证据")
print(f"fin_data 库连通性 | {'✅' if TEST1 else '❌'} | stock_basic {r} 行, watchlist_stocks {wl} 行")
print(f"fin_watchlist 库连通性 | {'✅' if TEST2 else '❌'} | 独立自选库可读")
print(f"fin_factor 库连通性 | {'✅' if TEST3 else '❌'} | 表清单可枚举")

print("\n=== 连通性验证完成 ===")
