# -*- coding: utf-8 -*-
"""
sz_daily_import.py — 深市个股日线导入（从通达信本地 sz*.day 解析）
用法:
    python sz_daily_import.py --dry-run   # 只验证不写库
    python sz_daily_import.py --sample    # 小批量(10个)验证+真实写库
    python sz_daily_import.py --full      # 全量导入

通达信 .day 格式: 每条32字节
    int32 date(YYYYMMDD) | int32 open(价格×100) | int32 high | int32 low | int32 close
    float amount | int32 vol | int32 reserved
"""
import os
import struct
import sqlite3
import sys

TDX_SZ_DIR = r'D:\new_tdx64\vipdoc\sz\lday'
DATA_DIR = r'D:\finance\data'

STOCK_PREFIX = ('000', '001', '002', '003', '300', '301')


def is_stock_code(code6: str) -> bool:
    """判断6位数字是否为深市个股"""
    return code6.startswith(STOCK_PREFIX)


def parse_day_file(path: str):
    """解析 .day 文件，返回 [(date_int, open, high, low, close, amount, vol), ...] 价格已除100"""
    rows = []
    with open(path, 'rb') as f:
        data = f.read()
    n = len(data) // 32
    for i in range(n):
        rec = data[i * 32:(i + 1) * 32]
        date_int, o, h, l, c, amount, vol, reserved = struct.unpack('<IIIIIfII', rec)
        rows.append((date_int, o / 100.0, h / 100.0, l / 100.0, c / 100.0, amount, vol))
    return rows


def import_sz(limit=None, dry_run=False):
    """导入深市日线。limit: 只处理前N个文件(测试用)。"""
    files = [f for f in os.listdir(TDX_SZ_DIR) if f.startswith('sz') and f.endswith('.day')]
    files.sort()
    if limit:
        files = files[:limit]

    total_files = 0
    total_rows = 0
    skipped = 0
    by_year = {}

    for fname in files:
        code6 = fname[2:8]  # sz000001.day -> 000001
        if not code6.isdigit() or not is_stock_code(code6):
            skipped += 1
            continue
        ts_code = f'{code6}.SZ'
        path = os.path.join(TDX_SZ_DIR, fname)
        rows = parse_day_file(path)
        if not rows:
            skipped += 1
            continue
        total_files += 1
        total_rows += len(rows)
        # 按每条记录统计年份
        for r in rows:
            yr = str(r[0])[:4]
            by_year[yr] = by_year.get(yr, 0) + 1

        if dry_run and (total_files <= 3 or fname in ('sz000001.day', 'sz000858.day', 'sz300750.day')):
            print(f'  {ts_code}: {len(rows)}条 {rows[0][0]}~{rows[-1][0]} O={rows[0][1]:.2f} H={rows[0][2]:.2f} L={rows[0][3]:.2f} C={rows[0][4]:.2f} amount={rows[0][5]:.0f} vol={rows[0][6]}')

    print(f'\n文件总数={len(files)} 个股文件={total_files} 跳过={skipped} 总行数={total_rows}')
    print(f'按年份: {dict(sorted(by_year.items()))}')

    if dry_run:
        print('\n[dry-run] 未写库')
        return

    # 正式导入
    imported_files = 0
    for fname in files:
        code6 = fname[2:8]
        if not code6.isdigit() or not is_stock_code(code6):
            continue
        ts_code = f'{code6}.SZ'
        path = os.path.join(TDX_SZ_DIR, fname)
        rows = parse_day_file(path)
        if not rows:
            continue
        # 按年份分组写入对应 tq_daily_YYYY.db
        year_rows = {}
        for r in rows:
            yr = str(r[0])[:4]
            year_rows.setdefault(yr, []).append(r)
        for yr, yrows in year_rows.items():
            db_path = os.path.join(DATA_DIR, f'tq_daily_{yr}.db')
            if not os.path.exists(db_path):
                continue
            conn = sqlite3.connect(db_path)
            cur = conn.cursor()
            prev_close = None
            for r in yrows:
                date_int, o, h, l, c, amount, vol = r
                trade_date = str(date_int)
                if prev_close and prev_close > 0:
                    pre_close = prev_close
                    change = c - pre_close
                    pct = change / pre_close * 100 if pre_close else 0.0
                else:
                    pre_close = None
                    change = None
                    pct = None
                cur.execute('''
                    INSERT OR IGNORE INTO stock_daily
                    (ts_code, trade_date, open, high, low, close, pre_close, change, pct_change, vol, amount, source, updated_at)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,'tdx',datetime('now','localtime'))
                ''', (ts_code, trade_date, o, h, l, c, pre_close, change, pct, vol, amount))
                prev_close = c
            conn.commit()
            conn.close()
        imported_files += 1
    print(f'\n[full] 导入完成 {imported_files} 个文件')


if __name__ == '__main__':
    if '--dry-run' in sys.argv:
        import_sz(dry_run=True)
    elif '--sample' in sys.argv:
        import_sz(limit=10, dry_run=False)
    elif '--full' in sys.argv:
        import_sz(dry_run=False)
    else:
        print(__doc__)
