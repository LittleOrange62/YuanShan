import sqlite3, pandas as pd, os, sys
watchlist_db = r'D:\finance\data\fin_watchlist.db'
data_db = r'D:\finance\data\fin_data_2026.db'
# load symbols
conn_w = sqlite3.connect(watchlist_db)
symbols = [row[0] for row in conn_w.execute('SELECT symbol FROM watchlist_stocks').fetchall()]
conn_w.close()
conn_d = sqlite3.connect(data_db)
# get recent distinct dates sorted asc
dates_df = pd.read_sql_query('SELECT DISTINCT trade_date FROM daily_kline ORDER BY trade_date ASC', conn_d)
# normalize dates to yyyymmdd string without hyphens
def norm(d):
    s = str(d)
    return s.replace('-', '')
dates = [norm(d) for d in dates_df['trade_date']]
# keep only last 30 dates as safety
dates = dates[-30:]

def compute(symbol, ndays):
    recent = dates[-ndays:]
    placeholders = ','.join(['?']*len(recent))
    sql = f"SELECT trade_date, close FROM daily_kline WHERE symbol=? AND trade_date IN ({placeholders})"
    rows = conn_d.execute(sql, (symbol, *recent)).fetchall()
    if not rows:
        return None
    closes = [r[1] for r in rows]
    if not closes or min(closes) == 0:
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
# sort descending
top10 = sorted(result10.items(), key=lambda x: x[1], reverse=True)[:5]
top20 = sorted(result20.items(), key=lambda x: x[1], reverse=True)[:5]
print('TOP10_10DAYS', top10)
print('TOP20_20DAYS', top20)
