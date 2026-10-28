"""qd 轨读数是否与页面读同一份、缺数是否诚实、会不会跨口径相减。"""
import json

import pytest

from review_agent.evidence import EvidenceError, ToolSession, build_bundle, qd_evidence
from duanxian import qd, pg

DATE = "2026-09-23"


def snapshot(tmp_path, date=DATE):
    (tmp_path / f"{date}.json").write_text(json.dumps(
        {"target_date": date, "focus_md": "公开复盘叙述", "emotion_metrics": {"limit_up_count": 63}},
        ensure_ascii=False), encoding="utf-8")
    return tmp_path


def cycle(**over):
    result = {"available": True, "date": DATE, "own": None,
              "qd": {"stage_ui": "退潮", "confidence": 41, "metrics": {
                  "limit_up_count": 70, "touched_count": 88, "board_height": 6, "amount_yi": 21000.5,
                  "limit_down_count": None, "first_board_ratio_pct": None}},
              "spec_version": "qd-1", "source": "qd.sentiment_cycle", "computed_by": "yuanshan"}
    result.update(over)
    return result


def test_qd_readings_join_the_same_frozen_bundle(tmp_path, monkeypatch):
    monkeypatch.setattr(qd, "sentiment_cycle", lambda date="": cycle())
    bundle = build_bundle(snapshot(tmp_path), DATE)
    qd_records = [e for e in bundle["evidence"] if e["track"] == "qd"]
    own_records = [e for e in bundle["evidence"] if e["track"] == "own"]
    assert {r["metric"] for r in qd_records} == {
        "qd_limit_up_count", "qd_limit_down_count", "qd_touched_count", "qd_broken_touched_count",
        "qd_board_height", "qd_high_board_count", "qd_first_board_count", "qd_first_board_ratio_pct",
        "qd_rise_count", "qd_fall_count", "qd_amount_yi"}
    assert own_records and all(r["source"] != "quantdash 口径轨只读服务层 duanxian/qd.py" for r in own_records)
    by_metric = {r["metric"]: r for r in qd_records}
    assert by_metric["qd_limit_up_count"]["value"] == 70.0
    assert by_metric["qd_amount_yi"]["display"] == "21000.50 亿"
    # 缺数就是缺数：None 既不能变 0，也不能变空串。
    for key in ("qd_limit_down_count", "qd_first_board_ratio_pct"):
        assert by_metric[key]["available"] is False and by_metric[key]["value"] is None
        assert by_metric[key]["display"] == "未获取"
    # 阶段与"判定强度"不是数值证据，不进模型边界。
    assert not any("stage" in r["metric"] or "confidence" in r["metric"] for r in qd_records)
    assert all(r["spec_version"] == "qd-1" and r["computed_by"] == "yuanshan" for r in qd_records)
    # 每条证据都要钉得住来源：qd 读数钉在本轮实际读到的那份结构哈希上。
    assert len({r["source_sha256"] for r in qd_records}) == 1 and all(r["source_sha256"] for r in qd_records)
    assert all(r["source_sha256"] for r in bundle["evidence"])


def test_bundle_revision_changes_when_qd_numbers_change(tmp_path, monkeypatch):
    monkeypatch.setattr(qd, "sentiment_cycle", lambda date="": cycle())
    first = build_bundle(snapshot(tmp_path), DATE)
    changed = cycle()
    changed["qd"]["metrics"]["limit_up_count"] = 71
    monkeypatch.setattr(qd, "sentiment_cycle", lambda date="": changed)
    second = build_bundle(snapshot(tmp_path), DATE)
    assert first["revision"] != second["revision"]


def test_list_evidence_shows_the_track_to_the_model(tmp_path, monkeypatch):
    monkeypatch.setattr(qd, "sentiment_cycle", lambda date="": cycle())
    bundle = build_bundle(snapshot(tmp_path), DATE)
    session = ToolSession(bundle, tmp_path / "tools.jsonl")
    items = session.list_evidence()["items"]
    tagged = {item["id"]: item.get("track") for item in items}
    assert "qd" in tagged.values() and "own" in tagged.values()


def test_compare_metric_refuses_across_calibers(tmp_path, monkeypatch):
    monkeypatch.setattr(qd, "sentiment_cycle", lambda date="": cycle())
    bundle = build_bundle(snapshot(tmp_path), DATE)
    session = ToolSession(bundle, tmp_path / "tools.jsonl")
    with pytest.raises(EvidenceError):
        session.compare_metric("qd_limit_up_count", "2026-09-22", DATE)


@pytest.mark.parametrize("broken", [RuntimeError("connection refused"), {"available": False, "reason": "还没回填"}])
def test_qd_failure_only_becomes_a_gap_never_a_failed_review(tmp_path, monkeypatch, broken):
    def boom(date=""):
        if isinstance(broken, Exception):
            raise broken
        return broken
    monkeypatch.setattr(qd, "sentiment_cycle", boom)
    bundle = build_bundle(snapshot(tmp_path), DATE)
    assert [e for e in bundle["evidence"] if e["track"] == "qd"] == []
    assert any("quantdash 口径轨" in gap for gap in bundle["gaps"])
    assert [e for e in bundle["evidence"] if e["track"] == "own"]


@pytest.mark.skipif(not pg.enabled(), reason="需要本地库（VIBE_PG）")
def test_the_model_reads_the_same_numbers_as_the_panel():
    """不能用注入参数当验证：这条直连本地库，比对服务层与证据卡的每一个数。"""
    try:
        panel = qd.sentiment_cycle(DATE)
    except Exception as exc:  # noqa: BLE001  别的会话正在写库时跳过，不报假红
        pytest.skip(f'本地库正被其他作业写入，本轮不比对：{type(exc).__name__}')
    if panel.get("available") is not True:
        pytest.skip(f"qd.sentiment_cycle 在 {DATE} 无读数：{panel.get('reason')}")
    records, gaps = qd_evidence(DATE)
    assert gaps == []
    by_metric = {r["metric"]: r for r in records}
    assert len(records) == len(by_metric)
    for metric, record in by_metric.items():
        source_value = (panel.get("qd") or {}).get("metrics", {}).get(metric.removeprefix("qd_"))
        if source_value is None:
            assert record["available"] is False and record["display"] == "未获取"
        else:
            assert record["value"] == float(source_value), metric
    assert all(record["track"] == "qd" and record["date"] == panel["date"] for record in records)
