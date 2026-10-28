#!/usr/bin/env python3
import sqlite3, os

data_dir = '/app/working/workspaces/data_in/data'
for f in sorted(os.listdir(data_dir)):
    if f.endswith('.db'):
        path = os.path.join(data_dir, f)
        size = os.path.getsize(path)
        try:
            conn = sqlite3.connect(path)
            tables = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
            table_info = []
            for t in tables:
                try:
                    cnt = conn.execute('SELECT COUNT(*) FROM "' + t[0] + '"').fetchone()[0]
                    table_info.append(f'{t[0]}({cnt}行)')
                except:
                    table_info.append(f'{t[0]}(?)')
            conn.close()
            info = ', '.join(table_info)
            print(f'{f} ({size/1024:.0f}KB): {info}')
        except Exception as e:
            print(f'{f} ({size/1024:.0f}KB): ERROR - {e}')
