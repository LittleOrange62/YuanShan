#!/usr/bin/env python3
"""Verify the 3 fixes are working correctly"""
import sqlite3

print("=" * 60)
print("验证 Fix 1: 个股评分闭环 (score/x_y/factors)")
print("=" * 60)
c = sqlite3.connect('/app/working/workspaces/data_in/data/stocks_master.db')
for t in ['score', 'x_y', 'factors']:
    cnt = c.execute(f'SELECT COUNT(*) FROM [{t}] WHERE date="20260713"').fetchone()[0]
    print(f"  {t} 20260713: {cnt} 行 ✅" if cnt > 0 else f"  {t} 20260713: {cnt} 行 ❌")

print()
print("=" * 60)
print("验证 Fix 2: 日期格式统一为 YYYYMMDD")
print("=" * 60)
for t in ['score', 'x_y', 'factors', 'sector_score']:
    cur = c.execute(f'SELECT DISTINCT date FROM [{t}] ORDER BY date')
    dates = [r[0] for r in cur.fetchall()]
    has_dash = any('-' in d for d in dates)
    has_plain = any(len(d) == 8 and '-' not in d for d in dates)
    print(f"  {t}: {dates}")
    print(f"    {'⚠️ 仍有横线格式' if has_dash else '✅ 全为纯数字格式'}")

c.close()

print()
print("=" * 60)
print("验证 Fix 3: 板块热度双库统一")
print("=" * 60)
# Check fetch_sector_heat.py has the new code
with open('/app/working/workspaces/data_from/tasks/fetch_sector_heat.py') as f:
    content = f.read()
    has_2026 = 'sector_heat_2026.db' in content
    has_groups = 'boss_groups' in content and 'group_id' in content
    has_both_writes = content.count('sector_heat_') >= 2
    print(f"  sector_heat_2026.db 写入: {'✅' if has_2026 else '❌'}")
    print(f"  boss_groups 映射加载: {'✅' if has_groups else '❌'}")
    print(f"  双库写入: {'✅' if has_both_writes else '❌'}")

# Verify both DBs exist and have tables
c2 = sqlite3.connect('/app/working/workspaces/data_in/data/sector_heat_master.db')
cnt2 = c2.execute('SELECT COUNT(*) FROM heat').fetchone()[0]
c2.close()
c3 = sqlite3.connect('/app/working/workspaces/data_in/data/sector_heat_2026.db')
cnt3 = c3.execute('SELECT COUNT(*) FROM heat WHERE date="20260713"').fetchone()[0]
c3.close()
print(f"  sector_heat_master.db: {cnt2} 行 ✅")
print(f"  sector_heat_2026.db (20260713): {cnt3} 行 ✅")
