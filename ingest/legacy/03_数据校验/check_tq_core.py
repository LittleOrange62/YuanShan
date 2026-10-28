import sqlite3, shutil, os

db = r'D:\finance\data\tq_core.db'
print(f'db exists: {os.path.exists(db)}')

# Check if tq_core.db exists, if not check other locations
if not os.path.exists(db):
    # Try to find it
    data_dir = r'D:\finance\data'
    if os.path.exists(data_dir):
        for f in os.listdir(data_dir):
            if 'tq_core' in f.lower() or 'core' in f.lower():
                print(f'Found: {f}')
    raise FileNotFoundError(f'{db} not found')

conn = sqlite3.connect(db)
cur = conn.cursor()

# List tables
cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
tables = [r[0] for r in cur.fetchall()]
print(f'tables: {tables}')

# Check stock_info count
cur.execute("SELECT COUNT(*) FROM stock_info")
total = cur.fetchone()[0]
print(f'stock_info total count: {total}')

# Current distribution
cur.execute("SELECT asset_type, COUNT(*) FROM stock_info GROUP BY asset_type ORDER BY asset_type")
print('current distribution:')
for r in cur.fetchall():
    print(f'  {r[0]}: {r[1]}')

# Check 000xxx.SZ mislabeled as index
cur.execute("SELECT COUNT(*) FROM stock_info WHERE code LIKE '000%' AND code LIKE '%.SZ' AND asset_type='index'")
sz_idx = cur.fetchone()[0]
print(f'000xxx.SZ mislabeled as index: {sz_idx}')

# 000xxx.SH as index
cur.execute("SELECT COUNT(*) FROM stock_info WHERE code LIKE '000%' AND code LIKE '%.SH' AND asset_type='index'")
sh_idx = cur.fetchone()[0]
print(f'000xxx.SH as index: {sh_idx}')

# 000xxx.SH NOT as index
cur.execute("SELECT COUNT(*) FROM stock_info WHERE code LIKE '000%' AND code LIKE '%.SH' AND asset_type!='index'")
sh_other = cur.fetchone()[0]
print(f'000xxx.SH not index: {sh_other}')

# Verify 000858.SZ
cur.execute("SELECT code, name, asset_type FROM stock_info WHERE code='000858.SZ'")
row = cur.fetchone()
print(f'000858.SZ: {row}')

# Show all 000xxx.SZ mislabeled
cur.execute("SELECT code, name, asset_type FROM stock_info WHERE code LIKE '000%' AND code LIKE '%.SZ' AND asset_type='index'")
print('000xxx.SZ mislabeled as index:')
rows = cur.fetchall()
for r in rows:
    print(f'  {r}')
print(f'  ... total: {len(rows)}')

conn.close()
