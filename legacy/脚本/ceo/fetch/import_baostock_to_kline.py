#!/usr/bin/env python3
"""
导入 Baostock CSV → kline_2026.db.daily_kline
处理字段映射和缺失列
"""
import sqlite3
import pandas as pd
import glob
import sys

DB_PATH = '/app/working/workspaces/data_in/data/kline_2026.db'
CSV_DIR = '/app/working/workspaces/data_from/data/'

DATES = ['20260708', '20260709', '20260710']

def import_csv_to_db(date_str):
    pattern = f'{CSV_DIR}baostock_daily_full_{date_str}.csv'
    files = glob.glob(pattern)
    if not files:
        print(f'❌ {date_str}: CSV 不存在')
        return 0
    if len(files) > 1:
        print(f'⚠️ {date_str}: 找到多个 CSV，使用第一个: {files[0]}')

    fp = files[0]
    df = pd.read_csv(fp)
    if df.empty:
        print(f'⚠️ {date_str}: CSV 为空')
        return 0

    # Baostock CSV 列: ts_code, trade_date, open, high, low, close, pre_close, change, pct_chg, vol, amount
    # daily_kline 表需要额外列: pct_change, turnover_r, pe, pb, total_mv, circ_mv, trade_status, source, data_date

    # 重命名 pct_chg → pct_change
    df.rename(columns={'pct_chg': 'pct_change'}, inplace=True)

    # 补充缺失字段
    df['turnover_r'] = None
    df['pe'] = None
    df['pb'] = None
    df['total_mv'] = None
    df['circ_mv'] = None
    df['trade_status'] = '交易'
    df['source'] = 'baostock'
    df['data_date'] = None  # 会在 INSERT 时自动填充

    # 确保 trade_date 是纯数字 YYYYMMDD
    df['trade_date'] = df['trade_date'].astype(str).str.replace('-', '')

    # 连接 DB
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    # 检查是否已有该日期的数据
    existing = cur.execute(
        'SELECT COUNT(*) FROM daily_kline WHERE trade_date = ?',
        (date_str,)
    ).fetchone()[0]

    if existing > 0:
        print(f'⚠️ {date_str}: daily_kline 已有 {existing} 条，先删除再覆盖')
        cur.execute('DELETE FROM daily_kline WHERE trade_date = ?', (date_str,))
        conn.commit()

    # 批量写入
    target_cols = ['ts_code', 'trade_date', 'open', 'high', 'low', 'close',
                   'pre_close', 'change', 'pct_change', 'vol', 'amount',
                   'turnover_r', 'pe', 'pb', 'total_mv', 'circ_mv',
                   'trade_status', 'source', 'data_date']
    df[target_cols].to_sql('daily_kline', conn, if_exists='append', index=False)
    conn.commit()

    cnt = len(df)
    print(f'✅ {date_str}: 导入 {cnt} 条 (覆盖 {existing} 条旧数据)')
    conn.close()
    return cnt

if __name__ == '__main__':
    total = 0
    for d in DATES:
        c = import_csv_to_db(d)
        total += c
    print(f'\n📊 合计导入: {total} 条')
