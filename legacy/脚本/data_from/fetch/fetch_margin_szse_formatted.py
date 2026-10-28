import akshare as ak
from datetime import datetime, timedelta
import json

def fetch(date):
    df = ak.stock_margin_detail_szse(date=date)
    if df.empty:
        return None
    df = df[df['证券代码'] == '000988']
    if df.empty:
        return None
    balance = df.iloc[0]['融资融券余额']
    # convert to 亿元 (1亿元=100,000,000) and 保留两位小数
    balance_yi = round(balance / 1e8, 2)
    return {'date': date, '融资融券余额(亿元)': balance_yi}

results = []
for i in range(7):
    d = (datetime(2026,8,17) - timedelta(days=i)).strftime('%Y%m%d')
    r = fetch(d)
    if r:
        results.append(r)

print(json.dumps(results, ensure_ascii=False, indent=2))
