import sqlite3
watchdb = r'D:\finance\data\fin_watchlist.db'
conn = sqlite3.connect(watchdb)
cur = conn.cursor()
symbols = ['SH600721','SZ301080','SZ301047','SH603186','SZ301217','SH688432','SZ301583']
for s in symbols:
    cur.execute('SELECT symbol, name FROM watchlist_stocks WHERE symbol=?', (s,))
    row = cur.fetchone()
    print(s, row)
conn.close()
