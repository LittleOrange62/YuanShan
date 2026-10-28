import sqlite3

conn = sqlite3.connect(r'D:\finance\data\tq_core.db')
cur = conn.cursor()

print('=== stock_info 中 000xxx 代码的名称与类型 ===')
for code in ['000001.SH', '000002.SH', '000003.SH', '000858.SH', '000016.SH', '000300.SH']:
    cur.execute('select ts_code, name, asset_type from stock_info where ts_code=?', (code,))
    r = cur.fetchone()
    print(r)

print()
print('=== stock_info 中 .SZ 股票抽样（应该有名称）===')
cur.execute("select ts_code, name, asset_type from stock_info where ts_code in ('000858.SZ','300750.SZ','002594.SZ','000001.SZ')")
for r in cur.fetchall():
    print(r)

conn.close()
