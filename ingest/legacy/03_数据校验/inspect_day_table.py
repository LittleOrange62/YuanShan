import sqlite3, sys
db = r'D:/finance/data/fin_data.db'
con = sqlite3.connect(db)
cur = con.cursor()
cur.execute('SELECT COUNT(*) FROM day_end_20260717')
cnt = cur.fetchone()[0]
cur.execute('SELECT MAX(HqDate) FROM day_end_20260717')
max_date = cur.fetchone()[0]
print('rows:',cnt)
print('max HqDate:', max_date)
