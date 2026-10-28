import os
from mootdx.reader import Reader

r = Reader.factory(market='std', tdxdir='D:/new_tdx64')
symbols = []

# 扫描所有交易所的 lday 目录
for ex in ['sh', 'sz', 'bj']:
    lday_dir = f'D:/new_tdx64/vipdoc/{ex}/lday'
    if not os.path.exists(lday_dir):
        continue
    for f in os.listdir(lday_dir):
        if f.endswith('.day'):
            code = f.replace('.day', '')
            # 跳过指数（指数通常以 0 开头或特定代码）
            # 简单判断：如果 code 是纯数字且长度6
            if code.isdigit() and len(code) == 6:
                # 判断市场
                if ex == 'sh':
                    ts_code = f'{code}.SH'
                elif ex == 'sz':
                    ts_code = f'{code}.SZ'
                else:
                    ts_code = f'{code}.BJ'
                symbols.append((ts_code, code, ex.upper()))
            elif ex == 'sh' and code.startswith('sh0') or code.startswith('sz0') or code.startswith('bj0'):
                # 指数
                pass

print(f'Total symbols: {len(symbols)}')
for s in symbols[:20]:
    print(s)
print('...')
for s in symbols[-10:]:
    print(s)
