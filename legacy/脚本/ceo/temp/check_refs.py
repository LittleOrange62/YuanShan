import os, re

base = r'C:\Users\Administrator\.qwenpaw\workspaces\ceo'
# Read AGENTS.md
with open(os.path.join(base, 'AGENTS.md'), encoding='utf-8') as fh:
    content = fh.read()

# Find all .md references
refs = re.findall(r'\[([^\]]+)\]\(\.?/?([^)]+\.md)\)', content)
print('=== AGENTS.md 引用的文档 ===')
for label, path in refs:
    p = path.split('#')[0]
    full = os.path.join(base, p)
    exists = os.path.exists(full)
    print(f'  {label:45s} -> {p:60s} {"OK" if exists else "MISSING!"}')

print()
print('=== configs/ 目录 ===')
for f in sorted(os.listdir(os.path.join(base, 'configs'))):
    print('  ', f)

print()
print('=== 引用的 configs 是否存在 ===')
for f in ['feishu_push_governance.md', 'supervision_protocol.md', 'model_switching.md', 'task_template.md', 'cron_tasks_governance.md', 'pyramid_knowledge_framework.md']:
    print(f'  {f}: {os.path.exists(os.path.join(base, "configs", f))}')
