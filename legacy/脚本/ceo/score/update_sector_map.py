#!/usr/bin/env python3
"""完善 stock_sector_map.json — 焦点股票的板块映射（手动补充）"""
import json

path = '/app/working/workspaces/data_in/data/stock_sector_map.json'
with open(path, 'r', encoding='utf-8') as f:
    mapping = json.load(f)

# 补充映射（基于已知主营业务）
additional = {
    '001208.SZ': '电线电缆',       # 华菱线缆
    '000600.SZ': '电力',          # 建投能源
    '920808.BJ': '芯片',          # 盛合晶微（半导体）
    '688820.SH': '半导体',        # 金宏气体（半导体材料）
    '688106.SH': '半导体',        # 金宏气体（科创板）
    '000988.SZ': '激光',          # 华工科技（激光设备）
    '000021.SZ': '存储芯片',      # 深科技
    '002138.SZ': '电子元件',      # 顺络电子
    '600487.SH': '光纤',          # 亨通光电
    '300346.SZ': '半导体材料',    # 南大光电
    '002182.SZ': '金属',          # 宝武镁业（镁合金）
}

mapping.update(additional)
print(f"总映射数量: {len(mapping)}")

with open(path, 'w', encoding='utf-8') as f:
    json.dump(mapping, f, ensure_ascii=False, indent=2)

print("已更新:", json.dumps(mapping, ensure_ascii=False, indent=2))
