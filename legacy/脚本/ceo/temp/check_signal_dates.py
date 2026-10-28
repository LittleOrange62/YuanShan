#!/usr/bin/env python3
"""Check signal data dates"""
import sqlite3
conn = sqlite3.connect('/app/working/workspaces/data_in/data/quant_signals.db')
tables = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
for t in tables:
    maxd = conn.execute("SELECT MAX(trade_date) FROM {0}".format(t[0])).fetchone()
    mind = conn.execute("SELECT MIN(trade_date) FROM {0}".format(t[0])).fetchone()
    cnt = conn.execute("SELECT COUNT(*) FROM {0}".format(t[0])).fetchone()
    print("  {0}: {1} rows, {2} ~ {3}".format(t[0], cnt[0], mind[0], maxd[0]))
conn.close()
