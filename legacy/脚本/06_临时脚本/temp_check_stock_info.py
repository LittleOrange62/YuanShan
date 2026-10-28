import sqlite3

db = r'D:\finance\data\tq_core.db'
conn = sqlite3.connect(db)
cur = conn.cursor()

# 1. 备份表
cur.execute('CREATE TABLE IF NOT EXISTS stock_info_bak_20260823 AS SELECT * FROM stock_info')
conn.commit()
print('=== 备份完成: stock_info_bak_20260823 ===')

# 2. 总量
cur.execute('SELECT COUNT(*) FROM stock_info')
total = cur.fetchone()[0]
print(f'\n=== 当前 stock_info 总量: {total} ===')

# 3. 按规则粗分统计
cur.execute("""
SELECT
    ts_code,
    CASE
        WHEN ts_code LIKE '880%' OR ts_code LIKE '881%' THEN 'sector'
        WHEN ts_code LIKE '000%' OR ts_code LIKE '399%' OR ts_code LIKE '899%' THEN 'index'
        WHEN ts_code LIKE '1%' AND (ts_code LIKE '%.SH' OR ts_code LIKE '%.SZ' OR ts_code LIKE '%.BJ') THEN 'bond'
        WHEN ts_code LIKE '5%' AND (ts_code LIKE '%.SH' OR ts_code LIKE '%.SZ') THEN 'fund'
        WHEN ts_code LIKE '%.SH' OR ts_code LIKE '%.SZ' OR ts_code LIKE '%.BJ' THEN 'stock'
        ELSE 'unknown'
    END AS category
FROM stock_info
""")
from collections import Counter
rows = cur.fetchall()
cats = Counter(r[1] for r in rows)
print('\n=== 修正前分类统计 ===')
for cat, cnt in cats.most_common():
    print(f'  {cat}: {cnt}')

# 抽样：各类型各取5条
print('\n=== 各类型抽样 ===')
for target in ['sector', 'index', 'bond', 'fund', 'stock', 'unknown']:
    codes = [r[0] for r in rows if r[1] == target]
    sample = codes[:5] if len(codes) >= 5 else codes
    print(f'  {target}: {sample}')

# 当前 asset_type 分布
print('\n=== 当前 asset_type 分布 ===')
cur.execute('SELECT asset_type, COUNT(*) FROM stock_info GROUP BY asset_type')
for r in cur.fetchall():
    print(f'  {r[0]}: {r[1]}')

# 待修正的行
cur.execute("""
SELECT ts_code, ts_name, asset_type
FROM stock_info
WHERE
    (ts_code LIKE '880%' OR ts_code LIKE '881%')
    OR (ts_code LIKE '000%' OR ts_code LIKE '399%' OR ts_code LIKE '899%')
    OR (ts_code LIKE '1%' AND (ts_code LIKE '%.SH' OR ts_code LIKE '%.SZ' OR ts_code LIKE '%.BJ'))
    OR (ts_code LIKE '5%' AND (ts_code LIKE '%.SH' OR ts_code LIKE '%.SZ'))
""")
to_update = cur.fetchall()
print(f'\n=== 待修正记录数: {len(to_update)} ===')
for r in to_update[:10]:
    print(f'  {r[0]:15s}  {r[1]:30s}  old_type={r[2]}')

conn.close()
