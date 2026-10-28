#!/usr/bin/env python3
"""检查板块映射名是否在热度数据中"""
import sqlite3

conn = sqlite3.connect('/app/working/workspaces/data_in/data/sector_heat_master.db')

# 看看我们的映射名是否命中
our_sectors = [
    '电线电缆', '电力', '芯片', '半导体', 'AI芯片', '第三代半导体',
    '光刻机(胶)', '激光', '存储芯片', '电子元件', '光纤', '半导体材料', '金属'
]

print("=== 映射命中检查 ===")
for s in our_sectors:
    r = conn.execute("SELECT heat_score FROM heat WHERE sector_name LIKE ?", (f'%{s}%',)).fetchall()
    if r:
        print(f"  ✅ '{s}' → 命中! score={r[0][0]:.1f}")
    else:
        print(f"  ❌ '{s}' → 未命中")

# 列出所有含"半导"的热度板块
print("\n=== 含'半导'的板块 ===")
for r in conn.execute("SELECT sector_name, heat_score FROM heat WHERE sector_name LIKE '%半导%'").fetchall():
    print(f"  {r[0]}: {r[1]:.1f}")

print("\n=== 含'芯片'的板块 ===")
for r in conn.execute("SELECT sector_name, heat_score FROM heat WHERE sector_name LIKE '%芯片%'").fetchall():
    print(f"  {r[0]}: {r[1]:.1f}")

print("\n=== 含'电力'的板块 ===")
for r in conn.execute("SELECT sector_name, heat_score FROM heat WHERE sector_name LIKE '%电力%'").fetchall():
    print(f"  {r[0]}: {r[1]:.1f}")
    
conn.close()
