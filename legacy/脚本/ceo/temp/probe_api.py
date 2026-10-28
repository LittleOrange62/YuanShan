import urllib.request, json

def try_get(url, timeout=5):
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return r.status, r.read().decode('utf-8', errors='replace')[:800]
    except Exception as e:
        return None, str(e)[:300]

base = 'http://127.0.0.1:63162'
for path in ['/api/agents', '/api/agent/ceo', '/api/agent/ceo/running-config', '/api/tools', '/health']:
    s, body = try_get(base + path)
    print(f'GET {path} -> {s}')
    if body:
        print(f'   {body[:400]}')
    print()
