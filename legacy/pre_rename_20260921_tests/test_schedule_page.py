# -*- coding: utf-8 -*-
"""
/schedule 与 /jobs 两页能渲染出来。

这两页是"调度器现在处于什么状态"的唯一出口，它们**打不开**比判错一次触发更糟 ——
人会觉得"既然看不见那就没生效"，于是去改配置、改完还是看不见。

这里只钉两件事：

1. `render()` 不抛异常。这一条真挡到过一次：`_switch_panel` 里写了
   `dbc.Row([...]) + [html.Div(...)]`，`dbc.Row` 是组件不是 list，
   打开页面直接 TypeError。纯逻辑测试与判定链测试都碰不到它，只有真渲染才行。
2. 调度器没开的时候，页面上必须能找到"总开关是关的"这类字样。
   开关关着却把 cron 显示成"已自动"，就是 `runner.py` 开头警告的那个坑。
"""
from __future__ import annotations

import pytest

from app.db.repo import schedule as sch_repo
from app.web.pages import jobs as jobs_page
from app.web.pages import schedule as sched_page


def _text(node) -> str:
    """
    把组件树里的所有文字拼成一句。

    字符串 child 要当叶子处理，**不能递归**：`'调度器'` 也是可迭代的，
    一路走进去会把每个字拆开，拼出来是"调 度 器"，任何 `in` 断言都失效。
    """
    out: list[str] = []

    def rec(n) -> None:
        if n is None:
            return
        if isinstance(n, str):
            out.append(n)
            return
        if isinstance(n, (list, tuple, set)):
            for x in n:
                rec(x)
            return
        rec(getattr(n, 'children', None))

    rec(node)
    return ' '.join(out)


def test_schedule_page_renders():
    assert sched_page.render() is not None


def test_schedule_page_reports_switch_state():
    node = sched_page.render()
    text = _text(node)
    assert '调度器' in text
    settings = sch_repo.settings()
    if settings['scheduler_enabled']:
        assert '运行中' in text or '已停止' in text
    else:
        assert '已停止' in text or '不会自动更新' in text or '关' in text


def test_jobs_page_still_renders_and_does_not_claim_auto():
    """
    /jobs 不能因为调度器存在就谎报"已在自动跑"。

    开关默认是 false，这时候「触发」列必须照实说"计划(调度器未启用)" ——
    措辞跟着 `scheduler_enabled` 走，两边都不能骗人。
    """
    node = jobs_page.render()
    assert node is not None
    on = bool(sch_repo.settings()['scheduler_enabled'])
    text = _text(node)
    if on:
        assert '调度器已接管' in text
    else:
        assert '调度器已接管' not in text
        assert '调度器未启用' in text


@pytest.mark.parametrize('key', sch_repo.SETTING_KEYS)
def test_every_documented_switch_has_a_value(key):
    """
    `docs/09 §5.3` 承诺的 8 个开关都必须有值。

    键缺行不会报错，只会在 `settings()` 里悄悄回落到代码默认值 —— 于是
    页面上没有那个勾、库里也查不到，行为和文档不一致却没有任何提示。
    """
    assert key in sch_repo.settings()
