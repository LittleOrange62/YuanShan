import json, os, glob

base = r'C:\Users\Administrator\.qwenpaw\workspaces'
for f in sorted(glob.glob(os.path.join(base, '*', 'agent.json'))):
    try:
        with open(f, encoding='utf-8') as fh:
            j = json.load(fh)
        run = j.get('running', {})
        lc = run.get('light_context_config', {})
        sc = lc.get('scroll_config', {})
        cc = lc.get('context_compact_config', {})
        am = j.get('active_model', {})
        print(f"=== {os.path.basename(os.path.dirname(f))} ===")
        print(f"  active_model: {am.get('provider_id')}:{am.get('model')}")
        print(f"  max_input_length: {run.get('max_input_length')}")
        print(f"  context_manager_backend: {run.get('context_manager_backend')}")
        print(f"  strategy: {lc.get('strategy')}")
        print(f"  scroll_config keys: {sorted(sc.keys())}")
        print(f"  history_retention_days: {sc.get('history_retention_days')}")
        print(f"  offload_dialog: {sc.get('offload_dialog')}")
        print(f"  compact_enabled: {cc.get('enabled')}, ratio: {cc.get('compact_threshold_ratio')}")
        print(f"  tool_result_pruning enabled: {lc.get('tool_result_pruning_config', {}).get('enabled')}")
    except Exception as e:
        print(f'=== {f} === ERROR: {e}')
