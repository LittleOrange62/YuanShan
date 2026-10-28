from mootdx.reader import Reader
r = Reader.factory(market='std', tdxdir='D:/new_tdx64')
print([m for m in dir(r) if not m.startswith('_')])
