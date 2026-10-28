# -*- coding: utf-8 -*-
"""测试腾讯/新浪批量行情接口获取 8-14 收盘数据"""
import requests, json

# 1. 腾讯批量接口（一次最多约 60 只）
def tencent_quotes(symbols):
    """symbols: ['sh600000','sz000001']"""
    url = 'https://qt.gtimg.cn/q=' + ','.join(symbols)
    try:
        r = requests.get(url, timeout=10, headers={'Referer': 'https://gu.qq.com'})
        r.encoding = 'gbk'
        lines = r.text.strip().split(';')
        result = []
        for line in lines:
            line = line.strip()
            if not line or '=' not in line:
                continue
            sym = line.split('=')[0].replace('v_', '')
            data = line.split('=')[1].strip('"').split('~')
            if len(data) > 35:
                result.append({
                    'symbol': sym,
                    'name': data[1],
                    'close': data[3],      # 当前价
                    'pre_close': data[4],  # 昨收
                    'open': data[5],
                    'volume': data[6],     # 手
                    'amount': data[37],    # 万
                    'high': data[33],
                    'low': data[34],
                    'date': data[30],
                })
        return result
    except Exception as e:
        print('腾讯接口失败:', e)
        return []

# 测试 10 只
syms = ['sh600000', 'sz000001', 'sz000002', 'sh600004', 'sz000004',
        'sh600006', 'sz000006', 'sh600007', 'sz000007', 'sh600008']
res = tencent_quotes(syms)
print('腾讯返回条数:', len(res))
for r in res[:5]:
    print(r)

# 2. 测试 50 只一批的速度
import time
syms50 = [f'sh60{i:04d}' for i in range(1, 51)]
start = time.time()
res50 = tencent_quotes(syms50)
elapsed = time.time() - start
print(f'\n50只耗时: {elapsed:.2f}s, 返回{len(res50)}条')
print(f'预估 9000 只: {elapsed/50*9000/60:.1f} 分钟')
