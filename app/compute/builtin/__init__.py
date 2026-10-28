# -*- coding: utf-8 -*-
"""
内置计算脚本。

每个模块实现 `run(ctx, params)`：
    ctx    —— app.compute.scoring.ScoreContext，带面板、截面和归一化工具
    params —— config.binding.params 的内容，外加 `_prior`（已算完的上游功能点得分）
    返回   —— pandas.Series，索引 security_code，取值落在功能点声明的区间

脚本不写库、不管权重、不管排名，那些是 app/compute/scoring.py 的职责。
这样在 /blocks 页面上调权重或停用某个分项，不需要碰任何脚本代码。
"""
