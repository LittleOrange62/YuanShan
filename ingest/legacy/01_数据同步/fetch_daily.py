import sqlite3
import pandas as pd
import akshare as ak
import time
import sys

# Paths
SRC_DB = r'D:\finance\data\fin_data.db'
TARGET_DB = r'D:\finance\data\fin_data_2026.db'

# Connect to source database and fetch symbols
try:
    src_conn = sqlite3.connect(SRC_DB)
    df_symbols = pd.read_sql('SELECT symbol FROM stock_basic', src_conn)
    src_conn.close()
except Exception as e:
    print(f'Error reading source DB: {e}')
    sys.exit(1)

symbols = df_symbols['symbol'].astype(str).tolist()

def to_sina(sym):
    sym = sym.strip()
    upper = sym.upper()
    if upper.startswith('SH'):
        return 'sh' + sym[2:]
    if upper.startswith('SZ'):
        return 'sz' + sym[2:]
    # assume numeric code
    if sym.startswith('6'):
        return 'sh' + sym
    else:
        return 'sz' + sym

sina_symbols = [to_sina(s) for s in symbols]

batch_size = 1000
success = 0
fail = 0

# Connect to target DB and ensure table exists
try:
    tgt_conn = sqlite3.connect(TARGET_DB)
    create_sql = '''
    CREATE TABLE IF NOT EXISTS daily_kline (
        symbol TEXT,
        trade_date TEXT,
        open REAL,
        high REAL,
        low REAL,
        close REAL,
        volume REAL,
        amount REAL,
        turnover REAL,
        asset_type TEXT
    )
    '''
    tgt_conn.execute(create_sql)
    tgt_conn.commit()
except Exception as e:
    print(f'Error preparing target DB: {e}')
    sys.exit(1)

start_date = '20260814'
end_date = '20260814'

total = len(sina_symbols)
for start in range(0, total, batch_size):
    batch = sina_symbols[start:start+batch_size]
    for sym in batch:
        try:
            df = ak.stock_zh_a_daily(symbol=sym, start_date=start_date, end_date=end_date)
            if df.empty:
                fail += 1
                continue
            # Ensure date column is present
            if isinstance(df.index, pd.DatetimeIndex):
                df['trade_date'] = df.index.strftime('%Y%m%d')
            elif 'date' in df.columns:
                df['trade_date'] = pd.to_datetime(df['date']).dt.strftime('%Y%m%d')
            else:
                df['trade_date'] = start_date
            df['symbol'] = sym
            # Asset type based on prefix
            df['asset_type'] = 'stock'
            # Select required columns (order matters)
            out_df = df[['symbol', 'trade_date', 'open', 'high', 'low', 'close', 'volume', 'amount', 'turnover', 'asset_type']]
            out_df.to_sql('daily_kline', tgt_conn, if_exists='append', index=False)
            success += 1
        except Exception as e:
            fail += 1
            # Optionally print error for debugging (comment out in production)
            # print(f'Failed {sym}: {e}')
    # Commit after each batch
    tgt_conn.commit()
    print(f'Processed {min(start+batch_size, total)} / {total} symbols. Success so far: {success}')
    # Simple pause to be gentle on the source
    time.sleep(1)

print('Fetching completed.')
print(f'Successful: {success}, Failed: {fail}')
# Verify count
try:
    cnt_df = pd.read_sql("SELECT COUNT(*) as cnt FROM daily_kline WHERE trade_date='20260814'", tgt_conn)
    print('Rows for 2026-08-14:', cnt_df.iloc[0]['cnt'])
except Exception as e:
    print('Error verifying count:', e)

tgt_conn.close()
