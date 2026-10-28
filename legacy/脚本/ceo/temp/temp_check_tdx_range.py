from mootdx.reader import Reader
r = Reader.factory(market='std', tdxdir='D:/new_tdx64')
df = r.daily('sh000001')
print('sh000001 date range:', df.index.min(), 'to', df.index.max())
print('Total rows:', len(df))

# 检查几只股票的日期范围
for code in ['600519.SH', '000858.SZ', '300750.SZ']:
    df2 = r.daily(code.split('.')[0])
    if df2 is not None and not df2.empty:
        print(f'{code}: {df2.index.min()} to {df2.index.max()}, rows={len(df2)}')
    else:
        print(f'{code}: no data')
