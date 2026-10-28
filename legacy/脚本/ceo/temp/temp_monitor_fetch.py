import os, time, sqlite3
from datetime import datetime

DB_PATH = r'D:\finance\data\tq_daily_2026.db'
last_size = 0
start_time = time.time()

while True:
    time.sleep(10)
    if not os.path.exists(DB_PATH):
        print(f'{datetime.now().strftime("%H:%M:%S")} - DB not exists yet')
        continue
    
    size = os.path.getsize(DB_PATH)
    try:
        conn = sqlite3.connect(f'file:{DB_PATH}?mode=ro', uri=True)
        cnt = conn.execute('SELECT COUNT(*) FROM stock_daily').fetchone()[0]
        conn.close()
    except Exception as e:
        print(f'{datetime.now().strftime("%H:%M:%S")} - DB locked, size={size}, waiting...')
        continue
    
    elapsed = time.time() - start_time
    speed = cnt / elapsed if elapsed > 0 else 0
    print(f'{datetime.now().strftime("%H:%M:%S")} - stock_daily rows: {cnt}, db_size: {size/1024/1024:.1f}MB, speed: {speed:.1f} rows/s')
    
    if size == last_size and cnt > 0:
        # 可能已经完成
        print('No size change, checking again in 30s...')
        time.sleep(30)
        try:
            conn = sqlite3.connect(f'file:{DB_PATH}?mode=ro', uri=True)
            cnt2 = conn.execute('SELECT COUNT(*) FROM stock_daily').fetchone()[0]
            conn.close()
            if cnt2 == cnt:
                print(f'Fetch complete. Total rows: {cnt}')
                break
        except:
            pass
    last_size = size
