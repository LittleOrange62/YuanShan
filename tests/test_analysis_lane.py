# -*- coding: utf-8 -*-
"""「按需研究」这一档的边界测试（用户 2026-09-27 裁定的方案 b）。

要钉住的是**"只放开一条通道，红线本身不动"**：

- 右侧「AI分析」栏（`task_kind='analysis'`）：用户逐条点了才跑、结果只存本机浏览器、
  不入库不进复盘报告 —— 这一档允许给参考价位/止损/预期空间，所以它的正文检查
  只挡空正文；
- 复盘报告、个股深析、页面对话、公开页面：**仍然**由 `check_report_text` +
  `PRODUCT_POLICY` 拦交易动作与点位。撤掉这条等于把页脚那句
  "不推荐个股、不给买卖时机"作废，所以必须有一测试盯着它没被顺手放宽。
"""
from __future__ import annotations

import pytest

from review_agent.api import ModelInput, PageChatInput
from review_agent.daily import check_analysis_text, check_report_text
from review_agent.product_policy import (ANALYSIS_POLICY, PRODUCT_POLICY,
                                         has_trade_recommendation, policy_for)

pytestmark = pytest.mark.unit

ADVICE = '低位区域 11.0–11.6 可分批低吸，止损 10.4，预期空间 15%。'


def test_红线本身没被放宽():
    """同一句话在复盘/页面档依然必须被拒 —— 这是这次改动唯一不能碰的东西。"""
    assert has_trade_recommendation(ADVICE) is True
    with pytest.raises(Exception) as exc:
        check_report_text(ADVICE)
    assert '交易动作或点位' in str(exc.value)


def test_研究档放行同一句话但空正文仍然不行():
    check_analysis_text(ADVICE)              # 不抛异常即通过
    check_analysis_text('分层结果：位置 58.4 / 76.7 / 100，参考区间见上。')
    with pytest.raises(Exception):
        check_analysis_text('   ')


def test_边界按通道选择默认仍是产品红线():
    assert policy_for('analysis') is ANALYSIS_POLICY
    for kind in (None, '', 'page', 'daily', 'stock', 'backtest'):
        assert policy_for(kind) is PRODUCT_POLICY, kind


def test_研究档的边界自己写明了出处与风险要求():
    text = ANALYSIS_POLICY
    assert '低吸区域' in text and '止损' in text, '这一档存在的意义就是允许给参考价位'
    assert '不是投资建议' in text, '放开点位的同一句话里必须自己声明不是投资建议'
    assert '失效条件' in text, '每个价位都要能说出什么时候不成立'
    assert '不许编' in text, '价位必须有出处，材料里没有的数字不许编'
    assert '反向风险' in text, '高低切最大的坑是退潮时补涨快速结束，必须主动提醒'


def _llm(tmp_path, purpose, text):
    """真 `Runtime` 不要：只把 `_invoke` 换成一句固定回答，测的就是**闸门本身**。"""
    import threading

    from review_agent.daily import DailyLLM

    class FakeRuntime:
        timeout = 60

        def _invoke(self, run, source, key, prompt, cancel, progress, budget, **kw):
            return text

    return DailyLLM(FakeRuntime(), 'new-api', 'k', tmp_path, '2026-09-24',
                    threading.Event(), lambda: 60, purpose=purpose)


def test_调用方之前那道闸也按档分(tmp_path):
    """`DailyLLM.invoke` 里还有一道 `check_report_text`，它在 chat_jobs 检查**之前**跑。

    2026-09-27 实测踩过：外层换成研究档、这道没换，右侧「AI分析」点了照样
    "包含交易动作或点位建议，未保存"，放开等于没放开。
    """
    assert _llm(tmp_path, 'analysis', ADVICE).invoke('跑一遍').content == ADVICE
    with pytest.raises(Exception) as exc:
        _llm(tmp_path, 'page', ADVICE).invoke('跑一遍')
    assert '交易动作或点位' in str(exc.value)


def test_接口只认这三种任务档():
    llm = ModelInput(provider='new-api', model='x', baseURL='http://127.0.0.1:3000/v1', apiKey='k')
    msg = [{'role': 'user', 'content': '把这题跑一遍'}]
    for kind in ('page', 'backtest', 'analysis'):
        assert PageChatInput(messages=msg, context='', allow_tools=False,
                             request_id='0' * 32, llm=llm, task_kind=kind).task_kind == kind
    with pytest.raises(Exception):
        PageChatInput(messages=msg, context='', allow_tools=False,
                      request_id='0' * 32, llm=llm, task_kind='anything-goes')
