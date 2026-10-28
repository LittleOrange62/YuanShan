import os

symbols = []
for ex in ['sh', 'sz', 'bj']:
    lday_dir = f'D:/new_tdx64/vipdoc/{ex}/lday'
    if not os.path.exists(lday_dir):
        continue
    for f in os.listdir(lday_dir):
        if f.endswith('.day'):
            code = f.replace('.day', '')
            # 格式如 sh600519, sz000001, bj830799
            if code.startswith(ex):
                pure_code = code[2:]
                if pure_code.isdigit() and len(pure_code) == 6:
                    ts_code = f'{pure_code}.{ex.upper()}'
                    symbols.append((ts_code, pure_code, ex.upper(), code))

print(f'Total symbols: {len(symbols)}')
for s in symbols[:20]:
    print(s)
print('...')
for s in symbols[-10:]:
    print(s)
