import sqlite3

db = r'D:\finance\data\tq_daily_2026.db'
conn = sqlite3.connect(db)
cur = conn.cursor()

# 验证：000858.SH 是否是五粮液（真实深市股票 000858.SZ 被标成 SH）
print('=== 疑似深市股票被标成 .SH 的验证 ===')
for code in ['000001', '000002', '000858', '300750', '002594', '600519']:
    for suf in ['.SH', '.SZ']:
        cur.execute('select ts_code, count(*), min(trade_date), max(trade_date), round(avg(close),2) from stock_daily where ts_code=?', (code+suf,))
        r = cur.fetchone()
        if r and r[1] > 0:
            print(f'{code+suf}: 行数={r[1]} 日期[{r[2]}~{r[3]}] avg_close={r[4]}')

print()
print('=== stock_daily 各代码前缀分布 ===')
cur.execute("select substr(ts_code,1,3) as pre, count(*) from stock_daily group by pre order by 2 desc limit 20")
for r in cur.fetchall():
    print(r)

conn.close()
