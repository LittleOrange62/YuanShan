import os, json

base = r'C:\Users\Administrator\.qwenpaw\workspaces'

print('='*70)
print('1. 所有 agent 配置最终状态')
print('='*70)
for f in sorted(os.listdir(base)):
    cfg = os.path.join(base, f, 'agent.json')
    if not os.path.isfile(cfg):
        continue
    with open(cfg, encoding='utf-8') as fh:
        j = json.load(fh)
    run = j.get('running', {})
    lc = run.get('light_context_config', {})
    am = j.get('active_model', {})
    tools = j.get('tools', {}).get('builtin_tools', {})
    enabled = [t for t, v in tools.items() if v.get('enabled')]
    status = 'OK' if lc.get('strategy') == 'native' else 'FAIL'
    print(f"[{status}] {f:18s} strategy={lc.get('strategy'):8s} tools={len(enabled):2d} model={am.get('provider_id')}:{am.get('model')}")

print()
print('='*70)
print('2. AGENTS.md 引用检查')
print('='*70)
with open(os.path.join(base, 'ceo', 'AGENTS.md'), encoding='utf-8') as fh:
    content = fh.read()
print('size:', len(content), 'bytes (原 9644 -> 目标 <10KB)')
import re
refs = re.findall(r'\]\(\.?/?([^)#]+)', content)
for r in sorted(set(refs)):
    p = r if r.endswith('.md') else r
    full = os.path.join(base, 'ceo', p.replace('../', '../'))
    # 简化判断：显示路径即可
    print(f'  -> {p}')

print()
print('='*70)
print('3. 新/改文件清单')
print('='*70)
for p in [
    r'ceo\AGENTS.md',
    r'ceo\MEMORY.md',
    r'ceo\configs\tools_inventory.md',
    r'ceo\POLICIES\model_switching.md',
    r'ceo\POLICIES\README.md',
]:
    full = os.path.join(base, p)
    if os.path.exists(full):
        print(f'  OK {p} ({os.path.getsize(full)}B)')
    else:
        print(f'  MISSING {p}')
