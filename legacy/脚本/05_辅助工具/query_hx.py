import sqlite3

conn = sqlite3.connect('tq_core.db')
cur = conn.cursor()
cur.execute("SELECT ts_code, symbol, name, market FROM stock_info WHERE name LIKE '%红星%'")
results = cur.fetchall()
print("=== 股票信息 ===")
for r in results:
    print(f"  {r[0]}  {r[1]}  {r[2]}  [{r[3]}]")
conn.close()

# Now query daily data for today
conn = sqlite3.connect('tq_daily_2026.db')
cur = conn.cursor()
for r in results:
    ts_code = r[0]
    name = r[2]
    cur.execute(
        "SELECT ts_code, trade_date, open, high, low, close, pct_change FROM stock_daily WHERE ts_code=? AND trade_date='20260824'",
        (ts_code,)
    )
    d = cur.fetchone()
    print(f"\n=== {name} 2026-08-24 ===")
    if d:
        print(f"  代码: {d[0]}")
        print(f"  日期: {d[1]}")
        print(f"  开盘: {d[2]}")
        print(f"  最高: {d[3]}")
        print(f"  最低: {d[4]}")
        print(f"  收盘: {d[5]}")
        print(f"  涨跌幅: {d[6]}%")
    else:
        print(f"  当日数据缺失")
conn.close()
