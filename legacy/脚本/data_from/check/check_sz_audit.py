# -*- coding: utf-8 -*-
"""只读核查：深市(.SZ)数据覆盖情况 — 分步1/2，不落库"""
import sqlite3, os, glob

DB26 = r"D:\finance\data\tq_daily_2026.db"
DB25 = r"D:\finance\data\tq_daily_2025.db"
LDIR = r"D:\new_tdx64\vipdoc\sz\lday"

def suffix_dist(conn, table):
    cur = conn.execute(f"SELECT COUNT(*) FROM {table}")
    total = cur.fetchone()[0]
    cur = conn.execute(
        f"SELECT substr(ts_code, -3) AS sfx, COUNT(*) AS c FROM {table} GROUP BY sfx ORDER BY c DESC"
    )
    rows = cur.fetchall()
    return total, rows

def sample_codes(conn, table, sfx, n=3):
    cur = conn.execute(
        f"SELECT ts_code, COUNT(*) AS c FROM {table} WHERE substr(ts_code, -3)=? GROUP BY ts_code LIMIT ?",
        (sfx, n),
    )
    return cur.fetchall()

print("=" * 60)
print("【1】tq_daily_2026.db / stock_daily 后缀分布")
conn26 = sqlite3.connect(DB26)
tables26 = [r[0] for r in conn26.execute("SELECT name FROM sqlite_master WHERE type='table'")]
print("表清单:", tables26)
if "stock_daily" in tables26:
    total, rows = suffix_dist(conn26, "stock_daily")
    print(f"总行数: {total}")
    for sfx, c in rows:
        print(f"  后缀 {sfx!r}: {c} 行")
        if sfx.startswith("."):
            print(f"    抽样: {sample_codes(conn26, 'stock_daily', sfx)}")
    oth = conn26.execute(
        "SELECT substr(ts_code,-3) sfx, COUNT(*) c FROM stock_daily WHERE substr(ts_code,-3) NOT IN ('.SH','.SZ') GROUP BY sfx"
    ).fetchall()
    print("非 .SH/.SZ 后缀明细:", oth)
else:
    print("无 stock_daily 表")

print("=" * 60)
print("【2】tq_daily_2026.db / index_daily 是否含 .SZ 指数")
if "index_daily" in tables26:
    cur = conn26.execute(
        "SELECT substr(ts_code,-3) sfx, COUNT(*) c FROM index_daily GROUP BY sfx ORDER BY c DESC"
    )
    for sfx, c in cur.fetchall():
        print(f"  后缀 {sfx!r}: {c} 行")
    sz_idx = conn26.execute(
        "SELECT ts_code, COUNT(*) c FROM index_daily WHERE substr(ts_code,-3)='.SZ' GROUP BY ts_code LIMIT 5"
    ).fetchall()
    print("index_daily 中 .SZ 指数样本:", sz_idx if sz_idx else "【无 .SZ 指数】")
    sh_idx = conn26.execute(
        "SELECT ts_code, COUNT(*) c FROM index_daily WHERE substr(ts_code,-3)='.SH' GROUP BY ts_code LIMIT 5"
    ).fetchall()
    print("index_daily 中 .SH 指数样本:", sh_idx)
else:
    print("无 index_daily 表")
conn26.close()

print("=" * 60)
print("【3】D:\\new_tdx64\\vipdoc\\sz\\lday\\ .day 文件数量")
days = glob.glob(os.path.join(LDIR, "*.day"))
print(f"目录存在: {os.path.isdir(LDIR)}; .day 文件数: {len(days)}")
print("样本:", sorted(os.path.basename(d) for d in days)[:5], "...")
sz6 = [os.path.basename(d) for d in days if os.path.basename(d).startswith("sz")]
print(f"以 sz 开头文件数: {len(sz6)}; 样本: {sz6[:5]}")

print("=" * 60)
print("【4】tq_daily_2025.db / stock_daily 是否也缺 .SZ")
conn25 = sqlite3.connect(DB25)
tables25 = [r[0] for r in conn25.execute("SELECT name FROM sqlite_master WHERE type='table'")]
print("表清单:", tables25)
if "stock_daily" in tables25:
    total, rows = suffix_dist(conn25, "stock_daily")
    print(f"总行数: {total}")
    for sfx, c in rows:
        print(f"  后缀 {sfx!r}: {c} 行")
        if sfx.startswith("."):
            print(f"    抽样: {sample_codes(conn25, 'stock_daily', sfx)}")
    sz_codes = conn25.execute(
        "SELECT COUNT(DISTINCT ts_code) FROM stock_daily WHERE substr(ts_code,-3)='.SZ'"
    ).fetchone()[0]
    sh_codes = conn25.execute(
        "SELECT COUNT(DISTINCT ts_code) FROM stock_daily WHERE substr(ts_code,-3)='.SH'"
    ).fetchone()[0]
    print(f"去重后: .SH 股票数={sh_codes}, .SZ 股票数={sz_codes}")
else:
    print("无 stock_daily 表")
conn25.close()
print("=" * 60)
print("核查完成（全程只读，未改任何数据）")
