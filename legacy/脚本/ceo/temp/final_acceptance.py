import sqlite3

print('========== 全量验收：深市日线补数 ==========')

# 1. 各年度 DB 的 .SZ 行数
print('\n=== 各年度 tq_daily_YYYY.db 的 .SZ 行数 ===')
total_sz = 0
for yr in ['2021', '2022', '2023', '2024', '2025', '2026']:
    try:
        conn = sqlite3.connect(rf'D:\finance\data\tq_daily_{yr}.db')
        cur = conn.cursor()
        cur.execute("select count(*) from stock_daily where ts_code like '%.SZ'")
        n = cur.fetchone()[0]
        total_sz += n
        cur.execute("select count(*) from stock_daily")
        total = cur.fetchone()[0]
        print(f'tq_daily_{yr}.db: .SZ={n} 总={total}')
        conn.close()
    except Exception as e:
        print(f'tq_daily_{yr}.db: ERROR {e}')

print(f'\n.SZ 总计: {total_sz}')

# 2. 关键股票验证（2026 年数据）
print('\n=== 关键深市股票 2026 年最新行情 ===')
conn = sqlite3.connect(r'D:\finance\data\tq_daily_2026.db')
cur = conn.cursor()
for code, name in [('000001.SZ', '平安银行'), ('000858.SZ', '五粮液'), ('300750.SZ', '宁德时代'), ('002594.SZ', '比亚迪'), ('300059.SZ', '东方财富')]:
    cur.execute("select trade_date, open, close, vol from stock_daily where ts_code=? order by trade_date desc limit 1", (code,))
    r = cur.fetchone()
    if r:
        print(f'{code} {name}: {r[0]} close={r[1]} vol={r[3]:.0f}')
    else:
        print(f'{code} {name}: 缺失!')

# 3. 后缀分布
print('\n=== tq_daily_2026.db 后缀分布 ===')
cur.execute("select substr(ts_code,-3) as suf, count(*) from stock_daily group by suf order by 2 desc")
for r in cur.fetchall():
    print(r)

# 4. 与 stock_info 元数据对比（预期 .SZ 股票数）
print('\n=== stock_info 元数据 .SZ 股票数 ===')
conn2 = sqlite3.connect(r'D:\finance\data\tq_core.db')
cur2 = conn2.cursor()
cur2.execute("select count(*) from stock_info where ts_code like '%.SZ' and asset_type='stock'")
print('stock_info .SZ stock 数:', cur2.fetchone()[0])

# 5. 数据完整性抽查：五粮液 2021-2026 每年有多少条
print('\n=== 五粮液 000858.SZ 各年度行数 ===')
for yr in ['2021', '2022', '2023', '2024', '2025', '2026']:
    c = sqlite3.connect(rf'D:\finance\data\tq_daily_{yr}.db')
    cc = c.cursor()
    cc.execute("select count(*) from stock_daily where ts_code='000858.SZ'")
    print(f'{yr}: {cc.fetchone()[0]}')
    c.close()

conn.close()
conn2.close()
