import sys, pandas as pd, json
sys.path.append('.')
import tdx_data
symbol = '002827'
# Get merged daily data
df = tdx_data.merge_latest(symbol)
if df.empty:
    print('No daily data')
else:
    # show last 5 rows
    print(df.tail().to_json(orient='records', force_ascii=False))
