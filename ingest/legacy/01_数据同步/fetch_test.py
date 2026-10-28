import sys
sys.path.append('.')
import tdx_data
symbol = '600036'
print('Fetching online bars for', symbol)
df = tdx_data.fetch_online_bars(symbol)
print('Result shape:', df.shape)
print(df.head())
