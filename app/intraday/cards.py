# -*- coding: utf-8 -*-
"""飞书卡片辅助（11-10 新增）：盯盘播报正文 → 飞书交互卡片（msg_type:interactive）。

- 正文按 **markdown** 元素渲染：支持 **加粗**/*斜体*/`行内码`/[链接](url)、emoji、换行；
  飞书卡片 markdown **不支持表格**——当前各播报正文均为列表/键值行，无表格；
  若日后正文出现表格，卡片会原样显示竖线，退化为可读性下降但不报错。
- 纯文本回退：`feishu_card` 配置关（缺省也关）时，本模块的 send_broadcast_with_card
  直接走 notify 纯文本，行为与改造前一字不差。
- 非飞书渠道（钉钉/企微）：notify 层天然忽略 card 走纯文本，本模块不感知。
"""

from __future__ import annotations

import json
from pathlib import Path

_CONFIG_PATH = Path(__file__).parent / "broadcast_config.json"

# 飞书卡片单条上限（官方文档：卡片内容 30K 字符左右；正文实际 <2K 字，取 8192 留足余量）
_MAX_MD_CHARS = 8192


def is_feishu_card_enabled() -> bool:
    """broadcast_config.json 的 `feishu_card` 开关；读不到配置=关（回退纯文本，宁保守）。"""
    try:
        with open(_CONFIG_PATH, "r", encoding="utf-8") as f:
            return bool(json.load(f).get("feishu_card", False))
    except Exception:
        return False


def make_feishu_card(title: str, body: str) -> dict:
    """构造飞书交互卡片 JSON（供 notify.send_to(card=...) 使用）。

    标题进卡片头（plain_text，蓝底模板）；正文整段进一个 markdown 元素，
    原文不动（换行/**加粗** 天然渲染）。超长按 8192 字硬截并留一句说明。
    """
    if len(body) > _MAX_MD_CHARS:
        body = body[:_MAX_MD_CHARS] + f"\n（正文原长 {len(body)} 字，已截断）"
    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "title": {"tag": "plain_text", "content": title},
            "template": "blue",
        },
        "elements": [
            {"tag": "markdown", "content": body},
        ],
    }


def send_broadcast_with_card(keys, title: str, body: str, **kw) -> dict:
    """所有盯盘推送的发送出口：卡片开关开→飞书走 interactive 卡片；关→纯文本（原行为）。"""
    from duanxian import notify

    if is_feishu_card_enabled():
        return notify.send_to(keys, title, body, card=make_feishu_card(title, body), **kw)
    return notify.send_to(keys, title, body, **kw)
