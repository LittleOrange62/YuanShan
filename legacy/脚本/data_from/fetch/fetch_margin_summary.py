import json, tushare as ts
TOKEN = "7bed30ebabc1106da6d346f0b8a9351085cf3442cf2616763ae07bde"
pro = ts.pro_api(TOKEN)
# margin summary for a stock
df = pro.margin(ts_code='000988.SZ')
if not df.empty:
    df = df[['trade_date', 'rzye', 'rqye']]
    df = df.sort_values('trade_date', ascending=False)
    data = df.head(5).to_dict(orient='records')
else:
    data = []
print(json.dumps(data, ensure_ascii=False, indent=2))
