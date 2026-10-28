# -*- coding: utf-8 -*-
"""测试 akshare 新浪源批量拉取 8-14 数据的速度"""
import akshare as ak
import time

# 测试 10 只股票拉取 8-14 数据
symbols = ['sh600000', 'sz000001', 'sz000002', 'sh600004', 'sz000004',
           'sh600006', 'sz000006', 'sh600007', 'sz000007', 'sh600008']

start = time.time()
ok = 0
fail = 0
for s in symbols:
    try:
        df = ak.stock_zh_a_daily(symbol=s, start_date="20260814", end_date="20260814", adjust="")
        if df is not None and len(df) > 0:
            ok += 1
            # 只打印第一个样本的字段
            if ok == 1:
                print('样本字段:', list(df.columns))
                print(df.head(1).to_string())
        else:
            fail += 1
    except Exception as e:
        fail += 1
        if fail <= 2:
            print(f'{s} 失败: {e}')
    time.sleep(0.3)

elapsed = time.time() - start
print(f'\n测试完成: 成功{ok} 失败{fail} 耗时{elapsed:.1f}s 平均{elapsed/len(symbols):.2f}s/只')
print(f'预估全市场 9000 只耗时: {elapsed/len(symbols)*9000/60:.1f} 分钟')
