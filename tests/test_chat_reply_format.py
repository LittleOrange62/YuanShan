# -*- coding: utf-8 -*-
"""页面对话/按需研究的回复解析：一次付费调用不该栽在 ```json 围栏上。

2026-09-27 实测：素材给足之后模型改成写散文式回答（前面一句客套 + 围栏），
`json.loads(text.strip())` 直接 ValueError → 整条任务以"未返回约定的回答格式"失败，
几十秒的调用白烧。现在容忍围栏与前后杂句，但**仍然只认一个 JSON 对象**，
并且只提醒一次 —— 无限重试等于把预算烧光。
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from review_agent import chat_jobs
from review_agent.chat_jobs import ChatJobs, parse_reply

pytestmark = pytest.mark.unit


@pytest.mark.parametrize("raw, want", [
    ('{"answer":"正文"}', '正文'),
    ('  {"answer":"带空格"}  ', '带空格'),
    ('```json\n{"answer":"带围栏"}\n```', '带围栏'),
    ('```\n{"answer":"只写围栏不写语言"}\n```', '只写围栏不写语言'),
    ('好的，结论如下：\n{"answer":"前面有客套"}\n以上。', '前面有客套'),
    ('{"answer":"串里有大括号 { 和转义引号 \\" 也不影响"}', '串里有大括号 { 和转义引号 " 也不影响'),
    ('{"answer":"嵌套","trace":{"a":[1,2]}}', '嵌套'),
])
def test_约定的几种写法都要能认(raw, want):
    got = parse_reply(raw)
    assert got is not None, f'这份回复必须解析出来：{raw!r}'
    assert got.get('answer') == want


@pytest.mark.parametrize("bad", [
    '', '   ', None, 123,
    '纯散文，一个字都没提 JSON',
    '{"answer": 未加引号}',
    '{"answer":"没闭合',
])
def test_认不出来的时候如实返回None(bad):
    """不许"猜一个出来"：解析不出来就交回调用方，宁可提醒一次也不把半截文本当回答。"""
    assert parse_reply(bad) is None


def test_包成数组时也把里面那个对象捞出来():
    """模型偶尔回 `[{...}]`。取出的仍是**它写的那个对象**，不是拼出来的半截文本；
    调用方还会再验一次键名，所以这一步放宽不越过任何内容红线。"""
    assert parse_reply('[{"answer":"数组里的那个对象"}]') == {'answer': '数组里的那个对象'}


def test_多个对象时只取第一个完整的():
    got = parse_reply('{"answer":"第一题"}\n{"answer":"第二题"}')
    assert got == {'answer': '第一题'}


def test_工具调用那份JSON也走同一条路():
    got = parse_reply('我要查一下：{"tool":"query_quote","args":{"codes":["600519"]}}')
    assert got == {'tool': 'query_quote', 'args': {'codes': ['600519']}}


class _StubLLM:
    """顶替 `DailyLLM`：每次 invoke 原样返回一个脚本好的"回复"，并记下收到的提示词。"""

    def __init__(self, replies):
        self.replies = list(replies)
        self.prompts = []

    def __call__(self, runtime, source, key, directory, date, cancel, check, purpose="page"):
        self.purpose = purpose
        return self

    def invoke(self, prompt, _correction=False):
        self.prompts.append(prompt)
        return SimpleNamespace(content=self.replies.pop(0) if self.replies else '还是散文')


def _run(monkeypatch, tmp_path, replies):
    """真跑 `ChatJobs._work`（打桩掉模型本身），看这条任务最后落到什么状态。"""
    stub = _StubLLM(replies)
    monkeypatch.setattr(chat_jobs, 'DailyLLM', stub)
    manager = SimpleNamespace(store=SimpleNamespace(root=tmp_path), runtime=None)
    jobs = ChatJobs(manager)
    jobs.current = {"job_id": "job1", "started": 0, "running": True, "status": "running"}
    payload = {"messages": [{"role": "user", "content": "跑一遍"}], "context": "材料",
               "allow_tools": False, "task_kind": "analysis"}
    jobs._work(payload, {"provider": "new-api"}, "")
    assert stub.purpose == "analysis", '研究档没传到 DailyLLM = 边界选错那一份'
    return jobs.current, stub


def test_围栏里的回答现在能落成complete(monkeypatch, tmp_path):
    current, stub = _run(monkeypatch, tmp_path, ['```json\n{"answer":"低位那几只：位置 20 分位"}\n```'])
    assert current["status"] == "complete", current.get("error")
    assert "位置 20 分位" in current["result"]["content"]
    assert len(stub.prompts) == 1, '一次就成，不该再提醒'


def test_认不出格式只提醒一次然后如实失败(monkeypatch, tmp_path):
    current, stub = _run(monkeypatch, tmp_path, ['纯散文一', '纯散文二', '纯散文三'])
    assert current["status"] == "failed"
    assert "未返回约定的回答格式" in current["error"]
    assert len(stub.prompts) == 2, f'应当只提醒一次：实发 {len(stub.prompts)} 次付费调用'
    assert "上一轮的输出不是约定的 JSON" in stub.prompts[1]
