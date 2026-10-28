import sqlite3

db = r'D:\finance\data\tq_daily_2026.db'
conn = sqlite3.connect(db)
cur = conn.cursor()

# 检查 sector_daily 的 calc_method/source 分布
print('=== sector_daily calc_method 分布 ===')
cur.execute('SELECT calc_method, COUNT(*) FROM sector_daily GROUP BY calc_method')
for r in cur.fetchall():
    print(f'  {r[0]}: {r[1]:,}')

print('\n=== sector_daily source 分布 ===')
cur.execute('SELECT source, COUNT(*) FROM sector_daily GROUP BY source')
for r in cur.fetchall():
    print(f'  {r[0]}: {r[1]:,}')

# 检查 stock_daily 的 source 分布
print('\n=== stock_daily source 分布 ===')
cur.execute('SELECT source, COUNT(*) FROM stock_daily GROUP BY source')
for r in cur.fetchall():
    print(f'  {r[0]}: {r[1]:,}')

# sector 去 calc_method/source 重复
print('\n=== sector 按 sector_code+trade_date（不考虑calc_method/source）重复 ===')
cur.execute("""
    SELECT sector_code, trade_date, COUNT(*) as cnt
    FROM sector_daily
    GROUP BY sector_code, trade_date
    HAVING cnt > 1
    ORDER BY cnt DESC
    LIMIT 10
""")
r = cur.fetchall()
print(f'重复组数: {len(r)}')
if r:
    total = sum(x[-2]-1 for x in r)
    print(f'需删除: {total} 条')

# stock 去 ts_code 重复的 trade_date
print('\n=== stock 按 ts_code 同一天多条 ===')
cur.execute("""
    SELECT ts_code, trade_date, COUNT(*) as cnt
    FROM stock_daily
    GROUP BY ts_code, trade_date
    HAVING cnt > 1
    ORDER BY cnt DESC
    LIMIT 5
""")
r = cur.fetchall()
print(f'重复组数: {len(r)}')

# sector 重复统计
cur.execute("SELECT COUNT(*) FROM sector_daily")
total_sec = cur.fetchone()[0]
cur.execute("SELECT COUNT(DISTINCT sector_code || '|' || trade_date) FROM sector_daily")
unique_sec = cur.fetchone()[0]
print(f'\nsector 总行数: {total_sec:,}, 唯一(sector_code,trade_date): {unique_sec:,}')
print(f'sector 重复数: {total_sec - unique_sec:,}')

# stock 同理
cur.execute("SELECT COUNT(*) FROM stock_daily")
total_stk = cur.fetchone()[0]
cur.execute("SELECT COUNT(DISTINCT ts_code || '|' || trade_date) FROM stock_daily")
unique_stk = cur.fetchone()[0]
print(f'stock 总行数: {total_stk:,}, 唯一(ts_code,trade_date): {unique_stk:,}')
print(f'stock 重复数: {total_stk - unique_stk:,}')

conn.close()
