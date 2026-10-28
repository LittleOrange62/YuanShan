import sqlite3

print('=== tq_core.db stock_info 后缀分布 ===')
conn = sqlite3.connect(r'D:\finance\data\tq_core.db')
cur = conn.cursor()
cur.execute("select substr(ts_code,-2) as suf, count(*) from stock_info group by suf order by 2 desc")
for r in cur.fetchall():
    print(r)
cur.execute("select count(*) from stock_info where asset_type='stock'")
print('asset_type=stock 总数:', cur.fetchone()[0])
cur.execute("select count(*) from stock_info where asset_type='stock' and ts_code like '%.SZ'")
print('asset_type=stock 且 .SZ:', cur.fetchone()[0])
cur.execute("select count(*) from stock_info where asset_type='stock' and ts_code like '%.SH'")
print('asset_type=stock 且 .SH:', cur.fetchone()[0])

print()
print('=== 本地 sz .day 可读性验证 ===')
import struct, os
path = r'D:\new_tdx64\vipdoc\sz\lday\sz000001.day'
if os.path.exists(path):
    with open(path, 'rb') as f:
        f.seek(0, 2)
        size = f.tell()
        n = size // 32
        f.seek(size - 32)
        data = f.read(32)
    date_int, o, h, l, c = struct.unpack('<Iffff', data[:20])
    print(f'sz000001.day: 记录数={n} 末条日期={date_int} close={c:.2f}')
else:
    print('sz000001.day 不存在')

conn.close()
