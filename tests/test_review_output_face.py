"""拿真实冻结材料走一遍复盘链路，检查**产出面**而不只是"闸门没报错"（判据 06 §3）。

本机至今零份成功复盘（`~/.duanxian-agents/reviews/` 只有 `_rejected/`，六次 daily 作业全
failed），所以"抽一篇真复盘看引用能不能点开"只能这样验：**真材料 + 合成裁判**。
材料是 2026-09-23 那次付费跑留下的冻结取数（116 条目录，正文长、字段多，和单元测试里
39 条的合成目录不是一个量级 —— 原先被拦回的两种形状都只在这个量级上出现过）。
"""
import json
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

DATE = "2026-09-23"
RUN = Path.home() / ".vibe-astock-agent/daily/cde801375e584edf9e126751d7afdf11"

pytestmark = pytest.mark.skipif(not RUN.is_dir(), reason="没有 2026-09-23 那次真跑的冻结取数可重放")


@pytest.fixture(scope="module")
def real_values():
    out = {}
    for path in RUN.glob("get_*.json"):
        row = json.loads(path.read_text(encoding="utf-8"))
        out[path.stem] = row["value"]
    assert len(out) == 8, "冻结输入不全，重放没有意义"
    return out


class Judge:
    """合成裁判：只交回**本段目录里的 ref**与不含自造数字的定性正文。

    被测的是链路不是模型 —— 这里要的是"真材料能不能一路走到落盘、引用全部解得开"。
    """
    def __init__(self):
        self.stages = []
        self.written_refs = []

    def invoke(self, prompt):
        context, _ = json.JSONDecoder().raw_decode(prompt.split("GROUNDING_CONTEXT=", 1)[1])
        records = context["records"]
        self.stages.append(context["stage"])
        if context["stage"] == "summary":
            anchor = max(e["target_date"] for e in records)
            basis = next(e for e in records if e.get("metric") == "limit_up_count"
                         and e.get("available") is True and e["target_date"] == anchor)
            refs = [basis["ref"]]
            payload = {
                "emotion_phase": "退潮",
                "market_oneliner": {"text": "涨停家数与晋级率同向转弱，尚不能确认修复。", "citations": refs},
                "focus_directions": [],
                "risk_alerts": [{"text": "晋级率转弱可能延续，反证条件未成立。", "citations": refs}],
                "verification_items": [{"metric": "limit_up_count", "direction": "下降",
                                        "reason": {"text": "以当日涨停家数为基准继续观察。", "citations": refs}}],
            }
            self.written_refs += [eid for finding in [payload["market_oneliner"], *payload["risk_alerts"],
                                                      *[v["reason"] for v in payload["verification_items"]]]
                                  for eid in finding["citations"]]
            return SimpleNamespace(content=json.dumps(payload, ensure_ascii=False))
        finding = {"text": "样本承接偏弱，缺口按材料所示，不作补齐。",
                   "citations": [records[0]["ref"], records[min(1, len(records) - 1)]["ref"]]}
        self.written_refs += finding["citations"]
        return SimpleNamespace(content=json.dumps({"findings": [finding]}, ensure_ascii=False))


def produce(tmp_path, real_values):
    from review_agent.daily import FrozenInputs
    from review_agent.grounding import generate_grounded
    inputs = FrozenInputs(DATE, tmp_path, lambda: None)
    inputs.values = dict(real_values)
    judge = Judge()
    state = generate_grounded(judge, inputs, DATE, lambda: None, lambda message: None)
    return state, judge


def test_真材料下裁判看到的只有短引用号(tmp_path, real_values):
    """这条直接对着 09-23 被拦回的那个形状：目录里不许再有能被人当编号抄的 `line`。"""
    from review_agent.grounding import build_catalog, cite
    catalog = build_catalog(real_values, DATE)
    assert len(catalog) > 100
    with_line = [record for record in catalog if "line" in record]
    assert len(with_line) > 100                                    # 落盘侧仍然留着行号
    view = cite(catalog)
    assert not any("line" in record for record in view)             # 喂给模型的那份没有行号
    assert [e["ref"] for e in view[:3]] == ["ev-1", "ev-2", "ev-3"]


def test_引用的短号全部落回真实材料(tmp_path, real_values):
    state, judge = produce(tmp_path, real_values)
    grounding = state["report_grounding"]
    ids = {record["id"] for record in grounding["records"]}
    cited = [eid for section in grounding["sections"] for finding in section["findings"]
             for eid in finding["citations"]]
    summary = grounding["summary"]
    cited += [eid for finding in [summary["market_oneliner"], *summary["risk_alerts"],
                                 *[v["reason"] for v in summary["verification_items"]]] for eid in finding["citations"]]
    assert judge.stages == ["sentiment", "capital", "theme", "dragon_tiger", "leader", "summary"]
    assert judge.written_refs and all(re.fullmatch(r"ev-\d{1,4}", ref) for ref in judge.written_refs)
    assert len(cited) == len(judge.written_refs)
    assert set(cited) <= ids                                       # 交回的都换成了真 id
    assert not any(re.fullmatch(r"ev-\d{1,4}", eid) for eid in cited)   # 短号一个字都不许留在报告里


def test_导出正文里每个引用号都有一条材料可对(tmp_path, real_values):
    """"能不能点开"的数据半环：导出用的 `[id]` 必须都能指回一条具体材料。"""
    from review_agent.grounding import render_markdown
    state, _ = produce(tmp_path, real_values)
    grounding = state["report_grounding"]
    text = render_markdown(grounding["summary"], grounding["records"])
    markers = re.findall(r"\[(ev-[a-f0-9]+)\]", text)
    assert markers
    ids = {record["id"] for record in grounding["records"]}
    assert set(markers) <= ids
    for record in grounding["records"]:
        if record["id"] in set(markers):
            assert str(record.get("text") or record.get("value") or "").strip()   # 指得开也读得到


def test_落盘载荷带得出材料时点与口径说明(tmp_path, real_values):
    state, _ = produce(tmp_path, real_values)
    grounding = state["report_grounding"]
    assert grounding["status"] == "references_validated"
    assert grounding["note"]
    dated = {record.get("target_date") for record in grounding["records"] if record.get("target_date")}
    assert DATE in dated                                            # 每条材料带自己的场次，不混分母
