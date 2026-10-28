# -*- coding: utf-8 -*-
"""只读：生成真实远山评分卡（基于 plan1 08-12 评分 + stock_basic 名称 + watchlist 池）"""
import sqlite3, re

FACTOR_DB = r'D:\finance\data\fin_factor.db'
DATA_DB = r'D:\finance\data\fin_data.db'
WATCH_DB = r'D:\finance\data\fin_watchlist.db'
TRADE_DATE = '2026-08-12'

# 1. 名称映射
con = sqlite3.connect(DATA_DB)
name_map = dict(con.execute("SELECT symbol, name FROM stock_basic").fetchall())
con.close()

# 2. 自选池（watchlist）
try:
    con = sqlite3.connect(WATCH_DB)
    cur = con.cursor()
    cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
    print('watchlist 表:', [r[0] for r in cur.fetchall()])
    watch_symbols = set()
    for tbl in ['watchlist_stocks', 'focus_stocks']:
        try:
            cur.execute(f"SELECT * FROM {tbl} LIMIT 1")
            cols = [d[0] for d in cur.description]
            print(f'{tbl} 字段:', cols)
            cur.execute(f"SELECT * FROM {tbl}")
            for r in cur.fetchall():
                row = dict(zip(cols, r))
                sym = str(row.get('symbol', row.get('code', '')))
                watch_symbols.add(sym)
        except Exception as e:
            print(f'{tbl} 失败: {e}')
    con.close()
    print('自选池原始符号数:', len(watch_symbols), list(watch_symbols)[:10])
except Exception as e:
    print('watchlist 打开失败:', e)
    watch_symbols = set()

# 3. 拉 plan1 08-12 评分
con = sqlite3.connect(FACTOR_DB)
cur = con.cursor()
cur.execute("""
SELECT symbol, x_score, y_score, oper_score, total_score
FROM score_results
WHERE plan_id=1 AND trade_date=?
""", (TRADE_DATE,))
rows = cur.fetchall()
con.close()
print(f'plan1 {TRADE_DATE} 总记录:', len(rows))

# 4. 过滤：仅保留真实A股（剔除指数/转债/基金/ETF/LOF）
def is_stock(sym):
    # SH600xxx-SH605xxx, SH688xxx(科创), SZ000xxx/001xxx/002xxx/003xxx, SZ300xxx/301xxx, BJ 8xxxxx/4xxxxx/92xxxx
    code = sym[2:]
    if sym.startswith('SH'):
        if code.startswith(('600','601','603','605','688','689')):
            return True
        return False
    if sym.startswith('SZ'):
        if code.startswith(('000','001','002','003','300','301')):
            return True
        return False
    if sym.startswith(('BJ','83','87','92')):
        return True
    return False

stock_rows = [(s, x, y, o, t) for s, x, y, o, t in rows if is_stock(s)]
stock_rows.sort(key=lambda r: r[4], reverse=True)
print('过滤后真实个股数:', len(stock_rows))
print('\n=== 🌐 全市场前20 ===')
for i, (s, x, y, o, t) in enumerate(stock_rows[:20], 1):
    nm = name_map.get(s, '?')
    print(f'{i}. {s} {nm} X={x:.2f} Y={y:.2f} Oper={o:.2f} Total={t:.2f}')

# 5. 自选前10（与自选池交集）
print('\n=== 📋 自选池（含重点）===')
watch_norm = set()
for w in watch_symbols:
    w2 = w.strip().upper()
    if not w2.startswith(('SH','SZ','BJ')):
        # 纯数字转前缀
        if w2.startswith(('6','68')):
            w2 = 'SH' + w2
        elif w2.startswith(('0','3')):
            w2 = 'SZ' + w2
    watch_norm.add(w2)
print('标准化自选池:', len(watch_norm))
watch_scored = [(s, x, y, o, t) for s, x, y, o, t in stock_rows if s in watch_norm]
watch_scored.sort(key=lambda r: r[4], reverse=True)
print('自选池有评分数:', len(watch_scored))
for i, (s, x, y, o, t) in enumerate(watch_scored[:15], 1):
    nm = name_map.get(s, '?')
    print(f'{i}. {s} {nm} Total={t:.2f} X={x:.2f}')

# 6. 板块24（SH880xxx 或板块池）
print('\n=== 📊 板块（SH88xxxx）===')
sector_rows = [(s, x, y, o, t) for s, x, y, o, t in rows if s.startswith('SH88') or s.startswith('SZ88')]
sector_rows.sort(key=lambda r: r[4], reverse=True)
print('板块记录数:', len(sector_rows))
for i, (s, x, y, o, t) in enumerate(sector_rows[:30], 1):
    nm = name_map.get(s, '?')
    print(f'{i}. {s} {nm} Total={t:.2f}')
