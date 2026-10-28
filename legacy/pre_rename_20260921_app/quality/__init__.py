# -*- coding: utf-8 -*-
"""
质量块：把 `config.quality_rule` 的规则按交易日执行，结果写 `ops.quality_result`。

对外只暴露 `audit` 模块，页面与脚本都从这里进：

    from app.quality import audit
    audit.gap()          # 现在差几天（只读）
    audit.catch_up()     # 缺几天补几天，一天一次
    audit.audit_day(d)   # 只审一天
"""
from __future__ import annotations

from app.quality import audit

__all__ = ['audit']
