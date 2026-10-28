import sqlite3, os
d = r'D:\finance\data'
for f in sorted(os.listdir(d)):
    if f.endswith('.db'):
        p = os.path.join(d, f)
        conn = sqlite3.connect(p)
        tables = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'").fetchall()
        print(f'== {f} ({len(tables)} tables) ==')
        for t in tables:
            cols = conn.execute(f'PRAGMA table_info({t[0]})').fetchall()
            cnt = conn.execute(f'SELECT COUNT(*) FROM {t[0]}').fetchone()[0]
            print(f'  {t[0]} ({cnt} rows): {[c[1] for c in cols]}')
        conn.close()
