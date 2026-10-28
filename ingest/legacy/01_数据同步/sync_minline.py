# -*- coding: utf-8 -*-
"""
sync_minline.py — vipdoc 分钟线(.lc1/.lc5) 增量同步到 tq_minline_2026.db

格式(已实测确认, 每条 32 字节):
    struct '<IfffffII':
      date   uint32: 高16位=当天分钟数(571=09:31, 900=15:00), 低16位=日期序号(自然日, 45881=2026-08-25)
      open/high/low/close float(元)
      amount float(元)
      vol    uint32(股)
      reserved uint32

同步规则(用户要求):
  - 对比 vipdoc 最新日期 vs 本地库最新日期, 差 N 天
  - 从"本地最新日期往前倒退 2 个交易日"开始, 到 vipdoc 最新日期, 全部覆盖式同步
  - 本地没有该股票/该周期 → 全量同步
  - vipdoc 没有的日期(数据空洞)不强求, 有记录就同步

用法:
  python scripts/sync_minline.py                 # 全量 (1m + 5m)
  python scripts/sync_minline.py --limit 20      # 只处理前 20 个 lc1 文件(测试)
  python scripts/sync_minline.py --period 1m     # 只同步 1m
  python scripts/sync_minline.py --period 5m     # 只同步 5m
"""
import os
import sqlite3
import struct
import sys
from datetime import date, timedelta

TDXDIR = r'D:\new_tdx64'
DATA_DIR = r'D:\finance\data'
DB_MIN = os.path.join(DATA_DIR, 'tq_minline_2026.db')
TABLE = 'stock_minline'

# 日期序号基准: lo=45881 → 2026-08-25 (实测校准)
LO_BASE = 45881
DATE_BASE = date(2026, 8, 25)

# 分钟线涉及年份的交易日历来源 (合并 2025 + 2026)
CAL_DB_YEARS = ['2025', '2026']

# 时段
PERIODS = {'1m': ('minline', '.lc1'), '5m': ('fzline', '.lc5')}


def lo_to_date(lo):
    return DATE_BASE - timedelta(days=LO_BASE - lo)


# 预计算: lo(日期序号) → 'YYYYMMDD' 字符串; minute → 'HH:MM' 字符串 (加速解析)
_DATE_STRS = {lo: f'{lo_to_date(lo):%Y%m%d}' for lo in range(45000, 46500)}
_TIME_STRS = {m: f'{m // 60:02d}:{m % 60:02d}' for m in range(1440)}


def parse_lc_file(filepath):
    """解析 .lc1/.lc5, 返回 [(trade_datetime, open, high, low, close, amount, vol), ...]
    trade_datetime 格式: 'YYYYMMDD HH:MM' (与现有库一致)
    vol 单位: 股 (与 .day 原始一致, 与旧 tq 分钟线一致)
    """
    with open(filepath, 'rb') as f:
        data = f.read()
    recs = []
    for d, o, h, l, c, amt, vol, rsv in struct.iter_unpack('<IfffffII', data):
        lo = d & 0xFFFF
        minute = d >> 16
        if lo == 0 or minute > 1439:
            continue  # 脏数据
        dt_str = _DATE_STRS.get(lo)
        if dt_str is None:
            day = lo_to_date(lo)
            dt_str = f'{day:%Y%m%d}'
            _DATE_STRS[lo] = dt_str
        dt_str = dt_str + ' ' + _TIME_STRS[minute]
        recs.append((dt_str, o, h, l, c, amt, float(vol)))
    return recs


def load_trade_calendar():
    """从日线库加载交易日列表 (排序升序)"""
    days = set()
    for y in CAL_DB_YEARS:
        p = os.path.join(DATA_DIR, f'tq_daily_{y}.db')
        if not os.path.exists(p):
            continue
        conn = sqlite3.connect(p)
        cur = conn.cursor()
        cur.execute('SELECT DISTINCT trade_date FROM stock_daily')
        for (d,) in cur.fetchall():
            days.add(d)
        conn.close()
    return sorted(days)


def prev_n_trade_days(cal, d_str, n):
    """在交易日历中找 d_str 往前数第 n 个交易日(更早的那个), 找不到返回 None"""
    try:
        idx = cal.index(d_str)
    except ValueError:
        # 不在日历中(如周末/非交易日) → 找最近一个 <= d_str 的
        idx = None
        for i in range(len(cal) - 1, -1, -1):
            if cal[i] <= d_str:
                idx = i
                break
        if idx is None:
            return None
    target = idx - n
    if target < 0:
        return cal[0]
    return cal[target]


def main():
    args = sys.argv[1:]
    limit = None
    periods = ['1m', '5m']
    markets = ['sh', 'sz']
    if '--limit' in args:
        limit = int(args[args.index('--limit') + 1])
    if '--period' in args:
        periods = [args[args.index('--period') + 1]]
    if '--market' in args:
        markets = [args[args.index('--market') + 1]]

    cal = load_trade_calendar()
    print(f'交易日历: {len(cal)} 天 [{cal[0]}~{cal[-1]}]')

    # 收集文件
    file_map = []  # (period, filepath, ts_code)
    for period in periods:
        subdir, ext = PERIODS[period]
        for market in markets:
            d = os.path.join(TDXDIR, 'vipdoc', market, subdir)
            if not os.path.isdir(d):
                print(f'[跳过] 目录不存在: {d}')
                continue
            for fn in sorted(os.listdir(d)):
                if fn.endswith(ext):
                    symbol = fn[2:8]
                    ts_code = f'{symbol}.{"SH" if market == "sh" else "SZ"}'
                    file_map.append((period, os.path.join(d, fn), ts_code))
    total = len(file_map)
    if limit:
        file_map = file_map[:limit]
    print(f'待处理文件: {len(file_map)} / {total}')

    conn = sqlite3.connect(DB_MIN)
    cur = conn.cursor()
    cur.execute('PRAGMA journal_mode=OFF')
    cur.execute('PRAGMA synchronous=OFF')
    cur.execute('PRAGMA cache_size=-200000')  # 200MB

    # 预取本地各股票各period最新日期
    cur.execute(f'SELECT ts_code, period, MAX(trade_datetime) FROM {TABLE} GROUP BY ts_code, period')
    local_max = {}
    for ts, per, mx in cur.fetchall():
        local_max[(ts, per)] = mx[:8] if mx else None  # 只取日期部分 YYYYMMDD

    stats = {'insert': 0, 'delete': 0, 'skip': 0, 'empty': 0}
    t0 = __import__('time').time()

    for idx, (period, fp, ts_code) in enumerate(file_map, 1):
        recs = parse_lc_file(fp)
        if not recs:
            stats['empty'] += 1
            continue

        # vipdoc 最新日期
        max_v = recs[-1][0][:8]
        max_l = local_max.get((ts_code, period))

        if max_l is not None and max_l >= max_v:
            stats['skip'] += 1
            continue

        # 起点: max_l 往前 2 个交易日; 无 max_l → 全量
        if max_l is not None:
            start = prev_n_trade_days(cal, max_l, 2)
            if start is None:
                start = max_l
            # 删除本地 >= start 的记录
            cur.execute(f"DELETE FROM {TABLE} WHERE ts_code=? AND period=? AND trade_datetime >= ?",
                        (ts_code, period, start + ' 00:00'))
            stats['delete'] += cur.rowcount
            insert_recs = [r for r in recs if r[0][:8] >= start]
        else:
            insert_recs = recs

        if insert_recs:
            cur.executemany(
                f"INSERT OR REPLACE INTO {TABLE} (ts_code, trade_datetime, period, open, high, low, close, vol, amount, source, updated_at) "
                f"VALUES (?,?,?,?,?,?,?,?,?,?,datetime('now','localtime'))",
                [(ts_code, dt, period, o, h, l, c, v, a, 'vipdoc') for dt, o, h, l, c, a, v in insert_recs])
            stats['insert'] += len(insert_recs)

        if idx % 1000 == 0:
            conn.commit()
            el = __import__('time').time() - t0
            print(f'  进度 {idx}/{len(file_map)} [{period}] 插入={stats["insert"]} 删除={stats["delete"]} '
                  f'跳过={stats["skip"]} 空={stats["empty"]} 耗时={el:.0f}s')

    conn.commit()
    conn.close()
    el = __import__('time').time() - t0
    print(f'===== 完成: 插入={stats["insert"]} 删除={stats["delete"]} 跳过={stats["skip"]} 空={stats["empty"]} 耗时={el:.0f}s =====')


if __name__ == '__main__':
    main()
