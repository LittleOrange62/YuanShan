import sqlite3, os, json
base=r'D:\finance\data'
dbs=['tq_core.db','tq_daily_2021.db','tq_daily_2022.db','tq_daily_2023.db','tq_daily_2024.db','tq_daily_2025.db','tq_daily_2026.db','tq_minline_2026.db','tq_financial.db','tq_computed.db','tq_internet.db','trend_research.db','tq_mdata.db','fin_data.db','fin_data_2026.db']
for db in dbs:
    path=os.path.join(base,db)
    print(f'===== {db} size={os.path.getsize(path)} =====')
    try:
        con=sqlite3.connect(path)
        cur=con.cursor()
        tables=cur.execute("select name,type from sqlite_master where type in ('table','view') and name not like 'sqlite_%' order by type,name").fetchall()
        if not tables:
            print('(no tables)')
        for name,typ in tables:
            print(f'[{typ}] {name}')
            cols=cur.execute('pragma table_info(%s)' % json.dumps(name)).fetchall()
            for c in cols:
                print('   ',c)
            idx=cur.execute('pragma index_list(%s)' % json.dumps(name)).fetchall()
            for i in idx:
                print('    IDX',i)
        con.close()
    except Exception as e:
        print('ERROR',e)
