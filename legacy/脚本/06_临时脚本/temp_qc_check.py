import sqlite3

core = r'D:\finance\data\tq_core.db'
daily = r'D:\finance\data\tq_daily_2026.db'

conn_c = sqlite3.connect(core)
cur_c = conn_c.cursor()

conn_d = sqlite3.connect(daily)
cur_d = conn_d.cursor()

# 1. stock_info asset_type 修正结果
print('='*70)
print('1. stock_info 中 asset_type 分布 (tq_core.db)')
print('='*70)
cur_c.execute('SELECT asset_type, COUNT(*) as cnt FROM stock_info GROUP BY asset_type ORDER BY cnt DESC')
for r in cur_c.fetchall():
    print(f'  {str(r[0]):>12s}: {r[1]} 条')
total = cur_c.execute('SELECT COUNT(*) FROM stock_info').fetchone()[0]
print(f'  {"合计":>12s}: {total} 条')

print('  stock_info 样例（前10条）:')
cur_c.execute('SELECT ts_code, name, asset_type FROM stock_info LIMIT 10')
for r in cur_c.fetchall():
    print(f'    {str(r[0]):>12s}  {str(r[1]):>20s}  asset_type={r[2]}')

# 2. sector_info / sector_member
print()
print('='*70)
print('2. sector_info 与 sector_member (tq_core.db)')
print('='*70)
cnt_si = cur_c.execute('SELECT COUNT(*) FROM sector_info').fetchone()[0]
print(f'  sector_info: {cnt_si} 行')
if cnt_si > 0:
    cur_c.execute('SELECT sector_type, COUNT(*) FROM sector_info GROUP BY sector_type')
    for r in cur_c.fetchall():
        print(f'    sector_type={str(r[0]):>6s}: {r[1]}')

    print('  sector_info 样例（前15条）:')
    cur_c.execute('SELECT sector_code, sector_name, sector_type FROM sector_info ORDER BY sector_type, sector_code LIMIT 15')
    for r in cur_c.fetchall():
        print(f'    {str(r[0]):>12s}  {str(r[1]):>25s}  type={r[2]}')

cnt_sm = cur_c.execute('SELECT COUNT(*) FROM sector_member').fetchone()[0]
print(f'  sector_member: {cnt_sm} 行')
if cnt_sm > 0:
    distinct_sec = cur_c.execute('SELECT COUNT(DISTINCT sector_code) FROM sector_member').fetchone()[0]
    distinct_sym = cur_c.execute('SELECT COUNT(DISTINCT member_ts_code) FROM sector_member').fetchone()[0]
    print(f'    唯一板块数: {distinct_sec}')
    print(f'    唯一成分股数: {distinct_sym}')

    print('  sector_member 样例（前10条）:')
    cur_c.execute('SELECT sector_code, member_ts_code, member_name, weight FROM sector_member LIMIT 10')
    for r in cur_c.fetchall():
        print(f'    sector={str(r[0]):>12s}  member={str(r[1]):>10s}  name={str(r[2]):>15s}  w={r[3]}')

# 3. sector_daily
print()
print('='*70)
print('3. sector_daily (tq_daily_2026.db)')
print('='*70)
try:
    cnt_sd = cur_d.execute('SELECT COUNT(*) FROM sector_daily').fetchone()[0]
    print(f'  sector_daily: {cnt_sd} 行')
    res = cur_d.execute('SELECT MAX(trade_date), MIN(trade_date) FROM sector_daily').fetchone()
    print(f'  trade_date 范围: {res[1]} ~ {res[0]}')
    print(f'  最新 trade_date: {res[0]}')
    cur_d.execute('SELECT sector_code, COUNT(*) as cnt FROM sector_daily GROUP BY sector_code ORDER BY cnt DESC LIMIT 10')
    print('  各板块记录数 TOP10:')
    for r in cur_d.fetchall():
        print(f'    {str(r[0]):>12s}: {r[1]} 行')
except Exception as e:
    print(f'  ERROR: {e}')
    cur_d.execute("SELECT name FROM sqlite_master WHERE type='table'")
    print(f'  tq_daily_2026.db 中所有表: {[r[0] for r in cur_d.fetchall()]}')

# 4. 视图检查
print()
print('='*70)
print('4. 视图存在性检查')
print('='*70)
cur_c.execute("SELECT name, sql FROM sqlite_master WHERE type='view'")
views = {r[0]: r[1] for r in cur_c.fetchall()}
print(f'  tq_core.db 中所有视图: {list(views.keys())}')
for vn in ['v_stock_daily_stock','v_stock_daily_index','v_security_daily']:
    status = 'EXISTS' if vn in views else 'NOT FOUND'
    print(f'  {vn:30s} : {status}')

# 5. security_info
print()
print('='*70)
print('5. security_info 覆盖与 asset_type 分布 (tq_core.db)')
print('='*70)
cnt_sei = cur_c.execute('SELECT COUNT(*) FROM security_info').fetchone()[0]
print(f'  总行数: {cnt_sei}')
cur_c.execute('SELECT asset_type, COUNT(*) as cnt FROM security_info GROUP BY asset_type ORDER BY cnt DESC')
for r in cur_c.fetchall():
    print(f'  {r[0]:>12s}: {r[1]} 条')
print('  security_info 样例（前10条）:')
cur_c.execute('SELECT * FROM security_info LIMIT 10')
cols = [d[0] for d in cur_c.description]
print(f'  列: {cols}')
for r in cur_c.fetchall():
    print(f'    {r}')

# 6. stock_daily 板块混入检查
print()
print('='*70)
print('6. stock_daily 板块数据混入检查 (tq_daily_2026.db)')
print('='*70)
sd_total = cur_d.execute('SELECT COUNT(*) FROM stock_daily').fetchone()[0]
sd_symbols = cur_d.execute('SELECT COUNT(DISTINCT ts_code) FROM stock_daily').fetchone()[0]
print(f'  stock_daily 总行数: {sd_total}')
print(f'  stock_daily 唯一ts_code数: {sd_symbols}')

# stock_daily 的 ts_code 是否包含 sector_code
sector_codes = [r[0] for r in cur_c.execute('SELECT sector_code FROM sector_info').fetchall()]
print(f'  sector_info 中共 {len(sector_codes)} 个板块code')

if sector_codes:
    ph = ','.join(['?']*len(sector_codes))
    hits = cur_d.execute(f'SELECT COUNT(*) FROM stock_daily WHERE ts_code IN ({ph})', sector_codes).fetchone()[0]
    if hits > 0:
        cur_d.execute(f'SELECT ts_code FROM stock_daily WHERE ts_code IN ({ph}) LIMIT 20', sector_codes)
        print(f'  WARNING: stock_daily 中有 {hits} 行ts_code匹配板块code:')
        for r in cur_d.fetchall():
            print(f'    {r[0]}')
    else:
        print(f'  OK: 无板块ts_code混入 stock_daily')

# ts_code 前缀分布
cur_d.execute('SELECT SUBSTR(ts_code,1,2), COUNT(*) FROM stock_daily GROUP BY SUBSTR(ts_code,1,2) ORDER BY COUNT(*) DESC LIMIT 20')
print('  stock_daily ts_code 前缀分布 TOP20:')
for r in cur_d.fetchall():
    print(f'    {str(r[0]):>10s}: {r[1]}')

conn_c.close()
conn_d.close()
print()
print('查询完成。')
