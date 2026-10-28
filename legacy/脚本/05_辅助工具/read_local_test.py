import sys
sys.path.append('.')
import tdx_data
symbol = '600036'
print('Reading local daily for', symbol)
df = tdx_data.read_local_daily(symbol)
print('Local shape:', df.shape)
print(df.tail())
