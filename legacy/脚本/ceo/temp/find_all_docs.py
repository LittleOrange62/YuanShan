import os, glob

base = r'C:\Users\Administrator\.qwenpaw\workspaces'
targets = ['supervision_protocol.md', 'model_switching.md', 'task_template.md',
           'cron_tasks_governance.md', 'compute_kline_factors.md',
           'feishu_push_governance.md', 'tools_inventory.md']

print('=== 全局搜索这些文档 ===')
for t in targets:
    hits = glob.glob(os.path.join(base, '**', t), recursive=True)
    print(f'{t:30s}: {len(hits)} 处')
    for h in hits:
        print(f'    {h}')
