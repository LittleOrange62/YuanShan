import akshare as ak, json
# fetch recent data for 000988
# start_date and end_date format YYYYMMDD
df = ak.stock_margin_detail(symbol='000988', start_date='20260701', end_date='20260817')
if not df.empty:
    # columns: 日期, 融资余量, 融券余量, 融资占比, 融券占比, 融资买入额, 融资偿还额, 融券买入额, 融券偿还额
    df = df[['日期', '融资余量', '融券余量']]
    df = df.sort_values('日期', ascending=False)
    data = df.head(5).to_dict(orient='records')
else:
    data = []
print(json.dumps(data, ensure_ascii=False, indent=2))
