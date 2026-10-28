import sqlite3

c = sqlite3.connect(r'D:\finance\data\tq_core.db')
cur = c.cursor()
cur.execute("select substr(ts_code,1,3) as pre, count(*) from stock_info where asset_type='stock' group by pre order by 2 desc")
print('=== stock_info asset_type=stock 前缀分布 ===')
for r in cur.fetchall():
    print(r)

# 检查是否有 204/131 等非股票混入
cur.execute("select ts_code from stock_info where asset_type='stock' and substr(ts_code,1,3) not in ('600','601','603','605','688','000','001','002','003','300','301','689') limit 20")
print('\n=== 可疑非股票代码 ===')
for r in cur.fetchall():
    print(r[0])

c.close()
