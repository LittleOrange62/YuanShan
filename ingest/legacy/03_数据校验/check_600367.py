import sqlite3

# Check if 600367.SH exists in stock_daily for 2026
conn = sqlite3.connect('tq_daily_2026.db')
cur = conn.cursor()

# Check all trade dates for 600367.SH
cur.execute("SELECT trade_date, open, high, low, close, pct_change FROM stock_daily WHERE ts_code='600367.SH' ORDER BY trade_date DESC LIMIT 5")
rows = cur.fetchall()
print("=== 600367.SH 最近5个交易日 ===")
for r in rows:
    print(f"  {r[0]}  open={r[1]}  high={r[2]}  low={r[3]}  close={r[4]}  pct_chg={r[5]}%")

# Check max date
cur.execute("SELECT MAX(trade_date), MIN(trade_date) FROM stock_daily WHERE ts_code='600367.SH'")
d = cur.fetchone()
print(f"\n  日期范围: {d[1]} ~ {d[0]}")

# Check 2026-08-24
cur.execute("SELECT trade_date, open, high, low, close, pct_change FROM stock_daily WHERE ts_code='600367.SH' AND trade_date='20260824'")
d24 = cur.fetchone()
print(f"\n=== 2026-08-24 ===")
if d24:
    print(f"  trade_date={d24[0]}  open={d24[1]}  high={d24[2]}  low={d24[3]}  close={d24[4]}  pct_chg={d24[5]}%")
else:
    print("  数据缺失")
conn.close()

# Also check the most recent available date
conn = sqlite3.connect('tq_daily_2026.db')
cur = conn.cursor()
cur.execute("SELECT trade_date, open, high, low, close, pct_change FROM stock_daily WHERE ts_code='600367.SH' ORDER BY trade_date DESC LIMIT 1")
latest = cur.fetchone()
print(f"\n=== 最新可用数据: {latest[0]} ===")
if latest:
    print(f"  open={latest[1]}  high={latest[2]}  low={latest[3]}  close={latest[4]}  pct_chg={latest[5]}%")
conn.close()
