import urllib.request, json

def try_get(url, timeout=5):
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return r.status, r.read().decode('utf-8', errors='replace')
    except Exception as e:
        return None, str(e)[:300]

base = 'http://127.0.0.1:63162'
paths = ['/api/agents', '/api/agents/ceo', '/api/agent-config/ceo', '/api/config/agent/ceo',
         '/api/agents/ceo/config', '/api/agent/ceo/config', '/api/config/running/ceo']
for path in paths:
    s, body = try_get(base + path)
    ok = 'OK' if s == 200 else '--'
    print(f'{ok} GET {path} -> {s}')
    if s == 200 and body:
        try:
            j = json.loads(body)
            if isinstance(j, dict):
                run = j.get('running', {})
                lc = run.get('light_context_config', {})
                print(f'     strategy={lc.get("strategy")} max_input={run.get("max_input_length")}')
            else:
                print(f'     {str(j)[:200]}')
        except Exception:
            print(f'     {body[:300]}')
    print()
