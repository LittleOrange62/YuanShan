# -*- coding: utf-8 -*-
"""
腾讯接口批量拉取 2026-08-14 日线 → 写入 fin_data_2026.db.daily_kline
（CEO 备用脚本：若 tq 未完成，data_in 可直接执行）
用法: python fetch_0814_tencent.py [--limit N]
"""
import requests
import sqlite3
import time
import sys
import os

TDX_DATE = '2026-08-14'
DATA_DB = r'D:\finance\data\fin_data_2026.db'
SRC_DB = r'D:\finance\data\fin_data.db'

def load_symbols():
    con = sqlite3.connect(SRC_DB)
    rows = con.execute("SELECT symbol FROM stock_basic WHERE symbol LIKE 'SH%' OR symbol LIKE 'SZ%'").fetchall()
    con.close()
    syms = [r[0] for r in rows]
    # 过滤明显非股票：指数/转债/基金等（腾讯接口主要返回股票，但保险起见过滤）
    out = []
    for s in syms:
        code = s[2:]
        if s.startswith('SH') and code.startswith(('600', '601', '603', '605', '688', '689')):
            out.append(s)
        elif s.startswith('SZ') and code.startswith(('000', '001', '002', '003', '300', '301')):
            out.append(s)
    return out

def to_tx(sym):
    return sym[:2].lower() + sym[2:]

def fetch_batch(tx_syms):
    url = 'https://qt.gtimg.cn/q=' + ','.join(tx_syms)
    try:
        r = requests.get(url, timeout=10, headers={'Referer': 'https://gu.qq.com'})
        r.encoding = 'gbk'
        out = []
        for line in r.text.strip().split(';'):
            line = line.strip()
            if not line or '=' not in line:
                continue
            data = line.split('=')[1].strip('"').split('~')
            if len(data) <= 37:
                continue
            date_str = data[30] if len(data) > 30 else ''
            if not date_str.startswith('20260814'):
                continue
            try:
                close = float(data[3])
                pre_close = float(data[4])
                open_ = float(data[5])
                high = float(data[33])
                low = float(data[34])
                vol_hand = float(data[36]) if data[36] else 0.0
                amount_wan = float(data[37]) if data[37] else 0.0
            except (ValueError, IndexError):
                continue
            sym_full = line.split('=')[0].replace('v_', '')
            sym = sym_full[:2].upper() + sym_full[2:]
            out.append((sym, TDX_DATE, open_, high, low, close,
                        vol_hand * 100, amount_wan * 10000, 0.0, 'stock'))
        return out
    except Exception as e:
        print(f'批量失败: {e}')
        return []

def main():
    limit = None
    if len(sys.argv) > 2 and sys.argv[1] == '--limit':
        limit = int(sys.argv[2])

    syms = load_symbols()
    print(f'待拉取股票数: {len(syms)}')
    if limit:
        syms = syms[:limit]

    con = sqlite3.connect(DATA_DB, timeout=60)
    con.execute('PRAGMA journal_mode=WAL')
    con.execute('PRAGMA busy_timeout=30000')
    total_ok = 0
    total_fail = 0
    BATCH = 50
    start = time.time()
    for i in range(0, len(syms), BATCH):
        batch = syms[i:i + BATCH]
        tx_batch = [to_tx(s) for s in batch]
        rows = fetch_batch(tx_batch)
        if rows:
            try:
                con.executemany(
                    "INSERT OR REPLACE INTO daily_kline (symbol, trade_date, open, high, low, close, volume, amount, turnover, asset_type) VALUES (?,?,?,?,?,?,?,?,?,?)",
                    rows
                )
                con.commit()
                total_ok += len(rows)
            except Exception as e:
                print(f'写入失败: {e}')
                total_fail += len(rows)
        else:
            total_fail += len(batch)
        if (i // BATCH) % 20 == 0:
            elapsed = time.time() - start
            print(f'进度: {i + len(batch)}/{len(syms)} 成功{total_ok} 失败{total_fail} 耗时{elapsed:.0f}s')
        time.sleep(0.3)

    con.close()
    elapsed = time.time() - start
    print(f'\n完成: 成功{total_ok} 失败{total_fail} 耗时{elapsed:.0f}s')

    # 验证
    con = sqlite3.connect(DATA_DB)
    n = con.execute("SELECT COUNT(*) FROM daily_kline WHERE trade_date=?", (TDX_DATE,)).fetchone()[0]
    con.close()
    print(f'验证: daily_kline {TDX_DATE} 共 {n} 条')

if __name__ == '__main__':
    main()
