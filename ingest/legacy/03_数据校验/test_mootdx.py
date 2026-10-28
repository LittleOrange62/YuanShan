import sys
try:
    from mootdx.reader import Reader
    r = Reader.factory(market='std', tdxdir='D:/new_tdx64')
    df = r.daily('600036')
    print('Columns:', list(df.columns))
    print(df.head())
except Exception as e:
    print('Error:', e)
