#!/usr/bin/env python3
"""生成 stock_sector_map.json — 焦点股票的板块映射"""
import json

# 先查stock_concept表看看我们的焦点股有没有映射
import sqlite3
conn = sqlite3.connect('/app/working/workspaces/data_in/data/stock_basics.db')

# 焦点股列表
focus_stocks = [
    '001208.SZ', '000600.SZ', '920808.BJ', '688820.SH', '688008.SH',
    '603893.SH', '000988.SZ', '300666.SZ', '002409.SZ', '000021.SZ',
    '002138.SZ', '600487.SH', '688106.SH', '300346.SZ', '002182.SZ',
]

# 查concept映射
print("=== 焦点股concept映射 ===")
mapping = {}
for ts in focus_stocks:
    concepts = conn.execute(
        "SELECT concept_name FROM stock_concept WHERE ts_code=?", (ts,)
    ).fetchall()
    if concepts:
        mapping[ts] = concepts[0][0]
        print(f"  {ts}: {concepts[0][0]}")
    else:
        print(f"  {ts}: 无映射")

conn.close()

print(f"\n=== 生成映射: {len(mapping)}只 ===")
print(json.dumps(mapping, ensure_ascii=False, indent=2))

# 保存
path = '/app/working/workspaces/data_in/data/stock_sector_map.json'
with open(path, 'w', encoding='utf-8') as f:
    json.dump(mapping, f, ensure_ascii=False, indent=2)
print(f"已保存到 {path}")
