import sqlite3

conn = sqlite3.connect(r'D:\finance\data\tq_core.db')
for t in ['sector_info', 'sector_member']:
    cur = conn.execute(f'PRAGMA table_info({t})')
    print(f'=== {t} ===')
    for row in cur:
        print(row)
    # 查看已有数据量
    cnt = conn.execute(f'SELECT COUNT(*) FROM {t}').fetchone()[0]
    print(f'  rows: {cnt}')
    print()
conn.close()
