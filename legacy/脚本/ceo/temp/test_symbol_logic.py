import sqlite3

DB_PATH = r'D:/finance/data/tq_core.db'
SH_PREFIX = ('600', '601', '603', '605', '688', '689')
SZ_PREFIX = ('000', '001', '002', '003', '300', '301')

def get_a_stock_symbols():
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    cur.execute("SELECT ts_code FROM stock_info WHERE asset_type='stock'")
    rows = [r[0] for r in cur.fetchall()]
    con.close()
    symbols = []
    for ts in rows:
        code, market = ts.split('.')
        if code.startswith(SH_PREFIX) and market == 'SH':
            symbols.append(f'sh{code}')
        elif code.startswith(SZ_PREFIX) and market == 'SZ':
            symbols.append(f'sz{code}')
    return symbols

symbols = get_a_stock_symbols()
print(f'A股 symbol 总数: {len(symbols)}')
sh = [s for s in symbols if s.startswith('sh')]
sz = [s for s in symbols if s.startswith('sz')]
print(f'沪: {len(sh)} 深: {len(sz)}')
print('SH sample:', sh[:5])
print('SZ sample:', sz[:5])
# 检查排除是否正确
excluded = {'204', '900', '200', '920', '999', '810', '302'}
bad = [s for s in symbols if s[2:5] in excluded]
print(f'异常残留(应为0): {len(bad)}')
