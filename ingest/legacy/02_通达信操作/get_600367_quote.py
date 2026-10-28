# -*- coding: utf-8 -*-
"""
通过通达信 TQ 接口获取 600367 实时行情快照
使用 tqcenter 模块（D:\new_tdx64\PYPlugins\sys\tqcenter.py）
"""
import sys
import os

# 添加 tqcenter 模块路径
TQCENTER_DIR = r"D:\new_tdx64\PYPlugins\sys"
if TQCENTER_DIR not in sys.path:
    sys.path.insert(0, TQCENTER_DIR)

from tqcenter import tq

def main():
    # 初始化连接（连接通达信客户端）
    print("初始化 TQ 连接...")
    tq.initialize(__file__)

    # 获取 600367 实时快照
    code = '600367.SH'
    print(f"获取 {code} 实时快照...")
    snapshot = tq.get_market_snapshot(stock_code=code)
    if not snapshot:
        print("快照获取失败！")
        tq.close()
        return

    print(f"ErrorId: {snapshot.get('ErrorId')}")
    print(f"Error: {snapshot.get('Error')}")

    # 打印关键字段
    key_fields = ['ItemNum', 'LastClose', 'Open', 'Max', 'Min', 'Now', 'Volume', 'NowVol',
                  'Amount', 'Average', 'HqDate', 'ZAF', 'ZTPrice', 'DTPrice', 'fHSL', 'Zsz', 'Ltsz']
    print("\n=== 关键行情字段 ===")
    for k in key_fields:
        print(f"  {k}: {snapshot.get(k)}")

    # 打印全部字段
    print(f"\n=== 全部字段 ({len(snapshot)} 个) ===")
    for k, v in snapshot.items():
        print(f"  {k}: {v}")

    tq.close()

if __name__ == '__main__':
    main()
