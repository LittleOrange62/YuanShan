"""板块热度进冻结证据包：与页面同一份服务层、算出来的零与缺数分开、两套热度判据不互翻。

这条接线的三条约束（都是既有裁定，不是风格偏好）：
  · **同一个取数路径**：读 `duanxian/quant.sector_heat()`，与 `/api/quant/sector-heat` 同一份结果；
  · **不给 AI 增加工具数**：读数只进包，`TOOLS` 那六个不变；
  · **缺数≠0**：分母读不到就是 `available:false` + 「未获取」；分母在场而某档缺席才是零。
"""
import json

import pytest

from duanxian import quant
from review_agent.evidence import EvidenceError, ToolSession, build_bundle, heat_evidence

DATE = "2026-09-23"


def snapshot(tmp_path, date=DATE):
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / f"{date}.json").write_text(json.dumps(
        {"target_date": date, "focus_md": "公开复盘叙述", "emotion_metrics": {"limit_up_count": 63}},
        ensure_ascii=False), encoding="utf-8")
    return tmp_path


def canned(**over):
    result = {"available": True, "date": DATE,
              "universe": {"rows_today": 688, "sectors": 688, "heat_null": 0, "unnamed_today": 30},
              "stage_distribution_today": [{"stage": "none", "n": 619}, {"stage": "coil", "n": 35},
                                            {"stage": "fade", "n": 33}, {"stage": "follow", "n": 1}],
              "heat_source_caliber": {"used": "sector（板块级直算，可回溯到 2021）"}}
    result.update(over)
    return result


def metrics(records):
    return [r["metric"] for r in records]


def test_heat_readings_carry_universe_and_five_stages(monkeypatch):
    monkeypatch.setattr(quant, "sector_heat", lambda date="", **kw: canned())
    records, gaps = heat_evidence(DATE)
    assert gaps == []
    assert metrics(records) == ["heat_universe_rows_today", "heat_universe_unnamed_today",
                                "heat_stage_ignite_count", "heat_stage_follow_count",
                                "heat_stage_fade_count", "heat_stage_coil_count", "heat_stage_none_count"]
    assert all(r["label"].startswith("【板块周期·热度口径】") and r["track"] == "own" for r in records)
    assert all(r["date"] == DATE for r in records)
    by = {r["metric"]: r for r in records}
    assert by["heat_universe_rows_today"]["value"] == 688.0
    # 点火档在当日分布里根本没出现，而母体数得出来 ⇒ 那是"零个板块被标为点火"，不是缺数
    assert by["heat_stage_ignite_count"]["value"] == 0.0 and by["heat_stage_ignite_count"]["available"] is True
    assert "算出来的零" in by["heat_stage_ignite_count"]["note"]
    assert by["heat_stage_follow_count"]["value"] == 1.0


def test_two_calibers_and_industry_blindness_travel_with_every_number(monkeypatch):
    monkeypatch.setattr(quant, "sector_heat", lambda date="", **kw: canned())
    for record in heat_evidence(DATE)[0]:
        assert "两套判据" in record["note"] and "两个榜" in record["note"]
        assert "不许互相翻译" in record["note"]
        # "行业点火恒 0 是算不出"必须逐条在场：只写在页面上，模型读不到就会把 0 说成没轮动
        assert "算不出" in record["note"] and "没有行业轮动" in record["note"]


def test_missing_universe_denominator_is_not_zero(monkeypatch):
    broken = canned()
    broken["universe"] = {"rows_today": None, "sectors": 688, "unnamed_today": None}
    monkeypatch.setattr(quant, "sector_heat", lambda date="", **kw: broken)
    by = {r["metric"]: r for r in heat_evidence(DATE)[0]}
    assert by["heat_universe_rows_today"]["available"] is False
    assert by["heat_universe_rows_today"]["display"] == "未获取"
    # 分母都没了，各档就不许报"零个" —— 那是把"没读到"说成"没发生"
    assert by["heat_stage_coil_count"]["value"] == 35.0            # 分布里明写着的原样交
    assert by["heat_stage_ignite_count"]["available"] is False
    assert by["heat_stage_ignite_count"]["value"] is None
    assert "heat_universe_unnamed_today" not in by                 # 缺了就整条不给，不凑 0


@pytest.mark.parametrize("broken", [RuntimeError("connection refused"),
                                    {"available": False, "reason": "该场没有热度行"},
                                    {"available": True, "date": "2026-09-22"}])
def test_heat_becomes_a_gap_and_never_fails_the_review(monkeypatch, broken):
    def boom(date="", **kw):
        if isinstance(broken, Exception):
            raise broken
        return broken
    monkeypatch.setattr(quant, "sector_heat", boom)
    records, gaps = heat_evidence(DATE)
    assert records == [] and len(gaps) == 1 and gaps[0]


def test_retired_member_agg_caliber_is_refused(monkeypatch):
    monkeypatch.setattr(quant, "sector_heat", lambda date="", **kw: canned(
        heat_source_caliber={"used": "member_agg（已废弃：摊下去用唯一主板块、聚回来用全部归属）"}))
    records, gaps = heat_evidence(DATE)
    assert records == [] and "member_agg" in gaps[0]


def test_bundle_pins_revision_and_keeps_tool_count(tmp_path, monkeypatch):
    """接线不许改变工具数，也不许让同类数据在两个通道里各算一遍。"""
    from review_agent.runtime import TOOLS

    assert len(TOOLS) == 6 and "read_sector_heat" not in TOOLS
    monkeypatch.setattr(quant, "sector_heat", lambda date="", **kw: canned())
    monkeypatch.setattr(quant, "quadrant", lambda date="", **kw: {
        "available": True, "date": DATE, "labels_source": "config.setting.quadrant_labels",
        "distribution": {"Q1": {"count": 4, "label": "买入"}}, "coverage": {}})
    reviews = snapshot(tmp_path / "reviews")
    first = build_bundle(reviews, DATE)
    changed = snapshot(tmp_path / "reviews2")
    monkeypatch.setattr(quant, "sector_heat", lambda date="", **kw: canned(
        stage_distribution_today=[{"stage": "none", "n": 618}, {"stage": "coil", "n": 35},
                                   {"stage": "fade", "n": 33}, {"stage": "follow", "n": 1},
                                   {"stage": "ignite", "n": 1}]))
    second = build_bundle(changed, DATE)
    assert first["revision"] != second["revision"]
    heat = [e for e in first["evidence"] if str(e.get("metric", "")).startswith("heat_")]
    assert heat and all(e["source_sha256"] for e in heat)
    assert {e["source_sha256"] for e in heat} == {heat[0]["source_sha256"]}   # 同一份结果钉同一个哈希
    session = ToolSession(first, tmp_path / "ledger.jsonl")
    with pytest.raises(EvidenceError, match="不支持这个指标"):
        session.compare_metric("heat_stage_coil_count", "2026-09-22", DATE)   # 跨日相减仍被拒
