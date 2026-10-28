# -*- coding: utf-8 -*-
"""
batch_sync_vipdoc.py — 批量解析 vipdoc 全市场日线 增量同步到 tq_daily_2026.db

数据源: D:\\new_tdx64\\vipdoc\\sh\\lday\\*.day + sz\\lday\\*.day
目标:   D:\\finance\\data\\tq_daily_2026.db 的 stock_daily 表
逻辑:   每只股票, 取 .day 中 trade_date > 数据库现有 max(trade_date) 的记录, 增量写入
单位:   vipdoc 价格/100=元, vol 股→手(/100), amount 元不变

用法:
  python batch_sync_vipdoc.py                 # 全市场
  python batch_sync_vipdoc.py --limit 10      # 只处理前10个文件(测试)
  python batch_sync_vipdoc.py --mkt sh        # 只处理上证
"""
import os
import struct
import sqlite3
import datetime
import sys
import glob

TDX_DIR = r'D:\new_tdx64'
DB_DAILY = r'D:\finance\data\tq_daily_2026.db'
TABLE = 'stock_daily'


def read_day_file(filepath):
    """读取 .day 文件, 返回 [(trade_date, open, high, low, close, amount, vol), ...]"""
    with open(filepath, 'rb') as f:
        data = f.read()
    n = len(data) // 32
    recs = []
    for i in range(n):
        rec = data[i*32:(i+1)*32]
        date_int, open_p, high_p, low_p, close_p, amount, vol, _ = struct.unpack('<IIIIIfII', rec)
        recs.append((str(date_int), open_p/100.0, high_p/100.0, low_p/100.0, close_p/100.0,
                     float(amount), vol/100.0))
    return recs


def main():
    args = sys.argv[1:]
    limit = None
    mkt = None
    if '--limit' in args:
        limit = int(args[args.index('--limit') + 1])
    if '--mkt' in args:
        mkt = args[args.index('--mkt') + 1]

    # 收集文件
    files = []
    if mkt in (None, 'sh'):
        files += glob.glob(os.path.join(TDX_DIR, 'vipdoc', 'sh', 'lday', '*.day'))
    if mkt in (None, 'sz'):
        files += glob.glob(os.path.join(TDX_DIR, 'vipdoc', 'sz', 'lday', '*.day'))
    files.sort()
    if limit:
        files = files[:limit]

    print(f'待处理文件数: {len(files)}')

    conn = sqlite3.connect(DB_DAILY)
    cur = conn.cursor()
    now_str = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    total_insert = 0
    total_files_ok = 0
    total_files_skip = 0
    total_files_err = 0
    skipped_symbols = []

    for idx, fp in enumerate(files, 1):
        fname = os.path.basename(fp)
        # 解析代码: sh600367.day → 600367.SH
        try:
            market_char = fname[:2].lower()
            symbol = fname[2:8]
            if market_char == 'sh':
                ts_code = f'{symbol}.SH'
            elif market_char == 'sz':
                ts_code = f'{symbol}.SZ'
            else:
                continue
        except Exception:
            continue

        try:
            recs = read_day_file(fp)
            if not recs:
                total_files_skip += 1
                continue

            # 查数据库现有最新日期
            cur.execute(f"SELECT MAX(trade_date) FROM {TABLE} WHERE ts_code=?", (ts_code,))
            max_date = cur.fetchone()[0]

            # 找增量
            insert_recs = [r for r in recs if max_date is None or r[0] > max_date]
            if not insert_recs:
                total_files_skip += 1
                continue

            # 找第一条增量的 pre_close: max_date 那天的 close(在 .day 中)
            pre_close = None
            if max_date:
                for r in recs:
                    if r[0] == max_date:
                        pre_close = r[4]
                        break

            batch = []
            for r in insert_recs:
                td, o, h, l, c, amt, vol = r
                if pre_close is None:
                    chg = None
                    pct = None
                    lu = None
                    ld = None
                else:
                    chg = round(c - pre_close, 2)
                    pct = round((c - pre_close) / pre_close * 100, 2) if pre_close else None
                    lu = round(pre_close * 1.1, 2)
                    ld = round(pre_close * 0.9, 2)
                batch.append((ts_code, td, o, h, l, c, pre_close, chg, pct, vol, amt,
                              1.0, lu, ld, 'vipdoc', now_str))
                pre_close = c  # 下一条的前收

            # 去重写入(先删后插)
            for b in batch:
                cur.execute(f"DELETE FROM {TABLE} WHERE ts_code=? AND trade_date=?", (b[0], b[1]))
                cur.execute(
                    f"INSERT INTO {TABLE} (ts_code, trade_date, open, high, low, close, pre_close, "
                    f"change, pct_change, vol, amount, adj_factor, limit_up, limit_down, source, updated_at) "
                    f"VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", b)
            conn.commit()
            total_insert += len(batch)
            total_files_ok += 1
            if idx % 500 == 0:
                print(f'  进度 {idx}/{len(files)} 已插入 {total_insert} 条')
        except Exception as e:
            total_files_err += 1
            print(f'  [错误] {fname}: {e}')

    conn.close()
    print('=' * 60)
    print(f'完成! 处理 {len(files)} 文件')
    print(f'  成功写入: {total_files_ok} 文件, 共 {total_insert} 条记录')
    print(f'  跳过(无增量): {total_files_skip} 文件')
    print(f'  失败: {total_files_err} 文件')


if __name__ == '__main__':
    main()
