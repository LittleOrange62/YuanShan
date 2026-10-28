#!/usr/bin/env python3
"""第一批：采集30个核心概念板块的成分股关联（快速版）"""
import akshare as ak
import pandas as pd
import time
import sys

# 第一批30个核心概念
CONCEPTS_BATCH1 = [
    'AI芯片', '国产芯片', '半导体概念', '光刻机(胶)', '存储芯片',
    '第三代半导体', '稀土永磁', '算力概念', '数据中心', '数据要素',
    '人工智能', '机器人概念', '人形机器人', '低空经济', '飞行汽车(eVTOL)',
    '消费电子概念', '华为汽车', '汽车芯片', '新能源', '新能源车',
    '减肥药', '创新药', 'CRO', '中药概念',
    'CPO光模块', '光伏概念', '锂电池概念', '储能概念', '氢能源', '信创'
]

all_rows = []
failed = []

for i, concept in enumerate(CONCEPTS_BATCH1):
    for attempt in range(2):
        try:
            df = ak.stock_board_concept_cons_em(symbol=concept)
            for _, row in df.iterrows():
                code = str(row['代码']).strip()
                if code.startswith('6'):
                    ts_code = f"{code}.SH"
                elif code.startswith('8') or code.startswith('4'):
                    ts_code = f"{code}.BJ"
                else:
                    ts_code = f"{code}.SZ"
                all_rows.append({
                    'ts_code': ts_code,
                    'concept_code': concept,
                    'concept_name': concept
                })
            print(f"[{i+1}/{len(CONCEPTS_BATCH1)}] {concept}: {len(df)} stocks ✅", flush=True)
            time.sleep(1.5)  # 缩短到1.5秒
            break
        except Exception as e:
            if attempt == 0:
                print(f"[{i+1}/{len(CONCEPTS_BATCH1)}] {concept}: 重试...", flush=True)
                time.sleep(3)
            else:
                print(f"[{i+1}/{len(CONCEPTS_BATCH1)}] {concept}: ❌ {e}", flush=True)
                failed.append(concept)

result = pd.DataFrame(all_rows)
if len(result) > 0:
    result = result.drop_duplicates(subset=['ts_code', 'concept_code'])
    result.to_csv('/app/working/workspaces/manager/temp/concept_stocks_batch1.csv', index=False)
    print(f"\n✅ 成功: {len(result)} 条, {result['concept_name'].nunique()} 个概念")
if failed:
    print(f"❌ 失败: {failed}")
