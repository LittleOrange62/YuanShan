import os
import sqlite3
import csv
from datetime import datetime

DATA_DIR = os.getenv('DATA_DIR', r'D:/finance/data')
DB_PATH = r'D:/finance/data/fin_data_2026.db'
TARGET_DATE = '2026-08-17'

def create_tables(conn):
    cur = conn.cursor()
    # minute_kline
    cur.execute('''
    CREATE TABLE IF NOT EXISTS minute_kline (
        id INTEGER PRIMARY KEY,
        symbol TEXT NOT NULL,
        datetime TEXT NOT NULL,
        open REAL,
        high REAL,
        low REAL,
        close REAL,
        amount REAL,
        volume REAL,
        UNIQUE(symbol, datetime)
    )
    ''')
    # tick_kline
    cur.execute('''
    CREATE TABLE IF NOT EXISTS tick_kline (
        id INTEGER PRIMARY KEY,
        symbol TEXT NOT NULL,
        price REAL,
        vol REAL,
        UNIQUE(symbol, price, vol)
    )
    ''')
    conn.commit()

def insert_daily(conn):
    cur = conn.cursor()
    count = 0
    for fname in os.listdir(DATA_DIR):
        if fname.endswith('_daily_merged.csv'):
            symbol = fname.replace('_daily_merged.csv', '')
            path = os.path.join(DATA_DIR, fname)
            with open(path, newline='', encoding='utf-8-sig') as f:
                reader = csv.DictReader(f)
                for row in reader:
                    # assuming column names include date or trade_date
                    date_key = row.get('date') or row.get('trade_date') or row.get('date')
                    if not date_key:
                        continue
                    if isinstance(date_key, str) and date_key.startswith(TARGET_DATE):
                        cur.execute('''
                        INSERT OR REPLACE INTO daily_kline (symbol, trade_date, open, high, low, close, volume, amount, turnover)
                        VALUES (?,?,?,?,?,?,?,?,?)
                        ''', (
                            symbol,
                            date_key[:10],
                            float(row.get('open',0)),
                            float(row.get('high',0)),
                            float(row.get('low',0)),
                            float(row.get('close',0)),
                            float(row.get('volume',0) or row.get('vol',0)),
                            float(row.get('amount',0) or row.get('money',0)),
                            0
                        ))
                        count += 1
    conn.commit()
    return count

def insert_minute(conn):
    cur = conn.cursor()
    count = 0
    for fname in os.listdir(DATA_DIR):
        if fname.endswith('_minute.csv'):
            symbol = fname.replace('_minute.csv', '')
            path = os.path.join(DATA_DIR, fname)
            with open(path, newline='', encoding='utf-8-sig') as f:
                reader = csv.DictReader(f)
                for row in reader:
                    dt = row.get('date') or row.get('datetime')
                    if not dt:
                        continue
                    if dt.startswith(TARGET_DATE):
                        cur.execute('''
                        INSERT OR REPLACE INTO minute_kline (symbol, datetime, open, high, low, close, amount, volume)
                        VALUES (?,?,?,?,?,?,?,?)
                        ''', (
                            symbol,
                            dt,
                            float(row.get('open',0)),
                            float(row.get('high',0)),
                            float(row.get('low',0)),
                            float(row.get('close',0)),
                            float(row.get('amount',0)),
                            float(row.get('volume',0))
                        ))
                        count += 1
    conn.commit()
    return count

def insert_tick(conn):
    cur = conn.cursor()
    count = 0
    for fname in os.listdir(DATA_DIR):
        if fname.endswith('_tick.csv'):
            symbol = fname.replace('_tick.csv', '')
            path = os.path.join(DATA_DIR, fname)
            with open(path, newline='', encoding='utf-8-sig') as f:
                reader = csv.DictReader(f)
                for row in reader:
                    # tick data may not have date, assume same day
                    cur.execute('''
                    INSERT OR REPLACE INTO tick_kline (symbol, price, vol)
                    VALUES (?,?,?)
                    ''', (
                        symbol,
                        float(row.get('price',0)),
                        float(row.get('vol',0))
                    ))
                    count += 1
    conn.commit()
    return count

def main():
    conn = sqlite3.connect(DB_PATH)
    create_tables(conn)
    daily_cnt = insert_daily(conn)
    minute_cnt = insert_minute(conn)
    tick_cnt = insert_tick(conn)
    conn.close()
    # write log
    log_path = os.path.join(DATA_DIR, 'tq_fetch_log.txt')
    with open(log_path, 'a', encoding='utf-8') as log:
        log.write(f"{datetime.now()}: Inserted daily:{daily_cnt}, minute:{minute_cnt}, tick:{tick_cnt}\n")
    print(f"✅ Inserted rows - daily:{daily_cnt}, minute:{minute_cnt}, tick:{tick_cnt}")

if __name__ == '__main__':
    main()
