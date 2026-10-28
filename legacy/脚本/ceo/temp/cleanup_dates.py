#!/usr/bin/env python3
"""Clean up legacy date formats in stocks_master.db"""
import sqlite3

c = sqlite3.connect('/app/working/workspaces/data_in/data/stocks_master.db')

tables = ['score', 'x_y', 'factors', 'sector_score']
total_updated = 0
for t in tables:
    cur = c.execute(f'SELECT DISTINCT date FROM [{t}] WHERE date LIKE "%-%"')
    dash_dates = [r[0] for r in cur.fetchall()]
    for d in dash_dates:
        nd = d.replace('-', '')
        c.execute(f'UPDATE [{t}] SET date=? WHERE date=?', (nd, d))
        affected = c.execute(f'SELECT COUNT(*) FROM [{t}] WHERE date=?', (nd,)).fetchone()[0]
        total_updated += affected
        print(f'  [{t}] {d} -> {nd}: {affected} rows')

c.commit()
c.close()
print(f'\nDone: {total_updated} rows unified to YYYYMMDD format')
