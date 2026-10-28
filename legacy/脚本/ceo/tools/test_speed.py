#!/usr/bin/env python3
"""小批量测试：采5个概念看速度"""
import akshare as ak
import pandas as pd
import time

test_concepts = ['AI芯片', '国产芯片', '半导体概念', '光刻机(胶)', '存储芯片']

for concept in test_concepts:
    start = time.time()
    try:
        df = ak.stock_board_concept_cons_em(symbol=concept)
        elapsed = time.time() - start
        print(f"{concept}: {len(df)} stocks, took {elapsed:.1f}s", flush=True)
    except Exception as e:
        elapsed = time.time() - start
        print(f"{concept}: ❌ after {elapsed:.1f}s - {e}", flush=True)
    time.sleep(2)
print("DONE", flush=True)
