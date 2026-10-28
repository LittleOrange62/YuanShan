import sqlite3

# 1. 验证 stock_daily 中 880xxx 是否与 sector_daily 重复
db1 = r'D:\finance\data\tq_daily_2026.db'
conn = sqlite3.connect(db1)
cur = conn.cursor()

print('=== stock_daily 中 880xxx 概况 ===')
cur.execute("select count(*) from stock_daily where ts_code like '880%'")
print('stock_daily 880xxx 行数:', cur.fetchone()[0])
cur.execute("select distinct ts_code from stock_daily where ts_code like '880%' order by ts_code limit 10")
print('880xxx 代码样本:', [r[0] for r in cur.fetchall()])

print()
print('=== sector_daily 中 TDX_880xxx 概况 ===')
cur.execute("select count(*) from sector_daily where sector_code like 'TDX_880%'")
print('sector_daily TDX_880xxx 行数:', cur.fetchone()[0])
cur.execute("select count(distinct sector_code) from sector_daily where sector_code like 'TDX_880%'")
print('sector_daily TDX_880xxx 板块数:', cur.fetchone()[0])

print()
print('=== 对比：stock_daily 的 880xxx 是否都能在 sector_daily 找到 ===')
cur.execute("""
select count(distinct s.ts_code) 
from (select distinct ts_code from stock_daily where ts_code like '880%') s
left join (select distinct substr(sector_code, 5) as code from sector_daily where sector_code like 'TDX_880%') sd
on s.ts_code = sd.code
where sd.code is null
""")
print('stock_daily 中 880xxx 但 sector_daily 没有的代码数:', cur.fetchone()[0])

print()
print('=== 其他重复检查：stock_daily 中 fund/bond 类 ===')
cur.execute("select count(*) from stock_daily where ts_code like '5%'")
print('stock_daily 5开头(fund类)行数:', cur.fetchone()[0])
cur.execute("select count(*) from stock_daily where ts_code like '11%' or ts_code like '12%'")
print('stock_daily 11/12开头(bond类)行数:', cur.fetchone()[0])

# 2. 确认 .SZ 是否真的缺失
print()
print('=== stock_daily 后缀分布 ===')
cur.execute("select substr(ts_code, -3) as suf, count(*) from stock_daily group by suf order by 2 desc")
for r in cur.fetchall():
    print(r)

# 3. 备份表情况
print()
print('=== tq_core.db 备份表 ===')
conn2 = sqlite3.connect(r'D:\finance\data\tq_core.db')
cur2 = conn2.cursor()
cur2.execute("select name from sqlite_master where type='table' and name like '%bak%'")
print('tq_core bak tables:', [r[0] for r in cur2.fetchall()])

print()
print('=== tq_daily_2026.db 备份表 ===')
cur.execute("select name from sqlite_master where type='table' and name like '%bak%'")
print('tq_daily bak tables:', [r[0] for r in cur.fetchall()])

conn.close()
conn2.close()
