import sqlite3, sys
db = r'D:/finance/data/fin_data.db'
con = sqlite3.connect(db)
cur = con.cursor()
cur.execute('SELECT MAX(HqDate) FROM day_end_20260717')
row = cur.fetchone()
print('max_date:', row[0] if row else None)
