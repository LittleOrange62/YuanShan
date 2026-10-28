import json, os

# 查找全局配置中的 provider 定义
candidates = [
    r'C:\Users\Administrator\.qwenpaw\config.json',
    r'C:\Users\Administrator\.qwenpaw\providers.json',
    r'C:\Users\Administrator\.qwenpaw\global_config.json',
]
for p in candidates:
    if os.path.exists(p):
        print('FOUND:', p)
        with open(p, encoding='utf-8') as fh:
            j = json.load(fh)
        def walk(o, path=''):
            if isinstance(o, dict):
                for k, v in o.items():
                    if k in ('providers', 'llm_providers', 'provider'):
                        print(f'{path}.{k}:')
                        print(json.dumps(v, ensure_ascii=False, indent=1)[:3000])
                    walk(v, path + '.' + k)
        walk(j)
