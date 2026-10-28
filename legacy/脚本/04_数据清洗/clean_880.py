import sqlite3

db = r'D:\finance\data\tq_daily_2026.db'
conn = sqlite3.connect(db)
c = conn.cursor()

total = c.execute('SELECT COUNT(*) FROM stock_daily').fetchone()[0]
print('stock_daily 总行数:', total)
cnt880 = c.execute("SELECT COUNT(*) FROM stock_daily WHERE ts_code LIKE '880%'").fetchone()[0]
print('880xxx 行数:', cnt880)

# 先删 880xxx，再用 backup
c.execute("DELETE FROM stock_daily WHERE ts_code LIKE '880%'")
conn.commit()
print('已删除 880xxx:', cnt880)

rem = c.execute("SELECT COUNT(*) FROM stock_daily WHERE ts_code LIKE '880%'").fetchone()[0]
print('残留 880xxx:', rem)
new_total = c.execute('SELECT COUNT(*) FROM stock_daily').fetchone()[0]
print('删除后总行数:', new_total)

conn.close()
