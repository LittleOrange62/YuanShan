"""引用编号的形状：模型抄不动 20 位十六进制 id，这是实测出来的，不是臆测。

2026-09-26 复盘 2026-09-23 那轮（浏览器点的「生成复盘」）两次被拦，原稿都还在
`~/.vibe-astock-agent/daily/<job>/*/validation.json` 里，直接重放出两种形状：
  · 一次把目录里的 `line`（材料行号）当引用号抄成 `ev-20 / ev-38 / ev-9 / ev-12`；
  · 一次把整封 JSON 交了，只漏了收尾的三反引号，被报成"JSON 或结构无效"。
两者的内容与依据都挑不出毛病。修法是在**边界**上把短引用号换回真 id，而不是放松核对：
别段的编号、截断或改写过的 id，照旧一律拒。
"""
import json

import pytest

from review_agent.evidence import EvidenceError
from review_agent.grounding import _unfence, build_catalog, cite, validate_section
from test_daily_grounding import DATE, inputs, section

FENCE = "```"


@pytest.fixture
def catalog():
    return build_catalog(inputs(), DATE)


@pytest.fixture
def view(catalog):
    return cite(catalog)


def test_cite_numbers_within_the_stage_and_hide_line(catalog, view):
    assert [r["ref"] for r in view] == ["ev-" + str(n) for n in range(1, len(view) + 1)]
    assert not any("line" in r for r in view), "行号会被当成引用号抄走，不该出现在喂给模型的目录里"
    assert [r["id"] for r in view] == [e["id"] for e in catalog], "落盘用的真 id 不能被换掉"


def test_ref_citation_is_stored_as_the_real_id(catalog, view):
    result = validate_section(section(view[4]["ref"]), view)
    assert result["findings"][0]["citations"] == [catalog[4]["id"]]


def test_a_full_id_is_still_accepted(catalog, view):
    result = validate_section(section(catalog[2]["id"]), view)
    assert result["findings"][0]["citations"] == [catalog[2]["id"]]


def test_ref_and_id_of_the_same_record_are_a_duplicate(catalog, view):
    with pytest.raises(EvidenceError, match="引用重复"):
        validate_section({"findings": [{"text": "样本承接偏弱，尚不能确认修复。",
                                        "citations": [view[0]["ref"], catalog[0]["id"]]}]}, view)


def test_a_number_outside_this_stage_is_still_refused(catalog, view):
    """换编号方式不放松边界：本段没有这个编号就是没有。"""
    market = [r for r in view if r["input"] == "get_market_facts"]
    outside = next(r for r in view if r["input"] == "get_leader_data" and r["ref"] not in {m["ref"] for m in market})
    with pytest.raises(EvidenceError) as caught:
        validate_section(section(outside["ref"]), market)
    assert "不在本段可用目录内" in str(caught.value)


def test_a_mangled_id_is_never_repaired_by_similarity(catalog, view):
    last = catalog[1]["id"][-1]
    mangled = catalog[1]["id"][:-1] + ("0" if last != "0" else "1")
    with pytest.raises(EvidenceError) as caught:
        validate_section(section(mangled), view)
    assert "截断或改写过的 id 不接受" in str(caught.value)


def test_unclosed_fence_is_not_a_structure_failure(catalog, view):
    payload = '{"findings":[{"text":"样本承接偏弱，尚不能确认修复。","citations":["ev-1"]}]}'
    assert _unfence(FENCE + "json\n" + payload + "\n" + FENCE) == payload
    assert _unfence(FENCE + "json\n" + payload) == payload        # 只漏了收尾那一行
    assert _unfence(payload) == payload                          # 本来就没围栏
    # 剥完仍走同一套校验：能落进目录的引用才算数
    assert validate_section(json.loads(_unfence(FENCE + "json\n" + payload)), view)["findings"]


def test_prose_around_json_is_still_not_an_envelope():
    mixed = "以下是我的分析：\n" + FENCE + "json\n{\"findings\": []}\n" + FENCE + "\n希望有帮助"
    assert _unfence(mixed) == mixed          # 不从散文里抠片段，这条纪律不动
    with pytest.raises(ValueError):
        json.loads(_unfence(mixed))


def test_half_open_fence_with_unparsable_body_stays_invalid():
    """只在"去掉围栏后整段能解析"时才宽容；否则仍然按结构无效拒掉。"""
    broken = FENCE + "json\n{\"findings\": [{\"text\": \"写了一半"
    assert _unfence(broken) == broken.strip()


GOOD = '{"findings":[{"text":"样本承接偏弱，尚不能确认修复。","citations":["ev-1"]}]}'


def test_surplus_closing_brackets_after_a_complete_object_are_trimmed():
    """2026-09-27 真跑实测的第三种形状：正文完整、引用是短号，只多敲了 `]}`。"""
    for tail in ("]}", "]}]}", " ]}\n", "`"):
        raw = GOOD + tail
        assert json.loads(_unfence(raw))["findings"], tail
        assert json.loads(_unfence(FENCE + "json\n" + raw + "\n" + FENCE))["findings"], tail


def test_a_second_object_in_the_tail_is_still_a_structure_failure():
    """削尾巴不等于捡正文：尾巴里出现第二个对象就照旧拒（那已经不是手滑）。"""
    raw = GOOD + '{"findings":[{"text":"再来一段","citations":["ev-2"]}]}'
    with pytest.raises(ValueError):
        json.loads(_unfence(raw))


def test_prose_or_field_names_in_the_tail_are_still_refused():
    for tail in ("　以上。", ' "note":"x"', " {oops}"):
        with pytest.raises(ValueError):
            json.loads(_unfence(GOOD + tail))


def test_surplus_brackets_no_longer_cost_a_second_paid_call(catalog, view):
    """第一轮只是收尾多敲两个括号：修完之后这一轮就该直接过，不该再敲第二次。"""
    from types import SimpleNamespace
    from review_agent.grounding import request_checked
    body = '{"findings":[{"text":"样本承接偏弱，尚不能确认修复。","citations":["ev-1"]}]}'
    prompts = []

    def invoke(prompt):
        prompts.append(prompt)
        return SimpleNamespace(content=body + "]}]")
    result = request_checked(SimpleNamespace(invoke=invoke), "分析",
                             {"stage": "sentiment", "records": view},
                             lambda obj: validate_section(obj, view))
    assert result["findings"] and len(prompts) == 1


def test_a_real_structure_failure_is_corrected_with_a_named_requirement(catalog, view):
    """真的坏了要说出坏在哪：光说"结构无效"，模型只能瞎猜着重写。"""
    from types import SimpleNamespace
    from review_agent.grounding import request_checked
    good = '{"findings":[{"text":"样本承接偏弱，尚不能确认修复。","citations":["ev-1"]}]}'
    prompts = []

    def invoke(prompt):
        prompts.append(prompt)
        return SimpleNamespace(content='{"findings": [{' if len(prompts) == 1 else good)
    result = request_checked(SimpleNamespace(invoke=invoke), "分析",
                             {"stage": "sentiment", "records": view},
                             lambda obj: validate_section(obj, view))
    assert result["findings"]
    assert len(prompts) == 2 and "结构要求" in prompts[1]
    assert "JSON 或结构无效" in prompts[1]
