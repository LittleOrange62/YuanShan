"""失败单步重试阶梯（`ingest/jobs/step_retry.py`）的判据回归。

钉的是用户 2026-10-08 那三句话，一条都不许松：
① **只重试之前失败的**（`refused` 按口径拒写、`ok` 成功都不许再跑）；
② **每一档先重读台账**（补成的当场摘掉，不许重复跑一份好数据）；
③ **档期用完就放弃**（等下一次收盘链），而且时点与档数在人手里可增删。

外加两条形状：跨午夜的档序（01:00 属于次日，不是当天最早）、
以及"页面上点一下会起真进程"这条洞在测试里必须硬挡。
"""
from __future__ import annotations

import datetime as dt
import json

import pytest

from duanxian import jobs as jstore
from ingest.jobs import e1_fill_all as e1
from ingest.jobs import step_retry as sr


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    """所有账都落在 tmp 里：阶梯会读写 `jobs/step_retry_plan.json` 与 `state.json`。"""
    monkeypatch.setenv("ASTOCK_DATA_HOME", str(tmp_path / "datahome"))
    monkeypatch.setenv("VIBE_BACKUP_DIR", str(tmp_path / "backup"))
    return tmp_path


DAY = dt.date(2026, 10, 8)          # 链收工那一天（17:21）
TEXT = "21:00,22:00,23:00,01:00,02:30,04:20"


def _ledger(target="2026-10-08", written="2026-10-08T17:21:45+08:00", steps=None):
    return {"target": target, "written_at": written, "verdict": "PARTIAL",
            "steps": steps if steps is not None else [
                {"name": "fundflow", "status": "fail", "rc": 1, "label": "东财大盘资金流"},
                {"name": "sectorflow", "status": "ok", "rc": 0, "label": "行业资金流"},
            ]}


def _write_ledger(monkeypatch, led):
    monkeypatch.setattr(e1, "read_ledger", lambda: led)


# ------------------------------------------------------------------ 档期

def test_档期按绝对时刻排而不是钟表时分():
    """10-08 实测踩过：按时分排会得到 `01:00、02:30、04:20、21:00…`，
    于是 21:00 那档永远轮不上，"最后一档"也判错（放弃时机就错了）。"""
    rows = sr.lanes(DAY, TEXT)
    assert [r["label"] for r in rows] == ["21:00", "22:00", "23:00", "01:00", "02:30", "04:20"]
    assert rows[0]["at"] == dt.datetime(2026, 10, 8, 21, 0)
    assert rows[3]["at"] == dt.datetime(2026, 10, 9, 1, 0), "01:00 属于次日"
    assert rows[-1]["at"] > rows[0]["at"]


def test_坏格子只跳过那一格不废整条阶梯():
    rows = sr.lanes(DAY, "21:00, 25:00 ,22:00,7:30")
    ok = [r["label"] for r in rows if not r.get("broken")]
    bad = [r["label"] for r in rows if r.get("broken")]
    assert "07:30" in ok and "21:00" in ok and bad == ["25:00"]


def test_times_的校验认整串并指认出哪一格():
    assert jstore._valid("times", "21:00,22:00", "") is None
    assert "不是 HH:MM" in jstore._valid("times", "21:00,25:00", "")
    assert jstore._valid("times", "", "")            # 空串要拒：留空档=阶梯名存实亡
    assert "至少" in (jstore._valid("times", " , ", "") or "")
    many = ",".join("%02d:00" % h for h in range(12))
    assert jstore._valid("times", many + ",07:30", "") is None or True
    assert "12 档" in (jstore._valid("times", many + ",07:30", "") or "")


# ------------------------------------------------------------------ 待补名单

def test_只取真失败_refused_与_ok_都不进名单():
    led = _ledger(steps=[
        {"name": "fundflow", "status": "fail", "rc": 1},
        {"name": "fundflow_rank", "status": "refused", "rc": 3},
        {"name": "sectorflow", "status": "ok", "rc": 0},
    ])
    assert sr.ledger_failures(led) == ["fundflow"]


def test_每档先重读台账补成的当场摘掉(home, monkeypatch):
    """台账被 `--only` 合并后，fundflow 变 ok → 待补名单应当立刻空掉，
    否则下一档会去重跑一份好数据（用户明确说"不失败的任务不需要重试"）。"""
    _write_ledger(monkeypatch, _ledger(steps=[{"name": "fundflow", "status": "ok", "rc": 0}]))
    st = sr.status(dt.datetime(2026, 10, 8, 21, 30))
    assert st["pending"] == [] and st["next_at"] is None
    row, why = sr.due_lane(dt.datetime(2026, 10, 8, 21, 30))
    assert row is None and "没有要重试的步骤" in why


def test_按口径拒写不进重试名单但要说清(home, monkeypatch):
    _write_ledger(monkeypatch, _ledger(steps=[
        {"name": "sectorflow", "status": "refused", "rc": 2}]))
    st = sr.status(dt.datetime(2026, 10, 8, 21, 30))
    assert st["pending"] == [] and st["pending_soft"] == ["sectorflow"]


# ------------------------------------------------------------------ 到点判断

def test_下一档没到就不跑并说为什么时候(home, monkeypatch):
    _write_ledger(monkeypatch, _ledger())
    row, why = sr.due_lane(dt.datetime(2026, 10, 8, 20, 59))
    assert row is None and "21:00" in why


def test_迟到超过补窗就不算这一档(home, monkeypatch):
    """21:00 + 90 分钟后不认：那一档的意思就是"那时再试一次"，拖太久只做噪声。"""
    _write_ledger(monkeypatch, _ledger())
    row, _ = sr.due_lane(dt.datetime(2026, 10, 8, 22, 40))     # 21:00 的窗已过、22:00 的窗内
    assert row is not None and row["label"] == "22:00"
    late, why = sr.due_lane(dt.datetime(2026, 10, 8, 23, 55))  # 23:00 刚过，窗还没关
    assert late is not None and late["label"] == "23:00"
    done, why2 = sr.due_lane(dt.datetime(2026, 10, 9, 6, 0))   # 04:20 + 90 分也过了
    assert done is None and "用完" in why2, why2


def test_最后一档跑完仍失败就放弃并记账(home, monkeypatch):
    """链 10-08 17:21 收工，最后一档 04:20 落在 10-09 凌晨 —— 锚点日期取台账那天。"""
    led = _ledger()
    _write_ledger(monkeypatch, led)
    monkeypatch.setattr(sr, "external_run_busy", lambda: None)
    monkeypatch.setattr(jstore, "claim", lambda key: True)
    monkeypatch.setattr(jstore, "release", lambda key: None)
    # 假"跑完了还是失败"：台账原样不动
    out = sr.run_due(dt.datetime(2026, 10, 9, 4, 25))
    assert out["ok"] is False and out["lane"] == "04:20", out
    plan = sr.read_plan()
    assert plan["gave_up"] is True and "04:20" in (plan.get("lanes_done") or {})
    rec = jstore.state().get("step_retry") or {}
    assert rec.get("last_status") == "fail" and "等下一次收盘链" in (rec.get("last_error") or "")


def test_开关关着与已放弃各说一句(home, monkeypatch):
    """两种"不跑"的原因不许糊成一句：关着 = 有人关的（会一直关到有人再打开）；
    已放弃 = 这一场的档期用完了，等下一次收盘链。"""
    led = _ledger()
    _write_ledger(monkeypatch, led)
    jstore.write_setting("step_retry", via="test", enabled=False)
    row, why = sr.due_lane(dt.datetime(2026, 10, 8, 21, 30))
    assert row is None and "关着" in why
    jstore.write_setting("step_retry", via="test", enabled=True)
    led2 = _ledger()
    sr.write_plan({"cycle": led2["written_at"], "written_at": led2["written_at"],
                   "target": led2["target"], "lanes_done": {}, "gave_up": True})
    row2, why2 = sr.due_lane(dt.datetime(2026, 10, 8, 21, 30))
    assert row2 is None and "放弃" in why2, why2
    # 而计划是按"哪一遍链"播种的：链重跑一次（cycle 变了）就必须重新有档可跑
    _write_ledger(monkeypatch, _ledger(written="2026-10-08T23:59:00+08:00"))
    row3, _ = sr.due_lane(dt.datetime(2026, 10, 8, 21, 30))
    assert row3 is not None, "新一场不能继承上一场的放弃"


def test_一档跑完后同一链次不会立刻再跑同一档(home, monkeypatch):
    """这一档跑完会写一份新台账（`written_at` 变了、`cycle` 继承），
    档期锚点**不能拿 written_at** —— 否则每一轮心跳都把它当新一场，21:00 那档会被反复重跑。"""
    led = _ledger()
    _write_ledger(monkeypatch, led)
    monkeypatch.setattr(sr, "external_run_busy", lambda: None)
    monkeypatch.setattr(jstore, "claim", lambda key: True)
    monkeypatch.setattr(jstore, "release", lambda key: None)

    def 跑完但仍失败(names, timeout):
        # 真实链就是这么写的：合并后台账刷新 written_at，cycle 继承上一次整遍链的
        monkeypatch.setattr(e1, "read_ledger",
                            lambda: dict(led, written_at="2026-10-08T21:20:00+08:00",
                                         cycle=led["written_at"], ran_only=["fundflow"]))
        return 3

    monkeypatch.setattr(sr, "_run_only", 跑完但仍失败)
    out = sr.run_due(dt.datetime(2026, 10, 8, 21, 30))
    assert out["lane"] == "21:00" and sr.read_plan()["cycle"] == led["written_at"]
    # 紧接着再问一次：21:00 已经被这一遍链用掉了，不许再端上来（下一档是 22:00，还没到点）
    row, why = sr.due_lane(dt.datetime(2026, 10, 8, 21, 31))
    assert row is None and "22:00" in why, why
    late, _ = sr.due_lane(dt.datetime(2026, 10, 8, 22, 10))
    assert late is not None and late["label"] == "22:00"


# ------------------------------------------------------------------ 跑一档

def test_跨午夜的档期不能被上一档写下的台账推到次夜(home, monkeypatch):
    """10-08 夜实测：`lanes_done` 只有 21/22/23/01 四档，02:30 与 04:20 **整夜没跑**，
    而 `gave_up=false` —— 因为 01:00 那档把 `written_at` 推到了 10-09，日锚点跟着挪一天，
    剩下两档就被算成了 10-10 凌晨。档期的"哪一天"必须跟着**链次**，不是跟着最后一次写台账。"""
    led = dict(_ledger(), written_at="2026-10-09T01:04:59+08:00",
               cycle="2026-10-08T21:23:31+08:00", ran_only=["fundflow"])
    _write_ledger(monkeypatch, led)
    sr.write_plan({"cycle": led["cycle"], "target": "2026-10-08", "gave_up": False,
                   "lanes_done": {lb: {"ran_at": "2026-10-09T01:04:59", "outcome": "fail",
                                       "ran": ["fundflow"], "rc": 3}
                                  for lb in ("21:00", "22:00", "23:00", "01:00")}})
    st = sr.status(dt.datetime(2026, 10, 9, 1, 10))
    assert st["day"] == "2026-10-08", "日锚点要跟链次那一天，不能跟 written_at"
    got = {r["label"]: r["at"] for r in st["lanes"]}
    assert got["02:30"] == "2026-10-09T02:30", got["02:30"]
    row, why = sr.due_lane(dt.datetime(2026, 10, 9, 2, 35))
    assert row is not None and row["label"] == "02:30", why
    last, _ = sr.due_lane(dt.datetime(2026, 10, 9, 4, 40))
    assert last is not None and last["label"] == "04:20"


def test_抢不到锁这一档不算用过(home, monkeypatch):
    """顺延 ≠ 失败 ≠ 用掉一档：记错了会让 22:00 那档白过。"""
    _write_ledger(monkeypatch, _ledger())
    monkeypatch.setattr(sr, "external_run_busy", lambda: None)
    monkeypatch.setattr(jstore, "claim", lambda key: False)
    out = sr.run_due(dt.datetime(2026, 10, 8, 21, 30))
    assert out["rc"] == 2 and out["busy"] is True and out["lane"] == "21:00"
    plan = sr.read_plan()
    assert "21:00" not in (plan.get("lanes_done") or {})


def test_外部有人在跑链就不起第二个进程(home, monkeypatch):
    """`claim` 是**进程内**的集合，挡不住终端里手敲的那遍 —— 两个 c11 同打一个源
    会被一起判成网络故障，所以起子进程前按命令行探一次。"""
    _write_ledger(monkeypatch, _ledger())
    monkeypatch.setattr(sr, "external_run_busy", lambda: "链正在另一个进程里跑（终端里的 e1_fill_all）")
    out = sr.run_due(dt.datetime(2026, 10, 8, 21, 30))
    assert out["busy"] is True and "顺延" in out["reason"]


def test_真子进程在测试里绝不起来(home, monkeypatch):
    def boom(*a, **k):
        raise AssertionError("阶梯在测试进程里真起了 subprocess —— 洞没守住")

    monkeypatch.setattr(sr.subprocess, "run", boom)
    assert sr._run_only(["fundflow"], 10) == 0
    assert sr.external_run_busy() is None      # 探测本身也不该出门


def test_某一档补成就从名单里摘掉并且不再跑(home, monkeypatch):
    led = _ledger()
    _write_ledger(monkeypatch, led)
    monkeypatch.setattr(sr, "external_run_busy", lambda: None)
    monkeypatch.setattr(jstore, "claim", lambda key: True)
    monkeypatch.setattr(jstore, "release", lambda key: None)

    def 补跑成功(names, timeout):
        # 模拟一次真实合并后的台账：fundflow 变 ok
        good = dict(led, steps=[{"name": "fundflow", "status": "ok", "rc": 0, "label": "大盘资金流"},
                                {"name": "sectorflow", "status": "ok", "rc": 0, "label": "行业资金流"}])
        monkeypatch.setattr(e1, "read_ledger", lambda: good)
        return 0

    monkeypatch.setattr(sr, "_run_only", 补跑成功)
    out = sr.run_due(dt.datetime(2026, 10, 8, 21, 30))
    assert out["ok"] is True and out["recovered"] == ["fundflow"] and out["gave_up"] is False
    after = sr.status(dt.datetime(2026, 10, 8, 21, 35))
    assert after["pending"] == [] and "21:00" in [l["label"] for l in after["lanes"] if l["ran_at"]]


def test_补成一部分算这一档干了活但仍说清还差几步(home, monkeypatch):
    """2026-10-12 21:00 档的现场：`quality` 补成功了、只剩 `fundflow`（东财 push2 那个口被封，
    今晚补不动、明晚也补不动）。原先 `ok=not still` 把这个结果记成"连败 1、最近成功停在 10-10"，
    doctor 于是念出"这一场要么没跑要么没跑成"—— 可它明明跑了还补成了一步。
    一个外部源永久封死的日子里，这条会夜夜涨连败，把真故障（阶梯自己没起、崩了）淹掉。
    判据改成"**这一档有没有把该补的补动**"；还差哪几步留在 detail.still_failing，
    而"库里到底缺不缺"由《当日数据到位》那一项按表判，不在这一格重复报红。"""
    led = _ledger(steps=[{"name": "fundflow", "status": "fail", "rc": 1, "label": "东财大盘资金流"},
                         {"name": "quality", "status": "fail", "rc": 1, "label": "质量闸"}])
    _write_ledger(monkeypatch, led)
    monkeypatch.setattr(sr, "external_run_busy", lambda: None)
    monkeypatch.setattr(jstore, "claim", lambda key: True)
    monkeypatch.setattr(jstore, "release", lambda key: None)

    def 只补成quality(names, timeout):
        good = dict(led, steps=[{"name": "fundflow", "status": "fail", "rc": 1, "label": "东财大盘资金流"},
                                {"name": "quality", "status": "ok", "rc": 0, "label": "质量闸"}])
        monkeypatch.setattr(e1, "read_ledger", lambda: good)
        return 1

    monkeypatch.setattr(sr, "_run_only", 只补成quality)
    out = sr.run_due(dt.datetime(2026, 10, 8, 21, 4))
    assert out["ok"] is False and out["worked"] is True, "整场没干净仍属未完成，但这一档确实补动了一步"
    assert out["recovered"] == ["quality"] and out["still_failing"] == ["fundflow"]
    rec = jstore.state().get("step_retry") or {}
    assert rec.get("last_status") == "ok" and rec.get("consecutive_failures") == 0, rec
    assert rec.get("last_error") is None, "成功的话不许把成功句塞进 last_error（那读起来就是红）"
    assert rec["last_detail"]["still_failing"] == ["fundflow"], "还差几步要留在台账里，别一起洗白"
    # 而"跑完什么都没补动"仍然必须报红
    led2 = _ledger(steps=[{"name": "fundflow", "status": "fail", "rc": 1, "label": "东财大盘资金流"}])
    _write_ledger(monkeypatch, led2)
    monkeypatch.setattr(sr, "_run_only", lambda names, timeout: 1)
    out2 = sr.run_due(dt.datetime(2026, 10, 8, 22, 4))
    assert out2["ok"] is False and out2["worked"] is False
    assert (jstore.state().get("step_retry") or {}).get("last_status") == "fail"


# ------------------------------------------------------------------ 合并台账

def test_只跑几步时台账是合并而不是覆盖():
    """10-08 的第一版覆盖写：补跑 fundflow 成功后，另外几步的失败被一起洗掉，
    面板从"4 步没成"变成"0 步没成"，而库里还缺着。合并逻辑住在 `main()` 里
    （要跑一整条链才触得到），所以这里钉源码 + 阶梯只认 `fail` 这一条闭环。"""
    import pathlib
    import re

    body = pathlib.Path(e1.__file__).read_text(encoding="utf-8")
    assert re.search(r"if only:\n(?:.*\n){0,12}?\s+old = read_ledger\(\)", body), \
        "--only 必须合并进上一份台账"
    assert "ledger['verdict'] = 'DONE' if not still else 'PARTIAL'" in body, \
        "结论要按合并后的整体说，不然一次成功的补跑会把同一天别的失败盖成'全好了'"
    # 合并后的台账里 refused/ok 都不该被阶梯再跑一遍
    assert sr.ledger_failures({"steps": [{"name": "a", "status": "refused"},
                                         {"name": "b", "status": "ok"},
                                         {"name": "c", "status": "fail"}]}) == ["c"]
