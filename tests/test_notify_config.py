"""消息推送（渠道配置 + 内容组装 + 定时计划）的回归。

钉的是四类"会静默骗人"的事：

① **凭据泄漏**：任何一份读数里都不许出现真 URL（这条一旦破，截图/贴会话就等于把
   群机器人的 webhook 交出去了，而那是改不掉的——只能去群里重建机器人）；
② **假功能**：面板能勾、发出来是空的（场景没有执行体、勾了每周没给周几、渠道指向
   不存在的变量、计划指着被删掉的渠道）；
③ **真发**：跑一次测试就给用户工作群发一条消息是不可接受的副作用（同 `_spawn_stage`
   那条红线），所以 `_post_json`（机器人）与 `_apprise`（邮件）在 pytest 里必须自己挡；
④ **取到了没翻译**：正文里出现 `['dgt', ...]`、`None、None`、`=None` 这类"数据是拿到的、
   话没说给人"的形状 —— 手机上一条看不懂的推送等于没有推送。
"""
from __future__ import annotations

import json
import os
import re
from datetime import datetime, timedelta
from pathlib import Path

import pytest

import notifyconfig as NC


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    """三个文件（配置/去重/历史）一律打到临时目录：这些用例会真写、真删。"""
    home = tmp_path / "datahome"
    (home / "notify").mkdir(parents=True)
    monkeypatch.setenv("ASTOCK_DATA_HOME", str(home))
    monkeypatch.setenv("VIBE_NOTIFY_CONFIG", str(home / "notify" / "config.json"))
    monkeypatch.setenv("VIBE_NOTIFY_SENT", str(home / "notify" / "sent.json"))
    monkeypatch.delenv("VIBE_NOTIFY_HISTORY", raising=False)
    return home


def _post_ok(url, payload):
    """机器人「真收到」的回体形状：HTTP 200 **+ 回体 code 0**。"""
    return 200, json.dumps({"code": 0, "msg": "ok"}).encode("utf-8")


def _post_rejected(url, payload):
    """对面回 **HTTP 200** 而回体写着没收到 —— apprise 的插件在这一格读成"已交付"。
    本程自己认回体，所以这一格必须判失败（10-16 实测：假 token 也回 200／85 字节）。"""
    return 200, json.dumps({"code": 9499, "msg": "invalid token"}).encode("utf-8")


# ---------------------------------------------------------------- ① 凭据
def test_读数里不许出现真凭据():
    cfg, why = NC.upsert_channel({"key": "feishu-1", "kind": "feishu", "label": "主群",
                                  "url": "feishu://SECRETTOKENVALUE1234567890"}, via="test")
    assert why is None
    for blob in (json.dumps(NC.state(), ensure_ascii=False),
                 json.dumps(NC.masked_channels(cfg), ensure_ascii=False)):
        assert "SECRETTOKENVALUE" not in blob, "掩码没生效，整份读数里能读到真 token"
    # 掩码只留最后 4 位：够认出是哪条群，不够拿去用
    assert "7890" in json.dumps(NC.masked_channels(cfg), ensure_ascii=False)
    assert "SECRETTOKENVALUE1234" not in json.dumps(NC.masked_channels(cfg), ensure_ascii=False)
    assert NC.real_url(cfg["channels"][0]).startswith("feishu://")   # 真串只有本进程拿得到


def test_编辑时留空不许把凭据抹掉():
    """面板只回显掩码：用户改了标签提交时 `url` 是空的 ⇒ 空串=不改，不是"删掉密钥"。"""
    NC.upsert_channel({"key": "m", "kind": "dingtalk", "url": "dingtalk://TOKENAAA111BBB222"}, via="t")
    cfg, why = NC.upsert_channel({"key": "m", "kind": "dingtalk", "label": "改个名", "url": ""}, via="t")
    assert why is None
    row = NC.masked_channels(cfg)[0]
    assert row["label"] == "改个名" and row["has_secret"] is True


def test_url_env指向不存在的变量时必须当场说():
    """这一格专治"页面绿、发送空"：配置写的是变量名，而 .env 里没有那个变量。"""
    NC.upsert_channel({"key": "e", "kind": "feishu", "url_env": "NOTIFY_DEFINITELY_MISSING"}, via="t")
    row = NC.masked_channels()[0]
    assert row["has_secret"] is False and "NOTIFY_DEFINITELY_MISSING" in row["missing_reason"]
    usable, problems = __import__("duanxian.notify", fromlist=["x"]).resolve_channels(["e"])
    assert not usable and problems and "变量" in problems[0]


# ---------------------------------------------------------------- ② 假功能
def test_只支持四种渠道且拒绝话说得清():
    assert NC.validate_kind("sms") and "执行体" in NC.validate_kind("sms")
    assert NC.validate_kind("snmp") and NC.validate_kind("feishu") is None
    for kind in NC.KINDS:
        assert NC.validate_kind(kind) is None


def test_渠道URL形状逐类校验():
    assert NC.validate_channel({"key": "x", "kind": "feishu", "url": "https://x"})
    assert NC.validate_channel({"key": "x", "kind": "mail", "url": "mailto://u:p@smtp.x.com"})  # 缺收件人
    assert NC.validate_channel({"key": "x", "kind": "wecom", "url": "wecombot://KEY"}) is None
    assert NC.validate_channel({"key": "x", "kind": "dingtalk", "url": "dingtalk://S@T/"}) is None


def test_选了每周没勾周几直接拒():
    """宿主侧 `freq_allows` 是"判不了就放行"⇒ 空周几等于天天发。写入面必须比宿主严。"""
    bad = {"id": "p", "name": "n", "run_at": "16:00", "run_freq": "weekly", "weekly_days": "",
           "channels": ["c"], "scenes": {"market": {}}}
    assert "每周" in NC.validate_plan(bad)
    good = dict(bad, weekly_days="0,2,4")
    assert NC.validate_plan(good) is None
    assert NC.validate_plan(dict(bad, run_at="25:00")).startswith("p: 时刻")


@pytest.mark.parametrize("raw,hour,minute", [("9:05", 9, 5), ("16:20", 16, 20), ("00:07", 0, 7)])
def test_时刻归一之后才对得上(raw, hour, minute):
    """不归一就会长出"面板显示 9:05、宿主按 09:05 判"这种偏差；
    而参数化用例里所有分支都用同一个时刻去判，也是同一种错 —— 时间要跟着参数走。"""
    norm = NC._norm_time(raw)
    assert norm == f"{hour:02d}:{minute:02d}"
    plan = {"id": "p", "enabled": True, "run_at": raw, "run_freq": "daily"}
    on_the_minute = datetime(2026, 10, 9, hour, minute)
    assert NC.due_minute(plan, on_the_minute) is True
    # ⚠️ 10-15 口径变更：下一分钟**不再是 False**。原先只认正好那一分钟，而宿主是
    # "处理完再睡 60 秒"——一条慢发送就能把那一档整片错过且什么都不留。
    # 现在认 `LATE_WINDOW_MIN` 分钟内的迟到；"不许变成自动重试"由去重挡住
    # （见 `test_窗口只补错过那一分钟不许变成自动重试`）。
    assert NC.due_minute(plan, on_the_minute + timedelta(minutes=1)) is True
    assert NC.due_minute(plan, on_the_minute + timedelta(minutes=NC.LATE_WINDOW_MIN)) is True
    assert NC.due_minute(plan, on_the_minute + timedelta(minutes=NC.LATE_WINDOW_MIN + 1)) is False
    assert NC.due_minute(plan, on_the_minute - timedelta(minutes=1)) is False
    assert NC.due_minute(dict(plan, enabled=False), on_the_minute) is False


def test_场景清单与执行体必须同集合():
    """面板能勾的每一档都要有真执行体 —— 否则就是"勾了个空正文"。"""
    from duanxian import notify
    assert set(NC.SCENE_KEYS) == set(notify.SCENE_BUILDERS) == set(notify.SCENE_LABELS)


def test_选了内容块不给参数就拒():
    base = {"id": "p", "name": "n", "run_at": "16:00", "channels": ["c"]}
    assert "一条规则都没选" in NC.validate_plan(dict(base, scenes={"screen": {"rules": []}}))
    # ⚠️ 10-16 口径变更（这条原本断的是 `"一只票都没填" in …`）：
    # `dossier.codes` **留空是有含义的**（= 按自选前几只，`_blk_dossier` 就是这么实现的），
    # 原先当必填拒掉，后端那条兜底永远走不到。现在留空合法，**类型写错才拒**。
    assert NC.validate_plan(dict(base, scenes={"dossier": {"codes": []}})) is None
    assert NC.validate_plan(dict(base, scenes={"dossier": {}})) is None
    assert "codes" in NC.validate_plan(dict(base, scenes={"dossier": {"codes": "600519"}}))
    assert "min_hits" in NC.validate_plan(dict(base, scenes={"confluence": {"min_hits": 9}}))
    assert NC.validate_plan(dict(base, scenes={"confluence": {"min_hits": 2},
                                               "watchlist": {"group_id": ""}})) is None


def test_删渠道时有计划指着它必须告警():
    NC.upsert_channel({"key": "c1", "kind": "feishu", "url": "feishu://TOKEN1234"}, via="t")
    NC.upsert_plan({"id": "p1", "name": "日报", "run_at": "16:00", "channels": ["c1"],
                    "scenes": {"market": {}}}, via="t")
    _cfg, why = NC.remove_channel("c1", via="t")
    assert why and "p1" in why and "无处可发" in why
    row = NC.plans_view()[0]
    assert row["sendable"] is False and row["channels"][0]["exists"] is False


def test_计划的可用状态是现算的而不是存进去的():
    """存 `sendable` 会过期；每次读时按当前渠道表算。"""
    NC.upsert_channel({"key": "c2", "kind": "wecom", "url": "wecombot://KEYKEY"}, via="t")
    NC.upsert_plan({"id": "p2", "name": "x", "run_at": "16:00", "channels": ["c2"],
                    "scenes": {"market": {}}}, via="t")
    assert NC.plans_view()[0]["sendable"] is True
    NC.upsert_channel({"key": "c2", "kind": "wecom", "url": "wecombot://KEYKEY", "enabled": False}, via="t")
    assert NC.plans_view()[0]["sendable"] is False       # 刚停用，立刻在计划上现形


# ---------------------------------------------------------------- ③ 不许真发
def test_pytest进程里硬挡真实发送(monkeypatch):
    from duanxian import notify
    monkeypatch.setenv("PYTEST_CURRENT_TEST", "tests/test_notify_config.py::t")
    with pytest.raises(RuntimeError, match="不许真发"):
        notify._post_json("https://open.feishu.cn/x", {})


def test_洞抛错时发送如实失败而不是把异常抛到界面(monkeypatch):
    from duanxian import notify
    def boom(url, payload):
        raise RuntimeError("pytest 进程里不许真发")
    monkeypatch.setattr(notify, "_post_json", boom)
    NC.upsert_channel({"key": "c", "kind": "feishu", "url": "feishu://TOKENX"}, via="t")
    out = notify.send_to(["c"], "t", "b")
    assert out["ok"] is False and "pytest" in out["detail"] and out["sent_to"] == []


def test_发送成功与失败都留一行历史且不含凭据(monkeypatch, isolated):
    """历史文件是"到底发出去没有"的唯一凭据 —— 但它一次都不能带出 URL。"""
    from duanxian import notify
    monkeypatch.setattr(notify, "_post_json", _post_ok)
    NC.upsert_channel({"key": "c", "kind": "feishu", "url": "feishu://SECRETNOTHIST"}, via="t")
    notify.run_plan({"id": "p9", "name": "日报", "run_at": "16:00", "channels": ["c"],
                     "scenes": {"positions": {}}}, now=datetime(2026, 10, 9, 16, 0))
    rows = notify.history()
    assert len(rows) == 1 and rows[0]["status"] == "ok" and rows[0]["sent_to"] == ["c"]
    assert "SECRETNOTHIST" not in json.dumps(rows, ensure_ascii=False), "历史里漏了 URL"


# ---------------------------------------------------------------- ④ 取到了要说人话
def test_正文里不许出现原始列表与None占位():
    """真库跑一遍（不打桩）：这三处形状都真错过一次，靠读代码发现不了。"""
    from duanxian import notify
    _t, body, meta = notify.compose({"confluence": {"min_hits": 2, "limit": 3},
                                     "screen": {"rules": [{"rule_key": "align_bear",
                                                            "params": {"min_layers": 3}}],
                                                "universe": "market", "limit": 3},
                                     "dossier": {"codes": ["600519"], "limit": 1}}, "")
    if meta["scenes"].get("confluence") != "ok":
        pytest.skip("库里没有共振榜数据，这一档本轮不判")
    assert "['" not in body and "hit_sources" not in body      # 列表原文
    assert "None、" not in body and "→ None" not in body        # 中文列名没映射上
    assert "=None" not in body                                  # 分项字段名错就会全 None


def test_一块取数坏了只让那一行失败整条照发(monkeypatch):
    """与 `stock_dossier` 同一取舍：一块坏不能带崩整条推送。"""
    from duanxian import notify

    def boom(params, day):
        raise RuntimeError("源挂了")
    monkeypatch.setitem(notify.SCENE_BUILDERS, "market", boom)
    _t, body, meta = notify.compose({"market": {}, "positions": {}}, "")
    assert meta["scenes"]["market"].startswith("error:")
    assert "源挂了" in body and "持仓" in body                  # 另一档照发


def test_计划到点执行会写state记账与去重(monkeypatch, isolated):
    from duanxian import notify
    from duanxian import jobs
    monkeypatch.setattr(notify, "_post_json", _post_ok)
    NC.upsert_channel({"key": "c", "kind": "feishu", "url": "feishu://TOKENZ"}, via="t")
    plan = {"id": "p1", "name": "日报", "run_at": "16:00", "enabled": True,
            "channels": ["c"], "scenes": {"positions": {}}}
    out = notify.run_plan(plan, now=datetime(2026, 10, 9, 16, 0))
    assert out["status"] == "ok", out
    st = jobs.state().get("notify_push") or {}
    assert st.get("last_status") == "ok" and st.get("last_detail", {}).get("plan") == "p1"
    assert notify.already_sent("p1", out["date"]) is True
    again = notify.run_plan(plan, now=datetime(2026, 10, 9, 16, 0))
    assert again["status"] == "skip" and "已经发过" in again["reason"]
    forced = notify.run_plan(plan, now=datetime(2026, 10, 9, 16, 0), force=True)
    assert forced["status"] == "ok"                             # 手动重发绕过去重，不绕渠道校验


def test_预览不发送但把会挡的原因说清(monkeypatch):
    from duanxian import notify
    called = []
    monkeypatch.setattr(notify, "_post_json",
                        lambda url, payload: called.append(url) or _post_ok(url, payload))
    NC.upsert_channel({"key": "c", "kind": "dingtalk", "url": "dingtalk://S@T"}, via="t")
    plan = {"id": "p", "name": "x", "run_at": "16:00", "channels": ["c", "missing-one"],
            "scenes": {"positions": {}}}
    out = notify.preview(plan)
    assert not called, "预览把消息真发出去了"
    assert out["would_send_to"] == ["c"] and out["blocked"] and "missing-one" in out["blocked"][0]
    assert out["body"] and out["title"].startswith("x·")


# ---------------------------------------------------------------- 端点那一层
@pytest.fixture()
def client(isolated):
    """走 `server.app`。⚠️ 不要 `sys.path.insert(0,"vr") + import app`：
    模块名 `app` 会被 `vr/app.py` 抢走，顶层 `app/` 包从此 import 不了（10-06 真踩过，
    同批红了别人 22 条测试）。"""
    from fastapi.testclient import TestClient
    import server
    return TestClient(server.app, base_url="http://127.0.0.1:8910", client=("127.0.0.1", 1))


def test_GET_notify_交掩码与目录但不交凭据(client, monkeypatch):
    from duanxian import notify
    monkeypatch.setattr(notify, "_post_json", _post_ok)
    r = client.post("/api/notify", json={"channel": {"key": "f1", "kind": "feishu",
                                                     "label": "主群",
                                                     "url": "feishu://TOKENABCDEFGH1234"}})
    assert r.status_code == 200, r.text
    got = client.get("/api/notify")
    assert got.status_code == 200
    blob = got.text
    assert "TOKENABCDEFGH" not in blob, "端点把真 token 交出去了"
    data = got.json()["data"]
    assert data["can_edit"] is True and data["counts"]["channels"] == 1
    assert data["channels"][0]["url_masked"].startswith("******")
    assert {s["value"] for s in data["catalog"]["scenes"]} == set(NC.SCENE_KEYS)
    assert data["last_run"]["last_run_at"] is None            # 还没发过就是 null，不编时刻


def test_POST_一次只改一件坏值拒收并给原话(client):
    assert client.post("/api/notify", json={"enabled": True, "channel": {"key": "x"}}).status_code == 400
    bad = client.post("/api/notify", json={"channel": {"key": "sms1", "kind": "sms"}})
    assert bad.status_code == 400 and "执行体" in bad.json()["detail"]
    plan = client.post("/api/notify", json={"plan": {"id": "p", "name": "n", "run_at": "16:00",
                                                     "run_freq": "weekly", "weekly_days": "",
                                                     "channels": ["nope"], "scenes": {"market": {}}}})
    assert plan.status_code == 400 and "周几" in plan.json()["detail"]


def test_测试发送走的是同一个洞且失败如实回502(client, monkeypatch):
    from duanxian import notify
    calls = []
    monkeypatch.setattr(notify, "_post_json",
                        lambda url, payload: calls.append((url, payload)) or _post_ok(url, payload))
    client.post("/api/notify", json={"channel": {"key": "d1", "kind": "dingtalk",
                                                 "url": "dingtalk://S@T"}})
    ok = client.post("/api/notify", json={"test_channel": "d1"})
    assert ok.status_code == 200 and calls and "测试" in json.dumps(calls[0][1], ensure_ascii=False)
    monkeypatch.setattr(notify, "_post_json", _post_rejected)
    bad = client.post("/api/notify", json={"test_channel": "d1"})
    assert bad.status_code == 502 and "invalid token" in bad.json()["detail"]
    missing = client.post("/api/notify", json={"test_channel": "no-such"})
    assert missing.status_code == 502 and "没有可用渠道" in missing.json()["detail"]


def test_预览不发送(client, monkeypatch):
    from duanxian import notify
    def boom(url, payload):
        raise AssertionError("预览不许真发")
    monkeypatch.setattr(notify, "_post_json", boom)
    r = client.post("/api/notify/preview", json={"plan": {"id": "p", "name": "日报",
                                                          "run_at": "16:00", "channels": ["ghost"],
                                                          "scenes": {"positions": {}}}})
    assert r.status_code == 200
    data = r.json()["data"]
    assert data["body"] and data["would_send_to"] == [] and data["blocked"]


# ---------------------------------------------------------------- 迟到窗口与「今天这一档」（10-15 补）
# 钉的是原先那个静默洞：宿主"处理完再睡 60 秒"，只认正好那一分钟时，
# 一条慢发送或一次重启就能把那一档整片错过，而"没执行"什么都不留下。

def _plan(**kw):
    p = {"id": "p1", "name": "日报", "run_at": "09:05", "enabled": True,
         "channels": ["c"], "scenes": {"positions": {}}}
    p.update(kw)
    return p


@pytest.mark.parametrize("hhmm,expect", [
    ("09:04", False),   # 还没到点
    ("09:05", True),    # 正好那一分钟
    ("09:20", True),    # 迟到 15 分钟：仍在窗口内
    ("09:21", False),   # 迟到 16 分钟：过窗，不再补（要补由人点「立刻发一次」）
])
def test_到点后认十五分钟内的迟到(hhmm, expect):
    hour, minute = (int(x) for x in hhmm.split(":"))
    assert NC.due_minute(_plan(), datetime(2026, 10, 16, hour, minute)) is expect


def test_窗口只补错过那一分钟不许变成自动重试(monkeypatch, isolated):
    """发失败也记当天一笔 ⇒ 下一分钟虽然在窗口内，也不该再发第二次。
    这一条钉的是"窗口"与"重试"的边界：重发的口子交给面板，不交给循环自己猜。"""
    from duanxian import notify
    monkeypatch.setattr(notify, "_post_json", _post_rejected)
    NC.upsert_channel({"key": "c", "kind": "feishu", "url": "feishu://TOKENZ"}, via="t")
    plan = _plan()
    out = notify.run_plan(plan, now=datetime(2026, 10, 16, 9, 5))
    assert out["status"] == "fail" and notify.already_sent("p1", out["date"]) is True
    assert notify.due_plans(datetime(2026, 10, 16, 9, 12)) == []   # 窗口内但已发过（失败也算发过）


def test_今天这一档的八种读数都给得出一句人话():
    got = {}
    for label, plan, when, kw in [
        ("waiting", _plan(), datetime(2026, 10, 16, 9, 0), {}),
        ("in_window", _plan(), datetime(2026, 10, 16, 9, 10), {}),
        ("sent", _plan(), datetime(2026, 10, 16, 9, 30), {"sent": True}),
        ("missed", _plan(), datetime(2026, 10, 16, 9, 30), {}),
        ("off", _plan(enabled=False), datetime(2026, 10, 16, 9, 30), {}),
        # 没配时刻**不是一档读数**：`validate_plan` 在写入面就把它拒了，走到这里只会是 `bad`
        # —— 所以这里刻意不给 `no_time` 分支留位置（为一个到不了的形状写代码就是死码）。
        ("bad", _plan(run_at=""), datetime(2026, 10, 16, 9, 30), {}),
        ("not_today", _plan(run_freq="weekly", weekly_days="0,2"),
         datetime(2026, 10, 22, 9, 30), {}),                       # 10-22 是周四
        ("not_trade_day", _plan(), datetime(2026, 10, 16, 9, 30), {"trade_day": False}),
    ]:
        st = NC.slot_state(plan, when, **kw)
        assert st["state"] == label, (label, st)
        assert st["label"] and st["label"] not in ("None", ""), label
        got[label] = st["label"]
    assert "立刻发一次" in got["missed"]                      # 错过了要给出路，不只是报坏
    assert "下一轮心跳" in got["in_window"]
    assert "时刻" in got["bad"]                                   # 那句得指到能改的地方


def test_交易日判不了不许显示成今天不跑():
    """与发送侧同一取舍：判不了照发 ⇒ 读数也不许降级成"休市"（那是拿猜测冒充读数）。"""
    assert NC.slot_state(_plan(), datetime(2026, 10, 16, 9, 30), trade_day=None)["state"] == "missed"


def test_迟到窗口的分钟数由后端交出而不是前端抄一份():
    st = NC.state()
    assert st.get("late_window_min") == NC.LATE_WINDOW_MIN
    assert str(NC.LATE_WINDOW_MIN) in st["explain"]           # 那句话里也真的说了这个数


def test_面板每条计划带今天这一档且措辞原样来自后端(client, monkeypatch):
    from duanxian import notify, util
    monkeypatch.setattr(notify, "_post_json", _post_ok)
    monkeypatch.setattr(util, "china_now", lambda: datetime(2026, 10, 16, 9, 30))
    monkeypatch.setattr(notify, "trade_day_or_none", lambda day: True)
    NC.upsert_channel({"key": "c", "kind": "feishu", "url": "feishu://TOKENZ"}, via="t")
    NC.upsert_plan(_plan(), via="t")
    body = client.get("/api/notify").json()["data"]
    plan = next(p for p in body["plans"] if p["id"] == "p1")
    assert plan["today_state"] in ("missed", "sent", "waiting", "in_window")
    assert plan["today_label"] and "—" not in plan["today_label"][:1]


def test_真发送路径要用假aprise模块跑一遍本体(monkeypatch):
    """10-16 现场：页面点「发一条测试」回的是 `发送异常（NameError: name 'aprise' is not defined）`
    —— 代码里把 `apprise` 打成 `aprise`。这条洞之所以活到今天，是因为**所有用例都把整个发送函数
    打桩掉了**，那几行真代码一次都没被执行过（"桩把契约打错了，两边一起错"的老形状）。
    所以这里**不打投递函数**，只把 `_apprise()` 那个"取模块的口子"换成一个形状正确的假货，
    让邮件那一路的本体真跑到底。
    """
    import types
    from duanxian import notify as notify_mod
    fake = types.ModuleType("aprise")
    seen = {}

    class _Ap:
        def add(self, url):
            seen.setdefault("added", []).append(url)
            return url.startswith("mailto://")

        def notify(self, title, body, notify_type=None):
            seen["notify"] = (title, body, getattr(notify_type, "value", str(notify_type)))
            return True

    class _Type:
        INFO = "info"

    fake.Apprise = _Ap
    fake.NotifyType = _Type
    monkeypatch.setattr(notify_mod, "_apprise", lambda: fake)   # 换"取模块的口子"，本体照跑
    res = notify_mod.deliver([{"key": "m", "kind": "mail", "url": "mailto://u:p@smtp.x/a@b.com"}],
                             "标题", "正文")
    assert res["ok"] is True, res["detail"]
    assert "NameError" not in res["detail"] and "发送异常" not in res["detail"], res["detail"]
    assert seen["added"] == ["mailto://u:p@smtp.x/a@b.com"]
    assert seen["notify"][0] == "标题" and seen["notify"][1] == "正文"
    assert seen["notify"][2] == "info"                           # notify_type 真的传到了


def test_apprise没装上时话说得清而不是抛到界面(monkeypatch):
    """前两种拦法**都没拦住**：`builtins.__import__` 与 `monkeypatch.setitem(sys.modules, ...)`
    在这份文件里都不生效（后者写完当场再读就是 False），于是测试进程真去调了 apprise 的飞书插件 ——
    只因为 `tests/conftest.py` 拦了出站 socket 才没打出去。**靠别的夹具兜住 ≠ 我拦住了**。
    现在换成直接把 `_apprise()` 这个口子换成会抛 ImportError 的实现：确定性。"""
    from duanxian import notify as notify_mod

    def boom():
        raise ImportError("No module named 'apprise'")

    monkeypatch.setattr(notify_mod, "_apprise", boom)
    res = notify_mod.deliver([{"key": "m", "kind": "mail", "url": "mailto://u:p@smtp.x/a@b.com"}],
                             "t", "b")
    assert res["ok"] is False and "scripts/setup" in res["detail"], res["detail"]


def test_没换口子的用例仍然被硬挡(monkeypatch):
    """硬挡现在的判据是"这个口子还是不是默认那个"，不是"现在是不是 pytest"。
    换了口子的用例天然绕过（递进来的是假货，根本不会出门）；**不换口子的必须照样挡** ——
    否则这道挡等于被新形状悄悄拆掉了。两个出口都钉：机器人 `_post_json`、邮件 `_apprise`。
    ⚠️ 注意 `deliver` 会把单条渠道的异常**收成一个失败**（一条坏了不许带走其余渠道），
    所以这里问的是两件事：直接打出口 ⇒ 炸；走 `deliver` ⇒ 不炸但判失败且原话留痕。"""
    from duanxian import notify as notify_mod
    assert notify_mod._post_json is notify_mod._POST_DEFAULT
    assert notify_mod._apprise is notify_mod._APPISE_DEFAULT
    with pytest.raises(RuntimeError, match="不许真发"):
        notify_mod._post_json("https://open.feishu.cn/x", {})
    for kind, url in (("feishu", "feishu://T"), ("mail", "mailto://u:p@h/a@b")):
        res = notify_mod.deliver([{"key": "x", "kind": kind, "url": url}], "t", "b")
        assert res["ok"] is False and "不许真发" in res["detail"], (kind, res)


# ---------------------------------------------------------------- ⑤ 200 不等于送达（10-16 改造的核心）
def test_对面回200而回体说没收到必须判失败(monkeypatch, isolated):
    """这条就是本轮改造的理由：apprise 2.0.1 的飞书插件 `send()` 只看 `status_code != 200`，
    而飞书/钉钉/企微对**无效 token 也回 HTTP 200**，错误写在 JSON 回体的 `code`/`errcode` 里。
    实测（假 token）：`POST https://open.feishu.cn/open-apis/bot/v2/hook/<假>/ → 200`、
    apprise 打 `INFO Sent Feishu notification.` ⇒ 页面显示「1 个渠道已交付」——
    一条永远不会到账的"成功"。"""
    from duanxian import notify
    monkeypatch.setattr(notify, "_post_json", _post_rejected)
    NC.upsert_channel({"key": "f", "kind": "feishu", "url": "feishu://FAKE"}, via="t")
    out = notify.send_to(["f"], "标题", "正文")
    assert out["ok"] is False and out["sent_to"] == [] and out["failed"] == ["f"]
    assert "invalid token" in out["detail"] and "9499" in out["detail"]


def test_三种机器人各自打到各自的官方地址(monkeypatch, isolated):
    from duanxian import notify
    seen = []
    monkeypatch.setattr(notify, "_post_json",
                        lambda url, payload: seen.append((url, payload)) or _post_ok(url, payload))
    for key, kind, url in (("f1", "feishu", "feishu://TOKEN1"),
                           ("w1", "wecom", "wecombot://KEY2"),
                           ("d1", "dingtalk", "dingtalk://TOKEN3")):
        NC.upsert_channel({"key": key, "kind": kind, "url": url}, via="t")
    out = notify.send_to(["f1", "w1", "d1"], "标题", "正文")
    assert out["ok"] is True and sorted(out["sent_to"]) == ["d1", "f1", "w1"], out
    hosts = [u.split("?")[0] for u, _p in seen]
    # 飞书按官方文档的形状（路径末段就是 token，**不带**apprise 那个尾斜杠）；
    # 真发那一格会验它收不收（`test_真发时密钥要一路带到投递器` 同一形状走真地址）。
    assert "https://open.feishu.cn/open-apis/bot/v2/hook/TOKEN1" in hosts
    assert "https://qyapi.weixin.qq.com/cgi-bin/webhook/send" in hosts
    assert "https://oapi.dingtalk.com/robot/send" in hosts
    by_kind = {u: p for u, p in seen}
    full = [u for u, _p in seen]                      # 带查询串的完整地址（`hosts` 是把 ? 之后切掉的）
    feishu_url = next(u for u in full if "feishu" in u)
    wecom_url = next(u for u in full if "weixin" in u)
    ding_url = next(u for u in full if "dingtalk" in u)
    # 各回各家：飞书的地址里只该有飞书的 token，串台就是把消息发进了别的群
    assert feishu_url.endswith("/TOKEN1") and "KEY2" not in feishu_url and "TOKEN3" not in feishu_url
    assert "key=KEY2" in wecom_url and "TOKEN1" not in wecom_url and "TOKEN3" not in wecom_url
    assert "access_token=TOKEN3" in ding_url and "TOKEN1" not in ding_url and "KEY2" not in ding_url
    bodies = [json.dumps(p, ensure_ascii=False) for _u, p in seen]
    assert all("标题" in b and "正文" in b for b in bodies)   # 标题与正文都上了屏
    assert not any(p.get("sign") for p in by_kind.values()), "没填密钥就不该出现签名字段"


def test_钉钉带secret时按官方口径加签(monkeypatch, isolated):
    """加签形状错了钉钉只会回 310000，而那是"200 + 回体错误"那一类 —— 上面那条已保证会被判失败。"""
    import base64
    import hashlib
    import hmac
    import urllib.parse
    from duanxian import notify
    seen = []
    monkeypatch.setattr(notify, "_post_json",
                        lambda url, payload: seen.append(url) or _post_ok(url, payload))
    NC.upsert_channel({"key": "d", "kind": "dingtalk",
                       "url": "dingtalk://TOKEN4?secret=SECRET4"}, via="t")
    assert notify.send_to(["d"], "t", "b")["ok"] is True
    q = urllib.parse.parse_qs(urllib.parse.urlsplit(seen[0]).query)
    stamp, sign = q["timestamp"][0], q["sign"][0]
    want = base64.b64encode(hmac.new(b"SECRET4", f"{stamp}\nSECRET4".encode(),
                                     hashlib.sha256).digest()).decode()
    assert sign == want and "access_token=TOKEN4" in seen[0]


def test_发送有超时不许把心跳挂住(monkeypatch):
    """原来一次调用没有超时（14 §十记过这条）：对面不回，60 秒心跳就被拖长，
    整分钟错过计划。现在 `_post_json` 自己带 `SEND_TIMEOUT_SEC`。"""
    import urllib.request
    from duanxian import notify
    got = {}

    class _Resp:
        status = 200

        def read(self):
            return b'{"code":0}'

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake_urlopen(req, timeout=None):
        got["timeout"] = timeout
        return _Resp()

    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    ok, note = notify._post_json("https://open.feishu.cn/x", {})
    assert ok == 200 and got["timeout"] == notify.SEND_TIMEOUT_SEC, got


def test_预览用计划行里的渠道引用时不许印原始dict(monkeypatch, isolated):
    """10-16 浏览器实测：面板上点「预览正文」传回来的是 `plan_view` 那一行的形状
    （`channels` 是带 `exists/usable` 的**对象**，不是键的字符串）。
    按字符串处理会得到两件事一起坏：屏幕上出现 `{'key': 'panel-check', ...}`，
    而一条**明明可用**的渠道被说成「配置里没有这条渠道」。"""
    from duanxian import notify
    NC.upsert_channel({"key": "pc", "kind": "feishu", "url": "feishu://TOKENPC"}, via="t")
    NC.upsert_plan({"id": "pv", "name": "预览", "run_at": "09:05", "channels": ["pc"],
                    "scenes": {"market": {}}}, via="t")
    row = NC.plans_view()[0]                       # 面板读到的那一行（channels 是对象）
    assert isinstance(row["channels"][0], dict)
    out = notify.preview(dict(row, scenes={"market": {}}))
    blob = str(out["blocked"]) + str(out["would_send_to"])
    assert "'key'" not in blob and "panel-check" not in blob, blob      # 没有原始 dict 上屏
    assert out["would_send_to"] == ["pc"], out["blocked"]
    assert out["blocked"] == [], out["blocked"]
    monkeypatch.setattr(notify, "_post_json", _post_ok)
    sent = notify.send_to(row["channels"], "标题", "正文")              # 同一形状也发得出去
    assert sent["ok"] is True and sent["sent_to"] == ["pc"], sent


def test_个股档案留空是按自选而不是被校验拒掉():
    """10-16 抓到的一对自相矛盾：`_blk_dossier` 写着"没点票就发我在盯的那几只"，
    而 `validate_scenes` 把空 `codes` 当非法拒收 ⇒ 后端那条兜底**永远走不到**。
    现在两头的说法必须一致：留空合法、且真按自选出内容。"""
    assert NC.validate_scenes({"dossier": {}}) is None
    assert NC.validate_scenes({"dossier": {"codes": []}}) is None
    assert NC.validate_scenes({"dossier": {"codes": "600519"}})          # 字符串不是列表：仍拒
    from duanxian import notify
    body = notify._blk_dossier({}, "2026-10-15")
    assert "既没指定个股、自选里也没有票可读" in body or "个股档案（2026-10-15" in body, body[:80]


def test_策略可以多选也可以整体不选但不许选了却一个不勾():
    assert NC.validate_scenes({"confluence": {"min_hits": 2}}) is None
    assert NC.validate_scenes({"confluence": {"min_hits": 3, "sources": ["dgt", "rank"]}}) is None
    assert NC.validate_scenes({"confluence": {"min_hits": 3, "sources": "dgt,rank"}}) is None
    bad = NC.validate_scenes({"confluence": {"min_hits": 2, "sources": []}})
    assert bad and "一个策略都没选" in bad
    assert NC.validate_scenes({"confluence": {"min_hits": 2, "sources": 5}})


def test_按策略筛时正文标题要说清筛了哪几个():
    """用户勾了「四维象限」，行里却还会列出「龙虎榜」——因为筛的是"有没有被勾中的策略选中"，
    不是"只由它命中"。这话必须在标题里说清，否则那一行看着就像筛选没生效（10-16 我自己就误读了一次）。"""
    from duanxian import notify
    head = notify._blk_confluence({"min_hits": 1, "sources": ["quadrant"]}, "2026-10-15").split("\n")[0]
    assert "四维象限" in head and "且必须有" in head, head
    all_head = notify._blk_confluence({"min_hits": 2}, "2026-10-15").split("\n")[0]
    assert "且必须有" not in all_head, all_head


# ---------------------------------------------------------------- 粘贴官方 webhook + 加签密钥
def test_群机器人那三条完整地址直接粘贴就该收下来():
    """用户 10-16 的现场：飞书后台给的是 `https://open.feishu.cn/open-apis/bot/v2/hook/<uuid>`
    加一串密钥，而我们的框只认 `feishu://` 前缀 ⇒ "加不进去"。两种形状现在都收。"""
    feishu = {"key": "f", "kind": "feishu",
              "url": "https://open.feishu.cn/open-apis/bot/v2/hook/62fff3d4-d48b-4d95-8664-cd5709666a9d"}
    ding = {"key": "d", "kind": "dingtalk",
            "url": "https://oapi.dingtalk.com/robot/send?access_token=abc123"}
    wecom = {"key": "w", "kind": "wecom",
             "url": "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=deadbeef"}
    for ch in (feishu, ding, wecom):
        assert NC.validate_channel(ch) is None, (ch["kind"], NC.validate_channel(ch))
    # 内部简写仍然收
    assert NC.validate_channel({"key": "f2", "kind": "feishu", "url": "feishu://TOKENOK"}) is None
    # 但不是任何 https 都收：主机不对就是把地址写错了
    evil = NC.validate_channel({"key": "e", "kind": "feishu", "url": "https://evil.example/hook/x"})
    assert evil and "open.feishu.cn" in evil, evil
    # 官方前缀下缺 token 也要指名道姓
    assert "没找到 webhook 密钥段" in NC.validate_channel(
        {"key": "d2", "kind": "dingtalk", "url": "https://oapi.dingtalk.com/robot/send"})


def test_加签密钥只写不读且编辑留空不覆盖(isolated):
    NC.upsert_channel({"key": "f", "kind": "feishu",
                       "url": "https://open.feishu.cn/open-apis/bot/v2/hook/TOKENAAA",
                       "secret": "SECRETLABELVALUE12345"}, via="t")
    view = NC.masked_channels()[0]
    blob = json.dumps(view, ensure_ascii=False) + json.dumps(NC.state(), ensure_ascii=False)
    assert "SECRETLABELVALUE" not in blob, "读数里漏了加签密钥"
    assert view["has_sign"] is True and view["kind_supports_sign"] is True
    assert view["sign_source"] == "配置文件内联"
    # 改了中文名、密钥那格留空 ⇒ 密钥必须还在
    NC.upsert_channel({"key": "f", "kind": "feishu", "label": "盯盘群"}, via="t")
    again = NC.masked_channels()[0]
    assert again["has_sign"] is True and again["label"] == "盯盘群"
    # 没开加签的渠道不该被催：企微只有 key
    NC.upsert_channel({"key": "w", "kind": "wecom", "url": "wecombot://KEYKEY"}, via="t")
    w = NC.masked_channels()[1]
    assert w["kind_supports_sign"] is False and w["sign_warning"] is None
    # 飞书没填密钥 ⇒ 事前就提醒，不等对面拒绝
    NC.upsert_channel({"key": "f2", "kind": "feishu", "url": "feishu://TOKENBBB"}, via="t")
    f2 = NC.masked_channels()[2]
    assert f2["has_sign"] is False and f2["sign_warning"] and "签名校验" in f2["sign_warning"]


def test_飞书签名按官方gen_sign算且写进请求体(monkeypatch):
    """⚠️ 两家取法**相反**：飞书是 `hmac.new(key="ts\nsecret")`（msg 空、时间戳**秒**、进 body），
    钉钉是 `hmac.new(key=secret, msg="ts\nsecret")`（时间戳**毫秒**、进 URL）。写错就是永远被拒。"""
    import base64
    import hashlib
    import hmac
    from duanxian import notify
    monkeypatch.setattr(notify.time, "time", lambda: 1700000000.6)
    url = "https://open.feishu.cn/open-apis/bot/v2/hook/TOKEN-XYZ"
    target, payload, why = notify._bot_target("feishu", url, "标题", "正文", "sekret")
    assert why is None and target.endswith("/hook/TOKEN-XYZ"), (target, why)
    want = base64.b64encode(hmac.new("1700000000\nsekret".encode(), b"", hashlib.sha256).digest()).decode()
    assert payload["timestamp"] == "1700000000" and payload["sign"] == want, payload
    assert payload["content"]["text"] == "标题\n正文"


def test_钉钉签名进URL且时间戳是毫秒():
    import base64
    import hashlib
    import hmac
    from urllib.parse import parse_qs, urlsplit
    from duanxian import notify
    target, payload, why = notify._bot_target(
        "dingtalk", "https://oapi.dingtalk.com/robot/send?access_token=abc", "t", "b", "sekret")
    assert why is None, why
    q = parse_qs(urlsplit(target).query)
    stamp = q["timestamp"][0]
    assert stamp.isdigit() and len(stamp) == 13, stamp          # 毫秒
    want = base64.b64encode(hmac.new(b"sekret", f"{stamp}\nsekret".encode(),
                                     hashlib.sha256).digest()).decode()
    assert q["sign"][0] == want
    import urllib.parse
    assert urllib.parse.unquote(q["access_token"][0]) == "abc"


def test_企微没有加签这一说密钥填了要拒():
    from duanxian import notify
    _t, _p, why = notify._bot_target("wecom", "wecombot://KEY", "t", "b", "sekret")
    assert why and "没有「加签」" in why, why


def test_真发时密钥要一路带到投递器(monkeypatch, isolated):
    """`real_secret` 算出来了但没人传，就等于那一格白填 —— 所以这条从 `send_to` 打进去验。"""
    from duanxian import notify
    seen = {}

    def spy(url, payload):
        seen["url"], seen["payload"] = url, payload
        return _post_ok(url, payload)

    monkeypatch.setattr(notify, "_post_json", spy)
    NC.upsert_channel({"key": "f", "kind": "feishu",
                       "url": "https://open.feishu.cn/open-apis/bot/v2/hook/TOKENREAL",
                       "secret": "SEKRETFORDELIVERY"}, via="t")
    out = notify.send_to(["f"], "标题", "正文")
    assert out["ok"] is True, out
    assert "sign" in seen["payload"] and "timestamp" in seen["payload"], seen["payload"]
    assert "SEKRETFORDELIVERY" not in json.dumps(seen, ensure_ascii=False)   # 密钥不进日志/历史


# ---------------------------------------------------------------- 正文长度按渠道取上限（企微 2048 是硬拒）
def test_正文上限按渠道取最小值而不是统一赌一个数():
    """原先 `MAX_BODY_BYTES=3500` 是个错的抽象：**企业微信 text 消息硬上限 2048 字节**，
    超了直接 `errcode 45004 size too large`。一条计划同时发企微与飞书时必须按小的那个切，
    否则企微那一路天天失败（而失败我们是看得见的，没人愿意天天看红字）。"""
    from duanxian import notify
    assert notify.body_limit(["wecom", "feishu"]) == 1900
    assert notify.body_limit(["feishu"]) == 4000
    assert notify.body_limit([]) == notify.MAX_BODY_BYTES          # 没渠道时用兜底，绝不回 0
    assert notify.body_limit(["qq"]) == notify.MAX_BODY_BYTES      # 未知渠道也一样
    cat = notify.scene_catalog()
    assert cat["body_limits"]["wecom"] == 1900, cat["body_limits"]   # 面板要说得清各家的数


def test_截断声明那一句自己也占字节():
    """切到上限**再**拼"已截断"，结果还是超上限 —— 这类差一句的坑只能靠断言总长钉住。
    ⚠️ 而且上限管的是**整条消息**：机器人收到的是 `标题\\n正文`，只按正文切就会把标题那几十字节
    漏在头外面（10-17 实测：切完 1899，实发 1927，企微照样硬拒）。"""
    from duanxian import notify
    for cap in (400, 1900, 4000):
        title, body, meta = notify.compose({"market": {"limit": 10}}, "2026-10-16",
                                           title_prefix="压测", limit=cap)
        n = len(body.encode("utf-8"))
        wire = len(f"{title}\n{body}".encode("utf-8"))
        assert n <= cap and wire <= cap, (cap, n, wire)
        assert meta["scenes"]["wire_bytes"] == wire
        if meta["scenes"].get("truncated"):
            assert body.endswith("…（内容过长已截断，完整请看网站）"), body[-40:]
    assert notify.compose({"market": {"limit": 1}}, "2026-10-16",
                          limit=9000)[2]["scenes"].get("truncated") is None


def test_预览与实发必须切到同一个长度(monkeypatch, isolated):
    """`preview` 原先在 `compose` 之后才解渠道 ⇒ 预览用的是兜底长度，实发用的是渠道长度，
    用户在预览里看到的"完整一段"发出去是半截。顺序改成与 `run_plan` 一致。"""
    from duanxian import notify
    seen = {}

    def spy(url, payload):
        seen["text"] = payload["text"]["content"] if "text" in payload else payload["content"]["text"]
        return _post_ok(url, payload)

    monkeypatch.setattr(notify, "_post_json", spy)
    NC.upsert_channel({"key": "w", "kind": "wecom", "url": "wecombot://KEYW"}, via="t")
    plan = {"id": "px", "name": "长正文", "run_at": "16:00", "channels": ["w"],
            "scenes": {"market": {"limit": 10}, "watchlist": {}}}
    view = notify.preview(plan)
    out = notify.run_plan(plan, now=datetime(2026, 10, 16, 16, 0))
    assert out["status"] == "ok", out
    assert len(f"{view['title']}\n{view['body']}".encode("utf-8")) <= 1900, len(view["body"])
    assert out["body_limit"] == 1900 and out["truncated"] is True, out
    assert len(seen["text"].encode("utf-8")) <= 1900        # 真发出去的那段也没超
    row = notify.history(1)[0]
    assert row["body_limit"] == 1900 and row["truncated"] is True, row   # 手机上收到半截时有据可查


# ---------------------------------------------------------------- 内容按"每一个点"分别配置
def test_每一档的条数默认值与上限只有一份():
    """面板上那些数字框的默认值/上限都从 `catalog` 来，而 catalog 读的是后端这两张表 ——
    两边各抄一份就会长成人界面上调的不是实发长度。"""
    from duanxian import notify
    cat = notify.scene_catalog()
    by = {x["value"]: x for x in cat["scenes"]}
    assert set(by) == set(NC.SCENE_KEYS), set(by) ^ set(NC.SCENE_KEYS)
    for key in NC.SCENE_KEYS:
        assert by[key]["default_limit"] == notify.DEFAULT_LIMITS[key], key
        assert by[key]["max_limit"] == notify.MAX_LIMITS[key], key
        assert by[key]["default_limit"] <= by[key]["max_limit"], key
        # 单位也在后端定：天梯数的是"层"、分组数的是"组"、快讯数的是"条"、板块数的是"个"，
        # 其余数的是"只" —— 前端猜就会说出"市场概览 2 条"这种看着对其实错的假话
        assert by[key]["limit_unit"] == notify.SCENE_LIMIT_UNITS[key], key


def test_条数写坏了要拒且不许静默变成默认():
    assert NC.validate_scenes({"market": {"limit": 2}}) is None
    assert NC.validate_scenes({"market": {}}) is None            # 没填就是用默认，合法
    assert "整数" in NC.validate_scenes({"watchlist": {"limit": "很多"}})
    assert "1~500" in NC.validate_scenes({"watchlist": {"limit": 0}})
    assert "1~500" in NC.validate_scenes({"watchlist": {"limit": 600}})


def test_市场概览与持仓的条数真的能动():
    """以前 `market` 的天梯层数写死 4、持仓写死 20：后端读 `params["limit"]` 但没人接，
    面板上也就没有框 —— 用户要的"内容根据各个点来配置"正是这一格。"""
    from duanxian import notify
    wide = notify._blk_market({"limit": 9}, "2026-10-15")
    narrow = notify._blk_market({"limit": 1}, "2026-10-15")
    def tiers(text):
        line = next((l for l in text.split("\n") if "连板天梯" in l), "")
        return line.count("板 ") + line.count("板  ")          # 数层级个数（"五连板 2只"这种）
    assert "没取到" not in wide, wide[:80]
    assert tiers(narrow) <= tiers(wide) and tiers(narrow) <= 1 + 1, (tiers(narrow), tiers(wide))
    import re
    assert re.search(r"最高板：", wide)                        # 层级之外那行不受条数影响
    # 持仓这一档本机现在可能没有未平仓的票 —— 有条数就不得超，没有就如实说，两样都不许编
    pos = notify._blk_positions({"limit": 1}, "2026-10-15")
    rows = [l for l in pos.split("\n")[1:] if l.strip().startswith("  ") and "｜" in l]
    assert len(rows) <= 1 or not rows, pos[:200]
    assert "持仓" in pos and ("没有未平仓" in pos or rows), pos[:120]


def test_一条计划各档用不同条数并发出去(monkeypatch, isolated):
    """端到端：配置里的 `scenes.<档>.limit` 真被逐档执行体读到。"""
    from duanxian import notify
    seen = {}

    def spy(url, payload):
        seen["text"] = payload["text"]["content"]
        return _post_ok(url, payload)

    monkeypatch.setattr(notify, "_post_json", spy)
    NC.upsert_channel({"key": "w", "kind": "wecom", "url": "wecombot://KEYE2E"}, via="t")
    plan = {"id": "pl", "name": "各档条数", "run_at": "16:00", "channels": ["w"],
            "scenes": {"market": {"limit": 1}, "watchlist": {"limit": 3}}}
    out = notify.run_plan(plan, now=datetime(2026, 10, 16, 16, 0))
    assert out["status"] == "ok", out
    text = seen["text"]
    assert "自选股当日" in text and "市场概览" in text
    assert out["body_limit"] == 1900 and out["bytes"] <= 1900, out
    # 自选那一档只给 3 条 ⇒ 行数明显少于默认的 30 条
    assert len([l for l in text.split("\n") if l.strip().startswith(("  ", " ") )
                and "现价" in l]) <= 3, text[:400]


# ---------------------------------------------------------------- 内容档位的登记表（10-17 扩到 17 档）
#: 10-17 新加的 11 档：全部调各页那份现成读层，参数就是接口自己的参数。
NEW_SCENES = ("voldbl", "breakout", "ambush", "quadrant", "abnormal", "spike",
              "trendstock", "sector", "groupanalytics", "news", "allocator")


def test_六张登记表必须还是同一批人():
    """加一档内容要动六个地方 —— 少一处就是一类假功能：
    有执行体没键 ⇒ 面板勾不到；有键没执行体 ⇒ 勾了发空；有名字没说明 ⇒ 用户看不出差别；
    有档位没条数表 ⇒ `DEFAULT_LIMITS[key]` 当场 KeyError，整条消息发不出去。"""
    from duanxian import notify
    keys = set(NC.SCENE_KEYS)
    assert keys == set(notify.SCENE_BUILDERS) == set(notify.SCENE_LABELS), keys ^ set(notify.SCENE_BUILDERS)
    assert keys == set(NC.SCENE_HINTS) == set(NC.SCENE_FIELDS), keys ^ set(NC.SCENE_FIELDS)
    assert keys == set(notify.DEFAULT_LIMITS) == set(notify.MAX_LIMITS) == set(notify.SCENE_LIMIT_UNITS)
    for key in keys:
        assert notify.DEFAULT_LIMITS[key] <= notify.MAX_LIMITS[key], key
        assert NC.SCENE_HINTS[key].startswith(notify.SCENE_LABELS[key]), \
            f"{key}：面板上的长说明要以短名开头，否则两排字看起来是两件事"
    assert set(NEW_SCENES) <= keys and len(keys) == 17, len(keys)


def test_描述符里登记的键执行体必须真的读():
    """面板上那一格能不能改变发出去的正文 —— 只看两件事：描述符有这一格、执行体读这个键。
    任缺其一就是"能填但没人读"（10-16 的 `confluence.sources` 缺后者，`dossier.codes` 缺前者）。"""
    import inspect
    import re
    from duanxian import notify
    for key, fields in NC.SCENE_FIELDS.items():
        src = inspect.getsource(notify.SCENE_BUILDERS[key])
        reads = set(re.findall(r'params\.get\("([a-z_]+)"', src))
        reads |= set(re.findall(r'_(?:num|flag)\(params, "([a-z_]+)"', src))
        declared = {fd["key"] for fd in fields}
        # 正向：登记的每一格都要被读到（`limit` 走 `_lim`，不是 `params.get`）
        missing = declared - reads - {"limit"}
        assert not missing, f"{key}：描述符登记了 {sorted(missing)}，执行体却不读"
        # 反向：执行体读的键都要登记（没登记的格子面板渲染不出来，配置里只能人手写）
        ghost = reads - declared
        assert not ghost, f"{key}：执行体读了没登记的键 {sorted(ghost)} —— 界面上填不了"


def test_参数格的每一种形状都要有校验():
    """`validate_scenes` 现在只按描述符判（不再有按档名写死的 if）—— 所以每一种 `type` 都得有一条。"""
    # 数字格：超范围要拒，留空是"用默认"要放行
    assert "0~30" in NC.validate_scenes({"spike": {"min_hi": 99}})
    assert NC.validate_scenes({"spike": {"min_hi": ""}}) is None
    # 2026-10-19：倍量那一档从整数次数换成加权分（浮点），描述符的 type 也跟着从 int 变 num
    assert "数字" in NC.validate_scenes({"voldbl": {"min_energy": "三"}})
    assert NC.validate_scenes({"voldbl": {"min_energy": 0.8}}) is None
    assert "min_count" not in str(NC.SCENE_FIELDS["voldbl"]), '旧参数名留着＝面板上还能填一个没人读的键'
    # 下拉：静态候选要认得值（票池这一格是静态的两个）
    assert "只认" in NC.validate_scenes({"screen": {"rules": [{"rule_key": "x"}], "universe": "all"}})
    assert NC.validate_scenes({"screen": {"rules": [{"rule_key": "x"}], "universe": "market"}}) is None
    # 多选：字典形状只允许规则那一格（执行体要 {rule_key, params}）
    assert "不是可选项" in NC.validate_scenes({"ambush": {"types": [{"rule_key": "x"}]}})
    assert NC.validate_scenes({"ambush": {"types": ["sticky_pull"]}}) is None
    assert NC.validate_scenes({"screen": {"rules": [{"rule_key": "align_bear", "params": {}}]}}) is None
    # 开关与文字
    assert NC.validate_scenes({"positions": {"only_loss": True}}) is None
    assert "要么勾要么不勾" in NC.validate_scenes({"positions": {"only_loss": "maybe"}})
    assert "太长" in NC.validate_scenes({"news": {"keyword": "长" * 30}})
    # 数字型 select（自选分组）：留空＝全部分组，填了就得是数字
    assert "数字" in NC.validate_scenes({"watchlist": {"group_id": "abc"}})
    assert NC.validate_scenes({"watchlist": {"group_id": "896"}}) is None


def test_目录要把候选就地解好():
    """面板那一排的候选必须是**已经解好的** `choices`（`choices_from` 指过去的那种），
    而且 `default` 要落在 `min~max` 里 —— 否则界面上会出现一个填不进去的数字框。"""
    from duanxian import notify
    cat = notify.scene_catalog()
    by = {x["value"]: x for x in cat["scenes"]}
    for key, fields in NC.SCENE_FIELDS.items():
        got = {f["key"]: f for f in by[key]["fields"]}
        assert set(got) == {f["key"] for f in fields}, key
        for fd in fields:
            one = got[fd["key"]]
            if fd.get("choices_from"):
                assert one["choices"], \
                    f"{key}.{fd['key']}：候选没解出来（{cat['reasons'].get(fd['choices_from'])}）"
            if one.get("min") is not None and one.get("max") is not None \
                    and one.get("default") is not None:
                assert one["min"] <= one["default"] <= one["max"], f"{key}.{fd['key']}"
    # 共振门槛那一格的上限 = 在册策略数（不是描述符里那个 6 —— 加第七个策略时不用回来改）
    hits = {f["key"]: f for f in by["confluence"]["fields"]}["min_hits"]
    assert hits["max"] == cat["confluence_max_hits"] == len(cat["confluence_sources"])
    # 单位与中文说明一起交出去（列表那一行的"7 只/2 层"就是从这里来的）
    assert by["groupanalytics"]["limit_unit"] == "组" and by["news"]["limit_unit"] == "条"
    assert by["voldbl"]["hint"].startswith("倍量形态榜")


def test_新档真库出文要说人话():
    """每一档真跑一遍现成读层：要么有内容、要么**如实说没取到**。
    正文里不许出现 `['a', 'b']`、`None`、`nan` 这类"取到了但没翻译"的形状。"""
    from duanxian import notify
    day = notify._session_date()
    for key in NEW_SCENES:
        out = notify.SCENE_BUILDERS[key]({}, day)
        assert isinstance(out, str) and out.strip(), key
        assert out.startswith(notify.SCENE_LABELS[key]) or "没取到" in out, out[:120]
        for bad in ("None", "['", "{'", "nan"):
            assert bad not in out, f"{key}：正文里出现 {bad}\n{out[:300]}"
        assert len(out.encode("utf-8")) <= 1900, \
            f"{key}：单档按默认条数就超了企微上限（{len(out.encode())} 字节）"


def test_每一档的参数真的改变发出去的正文():
    """参数不是摆设：同一档换参数必须看到差别（这条是"面板上填了、发出去没变"的唯一防线）。"""
    from duanxian import notify
    day = notify._session_date()

    def rows(text):
        return len([l for l in text.split("\n") if l.strip()])

    a = notify._blk_voldbl({"min_energy": 0, "limit": 20}, day)
    b = notify._blk_voldbl({"min_energy": 1.1, "limit": 20}, day)
    assert "近 40 场加权分≥0" in a and "近 40 场加权分≥1.1" in b
    assert rows(b) <= rows(a), f"门槛抬高档里剩下的行数反而更多\n{a[:200]}\n---\n{b[:200]}"
    # 门槛高过全场最高分 ⇒ 必须是"这场有数据、筛完是 0 只"，不许说成"没回填"
    none = notify._blk_voldbl({"min_energy": 40, "limit": 5}, day)
    assert "是有数据的" in none and "没有数据（未回填" not in none, none[:200]
    # 象限那格的计数口径：读层那份是**带倍量门槛、不带象限条件**的每档实有几只，
    # 所以说成"全场"就是假话（10-17 实测旧口径：门槛≥8 时"全场"从 191 只变成 5 只）
    q1 = notify._blk_voldbl({"min_energy": 1.1, "quadrants": ["Q1"], "limit": 3}, day).split("\n")[0]
    assert "加权分≥1.1 的票里" in q1 and "全场" not in q1, q1

    one = notify._blk_breakout({"signal_type": "first_break", "limit": 3}, day)
    every = notify._blk_breakout({"limit": 3}, day)
    assert "只要「首破」" in one and "三类都要" in every

    strict = notify._blk_ambush({"only_strict": True, "limit": 3}, day)
    loose = notify._blk_ambush({"limit": 3}, day)
    assert "只算严格通过" in strict and "只算严格通过" not in loose

    d1 = notify._blk_sector({"days": 1, "limit": 3}, day)
    d2 = notify._blk_sector({"days": 2, "limit": 3}, day)
    assert "往回 1 日" in d1 and "往回 2 日" in d2 and rows(d2) > rows(d1)

    focus = notify._blk_groupanalytics({"only_focus": True, "limit": 10}, day)
    every_g = notify._blk_groupanalytics({"limit": 10}, day)
    assert "重点分组" in focus and "重点分组" not in every_g
    assert rows(focus) <= rows(every_g)

    kw = notify._blk_news({"keyword": "涨停", "limit": 5}, day)
    plain = notify._blk_news({"limit": 5}, day)
    if "没取到" in plain:
        # 用例跑在临时 data home（`isolated` fixture）时快讯目录是空的 ——
        # 这一档只能**如实说没采到**，不许拿一句像样的空正文糊过去
        assert "没取到" in kw, plain[:160]
    else:
        assert "标题含「涨停」" in kw and "标题含「涨停」" not in plain

    parts = notify._blk_allocator({"parts": ["stages"], "limit": 3}, day)
    assert "[阶段]" in parts and "[收费]" not in parts and "[拥挤]" not in parts

    ab = notify._blk_abnormal({"recent": 5, "hot_limit": 3, "limit": 3}, day)
    assert "近 5 场" in ab and "偏离值榜（前" in ab

    ts = notify._blk_trendstock({"lookback": 10, "limit": 3}, day)
    assert "连续 10 天" in ts


def test_十七档一起compose也不许把某档静默丢掉():
    """一次勾满 17 档：每一档都要在正文里出现（名字或"没取到"那句），
    超上限时**必须留下截断声明** —— 手机上看不到后半段是常态，看不见"被切了"才是事故。"""
    from duanxian import notify
    scenes = {k: {} for k in notify.SCENE_BUILDERS}
    title, body, meta = notify.compose(scenes, "", limit=1900)
    assert meta["scenes"]["truncated"] is True and meta["wire_bytes"] <= 1900, meta
    assert body.endswith("（内容过长已截断，完整请看网站）"), body[-60:]
    per_scene = set(meta["scenes"]) - {"truncated", "body_bytes", "wire_bytes", "body_limit"}
    assert per_scene == set(notify.SCENE_BUILDERS), meta["scenes"]
    # 每一档要么 ok 要么 error，**不许有档"本程没有执行体"**
    assert not [k for k, v in meta["scenes"].items() if v == "unknown"], meta["scenes"]
    _, full, m2 = notify.compose(scenes, "", limit=400000)
    for key in notify.SCENE_BUILDERS:
        assert notify.SCENE_LABELS[key] in full or "没取到" in full, key
    assert m2["scenes"]["market"] == "ok", m2["scenes"]


def test_改备注不许把指向env的引用清掉():
    """10-17 用户点名「点开已配置的应该也能看到明细，也可以编辑和保存」。
    界面回填时靠的就是 `url_env` / `secret_env` 这两格**变量名**（名字不是凭据，屏幕上
    `secret_source`/`sign_source` 本来就写着它）。少交一格，面板上"编辑"就等于把这条指回
    `.env` 的引用清掉 —— 用户明明只想改一句备注。"""
    monkey = os.environ
    monkey["MY_PUSH_URL_1017"] = "https://oapi.dingtalk.com/robot/send?access_token=TOKENAAA111"
    monkey["MY_PUSH_SECRET_1017"] = "SECRETTOKENVALUE9999"
    try:
        NC.upsert_channel({"key": "d1017", "kind": "dingtalk",
                           "url_env": "MY_PUSH_URL_1017", "secret_env": "MY_PUSH_SECRET_1017",
                           "label": "盯盘钉钉", "note": "收盘看这个"}, via="t")
        view = NC.masked_channels()[0]
        assert view["url_env"] == "MY_PUSH_URL_1017"
        assert view["secret_env"] == "MY_PUSH_SECRET_1017", "变量名没交出来 —— 面板回填时那一格是空的"
        assert view["has_secret"] is True and view["has_sign"] is True
        assert view["sign_source"] == "环境变量 MY_PUSH_SECRET_1017"
        assert view["note"] == "收盘看这个"
        # 任何一份读数里都不许出现真串（变量名可以，值不行）
        blob = json.dumps(view, ensure_ascii=False) + json.dumps(NC.state(), ensure_ascii=False)
        assert "TOKENAAA111" not in blob and "SECRETTOKENVALUE9999" not in blob
        # 模拟面板的编辑提交：把读回来的名字原样送回去，只改备注 ⇒ 一切照旧
        cfg, why = NC.upsert_channel({"key": "d1017", "kind": "dingtalk",
                                      "url_env": view["url_env"], "secret_env": view["secret_env"],
                                      "label": view["label"], "note": "改过的备注"}, via="t")
        assert why is None
        after = NC.masked_channels()[0]
        assert after["note"] == "改过的备注" and after["has_secret"] and after["has_sign"]
        assert after["url_env"] == "MY_PUSH_URL_1017" and after["secret_env"] == "MY_PUSH_SECRET_1017"
        # 换种类而不重填凭据 ⇒ 后端必须拒（旧地址的前缀与新种类不配），不许静默收下
        _, why2 = NC.upsert_channel({"key": "d1017", "kind": "feishu",
                                     "url_env": "MY_PUSH_URL_1017"}, via="t")
        assert why2 and ("feishu" in why2 or "前缀" in why2 or "http" in why2), why2
    finally:
        monkey.pop("MY_PUSH_URL_1017", None)
        monkey.pop("MY_PUSH_SECRET_1017", None)
