import sqlite3

conn = sqlite3.connect(r'D:\finance\data\tq_daily_2026.db')
cur = conn.cursor()

print('=== 验证 .SZ 已写入 stock_daily ===')
cur.execute("select count(*) from stock_daily where ts_code like '%.SZ'")
print('stock_daily .SZ 总行数:', cur.fetchone()[0])

print()
print('=== 抽样 000001.SZ / 000858.SZ / 300750.SZ ===')
for code in ['000001.SZ', '000858.SZ', '300750.SZ']:
    cur.execute("select ts_code, trade_date, open, close, vol, source from stock_daily where ts_code=? order by trade_date limit 3", (code,))
    rows = cur.fetchall()
    print(f'{code}:')
    for r in rows:
        print(f'  {r}')
    cur.execute("select max(trade_date) from stock_daily where ts_code=?", (code,))
    print(f'  最新日期: {cur.fetchone()[0]}')

print()
print('=== 后缀分布 ===')
cur.execute("select substr(ts_code,-3) as suf, count(*) from stock_daily group by suf")
for r in cur.fetchall():
    print(r)

conn.close()
