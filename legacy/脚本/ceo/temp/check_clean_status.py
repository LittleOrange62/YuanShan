import sqlite3

db = r'D:\finance\data\tq_daily_2026.db'
conn = sqlite3.connect(db)
cur = conn.cursor()

print('=== stock_daily 现状 ===')
cur.execute('select count(*) from stock_daily')
print('总行数:', cur.fetchone()[0])
cur.execute("select count(*) from stock_daily where ts_code like '880%'")
print('880xxx 残留:', cur.fetchone()[0])
cur.execute("select substr(ts_code,-2) as suf, count(*) from stock_daily group by suf")
print('后缀分布:', cur.fetchall())

print()
print('=== 所有表（含备份）===')
cur.execute("select name from sqlite_master where type='table'")
print([r[0] for r in cur.fetchall()])

print()
print('=== tq_core.db 表 ===')
conn2 = sqlite3.connect(r'D:\finance\data\tq_core.db')
cur2 = conn2.cursor()
cur2.execute("select name from sqlite_master where type='table'")
print([r[0] for r in cur2.fetchall()])

conn.close()
conn2.close()
