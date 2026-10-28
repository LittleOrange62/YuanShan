import sqlite3, subprocess, os, sys

DB_PATH = r'D:/finance/data/fin_data.db'
TDX_SCRIPT = os.path.join(os.path.dirname(__file__), 'tdx_data.py')

if not os.path.isfile(TDX_SCRIPT):
    sys.exit('tdx_data.py not found')

con = sqlite3.connect(DB_PATH)
cur = con.cursor()
cur.execute("SELECT symbol FROM stock_basic")
symbols = [row[0] for row in cur.fetchall()]
print(f'Found {len(symbols)} symbols, starting incremental export...')

for i, sym in enumerate(symbols, 1):
    cmd = ['python', TDX_SCRIPT, 'daily', sym, '--csv']
    subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if i % 500 == 0:
        print(f'Processed {i}/{len(symbols)}')
print('Incremental export finished')
