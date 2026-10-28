import sys, pandas as pd, json
sys.path.append('.')
import tdx_data
symbol = '002827'
df = tdx_data.fetch_online_tick(symbol)
if df.empty:
    print('No data fetched')
else:
    print(df.tail().to_json(orient='records', force_ascii=False))
