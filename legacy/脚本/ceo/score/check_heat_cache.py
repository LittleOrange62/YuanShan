#!/usr/bin/env python3
import sqlite3
conn = sqlite3.connect('/app/working/workspaces/data_in/data/sector_heat_master.db')
cnt = conn.execute('SELECT COUNT(*) FROM heat').fetchone()[0]
print(f'sector_heat_master.db 当前有 {cnt} 条热度数据')
if cnt > 0:
    for r in conn.execute('SELECT * FROM heat LIMIT 5').fetchall():
        print(f'  {r}')
conn.close()
