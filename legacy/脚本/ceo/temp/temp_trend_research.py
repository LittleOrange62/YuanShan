import sqlite3
import pandas as pd
from mootdx.reader import Reader
from datetime import datetime

r = Reader.factory(market='std', tdxdir='D:/new_tdx64')
D = r'D:\finance\data'

# 读取沪深300和中证1000日线
df_hs300 = r.daily('sh000300')
df_zz1000 = r.daily('sh000852')

if df_hs300 is None or df_zz1000 is None or df_hs300.empty or df_zz1000.empty:
    print('Error: failed to fetch index data')
    exit(1)

# 合并
df = pd.DataFrame({
    'trade_date': df_hs300.index.strftime('%Y%m%d'),
    'hs300_close': df_hs300['close'].values,
    'zz1000_close': df_zz1000['close'].values
})

# 计算分化指标
df['divergence'] = df['zz1000_close'] - df['hs300_close']
df['divergence_pct'] = (df['divergence'] / df['hs300_close']) * 100

# 计算均线
for w in [5, 10, 20]:
    df[f'ma{w}_divergence'] = df['divergence'].rolling(window=w).mean()

# 计算趋势斜率（简单线性回归斜率，用最近N日）
def calc_slope(series, n):
    if len(series) < n:
        return None
    y = series[-n:].values
    x = list(range(n))
    x_mean = sum(x) / n
    y_mean = y.mean()
    numerator = sum((x[i] - x_mean) * (y[i] - y_mean) for i in range(n))
    denominator = sum((x[i] - x_mean) ** 2 for i in range(n))
    if denominator == 0:
        return 0
    return numerator / denominator

slopes = {'slope_5d': 5, 'slope_10d': 10, 'slope_20d': 20}
slope_values = {k: [] for k in slopes}

for i in range(len(df)):
    for k, n in slopes.items():
        if i + 1 >= n:
            slope_values[k].append(calc_slope(df['divergence'].iloc[:i+1], n))
        else:
            slope_values[k].append(None)

for k, vals in slope_values.items():
    df[k] = vals

# 趋势信号
def signal(row):
    if pd.isna(row['slope_5d']) or pd.isna(row['slope_10d']):
        return '数据不足'
    if row['divergence'] > 0 and row['slope_5d'] > 0 and row['slope_10d'] > 0:
        return '小盘强势'
    elif row['divergence'] < 0 and row['slope_5d'] < 0 and row['slope_10d'] < 0:
        return '大盘强势'
    elif abs(row['slope_5d']) < abs(row['slope_10d']):
        return '分化收敛'
    else:
        return '分化加剧'

df['signal'] = df.apply(signal, axis=1)

# 写入数据库
conn = sqlite3.connect(f'{D}/trend_research.db')
cur = conn.cursor()

for _, row in df.iterrows():
    cur.execute('''
        INSERT OR REPLACE INTO size_divergence 
        (trade_date, zz1000_close, hs300_close, divergence, divergence_pct, ma5_divergence, ma10_divergence, ma20_divergence, slope_5d, slope_10d, slope_20d, signal, source, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ''', (
        row['trade_date'], row['zz1000_close'], row['hs300_close'], row['divergence'], row['divergence_pct'],
        row.get('ma5_divergence'), row.get('ma10_divergence'), row.get('ma20_divergence'),
        row.get('slope_5d'), row.get('slope_10d'), row.get('slope_20d'), row['signal'],
        'tq', datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    ))

conn.commit()
conn.close()
print(f'Written {len(df)} rows to size_divergence.')
print(df.tail(10)[['trade_date', 'zz1000_close', 'hs300_close', 'divergence', 'signal']])
