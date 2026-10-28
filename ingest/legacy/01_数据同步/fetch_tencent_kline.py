import sqlite3, os
import requests
import time
import sys
from datetime import datetime

# Configurations
SOURCE_DB = r'D:\finance\data\fin_data.db'
TARGET_DB = r'D:\finance\data\fin_data_2026_tmp.db'
TABLE_SOURCE = 'stock_basic'
TABLE_TARGET = 'daily_kline'
BATCH_SIZE = 50
SLEEP_SECONDS = 0.5
TODAY_STR = datetime.now().strftime('%Y%m%d')  # e.g., 20260814
TRADE_DATE = datetime.now().strftime('%Y-%m-%d')

def get_symbols():
    conn = sqlite3.connect(SOURCE_DB)
    cur = conn.cursor()
    cur.execute(f"SELECT symbol FROM {TABLE_SOURCE}")
    rows = cur.fetchall()
    conn.close()
    symbols = [row[0] for row in rows]
    return symbols

def convert_symbol(sym):
    # sym like SH600036 or SZ000001
    prefix = sym[:2].lower()
    code = sym[2:]
    return f"{prefix}{code}"

def fetch_batch(tq_symbols):
    # tq_symbols is list of converted symbols like sh600036
    q = ','.join(tq_symbols)
    url = f"https://qt.gtimg.cn/q={q}"
    try:
        resp = requests.get(url, timeout=10)
        resp.encoding = 'gbk'
        text = resp.text
    except Exception as e:
        print(f"Error fetching {url}: {e}")
        return []
    lines = text.strip().split('\n')
    results = []
    for line in lines:
        # line format: v_sh600036="stock data string"
        if '="' not in line:
            continue
        _, data = line.split('="', 1)
        data = data.rstrip('"')
        fields = data.split('~')
        if len(fields) <= 37:
            continue
        # fields index per spec
        name = fields[1]
        cur_price = fields[3]
        prev_close = fields[4]
        open_price = fields[5]
        high = fields[33]
        low = fields[34]
        volume_hand = fields[36]
        amount_wan = fields[37]
        date_time = fields[30]
        if not date_time.startswith(TODAY_STR):
            continue
        # convert volume and amount
        try:
            volume = int(volume_hand) * 100  # 手 to 股
            amount = float(amount_wan) * 10000  # 万 to 元
        except Exception:
            volume = 0
            amount = 0.0
        # original symbol with prefix uppercase
        orig_sym = fields[2]  # field 2 is market code+code e.g., "sh600036"
        # convert back to upper case with SH/SZ prefix
        if orig_sym.startswith('sh'):
            symbol = 'SH' + orig_sym[2:]
        elif orig_sym.startswith('sz'):
            symbol = 'SZ' + orig_sym[2:]
        else:
            symbol = orig_sym.upper()
        results.append((symbol, open_price, high, low, cur_price, volume, amount))
    return results

def insert_records(records):
    conn = sqlite3.connect(TARGET_DB)
    cur = conn.cursor()
    # Ensure table exists (basic create if missing)
    cur.execute(f"""
    CREATE TABLE IF NOT EXISTS {TABLE_TARGET} (
        symbol TEXT,
        trade_date TEXT,
        open REAL,
        high REAL,
        low REAL,
        close REAL,
        volume INTEGER,
        amount REAL,
        turnover REAL,
        asset_type TEXT,
        created_at TEXT
    )
    """)
    sql = f"INSERT OR REPLACE INTO {TABLE_TARGET} (symbol, trade_date, open, high, low, close, volume, amount, turnover, asset_type, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)"
    now_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    data_to_insert = []
    for rec in records:
        symbol, open_p, high_p, low_p, close_p, vol, amt = rec
        data_to_insert.append((symbol, TRADE_DATE, float(open_p), float(high_p), float(low_p), float(close_p), vol, amt, 0.0, 'stock', now_str))
    cur.executemany(sql, data_to_insert)
    conn.commit()
    conn.close()

def main():
    # If target DB exists, remove it to avoid lock conflicts and start fresh
    if os.path.exists(TARGET_DB):
        try:
            os.remove(TARGET_DB)
        except Exception as e:
            print(f"Failed to remove existing target DB: {e}")
    symbols = get_symbols()
    total = len(symbols)
    print(f"Total symbols fetched: {total}")
    # Convert to tencent format
    tq_symbols = [convert_symbol(s) for s in symbols]
    # Process in batches
    for i in range(0, total, BATCH_SIZE):
        batch_syms = tq_symbols[i:i+BATCH_SIZE]
        # also need original mapping for symbol conversion back, but fetch_batch handles it using field[2]
        records = fetch_batch(batch_syms)
        if records:
            insert_records(records)
        # Progress report each 2000 symbols
        if (i // BATCH_SIZE + 1) * BATCH_SIZE % 2000 == 0:
            print(f"Processed {i+BATCH_SIZE} symbols (batch {(i//BATCH_SIZE)+1})")
        time.sleep(SLEEP_SECONDS)
    # Final count
    conn = sqlite3.connect(TARGET_DB)
    cur = conn.cursor()
    cur.execute(f"SELECT COUNT(*) FROM {TABLE_TARGET} WHERE trade_date=?", (TRADE_DATE,))
    count = cur.fetchone()[0]
    conn.close()
    print(f"Final count for date {TRADE_DATE}: {count}")

if __name__ == '__main__':
    main()
