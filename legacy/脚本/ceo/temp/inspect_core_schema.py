import sqlite3, os
base=r'D:\finance\data'
for dbname in ['tq_core.db','tq_daily_2026.db']:
    db=os.path.join(base,dbname)
    conn=sqlite3.connect(db)
    cur=conn.cursor()
    print('\n===', dbname, '===')
    cur.execute("select name from sqlite_master where type='table' order by name")
    tables=[r[0] for r in cur.fetchall()]
    print('tables:', tables)
    for t in tables:
        print('\n-- schema', t)
        cur.execute(f"pragma table_info({t})")
        for row in cur.fetchall():
            print(row)
        if t in ('watchlist_groups','watchlist_stocks','focus_stocks','index_info','stock_info','meta_tables'):
            cur.execute(f"select * from {t} limit 5")
            rows=cur.fetchall()
            print('sample:', rows)
    conn.close()
