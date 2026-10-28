import sqlite3, os
base=r'D:\finance\data'
for db in ['tq_daily_2026.db','tq_daily_2025.db','tq_daily_2024.db','tq_core.db','tq_internet.db','tq_minline_2026.db','tq_financial.db','tq_computed.db','trend_research.db']:
    path=os.path.join(base,db)
    con=sqlite3.connect(path)
    cur=con.cursor()
    print('\n###',db)
    tables=[r[0] for r in cur.execute("select name from sqlite_master where type='table' and name not like 'sqlite_%' order by name")]
    for t in tables:
        try:
            cnt=cur.execute(f'select count(*) from {t}').fetchone()[0]
            print(t,'rows',cnt)
        except Exception as e:
            print(t,'ERR',e)
    if db.startswith('tq_daily'):
        if 'stock_daily' in tables:
            print(' sample stock_daily',cur.execute('select ts_code,trade_date,open,close,vol,amount from stock_daily order by trade_date desc, ts_code limit 5').fetchall())
            print(' stock_daily prefix counts',cur.execute("select substr(ts_code,1,2), count(*) from stock_daily group by 1 order by 2 desc").fetchall())
            print(' stock_daily distinct ts_code sample',cur.execute('select distinct ts_code from stock_daily order by ts_code limit 20').fetchall())
        if 'index_daily' in tables:
            print(' sample index_daily',cur.execute('select ts_code,name,trade_date,open,close from index_daily order by trade_date desc, ts_code limit 5').fetchall())
            print(' index_daily prefix counts',cur.execute("select substr(ts_code,1,2), count(*) from index_daily group by 1 order by 2 desc").fetchall())
            print(' index_daily distinct ts_code sample',cur.execute('select distinct ts_code from index_daily order by ts_code limit 20').fetchall())
    if db=='tq_core.db':
        print('stock_info sample',cur.execute('select ts_code,name,asset_type,market,industry,area from stock_info order by ts_code limit 10').fetchall())
        print('stock_info asset counts',cur.execute('select asset_type,count(*) from stock_info group by asset_type order by count(*) desc').fetchall())
        print('index_info sample',cur.execute('select ts_code,name,market from index_info order by ts_code limit 10').fetchall())
    if db=='tq_internet.db':
        print('stock_basic_extra sample',cur.execute('select ts_code,industry,industry_code,substr(concept,1,40) from stock_basic_extra order by ts_code limit 10').fetchall())
    con.close()
