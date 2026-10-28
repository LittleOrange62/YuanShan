# -*- coding: utf-8 -*-
"""只读诊断：检查缺失股票是否在自选股/重点股池中"""
import sqlite3, os

# 检查 fin_watchlist.db
for db in [r'D:\finance\data\fin_watchlist.db', r'D:\finance\data\fin_config.db', r'D:\finance\data\scoring.db']:
    if os.path.exists(db):
        print(f'=== {db} ===')
        conn = sqlite3.connect(db)
        cur = conn.cursor()
        cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = [r[0] for r in cur.fetchall()]
        print('表:', tables)
        for t in tables:
            try:
                cur.execute(f"SELECT COUNT(*) FROM {t}")
                cnt = cur.fetchone()[0]
                print(f'  {t}: {cnt} 行')
                if cnt < 500:
                    cur.execute(f"SELECT * FROM {t} LIMIT 5")
                    cols = [d[0] for d in cur.description]
                    print(f'    字段: {cols}')
                    for r in cur.fetchall():
                        print(f'    {r}')
            except Exception as e:
                print(f'  {t}: {e}')
        conn.close()
        print()
