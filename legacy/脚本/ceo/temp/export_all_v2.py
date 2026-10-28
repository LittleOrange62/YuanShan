import sqlite3, subprocess, os, sys

# 2026-08-23 修复：原 DB_PATH 指向 fin_data.db（已空壳，无 stock_basic 表），
# 代码清单改为从 tq_core.db.stock_info 读取（含 SH/SZ 全部 A 股）。
DB_PATH = r'D:/finance/data/tq_core.db'

# Ensure tdx_data.py is in the same workspace directory
TDX_SCRIPT = os.path.join(os.path.dirname(__file__), 'tdx_data.py')

if not os.path.isfile(TDX_SCRIPT):
    print('tdx_data.py not found')
    sys.exit(1)

# 纯 A 股前缀（沪: 600/601/603/605/688/689，深: 000/001/002/003/300/301）
# 排除 B股(900/200)、逆回购(204)、北交所(920)、特殊段(999/810/302)等
SH_PREFIX = ('600', '601', '603', '605', '688', '689')
SZ_PREFIX = ('000', '001', '002', '003', '300', '301')


def get_a_stock_symbols():
    """从 stock_info 读取全部 A 股 ts_code，转为 mootdx 格式(sh600036/sz000001)。"""
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
print(f'Found {len(symbols)} symbols, starting export...')

for i, sym in enumerate(symbols, 1):
    # Run the tdx_data.py daily export with CSV output
    cmd = ['python', TDX_SCRIPT, 'daily', sym, '--csv']
    try:
        subprocess.run(cmd, check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception as e:
        print(f'Error exporting {sym}: {e}')
    if i % 500 == 0:
        print(f'Processed {i}/{len(symbols)}')

print('Export finished')
