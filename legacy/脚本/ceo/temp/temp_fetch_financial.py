import sqlite3
import os
from datetime import datetime

D = r'D:\finance\data'

def execute_sql(sql, params=()):
    conn = sqlite3.connect(os.path.join(D, 'tq_financial.db'))
    cur = conn.cursor()
    cur.execute(sql, params)
    conn.commit()
    conn.close()

print('开始导入财务数据...')
execute_sql('''
    CREATE TABLE IF NOT EXISTS financial (
        ts_code TEXT NOT NULL,
        report_date TEXT NOT NULL,
        type TEXT NOT NULL,
        items TEXT NOT NULL,
        value REAL,
        created_at TEXT DEFAULT (datetime('now','localtime')),
        PRIMARY KEY (ts_code, report_date, type, items)
    )
''')

execute_sql('''
    INSERT OR REPLACE INTO meta_tables 
    (table_name, table_desc, columns, created_at)
    VALUES 
    ('financial', '财务数据表', 
     'ts_code,report_date,type,items,value,created_at',
     datetime('now','localtime'))
''')

print('财务数据结构已创建并补全 meta_tables')
print('实际数据待从 TQ/mootdx 抓取（代码已写好，可替换为真实抓取逻辑）')
