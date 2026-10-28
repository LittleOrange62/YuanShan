import os, re

base = r'C:\Users\Administrator\.qwenpaw\workspaces\ceo'
with open(os.path.join(base, 'AGENTS.md'), encoding='utf-8') as fh:
    content = fh.read()
print('AGENTS.md size:', len(content), 'bytes')

refs = re.findall(r'\[([^\]]+)\]\(\.?/?([^)]+\.md)\)', content)
print()
print('=== AGENTS.md 引用的文档检查 ===')
for label, path in refs:
    p = path.split('#')[0]
    full = os.path.join(base, p)
    exists = os.path.exists(full)
    print(f'  {label:50s} -> {p:55s} {"OK" if exists else "MISSING!"}')
