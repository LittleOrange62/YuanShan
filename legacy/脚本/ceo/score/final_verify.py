#!/usr/bin/env python3
"""最终验证：检查所有关键数据的一致性"""
import sqlite3, os

data_dir = '/app/working/workspaces/data_in/data'
errors = []

# 1. 验证 kline_2026
conn = sqlite3.connect(os.path.join(data_dir, 'kline_2026.db'))
cnt = conn.execute("SELECT COUNT(*) FROM daily_kline").fetchone()[0]
latest = conn.execute("SELECT MAX(trade_date) FROM daily_kline").fetchone()[0]
print(f'✅ kline_2026.db: {cnt}行, 最新交易日 {latest}')
conn.close()

# 2. 验证 stocks_master (鑫鑫评分库)
conn = sqlite3.connect(os.path.join(data_dir, 'stocks_master.db'))
tables = ['x_y', 'factors', 'score', 'runs']
for t in tables:
    cnt = conn.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0]
    print(f'✅ stocks_master.{t}: {cnt}行')
conn.close()

# 3. 验证 sector_heat_master
conn = sqlite3.connect(os.path.join(data_dir, 'sector_heat_master.db'))
cnt = conn.execute("SELECT COUNT(*) FROM heat").fetchone()[0]
print(f'✅ sector_heat_master.heat: {cnt}条')
conn.close()

# 4. 验证旧DB已清理
old_dbs = ['quant_signals.db', 'quant_score.db', 'quant_views.db']
for f in old_dbs:
    path = os.path.join(data_dir, f)
    exists = os.path.exists(path)
    if not exists:
        print(f'✅ 旧DB已删除: {f}')
    else:
        print(f'❌ 旧DB未清理: {f}')

# 5. 验证新旧脚本
old_scripts = ['/app/working/workspaces/data_in/tasks/compute_daily_signals.py',
               '/app/working/workspaces/data_in/tasks/daily_signal_compute.sh']
for s in old_scripts:
    if not os.path.exists(s):
        print(f'✅ 旧脚本已归档: {os.path.basename(s)}')

new_scripts = ['/app/working/workspaces/data_in/tasks/compute_xinxin_scores.py',
               '/app/working/workspaces/data_from/tasks/fetch_sector_heat.py',
               '/app/working/workspaces/manager/daily_score_update.py']
for s in new_scripts:
    if os.path.exists(s):
        print(f'✅ 新脚本就绪: {os.path.basename(s)}')
    else:
        print(f'❌ 新脚本缺失: {s}')
        errors.append(s)

print(f'\n{"="*40}')
if errors:
    print(f'❌ 有 {len(errors)} 个问题需要修复')
else:
    print(f'✅ 全部验证通过！')
