"""`/api/datasource` 与 `/api/system/doctor` 的回归。钉的是"假新鲜"和 NaN 两条。"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.append(str(Path(__file__).resolve().parents[1] / "vr"))   # 末尾：插最前会让 vr/app.py 抢走顶层 app/ 包
import sysboard  # noqa: E402


@pytest.fixture
def fake_pg(monkeypatch):
    def install(rows=None, probe=None):
        def fake(sql, params=(), *a, **k):
            if "config.datasource" in sql:
                return rows
            if "datasource_probe" in sql:
                return probe
            return None
        monkeypatch.setattr(sysboard, "pg_enabled", lambda *a, **k: True)
        monkeypatch.setattr(sysboard, "fetch_df", fake)
    return install


def test_NULL_变_NaN_不许炸接口(fake_pg):
    """`row_count`/`latency_ms`/`last_check` 为 NULL 时 pandas 给 NaN，
    早期版本在这一步 int(NaN) 直接抛 → 页面 500。"""
    fake_pg(pd.DataFrame([{"datasource_key": "ts_moneyflow", "name": "tushare 资金流",
                           "kind": "http", "provider": "tushare", "layer": "md",
                           "ds_role": "primary", "cost": "free", "health": "no_permission",
                           "is_active": True, "last_check": float("nan"),
                           "last_ok_at": pd.NaT, "last_err": "缺 token",
                           "latency_ms": float("nan"), "row_count": float("nan"),
                           "auth_env_name": "FT_TUSHARE_TOKEN", "note": float("nan")}]))
    d = sysboard.datasource_payload()
    json.dumps(d, ensure_ascii=False, allow_nan=False)
    s = d["sources"][0]
    assert s["row_count"] is None and s["latency_ms"] is None
    assert s["last_probe_at"] is None and s["stale_seconds"] is None
    assert s["probe_stale"] is True                 # 从没探过 = 陈旧，不是"未知即健康"
    assert d["counts"]["no_permission"] == 1


def test_no_permission_单独一档不混进_down(fake_pg):
    fake_pg(pd.DataFrame([
        {"datasource_key": "a", "name": "a", "kind": "http", "provider": "x", "layer": "md",
         "ds_role": "p", "cost": "free", "health": "no_permission", "is_active": True,
         "last_check": None, "last_ok_at": None, "last_err": None, "latency_ms": None,
         "row_count": None, "auth_env_name": "FT_TUSHARE_TOKEN", "note": None},
        {"datasource_key": "b", "name": "b", "kind": "http", "provider": "x", "layer": "md",
         "ds_role": "p", "cost": "free", "health": "down", "is_active": True,
         "last_check": None, "last_ok_at": None, "last_err": None, "latency_ms": None,
         "row_count": None, "auth_env_name": None, "note": None}]), probe=pd.DataFrame(
        {"datasource_key": ["a", "b"], "at": pd.to_datetime(["2026-09-25", "2026-06-01"]),
         "n": [1, 1]}))
    d = sysboard.datasource_payload()
    assert d["counts"]["no_permission"] == 1 and d["counts"]["down"] == 1
    by = {s["datasource_key"]: s for s in d["sources"]}
    assert by["a"]["probe_stale"] is False          # 刚探过
    assert by["b"]["probe_stale"] is True           # 三个月没探 → 那个 down 是历史值


def test_库读不到时报不可读而不是空清单(fake_pg, monkeypatch):
    monkeypatch.setattr(sysboard, "pg_enabled", lambda *a, **k: False)
    d = sysboard.datasource_payload()
    assert d["available"] is False and d["reason"] and d["counts"] is None


def test_doctor_有旧值就先给旧值不让人等(monkeypatch):
    calls = []

    def fake_run():
        calls.append(1)
        return {"generated_at": "x", "available": True, "reason": None, "source": "s",
                "blocking_failures": 0, "advisory_failures": 0,
                "items": [{"key": "平台", "label": "平台", "ok": True,
                           "detail": "通过", "advisory": False}]}

    monkeypatch.setattr(sysboard, "_run_doctor", fake_run)
    monkeypatch.setattr(sysboard, "_doctor_cache", None)
    monkeypatch.setattr(sysboard, "_doctor_refreshing", False)
    first = sysboard.doctor_payload()
    second = sysboard.doctor_payload()
    assert first["age_seconds"] == 0 and second["age_seconds"] is not None
    assert len(calls) == 1, "第二次必须复用缓存：体检单跑约 9 秒，不能每次请求都算"
    assert second["items"][0]["label"] == "平台"
    assert re.compile(r"\bnan\b|\bNaT\b").search(json.dumps(second, ensure_ascii=False)) is None
