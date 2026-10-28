import sqlite3, glob, os
base=r'D:\finance\data'
for db in sorted(glob.glob(base+r'\tq_daily_*.db')):
    conn=sqlite3.connect(db)
    cur=conn.cursor()
    cur.execute("select name from sqlite_master where type='table' order by name")
    tables=[r[0] for r in cur.fetchall()]
    print('\nDB', os.path.basename(db), 'tables=', tables)
    if 'stock_daily' in tables:
        cur.execute('select count(*) from stock_daily')
        cnt=cur.fetchone()[0]
        cur.execute('select ts_code, trade_date, open, close from stock_daily limit 10')
        rows=cur.fetchall()
        print('stock_daily count', cnt)
        for r in rows:
            print(' ', r)
    if 'index_daily' in tables:
        cur.execute('select count(*) from index_daily')
        cnt=cur.fetchone()[0]
        cur.execute('select ts_code, trade_date, open, close from index_daily limit 10')
        rows=cur.fetchall()
        print('index_daily count', cnt)
        for r in rows:
            print(' ', r)
    conn.close()
