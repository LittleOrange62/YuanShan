import sqlite3, subprocess, os, sys

# 2026-08-23 修复：原 DB_PATH 指向 fin_data.db（已空壳，无 stock_basic 表），
# 代码清单改为从 tq_core.db.stock_info 读取（含 SH/SZ 全部 A 股）。
DB_PATH = r'D:/finance/data/tq_core.db'
TDX_SCRIPT = os.path.join(os.path.dirname(__file__), 'tdx_data.py')

if not os.path.isfile(TDX_SCRIPT):
    sys.exit('tdx_data.py not found')

# 纯 A 股前缀（沪: 600/601/603/605/688/689，深: 000/001/002/003/300/301）
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


# 读取全部 A 股 symbols
symbols = get_a_stock_symbols()

# 参数: start index (0‑based) and count
if len(sys.argv) != 3:
    print('Usage: incremental_batch.py <start> <count>')
    sys.exit(1)
start = int(sys.argv[1])
count = int(sys.argv[2])
subset = symbols[start:start+count]
print(f'Processing symbols {start}..{start+len(subset)-1} ({len(subset)} items)')

for i, sym in enumerate(subset, start):
    # tdx_data expects lower‑case symbol like sh000001, so convert
    sym_lower = sym.lower()
    cmd = ['python', TDX_SCRIPT, 'daily', sym_lower, '--csv']
    subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if (i+1) % 100 == 0:
        print(f'  processed {i+1}')
print('Batch finished')
