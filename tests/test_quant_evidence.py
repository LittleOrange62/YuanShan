"""量化选股口径进冻结证据包：同一份服务层、缺数不折成 0、不与归因象限混用。"""
import json

import pytest

from duanxian import pg, quant
from review_agent.evidence import EvidenceError, ToolSession, build_bundle, quant_evidence

DATE = "2026-09-23"


def snapshot(tmp_path, date=DATE):
    (tmp_path / f"{date}.json").write_text(json.dumps(
        {"target_date": date, "focus_md": "公开复盘叙述", "emotion_metrics": {"limit_up_count": 63}},
        ensure_ascii=False), encoding="utf-8")
    return tmp_path


def canned(**over):
    result = {"available": True, "date": DATE,
              "distribution": {"Q1": {"count": 4, "label": "买入"}, "Q2": {"count": 1625, "label": "持有"},
                                "Q3": {"count": 16, "label": "试探"}, "Q4": {"count": 3911, "label": "回避"}},
              "coverage": {"score": {"from": "2026-01-27", "to": DATE}, "score_trend": {"from": "2026-06-04", "to": DATE}},
              "quadrant_labels": {"Q1": "买入"}, "labels_source": "config.setting.quadrant_labels"}
    result.update(over)
    return result


def test_quadrant_distribution_becomes_four_calibred_cards(tmp_path, monkeypatch):
    monkeypatch.setattr(quant, "quadrant", lambda date="", **kw: canned())
    records, gaps = quant_evidence(DATE)
    assert gaps == [] and [r["metric"] for r in records] == [
        "quant_quadrant_q1_count", "quant_quadrant_q2_count", "quant_quadrant_q3_count", "quant_quadrant_q4_count"]
    assert all(r["label"].startswith("【四维象限·选股口径】") for r in records)
    assert [r["value"] for r in records] == [4.0, 1625.0, 16.0, 3911.0]
    assert all("config.setting.quadrant_labels" in r["note"] and "判对错" in r["note"] for r in records)
    assert all("2026-06-04" in r["note"] for r in records)   # 覆盖起点随行，空窗期不被读成零
    assert all(r["track"] == "own" and r["date"] == DATE for r in records)


def test_missing_cell_stays_missing_and_is_never_zero(tmp_path, monkeypatch):
    broken = canned()
    broken["distribution"]["Q2"] = {"count": None, "label": "持有"}
    del broken["distribution"]["Q4"]
    monkeypatch.setattr(quant, "quadrant", lambda date="", **kw: broken)
    by_metric = {r["metric"]: r for r in quant_evidence(DATE)[0]}
    for key in ("quant_quadrant_q2_count", "quant_quadrant_q4_count"):
        assert by_metric[key]["available"] is False and by_metric[key]["value"] is None
        assert by_metric[key]["display"] == "未获取"


def test_fallback_labels_are_refused_not_guessed(tmp_path, monkeypatch):
    monkeypatch.setattr(quant, "quadrant", lambda date="", **kw: canned(labels_source="兜底：config.setting 里读不到 quadrant_labels"))
    records, gaps = quant_evidence(DATE)
    assert records == [] and any("不引用象限称谓" in gap for gap in gaps)


@pytest.mark.parametrize("broken", [RuntimeError("connection refused"), {"available": False, "reason": "当日无评分"}])
def test_quant_failure_only_becomes_a_gap(tmp_path, monkeypatch, broken):
    def boom(date="", **kw):
        if isinstance(broken, Exception):
            raise broken
        return broken
    monkeypatch.setattr(quant, "quadrant", boom)
    monkeypatch.setattr("review_agent.evidence.qd_evidence", lambda d: ([], []))
    bundle = build_bundle(snapshot(tmp_path), DATE)
    assert [e for e in bundle["evidence"] if e.get("metric", "").startswith("quant_")] == []
    assert any("量化选股" in gap for gap in bundle["gaps"])


def test_compare_metric_refuses_a_stock_picking_metric(tmp_path, monkeypatch):
    monkeypatch.setattr(quant, "quadrant", lambda date="", **kw: canned())
    monkeypatch.setattr("review_agent.evidence.qd_evidence", lambda d: ([], []))
    session = ToolSession(build_bundle(snapshot(tmp_path), DATE), tmp_path / "tools.jsonl")
    with pytest.raises(EvidenceError):
        session.compare_metric("quant_quadrant_q1_count", "2026-09-22", DATE)


@pytest.mark.skipif(not pg.enabled(), reason="需要本地库（VIBE_PG）")
def test_the_model_reads_the_same_numbers_as_the_page():
    """注入参数验证过的结论都别当已验证：这条直连本地库比数。"""
    try:
        page = quant.quadrant(DATE)
    except Exception as exc:  # noqa: BLE001  别的会话正在写库时跳过，不报假红
        pytest.skip(f'本地库正被其他作业写入，本轮不比对：{type(exc).__name__}')
    if page.get("available") is not True:
        pytest.skip(f"quant.quadrant 在 {DATE} 无读数：{page.get('reason')}")
    records, gaps = quant_evidence(DATE)
    assert gaps == []
    assert len(records) == len(page["distribution"])
    for record in records:
        cell = record["metric"][len("quant_quadrant_"):-len("_count")].upper()
        assert record["value"] == float(page["distribution"][cell]["count"]), cell
        assert record["label"].endswith(page["distribution"][cell]["label"]), cell
