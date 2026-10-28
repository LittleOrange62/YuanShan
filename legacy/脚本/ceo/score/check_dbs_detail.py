#!/usr/bin/env python3
import sqlite3

# stocks_master
print("=== stocks_master.db ===")
conn = sqlite3.connect('/app/working/workspaces/data_in/data/stocks_master.db')
tables = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
for t in tables:
    tn = t[0]
    print("\n--- %s ---" % tn)
    cols = conn.execute('PRAGMA table_info("%s")' % tn).fetchall()
    for col in cols:
        print("  ", col)
    cnt = conn.execute('SELECT COUNT(*) FROM "%s"' % tn).fetchone()[0]
    print("  行数:", cnt)
    if cnt > 0:
        rows = conn.execute('SELECT * FROM "%s" LIMIT 2' % tn).fetchall()
        print("  样本:", rows)
conn.close()

# stock_basics
print("\n\n=== stock_basics.db ===")
conn = sqlite3.connect('/app/working/workspaces/data_in/data/stock_basics.db')
tables = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
for t in tables:
    tn = t[0]
    print("\n--- %s ---" % tn)
    cols = conn.execute('PRAGMA table_info("%s")' % tn).fetchall()
    for col in cols:
        print("  ", col)
    cnt = conn.execute('SELECT COUNT(*) FROM "%s"' % tn).fetchone()[0]
    print("  行数:", cnt)
    if cnt > 0 and tn == 'stock_info':
        rows = conn.execute('SELECT * FROM stock_info LIMIT 3').fetchall()
        print("  样本:", rows)
conn.close()

# sector_heat_master
print("\n\n=== sector_heat_master.db ===")
conn = sqlite3.connect('/app/working/workspaces/data_in/data/sector_heat_master.db')
tables = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
for t in tables:
    tn = t[0]
    print("\n--- %s ---" % tn)
    cols = conn.execute('PRAGMA table_info("%s")' % tn).fetchall()
    for col in cols:
        print("  ", col)
    cnt = conn.execute('SELECT COUNT(*) FROM "%s"' % tn).fetchone()[0]
    print("  行数:", cnt)
    if cnt > 0:
        rows = conn.execute('SELECT * FROM "%s" LIMIT 3' % tn).fetchall()
        print("  样本:", rows)
conn.close()

# stock_data.db trade_calendar
print("\n\n=== stock_data.db trade_calendar ===")
conn = sqlite3.connect('/app/working/workspaces/data_in/data/stock_data.db')
c = conn.execute('SELECT COUNT(*) FROM trade_calendar').fetchone()[0]
print("  trade_calendar行数:", c)
print("  is_open=1:", conn.execute('SELECT COUNT(*) FROM trade_calendar WHERE is_open=1').fetchone()[0])
print("  最近交易日:", conn.execute('SELECT cal_date FROM trade_calendar WHERE is_open=1 ORDER BY cal_date DESC LIMIT 5').fetchall())
conn.close()
