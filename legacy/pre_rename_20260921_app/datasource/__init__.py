# -*- coding: utf-8 -*-
"""
外部数据源治理。

三件事，缺一个就会重演旧系统的问题：
    注册   哪些源、走谁家、要哪个密钥、有没有配额  → config.datasource
    体检   现在通不通、多快、拿到几行            → ops.datasource_probe
    调用   带重试与降级地取数，并把健康状态写回   → registry.call()

**最重要的一条设计**：健康状态分五种而不是"好/坏"两种。
实测已经遇到三种完全不同的坏法 —— Tushare 无权限（40203）、akshare 接口自身
有 bug（Length mismatch）、东财偶发断连（RemoteDisconnected）。
混成一个"红灯"会让人一直去查网络，而实际该做的分别是买权限、换接口、加重试。
"""
from app.datasource.probe import probe_all, probe_one
from app.datasource.registry import call, health_of, list_sources, record

__all__ = ['call', 'health_of', 'list_sources', 'record', 'probe_all', 'probe_one']
