"""作业配置/状态契约的回归。对应 docs/系统配置工作指南/03-环境变量与作业配置.md。"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from duanxian import jobs

NEWS = {"enabled": True, "interval_seconds": 30, "backfill_minutes": 240}


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("ASTOCK_DATA_HOME", str(tmp_path / "datahome"))
    return tmp_path / "datahome"


def test_没有配置文件时全按默认():
    merged, warns = jobs.settings("news", NEWS)
    assert merged == NEWS and warns == []


def test_只写要改的键其余留默认():
    jobs.write_setting("news", interval_seconds=10)
    merged, warns = jobs.settings("news", NEWS)
    assert merged == {"enabled": True, "interval_seconds": 10, "backfill_minutes": 240}
    assert warns == []


def test_enabled_false_不许被读成0():
    """bool 是 int 的子类，任何整数强转都会把 false 变成 0（真值判断仍成立但类型错了）。"""
    jobs.set_enabled("news", False)
    merged, _ = jobs.settings("news", NEWS)
    assert merged["enabled"] is False


def test_非法值出声并回退默认而不是静默采用():
    jobs.write_setting("news", interval_seconds="三十")
    jobs.write_setting("news", run_at="25:99")
    merged, warns = jobs.settings("news", {**NEWS, "run_at": "15:10"})
    assert merged["interval_seconds"] == 30
    assert merged["run_at"] == "15:10"
    assert len(warns) == 2 and all("三十" in w or "25:99" in w for w in warns)


def test_未知配置项要点名():
    jobs.write_setting("news", typo_key=1)
    _merged, warns = jobs.settings("news", NEWS)
    assert warns and "typo_key" in warns[0]


def test_值留空等于交还默认():
    jobs.write_setting("news", interval_seconds=5)
    jobs.write_setting("news", interval_seconds=None)
    merged, _ = jobs.settings("news", NEWS)
    assert merged["interval_seconds"] == 30


def knobs_only(cfg: dict, name: str) -> dict:
    """去掉 `_changed_at` / `_changed_by` 这层留痕，只看配置键本身。

    留痕是 2026-09-28 加的：`enabled=false` 是会一直留着的持久状态，没有"谁改的"
    就只能互相猜（那天同一个作业被关三次，谁也答不上来）。它不是配置项，
    `settings()` 会跳过 `_` 前缀键，这里的比较也跟着剥掉。
    """
    return {k: v for k, v in (cfg.get(name) or {}).items() if not k.startswith("_")}


def test_改一个作业不许动到另一个():
    jobs.write_setting("news", interval_seconds=10)
    jobs.write_setting("fundflow", run_at="16:00")
    cfg = jobs.load_config()
    assert knobs_only(cfg, "news") == {"interval_seconds": 10}
    assert knobs_only(cfg, "fundflow") == {"run_at": "16:00"}


def test_写配置要留下谁改的():
    """开关被写坏成 false 之后不会自己恢复，"什么时候、从哪条路改的"是唯一线索。"""
    jobs.set_enabled("news", False, via="cli:jobs.py disable")
    note = jobs.change_note("news")
    assert note["changed_by"] == "cli:jobs.py disable" and note["changed_at"]
    merged, warns = jobs.settings("news", NEWS)
    assert merged["enabled"] is False
    assert not warns                      # 留痕键不许被当成"未知配置项"报出来


def test_只有成功才刷新最近成功时刻():
    jobs.record("news", ok=False, detail={"reason": "源挂了"})
    first = jobs.state()["news"]
    assert first["last_ok_at"] is None and first["consecutive_failures"] == 1
    jobs.record("news", ok=True, detail={"rows": 3})
    second = jobs.state()["news"]
    assert second["last_ok_at"] == second["last_run_at"]
    assert second["consecutive_failures"] == 0


def test_连败计数跨轮累加成功后归零():
    for _ in range(3):
        jobs.record("news", ok=False, detail={"reason": "x"})
    assert jobs.state()["news"]["consecutive_failures"] == 3
    jobs.record("news", ok=True)
    assert jobs.state()["news"]["consecutive_failures"] == 0


def test_状态覆盖不许冲掉用户配置():
    """config 与 state 分文件的全部意义就在这条。"""
    jobs.write_setting("news", interval_seconds=10)
    for _ in range(5):
        jobs.record("news", ok=True)
    assert knobs_only(jobs.load_config(), "news") == {"interval_seconds": 10}
    assert json.loads(Path(jobs.state_path()).read_text(encoding="utf-8"))["news"]["last_ok_at"]


def test_配置和状态是两个文件(home):
    assert jobs.config_path() != jobs.state_path()
    assert jobs.config_path() == str(home / "jobs" / "config.json")
    assert jobs.state_path() == str(home / "jobs" / "state.json")


def test_业务资产不许落在_local_下(home):
    """.local/ 按主 README 是"开发日志与诊断、不进版本控制、换机没人搬"，
    作业配置与状态属于业务资产，必须在 ASTOCK_DATA_HOME 那棵树里。"""
    assert jobs.config_path().startswith(str(home))
    assert jobs.state_path().startswith(str(home))
    assert ".local" not in jobs.config_path()
    assert jobs.data_home() == str(home)


def test_summary_带出告警和最近成功():
    jobs.write_setting("news", interval_seconds="坏值")
    jobs.record("news", ok=True)
    row = next(r for r in jobs.summary({"news": NEWS}) if r["name"] == "news")
    assert row["enabled"] is True
    assert row["warnings"] and "坏值" in row["warnings"][0]
    assert row["last_ok_at"]


# ---------------------------------------------------------------- 运行流水（10-10 新加）
# 之前每个作业在 state.json 里只留"最近一次"，于是"这周红过几次"没人答得上来；
# 而 10-10 那次看护循环假绿（台账冻 18 小时、页面写"正常"）也是因为只有一格，
# 看不出它是"刚记了一次"还是"再没记过"。

def test_每次记录都留一笔流水_带outcome与原因(home):
    jobs.record("news", ok=True, detail={"written": 12})
    jobs.record("news", ok=False, detail={"reason": "连接被对面断了"})
    jobs.record("news", ok=False, detail={"skipped": True, "reason": "非交易日按口径跳过"})
    runs = jobs.state()["news"]["runs"]
    assert [r["outcome"] for r in runs] == ["ok", "fail", "skipped"], runs
    assert runs[0]["rows_written"] == 12
    assert "连接被对面断了" in runs[1]["error"]
    assert runs[2]["error"] == "非交易日按口径跳过"
    # 三态都进流水：跳过不是失败，但它也是"这一轮动过"的证据，漏掉就成了"没跑过"
    assert jobs.state()["news"]["consecutive_failures"] == 1, "skipped 不进连败（与 outcome 分开）"


def test_流水有上限且丢最旧的(home):
    # 用失败的轮次测：成功轮次按设计不留 `last_error`（那一格是"为什么没成"，
    # 不是"这一轮干了什么"，编不出内容也不该硬塞）。
    for i in range(jobs.RUNS_KEEP + 7):
        jobs.record("news", ok=False, detail={"reason": f"第 {i} 次"})
    runs = jobs.state()["news"]["runs"]
    assert len(runs) == jobs.RUNS_KEEP, "文件是每次面板刷新都要读的，不能无限长"
    assert "第 7 次" in runs[0]["error"], "丢的必须是最旧的"
    assert f"第 {jobs.RUNS_KEEP + 6} 次" in runs[-1]["error"]


def test_原因太长要截住(home):
    jobs.record("news", ok=False, detail={"reason": "炸" * 4000})
    err = jobs.state()["news"]["runs"][-1]["error"]
    assert len(err) <= jobs.RUN_ERROR_CHARS


def test_state_有缓存但外部改过文件必须读到新的(home):
    """缓存按 mtime+size 判定；写盘走 atomic_write_json（换 inode），必然变。
    这条钉的是"别把旧台账读成现值" —— 缓存最容易犯的错。"""
    jobs.record("news", ok=True)
    assert jobs.state()["news"]["last_status"] == "ok"
    import json
    from pathlib import Path
    p = Path(jobs.state_path())
    data = json.loads(p.read_text(encoding="utf-8"))
    data["news"]["last_status"] = "fail"
    p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    assert jobs.state()["news"]["last_status"] == "fail", "换了内容还读到缓存 = 把没发生的报成发生过"


def test_交出去的副本被改过不许污染缓存(home):
    """`record()` 就是"读出来→改→写回"，所以 state() 必须给副本；
    真给缓存对象，写盘失败时缓存里就留着盘上没有的那一笔。"""
    a = jobs.state()
    a["news"] = {"last_status": "手工塞的"}
    assert "news" not in jobs.state(), "第二次读必须是文件里的内容"
