import urllib.request, json

def try_get(url, timeout=5):
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return r.status, r.read().decode('utf-8', errors='replace')
    except Exception as e:
        return None, str(e)[:300]

base = 'http://127.0.0.1:63162'

# ceo 完整运行配置
s, body = try_get(base + '/api/agents/ceo')
if s == 200:
    j = json.loads(body)
    print('=== /api/agents/ceo 关键字段 ===')
    print('strategy:', j.get('running', {}).get('light_context_config', {}).get('strategy'))
    cc = j.get('running', {}).get('light_context_config', {}).get('context_compact_config', {})
    print('compact_enabled:', cc.get('enabled'), 'ratio:', cc.get('compact_threshold_ratio'))
    print('active_model:', j.get('active_model'))

# tools 状态
s, body = try_get(base + '/api/tools')
if s == 200:
    tools = json.loads(body)
    print()
    print('=== /api/tools 全部工具状态 ===')
    for t in tools:
        print(f'  {t["name"]:28s} enabled={str(t["enabled"]):5s} async={t["async_execution"]}')
