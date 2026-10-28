import sqlite3, pandas as pd

def prefix(symbol):
    # simple rule: if starts with '6' -> SH, else -> SZ
    return ('SH' if symbol.startswith('6') else 'SZ') + symbol

watchlist_db = r'D:\finance\data\fin_watchlist.db'
data_db = r'D:\finance\data\fin_data_2026.db'

# load symbols
conn_w = sqlite3.connect(watchlist_db)
symbols_raw = [row[0] for row in conn_w.execute('SELECT symbol FROM watchlist_stocks').fetchall()]
conn_w.close()

# map to prefixed symbols used in daily_kline
def map_sym(s):
    return prefix(s)

symbols = [map_sym(s) for s in symbols_raw]

conn_d = sqlite3.connect(data_db)
# get recent distinct dates sorted asc
dates = pd.read_sql_query('SELECT DISTINCT trade_date FROM daily_kline ORDER BY trade_date ASC', conn_d)['trade_date'].astype(str).tolist()
# normalize to yyyymmdd without hyphens
def norm(d):
    return d.replace('-', '')
dates = [norm(d) for d in dates]
# keep last 30 dates for safety
dates = dates[-30:]

def compute(sym, ndays):
    recent = dates[-ndays:]
    placeholders = ','.join(['?']*len(recent))
    sql = f"SELECT close FROM daily_kline WHERE symbol=? AND trade_date IN ({placeholders})"
    rows = conn_d.execute(sql, (sym, *recent)).fetchall()
    if not rows:
        return None
    closes = [r[0] for r in rows]
    if min(closes) == 0:
        return None
    return (max(closes) - min(closes)) / min(closes)

result10 = {}
result20 = {}
for sym in symbols:
    e10 = compute(sym, 10)
    e20 = compute(sym, 20)
    if e10 is not None:
        result10[sym] = e10
    if e20 is not None:
        result20[sym] = e20
# sort descending and take top 5
top10 = sorted(result10.items(), key=lambda x: x[1], reverse=True)[:5]
top20 = sorted(result20.items(), key=lambda x: x[1], reverse=True)[:5]
print('TOP10_10DAYS', top10)
print('TOP20_20DAYS', top20)
