import sqlite3, os
p = r'D:\finance\data\tq_daily_2026.db'
if os.path.exists(p):
    conn = sqlite3.connect(p)
    cnt = conn.execute('SELECT COUNT(*) FROM stock_daily').fetchone()[0]
    idx_cnt = conn.execute('SELECT COUNT(*) FROM index_daily').fetchone()[0]
    conn.close()
    print(f'stock_daily rows: {cnt}')
    print(f'index_daily rows: {idx_cnt}')
else:
    print('DB not exists')
