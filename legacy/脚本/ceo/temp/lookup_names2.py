import sqlite3
conn = sqlite3.connect(r'D:\\finance\\data\\fin_data.db')
cur = conn.cursor()
symbols = ['SH600721','SZ301080','SZ301047','SH603186','SZ301217','SH688432','SZ301583']
for s in symbols:
    cur.execute('SELECT name FROM stock_basic WHERE symbol=?', (s,))
    row = cur.fetchone()
    print(s, row)
conn.close()
