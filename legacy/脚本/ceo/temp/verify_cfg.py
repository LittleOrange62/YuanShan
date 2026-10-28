import json, os, glob

base = r'C:\Users\Administrator\.qwenpaw\workspaces'
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
    print(f"{f:20s} strategy={lc.get('strategy'):8s} compact={lc.get('context_compact_config',{}).get('enabled')} ratio={lc.get('context_compact_config',{}).get('compact_threshold_ratio')} max_input={run.get('max_input_length')} model={am.get('provider_id')}:{am.get('model')} tools({len(enabled)})")
