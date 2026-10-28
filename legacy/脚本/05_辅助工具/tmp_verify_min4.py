# -*- coding: utf-8 -*-
import sqlite3

conn = sqlite3.connect(r'D:\finance\data\tq_minline_2026.db')
cur = conn.cursor()
# 找旧 tq 数据里的一只股票
cur.execute("SELECT DISTINCT ts_code FROM stock_minline WHERE source='tq' AND period='1m' LIMIT 1")
row = cur.fetchone()
if not row:
    print('无旧 tq 1m 数据')
else:
    code = row[0]
    print('旧 tq 股票:', code)
    # 该股票某天分钟 vol 求和
    cur.execute("SELECT substr(trade_datetime,1,8) d, SUM(vol) sv, SUM(amount) sa, COUNT(*) "
                "FROM stock_minline WHERE ts_code=? AND period='1m' AND source='tq' "
                "GROUP BY d ORDER BY d DESC LIMIT 3", (code,))
    for r in cur.fetchall():
        print(f'  {r[0]}: 分钟vol合计={r[1]:.0f} amount合计={r[2]:.0f} 条数={r[3]}')
    # 对照日线
    cur2 = sqlite3.connect(r'D:\finance\data\tq_daily_2026.db')
    c2 = cur2.cursor()
    c2.execute("SELECT trade_date, close, vol, amount FROM stock_daily WHERE ts_code=? ORDER BY trade_date DESC LIMIT 3", (code,))
    print('日线对照:')
    for r in c2.fetchall():
        print(f'  {r[0]}: vol={r[2]:.0f} amount={r[3]:.0f}')
    cur2.close()
conn.close()
