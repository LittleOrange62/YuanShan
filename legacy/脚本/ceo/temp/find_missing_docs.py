import os

# 检查各个可能的位置
checks = [
    r'C:\Users\Administrator\.qwenpaw\configs',
    r'C:\Users\Administrator\.qwenpaw\workspaces\ceo\configs',
    r'C:\Users\Administrator\.qwenpaw\workspaces\data_in\tasks',
    r'C:\Users\Administrator\.qwenpaw\workspaces\data_in\configs',
    r'C:\Users\Administrator\.qwenpaw\workspaces\data_from\configs',
    r'C:\Users\Administrator\.qwenpaw\workspaces\strategy_engineer\configs',
    r'C:\Users\Administrator\.qwenpaw\workspaces\work-check\configs',
]
targets = ['feishu_push_governance.md', 'supervision_protocol.md', 'model_switching.md',
           'task_template.md', 'cron_tasks_governance.md', 'compute_kline_factors.md']

for c in checks:
    if not os.path.isdir(c):
        continue
    print(f'[{c}]')
    for t in targets:
        if os.path.exists(os.path.join(c, t)):
            print(f'   FOUND: {t}')
    # 列出目录下所有 md
    mds = [f for f in os.listdir(c) if f.endswith('.md')]
    if mds:
        print(f'   md files: {", ".join(mds[:20])}')
    print()
