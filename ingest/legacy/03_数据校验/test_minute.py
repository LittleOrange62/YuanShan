from mootdx.reader import Reader
r = Reader.factory(market='std', tdxdir='D:/new_tdx64')
try:
    df = r.minute('600036')
    print('Minute columns:', list(df.columns))
    print(df.head())
except Exception as e:
    print('Error:', e)
