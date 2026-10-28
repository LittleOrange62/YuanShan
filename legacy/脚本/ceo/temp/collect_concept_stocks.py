#!/usr/bin/env python3
"""采集关键概念板块的成分股关联"""
import akshare as ak
import pandas as pd
import time
import sys

# 目标概念板块列表（60个）
TARGET_CONCEPTS = [
    # 8大分组相关
    'AI芯片', '国产芯片', '半导体概念', '光刻机(胶)', '存储芯片',
    '第三代半导体', '第四代半导体', '碳化硅',
    '稀土永磁', '有色金属',
    '算力概念', '数据中心', '数据要素', '数据安全',
    '大数据', '云计算', '数字经济',
    '数字货币', '区块链',
    '电源设备', '新能源', '新能源车',
    # AI/机器人相关
    '人工智能', 'AI', 'AI应用', 'AI智能体', 'AI眼镜',
    'AIGC概念', '多模态AI', '智谱AI概念',
    '机器人概念', '人形机器人', '机器人执行器',
    # 消费电子/汽车
    '消费电子概念', '华为汽车', '小米汽车', '汽车芯片',
    '汽车整车', '汽车热管理',
    # 航天/低空
    '低空经济', '飞行汽车(eVTOL)', '商业航天', '航天航空', '通用航空',
    '无人机',
    # 热门题材
    '减肥药', '创新药', '中药概念', 'CRO',
    '光伏概念', '锂电池概念', '氢能源',
    'CPO光模块', '光通信模块', '5G概念', '6G概念',
    '信创', '军工',
    '储能概念', '风电',
]

def collect_concept_stocks(concepts, max_retries=2):
    """采集概念成分股"""
    all_rows = []
    failed = []
    
    for i, concept in enumerate(concepts):
        for attempt in range(max_retries + 1):
            try:
                df = ak.stock_board_concept_cons_em(symbol=concept)
                for _, row in df.iterrows():
                    code = str(row['代码']).strip()
                    # 补全后缀
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
                print(f"[{i+1}/{len(concepts)}] {concept}: {len(df)} stocks ✅", flush=True)
                time.sleep(2)  # 2秒间隔，避免被限流
                break
            except Exception as e:
                if attempt < max_retries:
                    print(f"[{i+1}/{len(concepts)}] {concept}: 重试 {attempt+1}...", flush=True)
                    time.sleep(5)
                else:
                    print(f"[{i+1}/{len(concepts)}] {concept}: ❌ {e}", flush=True)
                    failed.append(concept)
    
    result = pd.DataFrame(all_rows)
    return result, failed

if __name__ == '__main__':
    print(f"开始采集 {len(TARGET_CONCEPTS)} 个概念板块成分股...")
    start = time.time()
    
    df, failed = collect_concept_stocks(TARGET_CONCEPTS)
    
    elapsed = time.time() - start
    print(f"\n采集完成！耗时: {elapsed:.1f}s")
    print(f"成功: {len(df)} 条, 涉及 {df['concept_name'].nunique() if len(df) > 0 else 0} 个概念")
    
    if failed:
        print(f"失败: {len(failed)} 个概念: {', '.join(failed)}")
    
    # 输出到 CSV
    if len(df) > 0:
        # 去重
        df = df.drop_duplicates(subset=['ts_code', 'concept_code'])
        df.to_csv('/app/working/workspaces/manager/temp/concept_stocks.csv', index=False)
        print(f"\n已保存 {len(df)} 条到 concept_stocks.csv")
    else:
        print("\n没有采集到任何数据！")
        sys.exit(1)
