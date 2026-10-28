import urllib.request, json

def try_get(url, timeout=5):
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return r.status, r.read().decode('utf-8', errors='replace')
    except Exception as e:
        return None, str(e)[:300]

base = 'http://127.0.0.1:63162'

s, body = try_get(base + '/api/agents/ceo')
if s == 200:
    j = json.loads(body)
    tools = j.get('tools', {}).get('builtin_tools', {})
    print('=== agent.json 中 tools 状态（API 读取） ===')
    for name, cfg in tools.items():
        print(f'  {name:28s} enabled={str(cfg.get("enabled"))}')

print()
s, body = try_get(base + '/api/agents/ceo/tools')
if s == 200:
    tools = json.loads(body)
    print('=== /api/agents/ceo/tools 列表 ===')
    for t in tools:
        print(f'  {t["name"]:28s} enabled={str(t["enabled"])}')
