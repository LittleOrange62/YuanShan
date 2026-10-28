"""「个人微信推送」执行层的回归。

⚠️ 这个文件的红线：**跑测试时绝不允许在这台机器的桌面上真按一次键盘**。
所以：

- 只读探测（找窗口、前台、空闲时长、渲染面采样、`IsWindow`）在 fixture 里全打成假货；
- 唯一真注入的口子 `_desktop_apply` **默认不被打桩**，而是单独有一条用例证明它在
  pytest 进程里会硬挡（那条用例传空序列：闸坏了也一个键都按不出去）；
- 要验执行体本体那圈循环时，用 `notify` 那边同一个招：**喂形状正确的假货**
  （打桩 `_send_key/_send_click/_clip_write/_window_action` 这些真副作用原语，
  再把闸的比对对象换掉），这样测的是本体代码而不是我自己写的桩。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

import wxpushconfig as WC

FAKE_HWND = 0x1234


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    home = tmp_path / "datahome"
    (home / "wxpush").mkdir(parents=True)
    monkeypatch.setenv("ASTOCK_DATA_HOME", str(home))
    monkeypatch.setenv("VIBE_WXPUSH_CONFIG", str(home / "wxpush" / "config.json"))
    monkeypatch.setenv("VIBE_WXPUSH_HISTORY", str(home / "wxpush" / "history.jsonl"))
    return home


@pytest.fixture
def desktop(isolated, monkeypatch):
    """只读探测的假货：这台机器上"有没有桌面"是环境事实，不能带进测试。"""
    import duanxian.wxpush as WX

    monkeypatch.setattr(WX, "find_weixin_window", lambda: FAKE_HWND)
    monkeypatch.setattr(WX, "foreground_hwnd", lambda: FAKE_HWND)
    monkeypatch.setattr(WX, "idle_seconds", lambda: 999.0)
    monkeypatch.setattr(WX, "render_surface_ratio", lambda rect: 0.62)
    monkeypatch.setattr(WX, "window_rect",
                        lambda hwnd: (100, 100, 100 + 900, 100 + 700))
    monkeypatch.setattr(WX.USER32, "IsWindow", lambda h: True)
    monkeypatch.setattr(WX, "clipboard_text", lambda: ("原来那段剪贴板", "text"))
    return isolated


def _recorder(monkeypatch, WX):
    """把真副作用原语换成记录器 —— 本体那圈循环照跑，一个键都不落到桌面上。"""
    hits = {"keys": [], "clicks": [], "clip": [], "window": []}
    monkeypatch.setattr(WX, "_APPLY_DEFAULT", object())     # 让硬挡闸认不出"还是默认实现"
    monkeypatch.setattr(WX, "_send_key", lambda vk, up: hits["keys"].append((vk, up)))
    monkeypatch.setattr(WX, "_send_text", lambda t: hits["keys"].append(("text", t)))
    monkeypatch.setattr(WX, "_send_click", lambda x, y: hits["clicks"].append((x, y)))
    monkeypatch.setattr(WX, "_clip_write", lambda t: hits["clip"].append(t))
    monkeypatch.setattr(WX, "_window_action",
                        lambda action, hwnd, was: hits["window"].append(action) or f"做了 {action}")
    return hits


# ---------------------------------------------------------------- 红线
def test_真注入在pytest进程里必须硬挡(isolated):
    """这条用例传的是**空序列**：万一闸被改坏了，也按不出任何东西，但断言会红。"""
    import duanxian.wxpush as WX

    with pytest.raises(RuntimeError, match="不许在桌面上真按键"):
        WX._desktop_apply([], hwnd=FAKE_HWND, was_visible=False)


def test_起真脚本的那道口同样硬挡(isolated):
    import duanxian.wxpush as WX

    with pytest.raises(RuntimeError, match="不许起真脚本"):
        WX.run_script("scripts/nope.py")


def test_预检函数本身一个键都不按(desktop, monkeypatch):
    """面板每打开一次就跑一遍预检 ⇒ 它必须是纯读的。这里把注入原语全换成"一调就炸"。"""
    import duanxian.wxpush as WX

    def _boom(*a, **k):
        raise AssertionError("预检里不许注入任何东西")

    monkeypatch.setattr(WX, "_send_key", _boom)
    monkeypatch.setattr(WX, "_send_text", _boom)
    monkeypatch.setattr(WX, "_send_click", _boom)
    monkeypatch.setattr(WX, "_clip_write", _boom)
    monkeypatch.setattr(WX, "_window_action", _boom)
    monkeypatch.setattr(WX, "USER32", WX.USER32)
    pre = WX.preflight(WC.load())
    assert isinstance(pre["checks"], list) and pre["checks"]


# ---------------------------------------------------------------- 序列形状
def test_出厂序列就是用户2026_10_20圈的那条流程(desktop):
    import duanxian.wxpush as WX

    cfg = WC.load()
    cfg["steps"] = WC.default_steps()
    ops, _ = WX.build_ops(cfg, to="文件传输助手", body="正文", hwnd=FAKE_HWND)
    labels = [o["label"] for o in ops]
    joined = "｜".join(labels)
    for must in ("按唤起快捷键", "打开搜索框", "把「文件传输助手」放进剪贴板", "粘贴搜索词",
                 "Enter 选中第一个结果", "全选输入框内容", "删掉原有内容", "把正文放进剪贴板",
                 "粘贴正文", "回车发送", "按收起快捷键", "还原剪贴板"):
        assert must in joined, f"出厂流程里少了「{must}」：{joined}"
    # 出厂默认：不切输入法、不点输入框（这两件事要靠用户自己勾）
    assert "切输入法" not in joined
    assert "点输入框" not in joined


def test_十二步各自都有真效果_开着就出现关掉就消失(desktop):
    """这一条防的是"界面上加了一格、执行体没接" —— 描述符写了、`build_ops` 忘了读，
    于是面板能改但发出去永远少一块；也防"关了这一步、序列里却还在按"。"""
    import duanxian.wxpush as WX

    probes = {
        "open_window": ("按唤起快捷键", None), "maximize": ("最大化窗口", "maximize"),
        "search": ("打开搜索框", None), "ime_english": ("切输入法", "shift"),
        "type_target": ("逐字键入「某人」", "keys"), "pick_first": ("点第一个搜索结果", "click_point"),
        "focus_input": ("点输入框", "click_point"), "select_all": ("全选输入框内容", None),
        "clear_old": ("删掉原有内容", None), "paste_body": ("粘贴正文", None),
        "enter_send": ("回车发送", None), "close_window": ("按收起快捷键", None),
    }
    assert set(probes) == set(WC.STEP_KEYS), "步骤表与这条用例的探针对不上，加了新步骤没补用例"

    def labels(step_key, *, mode=None, enabled=True):
        cfg = WC.load()
        cfg["steps"] = WC.default_steps()
        for st in cfg["steps"]:
            if st["key"] == step_key:
                st["enabled"] = enabled
                if mode:
                    st["mode"] = mode
                if step_key in ("pick_first", "focus_input") and mode == "click_point":
                    st["point"] = [200, 300]
        return "｜".join(o["label"] for o in WX.build_ops(cfg, to="某人", body="x",
                                                        hwnd=FAKE_HWND)[0])

    for key, (want, mode) in probes.items():
        got = labels(key, mode=mode)
        assert want in got, f"{key} 选了 {mode or '出厂做法'}，序列里却没出现「{want}」"
        assert want not in labels(key, mode=mode, enabled=False), \
            f"{key} 已经关掉了，序列里却还在按「{want}」"


def test_坐标是相对窗口左上角而不是屏幕绝对坐标(desktop):
    """窗口一挪，绝对坐标就会点到别的应用上 —— 那是会把按键打进你正在用的程序里的。"""
    import duanxian.wxpush as WX

    cfg = WC.load()
    cfg["steps"] = WC.default_steps()
    for st in cfg["steps"]:
        if st["key"] == "focus_input":
            st["mode"] = "click_point"
            st["point"] = [430, 600]
    ops, _ = WX.build_ops(cfg, to="某人", body="x", hwnd=FAKE_HWND)
    click = next(o for o in ops if o["op"] == "click")
    assert click["x"] == 100 + 430 and click["y"] == 100 + 600, "没把偏移换算成窗口坐标"
    assert click["offset"] == [430, 600]


def test_每一步之前都核对前台(desktop):
    """中途焦点被对话框抢走时，后面的 Ctrl+A / Delete / 粘贴绝不能打进别的程序。

    ⚠️ 窗口置前台**之前**那一步（唤起快捷键）故意不核对：它用的是全局快捷键，
    按下那一刻微信本来就不可能是前台窗口 —— 要求它先有前台就是自相矛盾。
    """
    import duanxian.wxpush as WX

    cfg = WC.load()
    cfg["steps"] = WC.default_steps()
    ops, _ = WX.build_ops(cfg, to="某人", body="x", hwnd=FAKE_HWND)
    kinds = [o["op"] for o in ops]
    focus_at = next(i for i, o in enumerate(ops)
                    if o["op"] == "window" and o.get("action") == "focus")
    checked = 0
    for i, k in enumerate(kinds):
        if i <= focus_at or k not in ("keys", "click", "clipboard", "type"):
            continue
        # 注入之前必须先核对过前台（往前找最近的一次）
        assert "verify_foreground" in kinds[max(focus_at, i - 3):i], f"第 {i} 步（{k}）之前没核对前台"
        checked += 1
    assert checked >= 6, f"只有 {checked} 处核对，注入点远不止这些"


# ---------------------------------------------------------------- 预检拦住
def test_没有输入桌面时一个键都不按并且说清为什么(desktop, monkeypatch):
    import duanxian.wxpush as WX

    called = []
    monkeypatch.setattr(WX, "_desktop_apply", lambda *a, **k: called.append(1) or {"ok": True})
    monkeypatch.setattr(WX, "foreground_hwnd", lambda: 0)     # RDP 断开时的形状
    cfg = WC.load()
    WC.save({**cfg, "enabled": True}, via="t")
    out = WX.send(to="某人", text="一句话", source="test")
    assert out["status"] == "blocked" and "input_desktop" in out["blocked"]
    assert not called, "预检没过却仍然去按键了"
    assert "远程桌面" in out["reason"]


def test_屏幕全黑也拦住(desktop, monkeypatch):
    import duanxian.wxpush as WX

    monkeypatch.setattr(WX, "render_surface_ratio", lambda rect: 0.0)
    monkeypatch.setattr(WX, "_desktop_apply", lambda *a, **k: {"ok": True})
    WC.save({**WC.load(), "enabled": True}, via="t")
    out = WX.send(to="某人", text="一句话", source="test")
    assert "render_surface" in out["blocked"]
    assert "渲染表面" in out["reason"]


def test_有人在用键盘时不插队(desktop, monkeypatch):
    import duanxian.wxpush as WX

    monkeypatch.setattr(WX, "idle_seconds", lambda: 1.0)
    monkeypatch.setattr(WX, "_desktop_apply", lambda *a, **k: {"ok": True})
    WC.save({**WC.load(), "enabled": True}, via="t")
    assert "idle" in WX.send(to="某人", text="一句话", source="test")["blocked"]


def test_总开关关着就是一条也不发(desktop, monkeypatch):
    import duanxian.wxpush as WX

    monkeypatch.setattr(WX, "_desktop_apply", lambda *a, **k: {"ok": True})
    WC.set_enabled(False, via="t")
    out = WX.send(to="某人", text="一句话", source="test")
    assert "enabled" in out["blocked"]


def test_判不了的时候按不放行(desktop, monkeypatch):
    """`idle_seconds` 读不到（None）不能当成"空闲很久"放行 —— 与配置层"宁可沉默"同一条取舍。"""
    import duanxian.wxpush as WX

    monkeypatch.setattr(WX, "idle_seconds", lambda: None)
    monkeypatch.setattr(WX, "render_surface_ratio", lambda rect: None)
    monkeypatch.setattr(WX, "_desktop_apply", lambda *a, **k: {"ok": True})
    WC.save({**WC.load(), "enabled": True}, via="t")
    out = WX.send(to="某人", text="一句话", source="test")
    assert set(out["blocked"]) >= {"idle", "render_surface"}


# ---------------------------------------------------------------- 参数与正文
def test_没有收件人_不发请求也不查预检(desktop):
    import duanxian.wxpush as WX

    WC.save({**WC.load(), "enabled": True, "default_to": ""}, via="t")
    out = WX.send(to="", text="一句话", source="test")
    assert out["status"] == "rejected" and out["stage"] == "收件人"


def test_正文超过上限拒发并带字数(desktop):
    import duanxian.wxpush as WX

    WC.save({**WC.load(), "enabled": True}, via="t")
    WC.set_guard({"key": "max_body_chars", "value": 50}, via="t")
    out = WX.send(to="某人", text="很长" * 40, source="test")
    assert out["status"] == "rejected" and out["stage"] == "正文"
    assert out["chars"] == 80 and "上限" in out["reason"]


def test_算出来是空的就不发空白(desktop):
    import duanxian.wxpush as WX

    WC.save({**WC.load(), "enabled": True}, via="t")
    out = WX.send(to="某人", text="   ", source="test")
    assert out["status"] == "rejected"


def test_dry_run一个键都不按但仍然给出完整序列(desktop, monkeypatch):
    import duanxian.wxpush as WX

    called = []
    monkeypatch.setattr(WX, "_desktop_apply", lambda *a, **k: called.append(1) or {"ok": True})
    WC.save({**WC.load(), "enabled": True}, via="t")
    out = WX.send(to="某人", text="一句话", dry_run=True, source="test")
    assert out["status"] == "dry_run" and out["ok"] is True
    assert not called
    assert out["op_count"] == len(out["ops"]) and out["op_count"] > 10
    assert any(o["op"] == "verify_foreground" for o in out["ops"])
    # 预检没过时 dry_run 也要照常回序列（它就是用来看"为什么发不出去"的）
    monkeypatch.setattr(WX, "foreground_hwnd", lambda: 0)
    out2 = WX.send(to="某人", text="一句话", dry_run=True, source="test")
    assert out2["ok"] is False and out2["op_count"] > 10 and not called


def test_scene内容档与四个渠道用的是同一个组装器(desktop, monkeypatch):
    """不打桩判据、只打桩"取数"这一层：桩的形状必须与真 `compose` 一模一样，
    否则两边一起错就成 8 小时假绿（10-10 那次的教训）。"""
    import duanxian.notify as N
    import duanxian.wxpush as WX

    seen = {}

    def fake_compose(scenes, date="", *, title_prefix="", limit=0):
        seen["scenes"] = scenes
        seen["date"] = date
        seen["title_prefix"] = title_prefix
        return "标题", "自选 3 只⏎某某", {"date": date, "limit": limit, "wire_bytes": 30,
                                     "scenes": {"watchlist": "ok"}}

    monkeypatch.setattr(N, "compose", fake_compose)
    WC.save({**WC.load(), "enabled": True}, via="t")
    out = WX.send(to="某人", scenes={"watchlist": {"limit": 3}}, dry_run=True, source="test")
    assert seen["scenes"] == {"watchlist": {"limit": 3}}
    assert out["body"] == "自选 3 只⏎某某"
    assert out["meta"]["source"] == "scene"


def test_text内容档的两个占位会真替换(desktop):
    import duanxian.wxpush as WX

    WC.upsert_template({"key": "t1", "label": "占位", "source": "text",
                        "text": "今天 {date}，此刻 {now}"}, via="t")
    WC.save({**WC.load(), "enabled": True}, via="t")
    out = WX.send(to="某人", template="t1", dry_run=True, source="test")
    assert "{" not in out["body"], out["body"]
    assert "此刻 20" in out["body"]


def test_script内容档取stdout(desktop, monkeypatch):
    import duanxian.wxpush as WX

    monkeypatch.setattr(WX, "run_script",
                        lambda rel, *, timeout=30: {"ok": True, "reason": None,
                                                    "text": "脚本给的正文", "exit": 0})
    WC.upsert_template({"key": "s1", "label": "脚本", "source": "script",
                        "script": "scripts/x.py"}, via="t")
    WC.save({**WC.load(), "enabled": True}, via="t")
    out = WX.send(to="某人", template="s1", dry_run=True, source="test")
    assert out["body"] == "脚本给的正文"


def test_script失败时不发半截正文(desktop, monkeypatch):
    import duanxian.wxpush as WX

    monkeypatch.setattr(WX, "run_script",
                        lambda rel, *, timeout=30: {"ok": False, "reason": "脚本退出码 1",
                                                    "text": "", "exit": 1})
    WC.upsert_template({"key": "s2", "label": "脚本", "source": "script",
                        "script": "scripts/x.py"}, via="t")
    WC.save({**WC.load(), "enabled": True}, via="t")
    out = WX.send(to="某人", template="s2", dry_run=True, source="test")
    assert "脚本退出码 1" in out["reason"]


# ---------------------------------------------------------------- 真跑一遍本体
def test_本体循环真跑一遍_键序剪贴板与窗口动作都对上(desktop, monkeypatch):
    import duanxian.wxpush as WX

    hits = _recorder(monkeypatch, WX)
    WC.save({**WC.load(), "enabled": True}, via="t")
    out = WX.send(to="某人", text="正文一句", source="test")
    assert out["ok"] is True and out["status"] == "injected"
    assert out["delivered"] is None, "这一侧读不回回执，绝不许自称已送达"
    joined = "".join(str(v) for v, _ in hits["keys"])
    assert "17" in joined and "70" in joined, "搜索框那一步的 Ctrl+F 没按出来"
    # 剪贴板被写了三回：搜索词、正文、还原原内容
    assert hits["clip"] == ["某人", "正文一句", "原来那段剪贴板"], hits["clip"]
    assert "restore" in hits["window"] and "hide_if_was_hidden" in hits["window"]


def test_中途焦点被抢走就当场停手(desktop, monkeypatch):
    """后面的全选/删除/粘贴绝不能打进别的程序 —— 停手时还要说清停在哪一步。"""
    import duanxian.wxpush as WX

    hits = _recorder(monkeypatch, WX)
    seq = [FAKE_HWND, FAKE_HWND, 999]      # 第三次核对时前台已经不是微信
    calls = {"n": 0}

    def fake_fg():
        i = min(calls["n"], len(seq) - 1)
        calls["n"] += 1
        return seq[i]

    monkeypatch.setattr(WX, "foreground_hwnd", fake_fg)
    WC.save({**WC.load(), "enabled": True}, via="t")
    out = WX.send(to="某人", text="正文一句", source="test")
    assert out["status"] == "aborted" and out["ok"] is False
    assert "打进别的程序" in out["reason"]
    assert out["aborted_at"], "停手了却没说停在哪一步"
    assert "正文一句" not in hits["clip"], "停手之后还继续往剪贴板里写正文"


def test_注入被系统拒绝时状态是failed而不是发了个寂寞(desktop, monkeypatch):
    import duanxian.wxpush as WX

    _recorder(monkeypatch, WX)

    def boom(vk, up):
        raise RuntimeError("SendInput 被系统拒绝（错误码 5）")

    monkeypatch.setattr(WX, "_send_key", boom)
    WC.save({**WC.load(), "enabled": True}, via="t")
    out = WX.send(to="某人", text="正文一句", source="test")
    assert out["status"] == "failed" and out["ok"] is False
    assert "SendInput" in out["reason"]


def test_原剪贴板不是纯文本时不动它(desktop, monkeypatch):
    import duanxian.wxpush as WX

    hits = _recorder(monkeypatch, WX)
    monkeypatch.setattr(WX, "clipboard_text", lambda: (None, "non_text"))
    WC.save({**WC.load(), "enabled": True}, via="t")
    out = WX.send(to="某人", text="正文一句", source="test")
    assert out["clipboard_restore"]["done"] is False
    assert "不是纯文本" in out["clipboard_restore"]["detail"]
    assert hits["clip"] == ["某人", "正文一句"], "还原时不该再写一次剪贴板"


# ---------------------------------------------------------------- 留痕
def test_历史成败都记并且不存正文全文(desktop, monkeypatch):
    import duanxian.wxpush as WX

    _recorder(monkeypatch, WX)
    WC.save({**WC.load(), "enabled": True}, via="t")
    long_body = "很长" * 200
    WX.send(to="某人", text=long_body[:200], source="test")   # 上限之内的长度
    monkeypatch.setattr(WX, "foreground_hwnd", lambda: 0)
    WX.send(to="某人", text="另一条", source="test")          # 这条会被预检拦住
    rows = WX.history()
    assert len(rows) == 2
    assert rows[0]["status"] == "blocked" and rows[1]["status"] == "injected"
    blob = json.dumps(rows, ensure_ascii=False)
    assert "很长" not in blob or "body_head" in blob
    for r in rows:
        assert "body" not in r, "历史里不许存正文全文（会进日志、会被贴进会话）"
        assert "checks_failed" in r


def test_冷却只数真按过的那几条(desktop, monkeypatch):
    """拒发/拦住/dry_run 都不该占冷却窗口 —— 否则连着试三次没出去会把第四次也锁死。"""
    import duanxian.wxpush as WX

    _recorder(monkeypatch, WX)
    WC.save({**WC.load(), "enabled": True}, via="t")
    monkeypatch.setattr(WX, "foreground_hwnd", lambda: 0)
    WX.send(to="某人", text="一条", source="test")
    assert WX.seconds_since_last_send() is None
    monkeypatch.setattr(WX, "foreground_hwnd", lambda: FAKE_HWND)
    WX.send(to="某人", text="一条", source="test")
    gap = WX.seconds_since_last_send()
    assert gap is not None and gap < 60
    WC.set_guard({"key": "cooldown_seconds", "value": 3600}, via="t")
    out = WX.send(to="某人", text="第二条", source="test")
    assert "cooldown" in out["blocked"]


def test_冷却关着时不拦(desktop, monkeypatch):
    import duanxian.wxpush as WX

    _recorder(monkeypatch, WX)
    WC.save({**WC.load(), "enabled": True}, via="t")
    WC.set_guard({"key": "cooldown_seconds", "value": 0}, via="t")
    WX.send(to="某人", text="第一条", source="test")
    out = WX.send(to="某人", text="第二条", source="test")
    assert out["ok"] is True


def test_留痕落在配置层说的那个文件里(desktop, monkeypatch):
    import duanxian.wxpush as WX

    _recorder(monkeypatch, WX)
    WC.save({**WC.load(), "enabled": True}, via="t")
    WX.send(to="某人", text="一条", source="test")
    assert Path(WC.history_path()).exists()
    assert "wxpush" in WC.state()["history_path"]
