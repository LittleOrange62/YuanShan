#!/usr/bin/env python3
"""测试原版鑫鑫板块热度接口"""
import sys
sys.path.insert(0, '/app/working/workspaces/manager/原智能体workspace')
from scripts.sector_heat.providers.sector_live import list_live_boards

boards = list_live_boards()
print(f'获取到 {len(boards)} 个板块')
for b in boards[:5]:
    print(f'  {b["name"]}: heat={b.get("live_heat", 0):.1f}')
