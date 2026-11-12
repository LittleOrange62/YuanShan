# -*- coding: utf-8 -*-
import urllib.request, json

r = urllib.request.urlopen('http://127.0.0.1:8910/api/market/first-board?boards=1|2|3|4+')
data = json.loads(r.read().decode())

# 统计涨停板块
industries = {}
zt_20cm = []
zt_10cm = []

for stock in data['data']['stocks']:
    industry = stock['industry'] or '未分类'
    industries[industry] = industries.get(industry, 0) + 1
    
    if stock.get('pct', 0) >= 20:
        zt_20cm.append(stock)
    elif stock.get('pct', 0) >= 9.5:
        zt_10cm.append(stock)

print('=== 板块分布 ===')
for ind, cnt in sorted(industries.items(), key=lambda x: x[1], reverse=True)[:10]:
    print(f'{ind}: {cnt}只')

print(f'\n=== 20cm涨停股 {len(zt_20cm)}只 ===')
for stock in zt_20cm[:3]:
    print(f'{stock["code"]} {stock["name"]} +{stock["pct"]}%')

print(f'\n=== 10cm涨停股 {len(zt_10cm)}只 ===')

# 按板块分布分析涨停原因
from collections import Counter
topics = Counter()
for stock in data['data']['stocks']:
    reason = stock.get('reason', '')
    # 简化原因分类
    if '机器人' in reason or '人形' in reason:
        topics['机器人/人形机器人'] += 1
    elif '商业航天' in reason or '卫星' in reason or '航天' in reason:
        topics['商业航天/卫星'] += 1
    elif 'AI' in reason or '算力' in reason:
        topics['AI/算力'] += 1
    elif '冰雪' in reason or '冰雪' in reason:
        topics['冰雪经济'] += 1
    elif '数据中心' in reason or '液冷' in reason:
        topics['数据中心/液冷'] += 1
    elif '煤炭' in reason or '焦化' in reason or '焦炭' in reason:
        topics['煤炭/焦化'] += 1
    else:
        topics['其他'] += 1

print('\n=== 涨停原因分析 ===')
for topic, cnt in topics.most_common(10):
    print(f'{topic}: {cnt}只')