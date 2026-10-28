#!/usr/bin/env python3
"""Clean CSV and import to stock_basics.db"""
import csv
import sqlite3

input_file = '/app/working/workspaces/manager/temp/stock_concept_from_kb.csv'

with open(input_file, 'r', encoding='utf-8-sig') as f:
    reader = csv.reader(f)
    header = next(reader)
    print(f'Header: {header}')
    rows = []
    for row in reader:
        if row and len(row) >= 3:
            rows.append(row)
        else:
            print(f'Skipping empty row: {row}')

print(f'Read {len(rows)} valid rows')

conn = sqlite3.connect('/app/working/workspaces/data_in/data/stock_basics.db')
cur = conn.cursor()

count = 0
for row in rows:
    ts_code = row[0].strip().strip("'").strip('"')
    concept_code = row[1].strip().strip("'").strip('"')
    concept_name = row[2].strip().strip("'").strip('"')
    if ts_code and concept_code:
        try:
            cur.execute(
                'INSERT OR REPLACE INTO stock_concept (ts_code, concept_code, concept_name) VALUES (?, ?, ?)',
                (ts_code, concept_code, concept_name)
            )
            count += 1
        except Exception as e:
            print(f'Error on {ts_code}/{concept_code}: {e}')

conn.commit()
cur.execute('SELECT COUNT(*) FROM stock_concept')
total = cur.fetchone()[0]
print(f'Inserted {count} rows, total in stock_concept: {total}')
conn.close()
