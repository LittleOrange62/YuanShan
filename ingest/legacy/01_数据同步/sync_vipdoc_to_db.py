# -*- coding: utf-8 -*-
"""
sync_vipdoc_to_db.py — 解析 vipdoc 日线 + TQ 快照 到本地数据库

数据源:
  1) vipdoc 日线文件 (D:\\new_tdx64\\vipdoc\\sh\\lday\\sh600367.day) — 增量补录
  2) 通达信日终快照库 (D:\\new_tdx64\\daily_close_data.db, day_end_YYYYMMDD 表) — 补最近交易日(08-24)

目标: D:\\finance\\data\\tq_daily_2026.db 的 stock_daily 表

用法:
  python sync_vipdoc_to_db.py 600367        # 默认上证
  python sync_vipdoc_to_db.py 600367 SH     # 指定市场
  python sync_vipdoc_to_db.py 000001 SZ
"""
import struct
import sqlite3
import datetime
import sys
import os

# ===== 配置 =====
TDX_DIR = r'D:\new_tdx64'
VIPDOC_LDAY = os.path.join(TDX_DIR, 'vipdoc', 'sh', 'lday')   # 上证日线目录
DB_DAILY = r'D:\finance\data\tq_daily_2026.db'                # 目标库
SNAP_DB = os.path.join(TDX_DIR, 'daily_close_data.db')        # 快照库
TABLE = 'stock_daily'


def read_day_file(filepath):
    """读取通达信 .day 文件，返回记录列表 [(date_str, open, high, low, close, amount, vol), ...]"""
    with open(filepath, 'rb') as f:
        data = f.read()
    n = len(data) // 32
    recs = []
    for i in range(n):
        rec = data[i*32:(i+1)*32]
        date_int, open_p, high_p, low_p, close_p, amount, vol, _ = struct.unpack('<IIIIIfII', rec)
        recs.append({
            'trade_date': str(date_int),
            'open': open_p / 100.0,
            'high': high_p / 100.0,
            'low': low_p / 100.0,
            'close': close_p / 100.0,
            'amount': float(amount),          # 元
            'vol': vol / 100.0,               # 股 → 手
        })
    return recs


def find_snapshot_row(symbol_code, trade_date):
    """在 daily_close_data.db 的 day_end_YYYYMMDD 表中按 OHLC 匹配目标股票（快照无代码列，用OHLC精确匹配）"""
    if not os.path.isfile(SNAP_DB):
        print(f'[跳过] 快照库不存在: {SNAP_DB}')
        return None
    conn = sqlite3.connect(SNAP_DB)
    cur = conn.cursor()
    # 找最近的 day_end_ 表
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'day_end_%' ORDER BY name DESC")
    tables = [r[0] for r in cur.fetchall()]
    conn.close()
    if not tables:
        print('[跳过] 快照库无 day_end_ 表')
        return None

    for t in tables:
        # 先看该表有没有对应日期
        conn = sqlite3.connect(SNAP_DB)
        cur = conn.cursor()
        cur.execute(f"SELECT ItemNum,LastClose,Open,Max,Min,Now,Volume,Amount,HqDate FROM \"{t}\" WHERE HqDate=?",
                    (trade_date,))
        rows = cur.fetchall()
        conn.close()
        if not rows:
            continue
        # 找与 vipdoc 昨日收盘一致的行（用开盘/最高/最低/现价匹配真实快照）
        # 这里需要外部传入昨收，由调用方处理；此处返回所有 HqDate=trade_date 的行
        return rows
    return None


def main():
    symbol = sys.argv[1] if len(sys.argv) > 1 else '600367'
    market = sys.argv[2].upper() if len(sys.argv) > 2 else 'SH'

    # 1. 读 vipdoc 日线
    day_dir = os.path.join(TDX_DIR, 'vipdoc', market.lower(), 'lday')
    day_file = os.path.join(day_dir, f'{market.lower()}{symbol}.day')
    if not os.path.isfile(day_file):
        print(f'[错误] 日线文件不存在: {day_file}')
        return
    recs = read_day_file(day_file)
    print(f'[vipdoc] {day_file} 共 {len(recs)} 条记录, 最新 {recs[-1]["trade_date"]}')

    ts_code = f'{symbol}.{market}'

    # 2. 连接目标库
    conn = sqlite3.connect(DB_DAILY)
    cur = conn.cursor()
    cur.execute(f"SELECT MAX(trade_date) FROM {TABLE} WHERE ts_code=?", (ts_code,))
    max_date = cur.fetchone()[0]
    print(f'[数据库] {ts_code} 现有最新记录: {max_date}')

    # 3. 增量：vipdoc 中 trade_date > max_date 的记录
    to_insert = []
    for r in recs:
        if max_date is None or r['trade_date'] > max_date:
            to_insert.append(r)
    print(f'[增量] vipdoc 待补 {len(to_insert)} 条: {[r["trade_date"] for r in to_insert]}')

    # 4. 快照表补最近交易日（若 vipdoc 没有）
    snap_rows = find_snapshot_row(symbol, '20260824')
    if snap_rows:
        print(f'[快照] day_end_20260824 匹配到 {len(snap_rows)} 行 HqDate=20260824')
        # 用 600367 特征行：开34.95 高38.15 低33.57 现38.15
        for row in snap_rows:
            itemnum, last_close, open_p, max_p, min_p, now_p, volume, amount, _ = row
            if abs(open_p - 34.95) < 0.01 and abs(now_p - 38.15) < 0.01 and abs(max_p - 38.15) < 0.01:
                exists = any(r['trade_date'] == '20260824' for r in to_insert)
                if not exists:
                    to_insert.append({
                        'trade_date': '20260824',
                        'open': open_p,
                        'high': max_p,
                        'low': min_p,
                        'close': now_p,
                        'amount': amount * 10000.0,   # 万元 → 元
                        'vol': volume,                 # 手
                        '_snap': True,
                    })
                print(f'[快照] 命中 600367: 昨收{last_close} 开{open_p} 高{max_p} 低{min_p} 收{now_p} 量{volume}手 额{amount}万')
                break

    # 5. 写入
    now_str = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    inserted = 0
    for r in to_insert:
        pre_close = None
        # 找前一日收盘价
        prev = [x for x in recs if x['trade_date'] < r['trade_date']]
        if prev:
            pre_close = prev[-1]['close']
        elif r['trade_date'] == '20260824':
            # 08-24 从快照 LastClose 取（已在上面的匹配行中，这里兜底用 08-21 收盘）
            pre_close = 34.68

        change = round(r['close'] - pre_close, 2) if pre_close else None
        pct = round((r['close'] - pre_close) / pre_close * 100, 2) if pre_close else None
        limit_up = round(pre_close * 1.1, 2) if pre_close else None
        limit_down = round(pre_close * 0.9, 2) if pre_close else None
        source = 'tq_snapshot' if r.get('_snap') else 'vipdoc'

        # 去重后写入 (INSERT OR REPLACE 语义: 先删后插)
        cur.execute(f"DELETE FROM {TABLE} WHERE ts_code=? AND trade_date=?", (ts_code, r['trade_date']))
        cur.execute(
            f"INSERT INTO {TABLE} (ts_code, trade_date, open, high, low, close, pre_close, change, pct_change, "
            f"vol, amount, adj_factor, limit_up, limit_down, source, updated_at) "
            f"VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (ts_code, r['trade_date'], r['open'], r['high'], r['low'], r['close'], pre_close, change, pct,
             r['vol'], r['amount'], 1.0, limit_up, limit_down, source, now_str)
        )
        inserted += 1
        print(f'  [写入] {r["trade_date"]} 开{r["open"]:.2f} 高{r["high"]:.2f} 低{r["low"]:.2f} '
              f'收{r["close"]:.2f} 前收{pre_close} 涨跌{change} ({pct}%) 量{r["vol"]:.0f}手 '
              f'额{r["amount"]:.0f}元 涨停{limit_up} 跌停{limit_down} 来源:{source}')

    conn.commit()

    # 6. 验证
    print('=' * 60)
    print('[验证] 数据库最新记录:')
    cur.execute(f"SELECT ts_code, trade_date, open, high, low, close, pre_close, pct_change, vol, amount, source "
                f"FROM {TABLE} WHERE ts_code=? ORDER BY trade_date DESC LIMIT 6", (ts_code,))
    for row in cur.fetchall():
        print(' ', row)
    conn.close()
    print(f'\n完成: 共写入 {inserted} 条到 {DB_DAILY}')


if __name__ == '__main__':
    main()
