import sqlite3

db = r'D:\finance\data\tq_daily_2026.db'
conn = sqlite3.connect(db)
cur = conn.cursor()

# 修正对比：stock_daily 的 ts_code 是 '880001.SH'，sector_daily 的 sector_code 是 'TDX_880001'
# 提取 880xxx 的 6 位数字部分对比
print('=== stock_daily 880xxx 代码总数（去重） ===')
cur.execute("select count(distinct ts_code) from stock_daily where ts_code like '880%'")
print('distinct:', cur.fetchone()[0])

print()
print('=== stock_daily 880xxx 是否都能在 sector_daily TDX_880xxx 找到 ===')
cur.execute("""
select count(*)
from (select distinct replace(ts_code, '.SH', '') as code from stock_daily where ts_code like '880%') s
left join (select distinct substr(sector_code, 5) as code from sector_daily where sector_code like 'TDX_880%') sd
on s.code = sd.code
where sd.code is null
""")
print('sector_daily 中没有的 880xxx 数量:', cur.fetchone()[0])

print()
print('=== stock_daily 880xxx 日期范围 ===')
cur.execute("select min(trade_date), max(trade_date) from stock_daily where ts_code like '880%'")
print(cur.fetchone())

print()
print('=== sector_daily TDX_880xxx 日期范围 ===')
cur.execute("select min(trade_date), max(trade_date) from sector_daily where sector_code like 'TDX_880%'")
print(cur.fetchone())

print()
print('=== 抽样对比同一板块同一日 ===')
cur.execute("""
select s.ts_code, s.trade_date, s.close as sd_close, sd.close as sdd_close
from stock_daily s
left join sector_daily sd on sd.sector_code = 'TDX_' || replace(s.ts_code, '.SH', '') and sd.trade_date = s.trade_date
where s.ts_code like '880%'
limit 10
""")
for r in cur.fetchall():
    print(r)

conn.close()
