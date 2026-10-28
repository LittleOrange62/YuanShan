from mootdx.reader import Reader
r = Reader.factory(market='std', tdxdir='D:/new_tdx64')
try:
    symbols = r.symbols()
    print('Symbols type:', type(symbols))
    if hasattr(symbols, 'head'):
        print(symbols.head())
    else:
        print(list(symbols)[:10])
except Exception as e:
    print('Error:', e)
