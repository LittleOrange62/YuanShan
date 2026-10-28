#!/usr/bin/env python3
"""清理旧的R信号数据库"""
import sqlite3, os

data_dir = '/app/working/workspaces/data_in/data'
to_clean = [
    # 旧R信号库（已经彻底废弃）
    ('quant_signals.db', '旧的R001-R009信号数据'),
    ('quant_score.db', '旧的评分数据'),
    ('quant_views.db', '旧视图数据（已由kline_2026替代）'),
]

for fname, desc in to_clean:
    path = os.path.join(data_dir, fname)
    if os.path.exists(path):
        size = os.path.getsize(path) / 1024
        os.remove(path)
        print(f'🗑️ 已删除 {fname} ({size:.0f}KB) — {desc}')
    else:
        print(f'  {fname} 不存在，跳过')

# 检查清理后的状态
print('\n=== 清理后data_in目录状态 ===')
for f in sorted(os.listdir(data_dir)):
    if f.endswith('.db'):
        path = os.path.join(data_dir, f)
        size = os.path.getsize(path) / 1024
        print(f'  {f}: {size:.0f}KB')
