import sqlite3
db=r'D:\finance\data\tq_core.db'
conn=sqlite3.connect(db)
cur=conn.cursor()
cur.execute("select name from sqlite_master where type='table'")
print('tables:', [r[0] for r in cur.fetchall()])
try:
    cur.execute("select asset_type, count(*) from stock_info group by asset_type")
    print('asset_type dist:', cur.fetchall())
except Exception as e:
    print('err', e)
try:
    cur.execute("select count(*) from stock_info where ts_code like '88%'")
    print('880 count:', cur.fetchone()[0])
except Exception as e:
    print('err2', e)
conn.close()
