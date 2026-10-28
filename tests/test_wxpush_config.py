"""「个人微信推送」配置层的回归。

钉的是四类会静默骗人的事：

① **假功能**：界面上有一格、执行体里没有对应阶段（所以 `STEP_KEYS` 与执行体必须同集合，
   加一步忘了接就当场红）；
② **按错的键**：组合键形状写错了不会报错，只会"按了个没反应的组合" —— 所以键名白名单、
   修饰键数量、空快捷键都必须在写入面拒掉；
③ **越界**：`script` 那一档给了绝对路径或 `..`，这条通道就变成"读任意文件"的口子；
④ **判不了却当成能发**：坏文件、缺步骤、指向不存在的键，读数必须出声（`_warning`），
   不许悄悄回落成一份"看着配好了"的配置。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

import wxpushconfig as WC


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    home = tmp_path / "datahome"
    (home / "wxpush").mkdir(parents=True)
    monkeypatch.setenv("ASTOCK_DATA_HOME", str(home))
    monkeypatch.setenv("VIBE_WXPUSH_CONFIG", str(home / "wxpush" / "config.json"))
    monkeypatch.setenv("VIBE_WXPUSH_HISTORY", str(home / "wxpush" / "history.jsonl"))
    return home


# ---------------------------------------------------------------- ① 登记表同集合
def test_步骤表与执行体同集合_界面能改的那十二步每一步都得真执行():
    """加一步只写描述符、忘了在执行体里接 —— 就是"面板能改、发出去少一块"。"""
    import duanxian.wxpush as WX

    src = Path(WX.__file__).read_text(encoding="utf-8")
    for key in WC.STEP_KEYS:
        assert f'"{key}"' in src, f"执行体里没有 {key} 这一步（界面上会有一格没人读的框）"


def test_键名白名单与键码表同集合():
    """界面上认得出的键，执行体必须按得出来；少一个就是"存得进去、发的时候静默按不出"。"""
    assert set(WC.KEYS) - set(WC.VK) == set()
    assert set(WC.VK) - set(WC.KEYS) == set()
    assert set(WC.MODS) == set(WC.MOD_VK)


def test_闸的登记表条数与默认值一处不多一处不少():
    assert len(WC.STEP_KEYS) == 12
    assert len(WC.GUARD_KEYS) == len(WC.default_guards())
    for g in WC.GUARD_DEFS:
        assert WC.default_guards()[g["key"]] == g["default"]


def test_出厂是关着的():
    """这一条通道在桌面上真按键盘，默认必须沉默 —— 打开是要人explicitly做的动作。"""
    assert WC.defaults()["enabled"] is False
    assert WC.load()["enabled"] is False


def test_每步出厂值都能过自己的校验():
    for step in WC.default_steps():
        assert WC.validate_step(step) is None, step


def test_输入法那一档要的是纯修饰键串():
    """`shift`、`alt+shift` 不是"组合键"（没有普通键），走另一份展开器；
    形状错了要在**展开时**炸出来，不能到了执行体才发现按了个空。"""
    assert WC.mods_seq("shift") == [(WC.MOD_VK["shift"], 0), (WC.MOD_VK["shift"], 1)]
    assert WC.mods_seq("ctrl+space")[0] == (WC.MOD_VK["ctrl"], 0)
    assert WC.mods_label("alt+shift") == "Alt+Shift"
    for bad in ("", "bogus", "ctrl+bogusbogus"):
        with pytest.raises(ValueError):
            WC.mods_seq(bad)


def test_内容档的三档名字与面板同源():
    st = WC.state()
    assert {t["value"] for t in st["scene_choices"]} == set(__import__("notifyconfig").SCENE_KEYS)


# ---------------------------------------------------------------- ② 组合键
@pytest.mark.parametrize("combo,label", [("ctrl+f", "Ctrl+F"), ("alt+1", "Alt+1"),
                                         ("enter", "Enter"), ("ctrl+alt+shift+f", "Ctrl+Alt+Shift+F"),
                                         ("shift", None), ("ctrl+", None), ("ctrl+a+b", None),
                                         ("ctrl+f+g", None), ("", None), ("随便写的中文", None)])
def test_组合键形状认不认得出来(combo, label):
    got = WC.parse_combo(combo)
    if label is None:
        assert got is None, f"{combo!r} 该被拒掉却读成了 {got}"
    else:
        assert got is not None
        assert WC.combo_label(combo) == label


def test_组合键展开成按下与抬起_抬起顺序是反的():
    seq = WC.to_vk_seq("ctrl+f")
    assert seq[0] == (WC.MOD_VK["ctrl"], 0)
    assert seq[-1] == (WC.MOD_VK["ctrl"], 1), "先按的修饰键要最后抬，抬错了就变成一路按着 Ctrl"
    assert len({v for v, _ in seq}) == 2


def test_组合键写坏了存不进去():
    cfg, why = WC.set_step({"key": "search", "value": "ctrl+bogusbogus"}, via="t")
    assert why and "search" in why
    assert WC.load()["steps"][WC.STEP_KEYS.index("search")]["value"] == "ctrl+f"


def test_勾了只按快捷键却不给键_要拒():
    """空快捷键不会报错，只会"这一步什么都不按" —— 那种"配好了其实没接"必须在写入面挡下。"""
    cfg, why = WC.set_step({"key": "open_window", "mode": "hotkey", "value": ""}, via="t")
    assert why and "open_window" in why


def test_显式传的值不许被换做法时的出厂值悄悄盖掉():
    """`set_step` 在换 mode 时会把没一起传的字段回落出厂 —— 但显式传了（含空串）就得用传的值。"""
    WC.set_step({"key": "open_window", "mode": "hotkey", "value": "alt+2"}, via="t")
    cfg, why = WC.set_step({"key": "open_window", "mode": "hotkey", "value": "ctrl+`"}, via="t")
    assert why is None
    row = next(s for s in cfg["steps"] if s["key"] == "open_window")
    assert row["value"] == "ctrl+`"


def test_换做法时没传的格子回落出厂值():
    WC.set_step({"key": "search", "value": "ctrl+q"}, via="t")
    cfg, _ = WC.set_step({"key": "pick_first", "mode": "click_point", "point": [300, 200]}, via="t")
    row = next(s for s in cfg["steps"] if s["key"] == "pick_first")
    assert row["mode"] == "click_point" and row["point"] == [300, 200]
    assert row["wait_ms"] == WC.STEP_BY_KEY["pick_first"]["wait_default"]


# ---------------------------------------------------------------- ③ 形状与越界
def test_坐标只在选了点击那一档时才要():
    assert WC.validate_step({"key": "pick_first", "mode": "enter"}) is None
    why = WC.validate_step({"key": "pick_first", "mode": "click_point", "point": [1]})
    assert why and "[x, y]" in why
    assert "超出合理范围" in WC.validate_step({"key": "pick_first", "mode": "click_point",
                                              "point": [99999, 5]})


def test_不认识的步骤与做法都拒():
    assert "没有 'nope' 这一步" in (WC.set_step({"key": "nope", "mode": "skip"}, via="t")[1] or "")
    # search 这一格没有可换做法（后端只认按键），所以任何 mode 都得拒
    assert "mode" in (WC.set_step({"key": "search", "mode": "bogus"}, via="t")[1] or "")


def test_闸的上下限():
    assert WC.set_guard({"key": "max_body_chars", "value": 99999}, via="t")[1]
    assert WC.set_guard({"key": "min_idle_seconds", "value": -3}, via="t")[1]
    assert WC.set_guard({"key": "max_body_chars", "value": 3000}, via="t")[1] is None
    assert WC.set_guard({"key": "require_window", "value": "开"}, via="t")[1], "布尔格收字符串"
    assert WC.set_guard({"key": "require_window", "value": False}, via="t")[1] is None


def test_脚本内容档只收仓库内相对路径():
    for bad in ("C:/Windows/x.py", "/etc/x.py", "../outside.py", "sub/../../x.py"):
        why = WC.validate_template({"key": "s", "label": "s", "source": "script", "script": bad})
        assert why and "相对路径" in why, bad
    assert WC.validate_template({"key": "s", "label": "s", "source": "script",
                                 "script": "scripts/x.py"}) is None


def test_内容档的占位符写错了要拒():
    """`{time}` 不会被替换、也不会报错 —— 会原样发进微信，那是最难查的一类错。"""
    assert WC.validate_template({"key": "t", "label": "t", "source": "text",
                                 "text": "今天 {date} 收盘，看 {time}"})
    assert WC.validate_template({"key": "t", "label": "t", "source": "text",
                                 "text": "今天 {date} 收盘 {now}"}) is None


def test_内容档空文字与空勾档都拒():
    assert "没写文字" in WC.validate_template({"key": "t", "label": "t", "source": "text", "text": "  "})
    assert "一档都没勾" in WC.validate_template({"key": "t", "label": "t", "source": "scene", "scenes": {}})
    assert "不认识的内容块" in WC.validate_template({"key": "t", "label": "t", "source": "scene",
                                                    "scenes": {"nope": {}}})


def test_内容档键名形状():
    assert WC.validate_template({"key": "1bad", "label": "x", "source": "text", "text": "a"})
    assert WC.validate_template({"key": "ok_key-1", "label": "x", "source": "text", "text": "a"}) is None


def test_正文超过单条上限存不进去():
    """微信会静默截断，所以别存一条永远发不全的内容档。"""
    why = WC.validate_template({"key": "t", "label": "t", "source": "text",
                                "text": "长" * (WC.MAX_BODY_CHARS_LIMIT + 1)})
    assert why and "静默截断" in why


# ---------------------------------------------------------------- ④ 读数与留痕
def test_坏文件按关闭处理并且出声(tmp_path):
    Path(WC.config_path()).write_text("{不是 json", encoding="utf-8")
    cfg = WC.load()
    assert cfg["enabled"] is False and cfg["_warning"], "读不出配置却不出声 = 用户以为软件不认他的配置"


def test_缺步骤用出厂补齐并把这件事说出来():
    Path(WC.config_path()).write_text(json.dumps(
        {"enabled": True, "default_to": "某人", "steps": [{"key": "search", "value": "ctrl+f"}]},
        ensure_ascii=False), encoding="utf-8")
    cfg = WC.load()
    assert len(cfg["steps"]) == 12
    assert "缺少这些步骤" in (cfg.get("_warning") or ""), "补齐是好事，但必须让人知道补了哪几步"


def test_认不出的步骤键不会被静默丢掉():
    Path(WC.config_path()).write_text(json.dumps(
        {"enabled": False, "steps": [{"key": "search", "value": "ctrl+f"}, {"key": "ghost", "mode": "x"}]},
        ensure_ascii=False), encoding="utf-8")
    assert "ghost" in (WC.load().get("_steps_dropped") or [])


def test_每次写入都盖章并且读数里带最后一次是谁改的():
    WC.set_enabled(True, via="面板某人")
    st = WC.state()
    assert st["changed_by"] == "面板某人" and st["changed_at"]
    assert st["counts"]["steps_off"] == 0


def test_保存拒绝坏值_不留半成品():
    with pytest.raises(ValueError):
        WC.save({"enabled": True, "steps": [{"key": "search", "value": "ctrl+bogus"}]}, via="t")
    assert not Path(WC.config_path()).exists()


def test_掩码这件事在这条通道上的形状():
    """这一条通道**没有凭据**（不存 token/URL），但收件人不是秘密、必须回显 ——
    否则面板上「编辑」一开就是空的，用户改个别的字段就会把默认收件人清空。"""
    WC.set_default_to("2026double", via="t")
    st = WC.state()
    assert st["default_to"] == "2026double"
    assert "token" not in json.dumps(st, ensure_ascii=False).lower()


def test_默认收件人太长要拒():
    assert WC.set_default_to("很" * 80, via="t")[1]
    assert WC.set_default_to("文件传输助手", via="t")[1] is None
