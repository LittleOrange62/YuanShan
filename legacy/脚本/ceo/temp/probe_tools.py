import urllib.request, json

def try_get(url, timeout=5):
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return r.status, r.read().decode('utf-8', errors='replace')
    except Exception as e:
        return None, str(e)[:300]

base = 'http://127.0.0.1:63162'

# 尝试带 agent 参数的 tools API
paths = [
    '/api/tools?agent_id=ceo',
    '/api/agent/ceo/tools',
    '/api/agents/ceo/tools',
    '/api/tools/ceo',
]
for p in paths:
    s, body = try_get(base + p)
    print(f'GET {p} -> {s}')
    if s == 200:
        print(f'   {body[:500]}')
    print()

# 看 agents 列表接口返回的完整 ceo 信息
s, body = try_get(base + '/api/agents/ceo')
if s == 200:
    j = json.loads(body)
    print('=== ceo agent 的 tools 字段 ===')
    print(json.dumps(j.get('tools', {}), ensure_ascii=False, indent=1)[:1500])
