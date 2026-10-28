#!/usr/bin/env python3
import sqlite3, os
db = r"D:\\finance\\data\\fin_factor.db"
print('文件', os.path.exists(db), os.path.getsize(db) if os.path.exists(db) else None)
conn = sqlite3.connect(db)
c = conn.cursor()
print('\n表列表:')
print(c.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall())
# check score_results
print('\nscore_results结构:')
print(c.execute("PRAGMA table_info(score_results)").fetchall())
print('\n最新 trade_date:')
try:
    print(c.execute("SELECT MAX(trade_date) FROM score_results").fetchone())
except Exception as e:
    print('错误', e)
print('\n最新条数:')
try:
    max_date = c.execute("SELECT MAX(trade_date) FROM score_results").fetchone()[0]
    cnt = c.execute("SELECT COUNT(*) FROM score_results WHERE trade_date=?", (max_date,)).fetchone()[0]
    print('date', max_date, 'count', cnt)
except Exception as e:
    print('错误', e)
conn.close()
