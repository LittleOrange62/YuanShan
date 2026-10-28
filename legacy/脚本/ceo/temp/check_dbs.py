# -*- coding: utf-8 -*-
"""只读检查：各 db 的表结构与最新数据日期"""
import sqlite3, os

DBS = [
    r'D:\finance\data\fin_data_2026.db',
    r'D:\finance\data\fin_data.db',
    r'D:\finance\data\fin_factor.db',
    r'D:\finance\data\scoring.db',
]

for db in DBS:
    if not os.path.exists(db):
        print(f'[缺] {db}')
        continue
    print(f'\n===== {os.path.basename(db)} ({os.path.getsize(db)//1024//1024}MB, 修改于 {os.path.getmtime(db)}) =====')
    try:
        con = sqlite3.connect(db)
        cur = con.cursor()
        cur.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
        tables = [r[0] for r in cur.fetchall()]
        print('表数量:', len(tables))
        print('表列表:', tables[:60])
        # 找 day_end 或日线相关表
        for t in tables:
            tl = t.lower()
            if 'day' in tl or 'kline' in tl or 'daily' in tl or 'score' in tl or 'heat' in tl:
                try:
                    cur.execute(f'SELECT COUNT(*) FROM "{t}"')
                    cnt = cur.fetchone()[0]
                    cur.execute(f'SELECT * FROM "{t}" LIMIT 1')
                    cols = [d[0] for d in cur.description]
                    print(f'  [{t}] 行数={cnt} 字段={cols}')
                    if cnt > 0:
                        row = cur.fetchone()
                        print(f'    样例: {row}')
                except Exception as e:
                    print(f'  [{t}] 查询失败: {e}')
        con.close()
    except Exception as e:
        print('打开失败:', e)
