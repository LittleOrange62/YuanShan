import sqlite3

db = r'D:\finance\data\tq_core.db'
conn = sqlite3.connect(db)
cur = conn.cursor()

# 查列名
cur.execute("PRAGMA table_info(stock_info)")
cols = cur.fetchall()
print("=== 列名 ===")
for c in cols:
    print(f"  {c[1]} ({c[2]})")

# 待修正
cur.execute("""
SELECT ts_code, asset_type
FROM stock_info
WHERE
    (ts_code LIKE '880%' OR ts_code LIKE '881%')
    OR (ts_code LIKE '000%' OR ts_code LIKE '399%' OR ts_code LIKE '899%')
    OR (ts_code LIKE '1%' AND (ts_code LIKE '%.SH' OR ts_code LIKE '%.SZ' OR ts_code LIKE '%.BJ'))
    OR (ts_code LIKE '5%' AND (ts_code LIKE '%.SH' OR ts_code LIKE '%.SZ'))
""")
to_update = cur.fetchall()
print(f'\n=== 待修正: {len(to_update)} 行 ===')

# 分组计数
sector = sum(1 for r in to_update if r[0].startswith(('880','881')))
idx = sum(1 for r in to_update if r[0].startswith(('000','399','899')))
bond = sum(1 for r in to_update if r[0].startswith('1') and ('.SH' in r[0] or '.SZ' in r[0] or '.BJ' in r[0]))
fund = sum(1 for r in to_update if r[0].startswith('5') and ('.SH' in r[0] or '.SZ' in r[0]))
print(f'  sector: {sector}')
print(f'  index:  {idx}')
print(f'  bond:   {bond}')
print(f'  fund:   {fund}')

# 执行 UPDATE
count = 0
for code, _ in to_update:
    if code.startswith(('880','881')):
        new_type = 'sector'
    elif code.startswith(('000','399','899')):
        new_type = 'index'
    elif code.startswith('1'):
        new_type = 'bond'
    elif code.startswith('5'):
        new_type = 'fund'
    else:
        continue
    cur.execute("UPDATE stock_info SET asset_type=? WHERE ts_code=?", (new_type, code))
    count += 1

conn.commit()
print(f'\n=== 已更新 {count} 行 ===')

# 修正后统计
cur.execute('SELECT asset_type, COUNT(*) FROM stock_info GROUP BY asset_type ORDER BY COUNT(*) DESC')
print('\n=== 修正后 asset_type 分布 ===')
for r in cur.fetchall():
    print(f'  {r[0]}: {r[1]}')

# 抽样验证
print('\n=== 抽样验证（各类型各3条）===')
for target, pattern in [('sector', '880%'), ('index', '000%'), ('bond', '110%'), ('fund', '501%'), ('stock', '001%')]:
    cur.execute("SELECT ts_code, asset_type FROM stock_info WHERE ts_code LIKE ? ORDER BY ts_code LIMIT 3", (pattern,))
    samples = cur.fetchall()
    status = 'OK' if all(s[1] == target for s in samples) else 'MISMATCH'
    print(f'  [{status}] {target}:')
    for s in samples:
        print(f'    {s[0]:15s}  type={s[1]}')

conn.close()
