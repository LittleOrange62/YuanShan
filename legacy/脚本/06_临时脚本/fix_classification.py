import sqlite3, shutil

db = r'D:\finance\data\tq_core.db'
conn = sqlite3.connect(db)
cur = conn.cursor()

# 1. Backup current stock_info
cur.execute("DROP TABLE IF EXISTS stock_info_bak_20260823_fix")
cur.execute("CREATE TABLE stock_info_bak_20260823_fix AS SELECT * FROM stock_info")
conn.commit()
print("[1] Backup done: stock_info_bak_20260823_fix created")

# 2. Check distribution before fix
cur.execute("SELECT asset_type, COUNT(*) FROM stock_info GROUP BY asset_type ORDER BY asset_type")
print("\n[2] Distribution before fix:")
for r in cur.fetchall():
    print(f"  {r[0]}: {r[1]}")

# 3. Count 000xxx.SZ mislabeled as index
cur.execute("""SELECT COUNT(*) FROM stock_info
    WHERE ts_code LIKE '000%.SZ' AND asset_type='index'""")
sz_mis = cur.fetchone()[0]
print(f"\n[3] 000xxx.SZ mislabeled as index: {sz_mis}")

# 4. Fix: change 000xxx.SZ from index to stock
cur.execute("""UPDATE stock_info SET asset_type='stock'
    WHERE ts_code LIKE '000%.SZ' AND asset_type='index'""")
print(f"[4] Updated rows: {cur.rowcount}")
conn.commit()

# 5. Verify 000858.SZ
cur.execute("SELECT ts_code, name, asset_type FROM stock_info WHERE ts_code='000858.SZ'")
row = cur.fetchone()
print(f"\n[5] 000858.SZ verification: {row}")

# 6. Total count after fix
cur.execute("SELECT COUNT(*) FROM stock_info")
total = cur.fetchone()[0]
print(f"[6] Total count after fix: {total}")

# 7. Distribution after fix
cur.execute("SELECT asset_type, COUNT(*) FROM stock_info GROUP BY asset_type ORDER BY asset_type")
print("\n[7] Distribution after fix:")
for r in cur.fetchall():
    print(f"  {r[0]}: {r[1]}")

# 8. Verify no more 000xxx.SZ as index
cur.execute("""SELECT COUNT(*) FROM stock_info
    WHERE ts_code LIKE '000%.SZ' AND asset_type='index'""")
left = cur.fetchone()[0]
print(f"\n[8] 000xxx.SZ still as index after fix: {left}")

conn.close()
print("\nDone.")
