import sqlite3

db=r'D:\finance\data\tq_daily_2026.db'
conn=sqlite3.connect(db)
cur=conn.cursor()

print('top ts_code counts:')
cur.execute('select ts_code,count(*) c from stock_daily group by ts_code order by c desc limit 20')
for r in cur.fetchall():
    print(r)

cur.execute('select count(distinct ts_code) from stock_daily')
print('distinct ts_code', cur.fetchone()[0])

cur.execute('select ts_code, trade_date, name from stock_daily limit 20')
print('sample with name:')
for r in cur.fetchall():
    print(r)

conn.close()
