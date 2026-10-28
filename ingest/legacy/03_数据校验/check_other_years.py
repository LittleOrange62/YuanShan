import sqlite3, os

for year in [2025, 2024]:
    db = rf'D:\finance\data\tq_daily_{year}.db'
    if os.path.exists(db):
        conn = sqlite3.connect(db)
        cur = conn.cursor()
        print(f'=== tq_daily_{year}.db stock_daily 后缀 ===')
        cur.execute("SELECT substr(code, -2), COUNT(*) FROM stock_daily GROUP BY substr(code, -2)")
        for r in cur.fetchall(): print(r)
        cur.execute('SELECT COUNT(*) FROM stock_daily')
        print(f'总行数:', cur.fetchone())
        conn.close()
    else:
        print(f'tq_daily_{year}.db 不存在')
    print()
