import time, sqlite3 
db=r'D:\\finance\\data\\fin_data_2026.db' 
for i in range(5): 
    try: 
        conn=sqlite3.connect(db, timeout=30) 
        conn.execute("DELETE FROM daily_kline WHERE trade_date='2026-08-14'") 
        conn.commit() 
        conn.close() 
        print('Deleted rows for 2026-08-14') 
        break 
    except Exception as e: 
        print('Attempt', i, 'failed', e) 
        time.sleep(1) 
