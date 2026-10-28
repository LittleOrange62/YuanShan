# -*- coding: utf-8 -*-
import sqlite3

conn = sqlite3.connect(r'D:\finance\data\tq_minline_2026.db')
cur = conn.cursor()
# 找旧 tq 数据里的个股 (6开头/000002等深圳个股)
cur.execute("SELECT DISTINCT ts_code FROM stock_minline WHERE source='tq' AND period='1m'")
codes = [r[0] for r in cur.fetchall()]
stock = [c for c in codes if c.startswith(('600', '601', '603', '000', '002', '300', '688'))]
print('旧tq代码数:', len(codes), '个股:', len(stock), stock[:10])
if stock:
    code = stock[0]
    print('用', code, '验证:')
    cur.execute("SELECT substr(trade_datetime,1,8) d, SUM(vol) sv, SUM(amount) sa "
                "FROM stock_minline WHERE ts_code=? AND period='1m' AND source='tq' "
                "GROUP BY d ORDER BY d DESC LIMIT 3", (code,))
    min_rows = cur.fetchall()
    cur2 = sqlite3.connect(r'D:\finance\data\tq_daily_2026.db')
    c2 = cur2.cursor()
    for d, sv, sa in min_rows:
        c2.execute("SELECT trade_date, close, vol, amount FROM stock_daily WHERE ts_code=? AND trade_date=?",
                   (code, d))
        r = c2.fetchone()
        if r:
            print(f'  {d}: 分钟vol合计={sv:.0f} vs 日线vol={r[2]:.0f} (手) → 比值={sv/r[2]:.2f}')
            print(f'      分钟amount合计={sa:.0f} vs 日线amount={r[3]:.0f} → 比值={sa/r[3]:.2f}')
    cur2.close()
conn.close()
