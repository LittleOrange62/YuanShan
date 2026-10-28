#!/usr/bin/env python3
"""测试原版鑫鑫板块热度接口（加强重试版）"""
import sys
sys.path.insert(0, '/app/working/workspaces/manager/原智能体workspace')
from scripts.sector_heat.providers.sector_live_eastmoney import fetch_boards

# 概念板块
boards = fetch_boards("m:90+t:3", page=1, page_size=50)
print(f'概念板块: {len(boards)} 个')
for b in boards[:3]:
    print(f'  {b["name"]}: pct={b["pct"]:.1f}')

# 行业板块
boards2 = fetch_boards("m:90+t:2", page=1, page_size=50)
print(f'行业板块: {len(boards2)} 个')
for b in boards2[:3]:
    print(f'  {b["name"]}: pct={b["pct"]:.1f}')
