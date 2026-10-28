"""Deterministic conversation categories: same question, same answer, every time.

分类不调模型：一次分类就是一次额外付费请求，而且换个来源或重跑一次就可能变，
用户按分类找历史时不可复现比"分得不够聪明"更糟。
"""
from __future__ import annotations

CATEGORIES = (
    ("板块与题材", ("板块", "行业", "概念", "题材", "轮动", "产业链", "赛道", "主线", "分支")),
    ("涨停与梯队", ("涨停", "炸板", "封板", "触板", "回封", "梯队", "龙头", "首板", "连板", "晋级", "涨停池")),
    ("情绪与周期", ("情绪", "温度", "周期", "冰点", "退潮", "修复", "发酵", "亢奋", "赚钱效应", "亏钱效应", "连板高度")),
    ("资金与风险", ("资金流", "主力", "北向", "两融", "龙虎榜", "成交额", "风险", "回撤", "量能")),
    ("个股研究", ("个股", "公司", "财报", "主营", "股东", "分红", "解禁", "公告", "估值")),
    ("回测与统计", ("回测", "胜率", "盈亏", "收益", "统计", "样本", "分布", "占比")),
    ("数据与口径", ("口径", "样本范围", "数据源", "缺失", "未获取", "复权", "交易日")),
)
FALLBACK = "其他"
MAX_CHARS = 2000


def category(question: str) -> str:
    """第一个命中的分类生效；命中多条时按上表顺序，保证结果唯一可复现。"""
    if not isinstance(question, str):
        return FALLBACK
    text = question[:MAX_CHARS]
    for name, words in CATEGORIES:
        if any(word in text for word in words):
            return name
    return FALLBACK
