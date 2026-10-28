import sqlite3
db=r'D:\finance\data\tq_core.db'
conn=sqlite3.connect(db)
cur=conn.cursor()
for t in ['sector_info','sector_member','security_info']:
    try:
        cur.execute(f'select count(*) from {t}')
        print(t, cur.fetchone()[0])
        cur.execute(f'select * from {t} limit 3')
        print('  sample:', cur.fetchall())
    except Exception as e:
        print(t, 'ERR', e)
conn.close()
