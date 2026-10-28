import sqlite3
db=r'D:\finance\data\tq_core.db'
conn=sqlite3.connect(db)
cur=conn.cursor()
for t in ['sector_info','sector_member','security_info']:
    try:
        cur.execute(f'select count(*) from {t}')
        print(t, cur.fetchone()[0])
    except Exception as e:
        print(t, 'ERR', e)
conn.close()
db2=r'D:\finance\data\tq_daily_2026.db'
conn2=sqlite3.connect(db2)
cur2=conn2.cursor()
try:
    cur2.execute('select count(*) from sector_daily')
    print('sector_daily', cur2.fetchone()[0])
except Exception as e:
    print('sector_daily ERR', e)
conn2.close()
