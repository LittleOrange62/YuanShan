#!/usr/bin/env python3
"""30日评分趋势报表"""
import sqlite3

conn = sqlite3.connect('/app/working/workspaces/data_in/data/quant_score.db')

# 重点股列表
FOCUS = ['001208.SZ','000600.SZ','920808.BJ','688820.SH','688008.SH',
         '603893.SH','000988.SZ','300666.SZ','002409.SZ','000021.SZ','002138.SZ','600487.SH']

print("=" * 80)
print("📊 重点股 30日评分趋势（20260515 → 20260629）")
print("=" * 80)
print(f"{'股票':<12} {'首日分':<8} {'末日分':<8} {'变动':<8} {'趋势':<6} {'末象限':<10} {'末综合分':<8}")
print("-" * 80)

for ts in FOCUS:
    first = conn.execute("SELECT composite_score, name FROM quant_scores WHERE ts_code=? AND trade_date='20260515'", (ts,)).fetchone()
    last = conn.execute("SELECT composite_score, quadrant, name FROM quant_scores WHERE ts_code=? AND trade_date='20260629'", (ts,)).fetchone()
    
    if first and last:
        fs = round(first[0], 1)
        ls = round(last[0], 1)
        name = first[1] or last[2] or ts
        change = round(ls - fs, 1)
        trend = '▲' if change > 5 else ('▼' if change < -5 else '→')
        print(f"{name:<12} {fs:<8} {ls:<8} {change:<+8} {trend:<6} {last[1]:<10} {ls:<8}")

print()
print("=" * 80)
print("📊 重点股最新日评分明细（2026-06-29）")
print("=" * 80)
print(f"{'名称':<10} {'X空间':<7} {'Y质量':<7} {'Z风险':<7} {'象限':<8} {'综合分':<7} {'说明'}")
print("-" * 80)

for ts in FOCUS:
    r = conn.execute("""
        SELECT q.name, q.x_raw, q.y_raw, q.z_raw, q.quadrant_raw, q.quadrant, q.composite_score
        FROM quant_scores q WHERE q.ts_code=? AND q.trade_date='20260629'
    """, (ts,)).fetchone()
    if r:
        name, x, y, z, qr, qf, cs = r
        note = ''
        if qr != qf:
            if qr in ['Q1','Q2'] and qf in ['Q2','Q3']:
                note = f'⚠️ Z轴降级({qr}→{qf})'
            else:
                note = f'Z轴过滤({qr}→{qf})'
        print(f"{name:<10} {round(x,1):<7} {round(y,1):<7} {round(z,1):<7} {qf:<8} {round(cs,1):<7} {note}")

print()
print("=" * 80)
print("📊 全量自选股（191只）四象限分布 — 20260629")
print("=" * 80)

for q in ['Q1','Q2','Q3','Q4']:
    cnt = conn.execute("SELECT COUNT(*) FROM quant_scores WHERE trade_date='20260629' AND quadrant=?", (q,)).fetchone()[0]
    desc = {'Q1':'核心持仓','Q2':'底仓持有','Q3':'回避','Q4':'谨慎投机'}[q]
    print(f"  {q} {desc:<10} → {cnt}只")

print()
print("自选股 Top 10（综合分）:")
print("-" * 60)
for r in conn.execute("""
    SELECT name, ROUND(x_raw,1), ROUND(y_raw,1), ROUND(z_raw,1), quadrant, ROUND(composite_score,1)
    FROM quant_scores WHERE trade_date='20260629'
    ORDER BY composite_score DESC LIMIT 10
"""):
    name, x, y, z, q, cs = r
    print(f"  {name:<12} X={x} Y={y} Z={z} {q:<8} {cs}分")

print()
print("自选股 Bottom 5（综合分）:")
print("-" * 60)
for r in conn.execute("""
    SELECT name, ROUND(x_raw,1), ROUND(y_raw,1), ROUND(z_raw,1), quadrant, ROUND(composite_score,1)
    FROM quant_scores WHERE trade_date='20260629'
    ORDER BY composite_score ASC LIMIT 5
"""):
    name, x, y, z, q, cs = r
    print(f"  {name:<12} X={x} Y={y} Z={z} {q:<8} {cs}分")

conn.close()
