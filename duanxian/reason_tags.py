# -*- coding: utf-8 -*-
"""涨停原因串 → 词频汇总：**全站一份实现**（首板页与首页「首板分析」都读这里）。

为什么收成共用块：原来只有 `vr/firstboard.py::_tally` 一份，它吃的是**当场抓回来的东财池内存名单**；
首页那块要按**库里这一场全部首板**汇总（不是分页后的那几十行），两边算法必须一字不差，
否则同一个"哪个题材重复出现"在两屏会给出两个答案。

三条判据（照旧，改这里等于改两屏）：
  · 一只票的串里同一个词重复出现只算一次（那是来源写法重复，不是两只票）；
  · 只列 `count > 1` 的（用户 09-28 点名：数量不为 1 的才汇总），
    但**被滤掉的单源类别条数一起交回** —— 不然"只剩三条"看着像今天就这么点题材；
  · 没有串的只数单列 `no_reason`：它不等于"原因不存在"，只等于"这一列没取到"。

这里**不做属性词过滤**（`duanxian/theme_tree.py:_is_generic` 那套是给题材树用的）——
这一屏要回答的是"这一批票的原因里哪个词重复出现"，把「国企改革」这类词悄悄滤掉，
用户看到的就是一个少了类别的榜：少类别比多类别更难发现。
"""
from __future__ import annotations

import re
from collections import Counter

TAG_SPLIT = re.compile(r"[+＋、,，/／|｜]+")

#: 汇总口径那句要上屏的话（两屏都读它，别在组件里另写一份）
SCOPE_NOTE = '这一批票的涨停原因里哪个词重复出现（原因＝源站编辑归因，不是公司确认事实）'


def split_tags(reason) -> list[str]:
    """`'端侧AI+机器人+业绩增长'` → `['端侧AI', '机器人', '业绩增长']`；空/None → `[]`。"""
    raw = str(reason or '').strip()
    if not raw:
        return []
    return list(dict.fromkeys(x.strip() for x in TAG_SPLIT.split(raw) if x.strip()))


def tally(items: list[dict], *, scope: str = SCOPE_NOTE) -> dict:
    """`items` 形如 `[{'reason': 串或 None, 'industry': 名或 None}, …]`（一次批量的全部成员）。"""
    tags: Counter = Counter()
    inds: Counter = Counter()
    no_reason = 0
    for s in items:
        parts = split_tags(s.get('reason'))
        if not parts:
            no_reason += 1
        for t in parts:                        # split_tags 已按只去重，这里跨只票累加
            tags[t] += 1
        ind = str(s.get('industry') or '').strip()
        if ind:
            inds[ind] += 1

    def rows(cnt: Counter) -> tuple[list[dict], int]:
        ordered = [{'name': k, 'count': v} for k, v in sorted(cnt.items(), key=lambda kv: (-kv[1], kv[0]))]
        repeat = [x for x in ordered if x['count'] > 1]
        return repeat, len(ordered) - len(repeat)

    reason_rows, dropped_reason = rows(tags)
    industry_rows, dropped_industry = rows(inds)
    return {
        'reason_summary': reason_rows,
        'industry_summary': industry_rows,
        'summary_meta': {
            'scope': scope,
            'stock_count': len(items),
            'no_reason': no_reason,
            'tag_total': len(tags),
            'dropped_single_reason': dropped_reason,
            'dropped_single_industry': dropped_industry,
        },
    }
