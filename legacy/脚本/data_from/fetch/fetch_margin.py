import json, tushare as ts
# Token from .env
TOKEN = "7bed30ebabc1106da6d346f0b8a9351085cf3442cf2616763ae07bde"
pro = ts.pro_api(TOKEN)
# Fetch recent 30 days (20260701-20260817)
df = pro.margin_detail(ts_code='000988.SZ', start_date='20260701', end_date='20260817')
if not df.empty:
    df = df[['trade_date', 'rzye', 'rqye']]
    df = df.sort_values('trade_date', ascending=False)
    data = df.head(5).to_dict(orient='records')
else:
    data = []
print(json.dumps(data, ensure_ascii=False, indent=2))
