import sqlite3, sys
con = sqlite3.connect(r'D:/finance/data/fin_data.db')
cur = con.cursor()
cur.execute('SELECT symbol FROM stock_basic LIMIT 20')
for row in cur.fetchall():
    print(row[0])
