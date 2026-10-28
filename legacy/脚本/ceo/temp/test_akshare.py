# -*- coding: utf-8 -*-
"""测试 akshare 能否拉到 2026-08-14 日线"""
import akshare as ak
import pandas as pd

# 1. 东财个股日线
try:
    df = ak.stock_zh_a_hist(symbol="600036", period="daily",
                            start_date="20260813", end_date="20260814", adjust="")
    print('=== 东财 600036 日线 ===')
    print(df.tail(3).to_string())
    print('日期列:', list(df.columns))
except Exception as e:
    print('东财失败:', e)

# 2. 新浪日线
try:
    df2 = ak.stock_zh_a_daily(symbol="sh600036", start_date="20260813", end_date="20260814", adjust="")
    print('\n=== 新浪 600036 日线 ===')
    print(df2.tail(3).to_string())
except Exception as e:
    print('新浪失败:', e)
