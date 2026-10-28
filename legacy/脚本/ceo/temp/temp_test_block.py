from mootdx.reader import Reader
r = Reader.factory(market='std', tdxdir='D:/new_tdx64')
df = r.block_new()
print(df.head())
print(df.columns)
