import sqlite3

db = r'D:\finance\data\tq_daily_2026.db'
conn = sqlite3.connect(db)
cur = conn.cursor()

print('='*60)
print('  stock_daily 完全重复行分析')
print('='*60)

# 统计总行数
cur.execute('SELECT COUNT(*) FROM stock_daily')
total_before = cur.fetchone()[0]
print(f'stock_daily 总行数: {total_before:,}')

# 找完全重复的行（所有字段相同）
# 方法：按所有列分组，看哪些组有多条
cols = 'ts_code,trade_date,open,high,low,close,pre_close,change,pct_change,vol,amount,turnover_rate,total_mv,float_mv,total_share,float_share,adj_factor,limit_up,limit_down,source,updated_at'
cur.execute(f"""
    SELECT {cols}, COUNT(*) as cnt
    FROM stock_daily
    GROUP BY {cols}
    HAVING cnt > 1
    ORDER BY cnt DESC
    LIMIT 10
""")
dup_rows = cur.fetchall()
print(f'\n完全重复行数: {len(dup_rows)} 组')

if dup_rows:
    total_dups = sum(r[-1]-1 for r in dup_rows)
    print(f'共需删除: {total_dups} 条')
    print(f'示例（前3组）:')
    for r in dup_rows[:3]:
        print(f'  {r[0]} | {r[1]} | 重复 {r[-1]} 次')
else:
    print('无完全重复行')

# 也检查按 ts_code+trade_date 的重复（可能 updated_at 不同）
print('\n' + '-'*60)
print('  stock_daily 按 ts_code+trade_date 检查（updated_at 可能不同）')
print('-'*60)

cur.execute("""
    SELECT ts_code, trade_date, COUNT(*) as cnt, COUNT(DISTINCT updated_at) as dts
    FROM stock_daily
    GROUP BY ts_code, trade_date
    HAVING cnt > 1
    ORDER BY cnt DESC
    LIMIT 10
""")
dup_ts = cur.fetchall()
print(f'重复分组: {len(dup_ts)} 组')
if dup_ts:
    total_ts_dups = sum(r[-2]-1 for r in dup_ts)
    print(f'共需删除: {total_ts_dups} 条')
    for r in dup_ts[:5]:
        print(f'  {r[0]} | {r[1]} | {r[2]} 条, {r[3]} 个不同 updated_at')
else:
    print('无重复分组')

# sector_daily
print('\n' + '='*60)
print('  sector_daily 重复分析')
print('='*60)

cur.execute('SELECT COUNT(*) FROM sector_daily')
sector_total = cur.fetchone()[0]
print(f'sector_daily 总行数: {sector_total:,}')

# 完全重复
cols_sec = 'sector_code,trade_date,sector_name,open,high,low,close,pre_close,change,pct_change,vol,amount,member_count,up_count,down_count,flat_count,calc_method,source,updated_at'
cur.execute(f"""
    SELECT {cols_sec}, COUNT(*) as cnt
    FROM sector_daily
    GROUP BY {cols_sec}
    HAVING cnt > 1
    ORDER BY cnt DESC
    LIMIT 10
""")
sec_dup = cur.fetchall()
print(f'\nsector_daily 完全重复行数: {len(sec_dup)} 组')
if sec_dup:
    total_sec_dups = sum(r[-1]-1 for r in sec_dup)
    print(f'共需删除: {total_sec_dups} 条')
    for r in sec_dup[:5]:
        print(f'  {r[0]} | {r[1]} | 重复 {r[-1]} 次')

# 按 sector_code+trade_date 检查
print('\n' + '-'*60)
print('  sector_daily 按 sector_code+trade_date 检查')
print('-'*60)
cur.execute("""
    SELECT sector_code, trade_date, calc_method, source, COUNT(*) as cnt
    FROM sector_daily
    GROUP BY sector_code, trade_date, calc_method, source
    HAVING cnt > 1
    ORDER BY cnt DESC
    LIMIT 10
""")
sec_ts = cur.fetchall()
print(f'按 sector_code+trade_date+calc_method+source 重复分组: {len(sec_ts)} 组')

print('\n' + '='*60)

conn.close()
