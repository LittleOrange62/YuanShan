import sqlite3

conn = sqlite3.connect(r'D:\finance\data\tq_daily_2026.db')
cur = conn.cursor()

print('=== stock_daily 后缀分布 ===')
cur.execute("SELECT substr(code, -2), COUNT(*) FROM stock_daily GROUP BY substr(code, -2) ORDER BY substr(code, -2)")
for r in cur.fetchall(): print(r)

print('\n=== stock_daily 抽样 (SH) ===')
cur.execute("SELECT code FROM stock_daily WHERE substr(code,-2)='.SH' LIMIT 3")
for r in cur.fetchall(): print(r)

print('\n=== stock_daily 抽样 (SZ) ===')
cur.execute("SELECT code FROM stock_daily WHERE substr(code,-2)='.SZ' LIMIT 5")
rows = cur.fetchall()
print(rows if rows else '(无)')

print('\n=== stock_daily 总行数 ===')
cur.execute('SELECT COUNT(*) FROM stock_daily')
print(cur.fetchone())

print('\n=== stock_info 后缀分布 ===')
cur.execute("SELECT substr(code, -2), COUNT(*) FROM stock_info GROUP BY substr(code, -2)")
for r in cur.fetchall(): print(r)

print('\n=== index_daily 后缀分布 ===')
cur.execute("SELECT substr(code, -2), COUNT(*) FROM index_daily GROUP BY substr(code, -2)")
for r in cur.fetchall(): print(r)

print('\n=== index_daily 399 开头 ===')
cur.execute("SELECT code FROM index_daily WHERE substr(code,1,3)='399' LIMIT 5")
for r in cur.fetchall(): print(r)

conn.close()
