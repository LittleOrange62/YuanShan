#!/usr/bin/env python3
"""更新 AGENTS.md 中的全公司任务蓝图表"""
import os

path = '/app/working/workspaces/manager/AGENTS.md'
with open(path, 'r') as f:
    content = f.read()

# 更新任务蓝图表
old_flow = """```mermaid
graph LR
    A[data_from 盘前检查] --> B[data_from 集合竞价]
    B --> C[data_from 收盘采集]
    C --> D[data_in 信号计算]
    D --> E[manager 自选股评分]
    E --> H[manager 当日收盘总结]
    D --> F[strategy_engineer 明日计划]
    E --> F
    A -.->|晚间| G[data_from 晚间新闻]
    
    style A fill:#d4f1f9
    style B fill:#d4f1f9
    style C fill:#d4f1f9
    style D fill:#d5f5e3
    style E fill:#fdebd0
    style H fill:#fdebd0
    style F fill:#f5e1f7
    style G fill:#d4f1f9
```"""

new_flow = """```mermaid
graph LR
    A[data_from 盘前检查] --> B[data_from 集合竞价]
    B --> C[data_from 收盘采集]
    C --> C2[data_from 板块热度采集]
    C2 --> D[data_in 鑫鑫评分计算]
    D --> E[manager 远山评分卡]
    E --> H[manager 当日收盘总结]
    A -.->|晚间| G[data_from 晚间新闻]
    
    style A fill:#d4f1f9
    style B fill:#d4f1f9
    style C fill:#d4f1f9
    style C2 fill:#d4f1f9
    style D fill:#d5f5e3
    style E fill:#fdebd0
    style H fill:#fdebd0
    style G fill:#d4f1f9
```"""

content = content.replace(old_flow, new_flow)

with open(path, 'w') as f:
    f.write(content)
print('AGENTS.md 已更新')
