import sqlite3

# Search broadly in both databases
conn = sqlite3.connect('tq_core.db')
cur = conn.cursor()

# Check all names
cur.execute("SELECT ts_code, symbol, name, market FROM stock_info WHERE name LIKE '%红%' OR name LIKE '%星%'")
results = cur.fetchall()
print("=== 含'红'或'星'的股票（前50条）===")
for r in results[:50]:
    print(f"  {r[0]}  {r[1]}  {r[2]}  [{r[3]}]")
print(f"共 {len(results)} 条")

# Also search ts_code or symbol for anything red/star related
conn.close()

# Check what the actual code might be - 红星发展 is 600367.SH
conn = sqlite3.connect('tq_core.db')
cur = conn.cursor()
cur.execute("SELECT ts_code, symbol, name, market FROM stock_info WHERE symbol IN ('600367', '600367.SH')")
r = cur.fetchone()
print(f"\n=== 600367 ===")
if r:
    print(f"  {r[0]}  {r[1]}  {r[2]}  [{r[3]}]")
else:
    print("  NOT FOUND")
conn.close()
