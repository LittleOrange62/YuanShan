import sqlite3
import requests
import time
import itertools
from typing import List

DB_PATH_SRC = r'D:\finance\data\fin_data.db'
DB_PATH_DST = r'D:\finance\data\fin_data_2026.db'
TABLE_NAME = 'daily_kline'
TRADE_DATE = '2026-08-14'
DATE_PREFIX = '20260814'  # YYYYMMDD

BATCH_SIZE = 50  # as per requirement
SLEEP_INTERVAL = 0.5  # seconds between batches

def fetch_symbols() -> List[str]:
    """Read all symbols from stock_basic table and return list of original symbols (e.g., 'SH600036')."""
    conn = sqlite3.connect(DB_PATH_SRC)
    cur = conn.cursor()
    cur.execute('SELECT symbol FROM stock_basic')
    rows = cur.fetchall()
    conn.close()
    return [r[0] for r in rows]

def to_tencent_format(sym: str) -> str:
    """Convert 'SH600036' -> 'sh600036', 'SZ000001' -> 'sz000001'."""
    prefix = sym[:2].lower()
    code = sym[2:]
    return f"{prefix}{code}"

def batch(iterable, n=1):
    """Utility to split iterable into batches of size n."""
    l = len(iterable)
    for ndx in range(0, l, n):
        yield iterable[ndx:min(ndx + n, l)]

def parse_response(text: str, original_symbols: List[str]):
    """Parse the API response text (gbk decoded) and return list of dicts with needed fields.
    `original_symbols` should be in the same order as request.
    """
    results = []
    # response example: v_sh600036="1~平安银行~12.34~...";v_sz000001="...";
    # split by ';' and ignore empty strings
    lines = [ln for ln in text.strip().split(';') if ln]
    for line in lines:
        # line format: v_sh600036="..."
        try:
            _, payload = line.split('=', 1)
            payload = payload.strip('"')
            fields = payload.split('~')
            # indices per spec (0-based):
            # 1 name, 3 current price, 4 yesterday close, 5 today open,
            # 30 date, 33 high, 34 low, 36 volume (hand), 37 amount (10k)
            name = fields[1]
            close = float(fields[3])
            yesterday_close = float(fields[4])
            open_price = float(fields[5])
            high = float(fields[33]) if len(fields) > 33 and fields[33] else 0.0
            low = float(fields[34]) if len(fields) > 34 and fields[34] else 0.0
            volume_hand = float(fields[36]) if len(fields) > 36 and fields[36] else 0.0
            amount_10k = float(fields[37]) if len(fields) > 37 and fields[37] else 0.0
            date_str = fields[30] if len(fields) > 30 else ''
            # filter by today's date prefix
            if not date_str.startswith(DATE_PREFIX):
                continue
            # symbol extraction: first field after 'v_'
            # original code part is after the first underscore, e.g., v_sh600036 -> sh600036
            code_part = line.split('_', 1)[1].split('=')[0]
            # map back to original uppercase format
            if code_part.startswith('sh'):
                symbol_original = f"SH{code_part[2:]}"
            elif code_part.startswith('sz'):
                symbol_original = f"SZ{code_part[2:]}"
            else:
                symbol_original = code_part.upper()
            result = {
                'symbol': symbol_original,
                'open': open_price,
                'high': high,
                'low': low,
                'close': close,
                'volume': int(volume_hand * 100),
                'amount': amount_10k * 10000,
                'turnover': 0,
                'asset_type': 'stock',
            }
            results.append(result)
        except Exception as e:
            # ignore malformed line
            continue
    return results

def insert_rows(rows: List[dict]):
    if not rows:
        return 0
    conn = sqlite3.connect(DB_PATH_DST, timeout=30)
    cur = conn.cursor()
    sql = f"INSERT OR REPLACE INTO {TABLE_NAME} (symbol, trade_date, open, high, low, close, volume, amount, turnover, asset_type) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
    data = [(r['symbol'], TRADE_DATE, r['open'], r['high'], r['low'], r['close'], r['volume'], r['amount'], r['turnover'], r['asset_type']) for r in rows]
    cur.executemany(sql, data)
    conn.commit()
    conn.close()
    return len(rows)

def main(test_mode: bool = False):
    symbols = fetch_symbols()
    # Transform to tencent format
    tencent_symbols = [to_tencent_format(sym) for sym in symbols]
    total = len(tencent_symbols)
    print(f"Total symbols fetched: {total}")
    processed = 0
    inserted = 0
    # Clean previous data for this trade_date to avoid duplicate primary key conflicts
    conn_clean = sqlite3.connect(DB_PATH_DST)
    cur_clean = conn_clean.cursor()
    cur_clean.execute(f"DELETE FROM {TABLE_NAME} WHERE trade_date=?", (TRADE_DATE,))
    conn_clean.commit()
    conn_clean.close()
    # Process in batches
    for batch_syms in batch(tencent_symbols, BATCH_SIZE):
        # If test mode, limit to first batch only
        if test_mode and processed >= BATCH_SIZE:
            break
        url = "https://qt.gtimg.cn/q=" + ",".join(batch_syms)
        try:
            resp = requests.get(url, timeout=10)
            # response is gbk encoded
            resp.encoding = 'gbk'
            text = resp.text
            rows = parse_response(text, batch_syms)
            cnt = insert_rows(rows)
            inserted += cnt
        except Exception as e:
            print(f"Error processing batch starting at {processed}: {e}")
        processed += len(batch_syms)
        if not test_mode and processed % 2000 == 0:
            print(f"Processed {processed} symbols, inserted {inserted} rows so far.")
        time.sleep(SLEEP_INTERVAL)
    print(f"Finished. Total processed: {processed}, total inserted rows: {inserted}")
    # Verify count
    conn = sqlite3.connect(DB_PATH_DST, timeout=30)
    cur = conn.cursor()
    cur.execute(f"SELECT COUNT(*) FROM {TABLE_NAME} WHERE trade_date=?", (TRADE_DATE,))
    count = cur.fetchone()[0]
    conn.close()
    print(f"Verification count for trade_date {TRADE_DATE}: {count}")

if __name__ == '__main__':
    import sys
    test = False
    if len(sys.argv) > 1 and sys.argv[1] in ('-t', '--test'):
        test = True
    # Set test_mode=False for full run. Use -t or --test for initial 50-symbol test.
    main(test_mode=test)
