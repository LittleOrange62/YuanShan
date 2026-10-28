# -*- coding: utf-8 -*-
"""
sync_daily_by_year.py — vipdoc .day 全量按年分库同步（口径统一）

背景:
  - tq_daily_2026.db 之前 sync-all 混入了 2021~2025 的记录(口径不一致)
  - 用户要求: 所有股票/指数/基金从 2021 下半年起都要有, 每年数据放对应年份库
  - vipdoc .day 文件统一从 20210802 开始 (2021下半年), 正好满足要求

做法:
  1. 遍历 vipdoc sh/sz lday 全部 .day 文件
  2. 每文件解析全部记录, 连续计算 pre_close/change/pct_change
  3. 按年份 INSERT OR REPLACE 到 tq_daily_YYYY.db (覆盖旧 tdx/tq 记录, 统一为 vipdoc)
  4. 2026 库先清理 trade_date < '20260101' 的混入记录
  5. 执行后各库均为 vipdoc 口径, 全代码覆盖(指数/基金/股票)

用法:
  python scripts/sync_daily_by_year.py                # 全量
  python scripts/sync_daily_by_year.py --limit 50     # 只处理前 50 个文件(测试)
"""
import os
import sqlite3
import struct
import sys
from datetime import datetime

TDXDIR = r'D:\new_tdx64'
DATA_DIR = r'D:\finance\data'
YEARS = ['2021', '2022', '2023', '2024', '2025', '2026']
DB_PATHS = {y: os.path.join(DATA_DIR, f'tq_daily_{y}.db') for y in YEARS}
NOW = datetime.now().strftime('%Y-%m-%d %H:%M:%S')


def read_day_file(filepath):
    """解析 .day: [(trade_date, open, high, low, close, amount, vol), ...]"""
    with open(filepath, 'rb') as f:
        data = f.read()
    n = len(data) // 32
    recs = []
    for i in range(n):
        rec = data[i*32:(i+1)*32]
        date_int, o, h, l, c, amt, vol, _ = struct.unpack('<IIIIIfII', rec)
        recs.append((str(date_int), o/100.0, h/100.0, l/100.0, c/100.0, float(amt), vol/100.0))
    return recs


def enrich(recs):
    """连续计算 pre_close/change/pct_change/limit_up/limit_down"""
    out = []
    prev_close = None
    for td, o, h, l, c, amt, vol in recs:
        if prev_close is None:
            chg = pct = lu = ld = None
        else:
            chg = round(c - prev_close, 2)
            pct = round((c - prev_close) / prev_close * 100, 2)
            # 简化: 主板 10%, 创业板/科创板 20% (可后续用 adj 修正)
            lu = round(prev_close * 1.1, 2)
            ld = round(prev_close * 0.9, 2)
        out.append((td, o, h, l, c, prev_close, chg, pct, vol, amt, lu, ld))
        prev_close = c
    return out


def market_of(symbol):
    return 'sh' if symbol.startswith(('6', '9', '5')) else 'sz'


def main():
    limit = None
    args = sys.argv[1:]
    if '--limit' in args:
        i = args.index('--limit')
        limit = int(args[i+1])

    # 1. 清理 2026 库中的非 2026 记录
    print('===== 清理 tq_daily_2026.db 中的 2021~2025 混入记录 =====')
    conn = sqlite3.connect(DB_PATHS['2026'])
    cur = conn.cursor()
    cur.execute("DELETE FROM stock_daily WHERE trade_date < '20260101'")
    print(f'已删除 {cur.rowcount} 条混入记录')
    conn.commit()
    conn.close()

    # 2. 收集文件列表
    files = []
    for market in ['sh', 'sz']:
        lday = os.path.join(TDXDIR, 'vipdoc', market, 'lday')
        for fn in sorted(os.listdir(lday)):
            if fn.endswith('.day'):
                files.append(os.path.join(lday, fn))
    total = len(files)
    if limit:
        files = files[:limit]
    print(f'待处理 .day 文件: {len(files)} / {total}')

    # 3. 按年分库连接
    conns = {y: sqlite3.connect(DB_PATHS[y]) for y in YEARS}
    curs = {y: conns[y].cursor() for y in YEARS}

    # 4. 遍历文件, 按年攒批
    batch = {y: [] for y in YEARS}   # year -> [(ts_code, td, o,h,l,c, pc,chg,pct, vol,amt, lu,ld, source, updated_at), ...]
    FLUSH_EVERY = 200
    total_insert = 0
    skipped = 0

    for idx, fp in enumerate(files, 1):
        base = os.path.basename(fp)
        market = 'sh' if '\\sh\\' in fp or '/sh/' in fp else 'sz'
        symbol = base[2:8] if base.startswith(('sh', 'sz')) else base[:6]
        ts_code = f'{symbol}.{"SH" if market == "sh" else "SZ"}'

        try:
            recs = read_day_file(fp)
        except Exception as e:
            print(f'[跳过] {base}: {e}')
            skipped += 1
            continue
        if not recs:
            skipped += 1
            continue

        enriched = enrich(recs)
        # 按年分组
        for td, o, h, l, c, pc, chg, pct, vol, amt, lu, ld in enriched:
            y = td[:4]
            if y not in batch:
                continue  # 只处理 2021~2026
            batch[y].append((ts_code, td, o, h, l, c, pc, chg, pct, vol, amt, lu, ld, 'vipdoc', NOW))
            total_insert += 1

        if idx % FLUSH_EVERY == 0:
            for y in YEARS:
                if batch[y]:
                    curs[y].executemany(
                        f"INSERT OR REPLACE INTO stock_daily (ts_code, trade_date, open, high, low, close, "
                        f"pre_close, change, pct_change, vol, amount, adj_factor, limit_up, limit_down, source, updated_at) "
                        f"VALUES (?,?,?,?,?,?,?,?,?,?,?,1.0,?,?,?,?)",
                        batch[y])
                    conns[y].commit()
                    batch[y] = []
            print(f'  进度 {idx}/{len(files)} 累计 {total_insert} 条')

    # 最终 flush
    for y in YEARS:
        if batch[y]:
            curs[y].executemany(
                f"INSERT OR REPLACE INTO stock_daily (ts_code, trade_date, open, high, low, close, "
                f"pre_close, change, pct_change, vol, amount, adj_factor, limit_up, limit_down, source, updated_at) "
                f"VALUES (?,?,?,?,?,?,?,?,?,?,?,1.0,?,?,?,?)",
                batch[y])
            conns[y].commit()

    for y in YEARS:
        conns[y].close()

    print(f'===== 完成: 共插入 {total_insert} 条, 跳过 {skipped} 文件 =====')
    # 汇总各库
    for y in YEARS:
        conn = sqlite3.connect(DB_PATHS[y])
        cur = conn.cursor()
        cur.execute('SELECT COUNT(*), COUNT(DISTINCT ts_code), MIN(trade_date), MAX(trade_date) FROM stock_daily')
        cnt, codes, mn, mx = cur.fetchone()
        cur.execute("SELECT COUNT(*) FROM stock_daily WHERE source != 'vipdoc'")
        non_vip = cur.fetchone()[0]
        print(f'tq_daily_{y}.db: {cnt} 条, {codes} 代码, [{mn}~{mx}], 非vipdoc残留={non_vip}')
        conn.close()


if __name__ == '__main__':
    main()
