#!/usr/bin/env python3
import sqlite3

conn = sqlite3.connect('/app/working/workspaces/data_in/data/kline_factors.db')

print("=== kline_factors 字段完整性 ===")
fields = ['open', 'close', 'high', 'low', 'vol', 'ma5', 'ma10', 'ma20', 
          'xmorph', 'xtrend', 'xpos', 'xchip', 'vhealth']
for f in fields:
    cnt = conn.execute('SELECT COUNT(*) FROM kline_factors WHERE "' + f + '" IS NOT NULL').fetchone()[0]
    null_cnt = conn.execute('SELECT COUNT(*) FROM kline_factors WHERE "' + f + '" IS NULL').fetchone()[0]
    print(f"  {f}: {cnt}有值 / {null_cnt}为空")

print("\n=== 样例数据（深科技） ===")
r = conn.execute(
    "SELECT ts_code, trade_date, close, ma5, xmorph, xtrend, xpos, xchip, vhealth "
    "FROM kline_factors WHERE ts_code='000021.SZ' ORDER BY trade_date DESC LIMIT 1"
).fetchone()
if r:
    print(f"  股票：{r[0]}, 日期：{r[1]}, 收盘价：{r[2]}")
    print(f"  MA20: {r[3]:.2f}")
    print(f"  X 因子 - xmorph:{r[4]:.1f} xtrend:{r[5]:.1f} xpos:{r[6]:.1f} xchip:{r[7]:.1f} vhealth:{r[8]:.1f}")

conn.close()
