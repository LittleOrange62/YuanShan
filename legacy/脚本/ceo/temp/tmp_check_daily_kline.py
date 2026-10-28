import sqlite3, json
db=r'D:\\finance\\data\\fin_data_2026.db'
conn=sqlite3.connect(db)
cols=[c[1] for c in conn.execute('PRAGMA table_info(daily_kline)')]
print('Columns:',cols)
conn.close()