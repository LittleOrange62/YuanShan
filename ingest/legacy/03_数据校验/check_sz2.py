import sqlite3, os

db26 = r'D:\finance\data\tq_daily_2026.db'
conn = sqlite3.connect(db26)
cur = conn.cursor()

# 1. stock_daily 后缀分布
print('=== stock_daily 后缀分布 (2026) ===')
cur.execute("SELECT substr(ts_code, -2), COUNT(*) FROM stock_daily GROUP BY substr(ts_code, -2) ORDER BY substr(ts_code, -2)")
for r in cur.fetchall(): print(r)

print('\n=== stock_daily 总行数 ===')
cur.execute('SELECT COUNT(*) FROM stock_daily')
print(cur.fetchone())

# 抽样 .SH
print('\n=== stock_daily 抽样 (.SH) ===')
cur.execute("SELECT ts_code FROM stock_daily WHERE substr(ts_code,-2)='.SH' LIMIT 5")
for r in cur.fetchall(): print(r)

# 抽样深市（如果有的话）
print('\n=== stock_daily 抽样 (深市代码) ===')
cur.execute("SELECT DISTINCT substr(ts_code,1,6) FROM stock_daily WHERE ts_code LIKE '0%' OR ts_code LIKE '3%' LIMIT 10")
for r in cur.fetchall(): print(r)

# 列出所有不同后缀
print('\n=== 所有不同的后缀 ===')
cur.execute("SELECT DISTINCT substr(ts_code, -3) FROM stock_daily")
for r in cur.fetchall(): print(r[0])

# 找所有 .SZ 结尾
print('\n=== 是否有任何 .SZ ===')
cur.execute("SELECT COUNT(*) FROM stock_daily WHERE substr(ts_code,-2)='.SZ'")
print(cur.fetchone())

# 2. 查看库中有哪些表
print('\n=== 所有表 ===')
cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
for r in cur.fetchall(): print(r[0])

conn.close()

# 3. 检查其他年份
for year in [2025, 2024]:
    db = rf'D:\finance\data\tq_daily_{year}.db'
    if os.path.exists(db) and os.path.getsize(db) > 0:
        conn = sqlite3.connect(db)
        cur = conn.cursor()
        print(f'\n=== tq_daily_{year}.db stock_daily 后缀 ===')
        cur.execute("SELECT substr(ts_code, -2), COUNT(*) FROM stock_daily GROUP BY substr(ts_code, -2)")
        for r in cur.fetchall(): print(r)
        cur.execute('SELECT COUNT(*) FROM stock_daily')
        print(f'总行数:', cur.fetchone())
        conn.close()
    else:
        print(f'\ntq_daily_{year}.db 不存在或为空')
