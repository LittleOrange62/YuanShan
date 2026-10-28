import sqlite3, sys
db = r'D:/finance/data/fin_data.db'
con = sqlite3.connect(db)
cur = con.cursor()
cur.execute('SELECT symbol FROM stock_basic')
with open('symbols.txt','w',encoding='utf-8') as f:
    for row in cur.fetchall():
        f.write(row[0]+'\n')
print('dumped symbols')
