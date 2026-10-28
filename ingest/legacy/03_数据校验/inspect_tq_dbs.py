import sqlite3, os

def inspect(db, tables):
    print('DB', db)
    con = sqlite3.connect(db)
    cur = con.cursor()
    for t in tables:
        print('###', t)
        cols = cur.execute(f'pragma table_info({t})').fetchall()
        print('cols', [(c[1], c[2]) for c in cols])
        try:
            cnt = cur.execute(f'select count(*) from {t}').fetchone()[0]
            print('rows', cnt)
            print('sample', cur.execute(f'select * from {t} limit 3').fetchall())
        except Exception as e:
            print('err', repr(e))
    con.close()

inspect(r'D:\finance\data\tq_daily_2026.db', ['stock_daily','index_daily','meta_tables'])
inspect(r'D:\finance\data\tq_core.db', ['stock_info','index_info','watchlist_groups','watchlist_stocks','focus_stocks','meta_tables'])
inspect(r'D:\finance\data\tq_minline_2026.db', ['stock_minline','meta_tables'])
