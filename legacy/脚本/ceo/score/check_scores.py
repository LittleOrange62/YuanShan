#!/usr/bin/env python3
import sqlite3
import os

DATA_DIR = '/app/working/workspaces/data_in/data'
conn = sqlite3.connect(os.path.join(DATA_DIR, 'stocks_master.db'))
cur = conn.cursor()

# Check distinct dates
cur.execute('SELECT DISTINCT date FROM score ORDER BY date DESC')
dates = cur.fetchall()
print('All dates in score:', [r[0] for r in dates])

latest_date = dates[0][0] if dates else None
print('\nLatest score date:', latest_date)

# Check what columns exist in x_y (has quadrant)
cur.execute('SELECT symbol, x, y, quadrant FROM x_y WHERE date=? ORDER BY rowid DESC LIMIT 5', (latest_date,))
print('\nSample x_y:', cur.fetchall())

# Check score columns
cur.execute('SELECT symbol, x, y, final, oper FROM score WHERE date=? ORDER BY final DESC LIMIT 20', (latest_date,))
print('\nTop 20 score:')
for row in cur.fetchall():
    print('  {:12s} X={:6.1f} Y={:6.1f} Final={:6.1f} Oper={:6.1f}'.format(*row))

# Focus stocks from score
print('\n=== FOCUS STOCKS ===')
focus = ['001208.SZ','000600.SZ','920808.BJ','688820.SH','688008.SH','603893.SH','000988.SZ','300666.SZ','002409.SZ','000021.SZ','002138.SZ','600487.SH','688106.SH','300346.SZ','002182.SZ']
placeholders = ','.join(['?'] * len(focus))
cur.execute('SELECT symbol, x, y, final, oper FROM score WHERE date=? AND symbol IN ({}) ORDER BY final DESC'.format(placeholders), [latest_date] + focus)
for row in cur.fetchall():
    print('  {:12s} X={:6.1f} Y={:6.1f} Final={:6.1f} Oper={:6.1f}'.format(*row))

found = cur.execute('SELECT COUNT(*) FROM score WHERE date=? AND symbol IN ({})'.format(placeholders), [latest_date] + focus).fetchone()[0]
print('Focus stocks found:', found)

# Sector scores - check both tables
print('\n=== SECTOR SCORES (sector_scores table) ===')
cur.execute('SELECT DISTINCT date FROM sector_scores ORDER BY date DESC')
print('Dates:', [r[0] for r in cur.fetchall()])

cur.execute('SELECT MAX(date) FROM sector_scores')
latest_sector = cur.fetchone()[0]
print('Latest sector date:', latest_sector)
cur.execute('SELECT group_name, x, y, final, oper, quadrant FROM sector_scores WHERE date=? ORDER BY final DESC', (latest_sector,))
for row in cur.fetchall():
    print('  {:30s} X={:6.1f} Y={:6.1f} Final={:6.1f} Oper={:6.1f} {}'.format(row[0], row[1], row[2], row[3], row[4], row[5]))

# Kline dates
print('\n=== KLINE DATES ===')
kline_conn = sqlite3.connect(os.path.join(DATA_DIR, 'kline_2026.db'))
kcur = kline_conn.cursor()
kcur.execute('SELECT DISTINCT trade_date FROM daily_kline ORDER BY trade_date DESC LIMIT 5')
print('Recent kline dates:', [r[0] for r in kcur.fetchall()])
kline_conn.close()

conn.close()
