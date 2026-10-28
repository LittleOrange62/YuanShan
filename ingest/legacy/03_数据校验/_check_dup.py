import sqlite3
from datetime import datetime

db = r'D:\finance\data\tq_daily_2026.db'
conn = sqlite3.connect(db)
cur = conn.cursor()

print('='*60)
print('  stock_daily 重复分析')
print('='*60)

# 查 stock_daily 总行数
cur.execute('SELECT COUNT(*) FROM stock_daily')
total_before = cur.fetchone()[0]
print(f'stock_daily 总行数（清理前）: {total_before:,}')

# 查重复分组（按 ts_code + trade_date 分组，有重复的）
cur.execute("""
    SELECT ts_code, trade_date, COUNT(*) as cnt
    FROM stock_daily
    GROUP BY ts_code, trade_date
    HAVING cnt > 1
    ORDER BY cnt DESC, ts_code
""")
dup_groups = cur.fetchall()
print(f'\n重复分组数: {len(dup_groups)}')
print(f'重复分组详情（前10组）:')
for g in dup_groups[:10]:
    print(f'  {g[0]} | {g[1]} | 共 {g[2]} 条')

# 计算需要删除的条数
cur.execute("""
    SELECT SUM(cnt - 1) FROM (
        SELECT COUNT(*) as cnt
        FROM stock_daily
        GROUP BY ts_code, trade_date
        HAVING cnt > 1
    )
""")
to_delete_stock = cur.fetchone()[0]
print(f'\nstock_daily 需要删除: {to_delete_stock} 条')
print(f'预计剩余: {total_before - to_delete_stock:,} 条')

# 预览将要删除的记录（每组保留 updated_at 最大的）
cur.execute("""
    SELECT ts_code, trade_date, COUNT(*) as cnt, MAX(updated_at) as max_updated
    FROM stock_daily
    GROUP BY ts_code, trade_date
    HAVING cnt > 1
    LIMIT 3
""")
print('\n示例重复组（保留 updated_at 最大的）:')
for g in cur.fetchall():
    print(f'  {g[0]} | {g[1]} | 共 {g[2]} 条, 最大 updated_at={g[3]}')

print('\n' + '='*60)
print('  sector_daily 重复分析')
print('='*60)

# 查 sector_daily 总行数
cur.execute('SELECT COUNT(*) FROM sector_daily')
total_sector_before = cur.fetchone()[0]
print(f'sector_daily 总行数（清理前）: {total_sector_before:,}')

# 查 sector_daily 重复
cur.execute("""
    SELECT sector_code, trade_date, calc_method, source, COUNT(*) as cnt
    FROM sector_daily
    GROUP BY sector_code, trade_date, calc_method, source
    HAVING cnt > 1
    ORDER BY cnt DESC, sector_code
""")
dup_sector = cur.fetchall()
print(f'\nsector_daily 重复分组数: {len(dup_sector)}')
for g in dup_sector[:10]:
    print(f'  {g[0]} | {g[1]} | calc={g[2]}, src={g[3]} | 共 {g[4]} 条')

if dup_sector:
    cur.execute("""
        SELECT SUM(cnt - 1) FROM (
            SELECT COUNT(*) as cnt
            FROM sector_daily
            GROUP BY sector_code, trade_date, calc_method, source
            HAVING cnt > 1
        )
    """)
    to_delete_sector = cur.fetchone()[0]
    print(f'\nsector_daily 需要删除: {to_delete_sector} 条')
    print(f'预计剩余: {total_sector_before - to_delete_sector:,} 条')
else:
    to_delete_sector = 0

# 预览 sector 重复
if dup_sector:
    cur.execute("""
        SELECT sector_code, trade_date, calc_method, source, COUNT(*) as cnt, MAX(updated_at) as max_updated
        FROM sector_daily
        GROUP BY sector_code, trade_date, calc_method, source
        HAVING cnt > 1
        LIMIT 3
    """)
    print('\n示例 sector 重复组:')
    for g in cur.fetchall():
        print(f'  {g[0]} | {g[1]} | calc={g[2]}, src={g[3]} | 共 {g[4]} 条, 最大 updated_at={g[5]}')

print('\n' + '='*60)
print(f'汇总: stock_daily 待删 {to_delete_stock} | sector_daily 待删 {to_delete_sector}')
print('='*60)

conn.close()
