import akshare as ak, pandas as pd, json

def fetch(date):
    df = ak.stock_margin_detail_szse(date=date)
    if df.empty:
        return []
    df = df[df['证券代码'] == '000988']
    if df.empty:
        return []
    # select relevant columns
    df = df[['证券代码', '融资融券余额']]
    df['date'] = date
    return df.to_dict(orient='records')

# try recent dates (last 7 days)
from datetime import datetime, timedelta
results = []
for i in range(7):
    d = (datetime(2026,8,17) - timedelta(days=i)).strftime('%Y%m%d')
    results.extend(fetch(d))
print(json.dumps(results, ensure_ascii=False, indent=2))
