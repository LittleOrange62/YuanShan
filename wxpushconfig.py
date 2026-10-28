"""「个人微信推送」的配置层：界面自动化步骤、预检闸、内容档的**唯一来源**（只用标准库）。

为什么单独一个文件、还只用标准库：照 `notifyconfig.py` / `netconfig.py` 的规矩 ——
`scripts/manage.py` 在依赖还没装的时候也要能读出"这条通道开没开、步骤配成什么样"，
而执行侧（`duanxian/wxpush.py`）要拉 ctypes 与那套读数。两边共用一份判据，
才不会出现「界面上看着配好了、真发时才发现是坏值」。

配置落在业务数据根下（与 `notify/config.json`、`jobs/config.json` 同一套规矩）：

    <ASTOCK_DATA_HOME>/wxpush/config.json

    {"enabled": false,
     "default_to": "文件传输助手",
     "steps":   [{"key": "search", "enabled": true, "mode": "hotkey",
                  "value": "ctrl+f", "wait_ms": 800}],
     "guards":  {"min_idle_seconds": 10, "max_body_chars": 1200, ...},
     "templates": [{"key": "close_note", "label": "收盘小结", "source": "scene",
                    "scenes": {"watchlist": {"limit": 8}}, "enabled": true}]}

**这条通道与四个 apprise 渠道的根本区别**（用户 2026-10-20 选的就是这一条，代价要说清）：
它不是网络请求，是**在这台机器的桌面上替你敲键盘**。所以：

1. 它只在**有人连着远程桌面时会用**（实测 2026-10-20：RDP 客户端最小化/断开后，
   会话既没有渲染表面也没有输入桌面 —— 截图全黑、`GetForegroundWindow()` 回 0、
   `SendInput` 一律 `ERROR_ACCESS_DENIED`）。这不是代码能绕的，是 Windows 的会话行为。
2. 它会**抢前台焦点**：发送那两三秒里，这台机器上别的程序不能同时在用键盘。
3. 它会**动用户的剪贴板**（粘贴是唯一可靠办法：微信 4.1.8 的控件树对 UIA 完全不暴露，
   实测 `descendants()` = 0，所以既读不到输入框、也没法按控件名点）。
   进出都要保存/还原，原内容不是纯文本时**不动它**。
4. 「全选 + 删除」那一步会**把人家的未发草稿一起删掉**（读不到就无法还原）。
   所以这一档做成可关的 `clear_before_paste`，默认开着但在界面上明说后果。

界面上**不许出现"能填但没人读"的格子**：`STEP_DEFS` 里每一条都必须被
`duanxian/wxpush.py::run_steps` 真执行；加了描述符忘了接执行体，由测试钉住。
"""
from __future__ import annotations

import json
import os
import re
import threading
from datetime import datetime
from pathlib import Path

CONFIG_FILE = "wxpush/config.json"
HISTORY_FILE = "wxpush/history.jsonl"
MAX_TEMPLATES = 20
#: 微信文本消息的长度：界面上能粘进去的远不止这些，但**粘贴超长内容时客户端会静默截断**
#: （截断点还不告诉你）。压到 1200 字是"宁可少发，不发半截"的取值，可配。
MAX_BODY_CHARS_LIMIT = 4000
WAIT_MS_MAX = 15000

#: 修饰键的中文名（界面上写"Ctrl+Alt+F"这类组合时按这个展开）。
MODS = {"ctrl": "Ctrl", "alt": "Alt", "shift": "Shift", "win": "Win"}
#: 非修饰键白名单。**不在名单里的键一律存不进去**：写错一个键名不会报错、
#: 只会让那一帧"按了个没反应的组合"，而那种错最难查（发不出去但没人说为什么）。
KEYS = {
    "enter": "Enter", "esc": "Esc", "escape": "Esc", "tab": "Tab", "space": "空格",
    "backspace": "Backspace", "delete": "Delete", "del": "Delete",
    "up": "↑", "down": "↓", "left": "←", "right": "→",
    "home": "Home", "end": "End", "pgup": "PageUp", "pgdn": "PageDown",
    "a": "A", "c": "C", "d": "D", "e": "E", "f": "F", "g": "G", "h": "H", "i": "I",
    "j": "J", "k": "K", "l": "L", "m": "M", "n": "N", "o": "O", "p": "P", "q": "Q",
    "r": "R", "s": "S", "t": "T", "u": "U", "v": "V", "w": "W", "x": "X", "y": "Y", "z": "Z",
    "0": "0", "1": "1", "2": "2", "3": "3", "4": "4", "5": "5", "6": "6", "7": "7",
    "8": "8", "9": "9",
    "f1": "F1", "f2": "F2", "f3": "F3", "f4": "F4", "f5": "F5", "f6": "F6",
    "f7": "F7", "f8": "F8", "f9": "F9", "f10": "F10", "f11": "F11", "f12": "F12",
    "-": "-", "=": "=", "[": "[", "]": "]", ";": ";", "'": "'", ",": ",", ".": ".",
    "/": "/", "\\": "\\", "`": "`",
}
#: 虚拟键码。执行体按 `wVk` 注入，所以这里必须与 `KEYS` 同集合（测试钉住）。
VK = {
    "enter": 0x0D, "esc": 0x1B, "escape": 0x1B, "tab": 0x09, "space": 0x20,
    "backspace": 0x08, "delete": 0x2E, "del": 0x2E,
    "left": 0x25, "up": 0x26, "right": 0x27, "down": 0x28,
    "home": 0x24, "end": 0x23, "pgup": 0x21, "pgdn": 0x22,
    "a": 0x41, "c": 0x43, "d": 0x44, "e": 0x45, "f": 0x46, "g": 0x47, "h": 0x48,
    "i": 0x49, "j": 0x4A, "k": 0x4B, "l": 0x4C, "m": 0x4D, "n": 0x4E, "o": 0x4F,
    "p": 0x50, "q": 0x51, "r": 0x52, "s": 0x53, "t": 0x54, "u": 0x55, "v": 0x56,
    "w": 0x57, "x": 0x58, "y": 0x59, "z": 0x5A,
    "0": 0x30, "1": 0x31, "2": 0x32, "3": 0x33, "4": 0x34, "5": 0x35, "6": 0x36,
    "7": 0x37, "8": 0x38, "9": 0x39,
    "f1": 0x70, "f2": 0x71, "f3": 0x72, "f4": 0x73, "f5": 0x74, "f6": 0x75,
    "f7": 0x76, "f8": 0x77, "f9": 0x78, "f10": 0x79, "f11": 0x7A, "f12": 0x7B,
    "-": 0xBD, "=": 0xBB, "[": 0xDB, "]": 0xDD, ";": 0xBA, "'": 0xDE,
    ",": 0xBC, ".": 0xBE, "/": 0xBF, "\\": 0xDC, "`": 0xC0,
}
MOD_VK = {"ctrl": 0x11, "alt": 0x12, "shift": 0x10, "win": 0x5B}
#: `KEYS` 与 `VK` 必须同集合（测试钉住）：界面上认得出的键，执行体必须按得出来，
#: 少一个就是"那一格配得进去、发的时候静默按不出来"。

#: **步骤表（用户 2026-10-20 逐条圈的顺序）**。一条 = 界面上的一行 = 执行体里的一个阶段。
#: 描述符字段：
#:   key/label        —— 内部名与面板上的中文
#:   kind             —— hotkey（按一个组合键）/ window（对窗口本身动作）/ keys（按普通键）
#:                        / click（鼠标点击）/ text（把内容送进当前焦点处）/ ime（切输入法）
#:   modes            —— 这一行可选的做法（面板上就是那一排单选按钮）；只有一个时不渲染选择
#:   default_mode     —— 出厂取值
#:   default_value    —— 组合键形状（`hotkey`/`keys` 用），如 `ctrl+f`
#:   default_point    —— 点击行的默认坐标偏移（相对微信主窗口左上角，**不是屏幕绝对坐标**：
#:                        窗口一挪，绝对坐标就点到别的应用上，那是最难查的一类误操作）
#:   wait_default     —— 这一步之后等多久（毫秒）；客户端慢半拍是这个方案最主要的失败来源
#:   hint             —— 面板上跟在后面的那句人话
#:   risky            —— 勾上就在界面上标出「这一步会动你桌上的东西」
STEP_DEFS: tuple[dict, ...] = (
    {"key": "open_window", "label": "唤起微信窗口", "kind": "window",
     "modes": [{"value": "hotkey_then_restore", "label": "先按快捷键、没起来就直接唤窗"},
               {"value": "hotkey", "label": "只按快捷键"},
               {"value": "restore", "label": "只直接唤窗（不按键）"},
               {"value": "skip", "label": "不做这步"}],
     "default_mode": "hotkey_then_restore", "default_value": "alt+1", "wait_default": 900,
     "hint": "「只按快捷键」要求你在微信自己的设置里把「打开主窗口」设成同一个组合键；"
             "两处不一致时按了没反应，所以出厂是「按完没起来就直接唤窗」，结果里会写明用的是哪条。"},
    {"key": "maximize", "label": "全屏微信", "kind": "window",
     "modes": [{"value": "maximize", "label": "最大化窗口"},
               {"value": "hotkey", "label": "按快捷键"},
               {"value": "skip", "label": "保持原尺寸（推荐：坐标点击更稳）"}],
     "default_mode": "skip", "default_value": "", "wait_default": 600,
     "hint": "最大化会把「点第一个结果 / 点输入框」那两格的坐标整体挪位 —— "
             "要开这一格，就得重量那两个坐标。默认不动窗口。"},
    {"key": "search", "label": "打开搜索框", "kind": "hotkey",
     "modes": None, "default_mode": "hotkey", "default_value": "ctrl+f", "wait_default": 900,
     "hint": "微信 4.x 里搜索默认就是 Ctrl+F；改过就跟着改这一格。"},
    {"key": "ime_english", "label": "切换成英文输入法", "kind": "ime",
     "modes": [{"value": "none", "label": "不切（推荐）"},
               {"value": "shift", "label": "按一次 Shift"},
               {"value": "ctrl+space", "label": "按 Ctrl+Space"},
               {"value": "alt+shift", "label": "按 Alt+Shift"}],
     "default_mode": "none", "default_value": "", "wait_default": 300,
     "hint": "出厂不切：联系人与正文都走剪贴板粘贴，**根本不经过输入法**，"
             "切输入法反而多一个会错的环节。你的输入法必须靠按键切换时才需要开。"},
    {"key": "type_target", "label": "输入要发给谁（搜索词）", "kind": "text",
     "modes": [{"value": "paste", "label": "粘贴（推荐）"}, {"value": "keys", "label": "逐字键入"}],
     "default_mode": "paste", "default_value": "", "wait_default": 1400,
     "hint": "粘贴走剪贴板，中文联系人也能一次进框；逐字键入只适合纯英文名，"
             "且会把内容按你当前的输入法原样吐出去（中文输入法下会变拼音）。"
             "搜索结果列表要等一会才出来，这一步之后的等待别压太小。"},
    {"key": "pick_first", "label": "选中搜索结果里的第一个", "kind": "click",
     "modes": [{"value": "enter", "label": "按 Enter（推荐：不依赖坐标）"},
               {"value": "down_enter", "label": "先按 ↓ 再 Enter"},
               {"value": "click_point", "label": "鼠标点固定坐标"},
               {"value": "skip", "label": "不做这步"}],
     "default_mode": "enter", "default_value": "", "default_point": [220, 120],
     "wait_default": 1200,
     "hint": "这一客户端的控件树对 UIA 完全不暴露（实测 0 个子控件），所以「点第一个」"
             "只能靠按键走到那一项，或者按你量的固定坐标去点。坐标点错了不会报错，"
             "只会点到窗口里别的东西上 —— 出厂用 Enter。"},
    {"key": "focus_input", "label": "点到输入框", "kind": "click",
     "modes": [{"value": "click_point", "label": "鼠标点固定坐标"},
               {"value": "skip", "label": "不做这步（选会话后焦点本来就在输入框）"},
               {"value": "tab", "label": "按 Tab 移焦点"}],
     "default_mode": "skip", "default_value": "", "default_point": [430, 600],
     "wait_default": 500,
     "hint": "坐标是相对微信主窗口左上角的偏移。窗口被拖动/改尺寸后要重量，"
             "否则「全选 + 删除」会作用在别的控件上。"},
    {"key": "select_all", "label": "全选输入框内容（Ctrl+A）", "kind": "keys",
     "modes": None, "default_mode": "keys", "default_value": "ctrl+a", "wait_default": 150,
     "hint": "只在下一格「清掉原有内容」开着时才有意义。", "risky": True},
    {"key": "clear_old", "label": "删掉输入框里的原内容", "kind": "keys",
     "modes": [{"value": "delete", "label": "按 Delete（推荐）"},
               {"value": "backspace", "label": "按 Backspace"},
               {"value": "skip", "label": "不清（直接接在原内容后面）"}],
     "default_mode": "delete", "default_value": "delete", "wait_default": 150,
     "hint": "⚠️ 这一步会把你**没发出去的草稿**一起删掉，而且删完读不回来（没有控件树）。"
             "不想冒这个险就选「不清」。", "risky": True},
    {"key": "paste_body", "label": "把要推的内容粘进去", "kind": "text",
     "modes": [{"value": "paste", "label": "粘贴（推荐）"}, {"value": "keys", "label": "逐字键入"}],
     "default_mode": "paste", "default_value": "", "wait_default": 700,
     "hint": "超长内容会被客户端**静默截断**（截在哪它不说），所以上限由「正文长度上限」那格管。"},
    {"key": "enter_send", "label": "回车发送", "kind": "keys",
     "modes": None, "default_mode": "keys", "default_value": "enter", "wait_default": 900,
     "hint": "", "risky": True},
    {"key": "close_window", "label": "收起微信窗口", "kind": "window",
     "modes": [{"value": "hotkey_then_hide", "label": "先按快捷键、没收就直接隐藏"},
               {"value": "hotkey", "label": "只按快捷键"},
               {"value": "hide", "label": "只隐藏窗口"},
               {"value": "none", "label": "不收（保持显示，方便你看着它发）"}],
     "default_mode": "hotkey_then_hide", "default_value": "alt+1", "wait_default": 300,
     "hint": "出厂会**还原成发送前的样子**：发送前窗口本来就藏着才收回去，本来就开着就留着。"},
)
STEP_KEYS = tuple(s["key"] for s in STEP_DEFS)
STEP_BY_KEY = {s["key"]: s for s in STEP_DEFS}

#: 预检闸（用户 10-20 没提、但这条通道必须有：发不出去还不出声是最坏的一种）。
#: 每一项都是**只读**检查 ⇒ 面板每次打开都会现算一遍，绝不为了"看看行不行"去按键。
GUARD_DEFS: tuple[dict, ...] = (
    {"key": "require_input_desktop", "label": "要求会话有输入桌面", "type": "bool", "default": True,
     "note": "判法：`GetForegroundWindow()` 必须回一个真窗口。远程桌面一断开/最小化，"
             "这一项就是 0，此时任何按键都会被系统回「拒绝访问」—— 拒发比"
             "\"按了没人接\"诚实得多。"},
    {"key": "require_render_surface", "label": "要求屏幕真在渲染", "type": "bool", "default": True,
     "note": "判法：对微信窗口那块矩形做 GDI 取色，全黑就判定没有渲染表面"
             "（实测：无桌面时整屏采样 5.6 万个点**全为 0**）。"},
    {"key": "require_window", "label": "要求微信主窗口存在", "type": "bool", "default": True,
     "note": "客户端没启动/没登录时拒发 —— 这一步不谈快捷键，只谈窗口在不在。"},
    {"key": "min_idle_seconds", "label": "要求键盘空闲不少于", "type": "int", "default": 10,
     "min": 0, "max": 600, "unit": "秒",
     "note": "0 = 不查（发送会与人抢键盘）。设成正数意味着「你正在用这台电脑时它不插队」。"},
    {"key": "cooldown_seconds", "label": "两次发送之间至少隔", "type": "int", "default": 20,
     "min": 0, "max": 3600, "unit": "秒",
     "note": "防止两个调用方同时抢桌面；到点的那条计划也不会和手动的那条撞车。"},
    {"key": "max_body_chars", "label": "正文长度上限", "type": "int", "default": 1200,
     "min": 50, "max": MAX_BODY_CHARS_LIMIT, "unit": "字",
     "note": "超长会被客户端静默截断，所以超上限时**拒发**并告诉你超了多少，"
             "而不是发出去半截。"},
    {"key": "restore_clipboard", "label": "发送后还原剪贴板", "type": "bool", "default": True,
     "note": "原内容不是纯文本（图片/文件）时**不动它** —— 覆盖掉你的图片比留下"
             "一段粘贴文本更坏。"},
)
GUARD_KEYS = tuple(g["key"] for g in GUARD_DEFS)

#: 内容档（用户 10-20：「发送的内容可以配置，做成一个单独的功能点，给其他程序调用」）。
#: `source` 只有三种形状，界面上不给第四种框：
#:   text   —— 面板上直接写的固定文字（可带 `{now}`/`{date}` 两个占位）
#:   scene  —— 复用四个渠道那 17 档内容的组装器（`duanxian/notify.compose`），
#:             **与手机企微/飞书收到的那段正文同源**，不在这里重算一遍判据
#:   script —— 指向仓库内的一个脚本，由它现生成正文（`argv` 传参）；
#:             这是"给其他程序用"那一档的兜底口子，跑的是本地文件、不接网络
SOURCES = ("text", "scene", "script")
SOURCE_LABELS = {"text": "固定文字", "scene": "用现成内容档（与企微/飞书同源）",
                 "script": "跑一个本地脚本生成"}
TEXT_PLACEHOLDERS = ("{now}", "{date}")
SCRIPT_TIMEOUT_MAX = 120

_write_lock = threading.Lock()


def dt_now() -> str:
    return datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S")


def data_home() -> Path:
    return Path(os.environ.get("ASTOCK_DATA_HOME") or (Path.home() / ".duanxian-agents"))


def config_path() -> str:
    """`VIBE_WXPUSH_CONFIG` 只给测试注入用，日常不要设。"""
    return os.environ.get("VIBE_WXPUSH_CONFIG", "").strip() or str(data_home() / CONFIG_FILE)


def history_path() -> str:
    return os.environ.get("VIBE_WXPUSH_HISTORY", "").strip() or str(data_home() / HISTORY_FILE)


#: 出厂步骤 = 用户 10-20 那条流程的形状；默认值是**实测出来唯一走得通的那组**
#: （坐标默认不参与，因为这一客户端读不到控件树，坐标只能盲点）。
def default_steps() -> list[dict]:
    out = []
    for s in STEP_DEFS:
        out.append({"key": s["key"], "enabled": True, "mode": s["default_mode"],
                    "value": s.get("default_value") or "",
                    "point": list(s.get("default_point") or [0, 0]),
                    "wait_ms": int(s["wait_default"])})
    return out


def default_guards() -> dict:
    return {g["key"]: g["default"] for g in GUARD_DEFS}


def defaults() -> dict:
    return {"enabled": False, "default_to": "", "steps": default_steps(),
            "guards": default_guards(), "templates": []}


# ---------------------------------------------------------------- 组合键
def parse_combo(text: str) -> tuple[list[str], str] | None:
    """`ctrl+f` → (['ctrl'], 'f')。不合法返回 None（调用方负责出一句人话）。

    只认「若干修饰键 + 一个普通键」：多个普通键（`ctrl+a+b`）一定是有人的键名打错了，
    静默接受 = 那一步永远按不出来。
    """
    raw = str(text or "").strip().lower()
    if not raw:
        return None
    parts = [p.strip() for p in re.split(r"[+\-]{1,2}| ", raw) if p.strip()]
    if raw in KEYS:                        # 单键形状：`enter`、`esc`
        parts = [raw]
    if len(parts) < 1 or len(parts) > 5:
        return None
    mods = [p for p in parts if p in MODS]
    rest = [p for p in parts if p not in MODS]
    if len(rest) != 1 or rest[0] not in KEYS:
        return None
    if not mods and len(parts) > 1:
        return None                        # `a+b` 这种不是组合键
    return mods, rest[0]


def combo_label(text: str) -> str:
    """面板/结果里显示的形状（`ctrl+f` → `Ctrl+F`）。读不懂时原样返回，不编一个像样的。"""
    got = parse_combo(text)
    if not got:
        return str(text or "")
    mods, key = got
    return "+".join([MODS[m] for m in mods] + [KEYS[key]])


def validate_combo(field: str, text: str, *, allow_empty: bool = False) -> str | None:
    if not str(text or "").strip():
        return None if allow_empty else f"{field} 是空的：这一步要按哪个组合键？"
    if parse_combo(text) is None:
        return (f"{field}={text!r} 看不懂：形状应是「修饰键+单键」，如 ctrl+f / alt+1 / enter。"
                f"修饰键可用 {'、'.join(sorted(MODS))}；"
                f"示例的组合键里混进了不认识的一个：{text}")
    return None


def to_vk_seq(text: str) -> list[tuple[int, int]]:
    """组合键 → (虚拟键码, 是否抬起) 序列。执行体只认这一份展开，别处不再抄键码表。"""
    got = parse_combo(text)
    if not got:
        raise ValueError(f"组合键读不懂：{text!r}")
    mods, key = got
    down = [MOD_VK[m] for m in mods] + [VK[key]]
    seq = [(vk, 0) for vk in down] + [(vk, 1) for vk in reversed(down)]
    return seq


#: 切输入法那一档要的是**纯修饰键**组合（`shift`、`alt+shift`），
#: 那不是"组合键"（`parse_combo` 要求带一个普通键），所以单独一份展开器。
#: 取值仍受 `STEP_DEFS` 里那条 `modes` 的封闭清单约束，不是随便填。
def mods_seq(text: str) -> list[tuple[int, int]]:
    parts = [p.strip() for p in str(text or "").lower().split("+") if p.strip()]
    down = []
    for p in parts:
        vk = MOD_VK.get(p) if p in MODS else (VK.get(p) if p in KEYS else None)
        if vk is None:
            raise ValueError(f"输入法切换串读不懂：{text!r}（每一项都要是修饰键或白名单里的键）")
        down.append(vk)
    if not down:
        raise ValueError(f"输入法切换串是空的：{text!r}")
    return [(vk, 0) for vk in down] + [(vk, 1) for vk in reversed(down)]


def mods_label(text: str) -> str:
    return "+".join(MODS.get(p, KEYS.get(p, p))
                    for p in (x.strip() for x in str(text or "").lower().split("+")) if p)


# ---------------------------------------------------------------- 校验
def validate_step(step: dict) -> str | None:
    if not isinstance(step, dict):
        return f"步骤应为对象，收到 {type(step).__name__}"
    key = str(step.get("key") or "").strip()
    d = STEP_BY_KEY.get(key)
    if d is None:
        return (f"不认识步骤 {key!r}（可用：{'、'.join(STEP_KEYS)}；"
                "想加一步要先在 duanxian/wxpush.py 的执行体里有对应阶段，"
                "否则界面上就是一格没人读的框）")
    modes = d["modes"]
    mode = str(step.get("mode") or d["default_mode"])
    if modes and mode not in [m["value"] for m in modes]:
        return (f"{d['label']}（{key}）的做法只认 "
                f"{'、'.join(m['value'] for m in modes)}，收到 {mode!r}")
    if not modes and mode not in ("hotkey", "keys", "text", "paste", "ime"):
        return f"{d['label']}（{key}）不该出现 mode={mode!r}"
    if key in ("open_window", "close_window") and mode in ("hotkey", "hotkey_then_restore",
                                                            "hotkey_then_hide"):
        why = validate_combo(f"{key} 的快捷键", step.get("value") or "")
        if why:
            return why
    if key == "search":
        return validate_combo("search 的快捷键", step.get("value") or "ctrl+f")
    if key in ("select_all", "enter_send", "clear_old") and mode != "skip":
        why = validate_combo(f"{key} 的按键", step.get("value") or d.get("default_value") or "")
        if why:
            return why
    if d["kind"] == "click" and mode == "click_point":
        pt = step.get("point")
        if not isinstance(pt, (list, tuple)) or len(pt) != 2 or \
                any(not isinstance(v, int) or isinstance(v, bool) for v in pt):
            return (f"{d['label']}（{key}）选了「鼠标点固定坐标」却没给一对整数偏移 "
                    f"[x, y]（相对微信主窗口左上角），收到 {pt!r}")
        if not 0 <= pt[0] <= 4000 or not 0 <= pt[1] <= 4000:
            return f"{d['label']}（{key}）的偏移 {list(pt)} 超出合理范围（0~4000）"
    wait = step.get("wait_ms", d["wait_default"])
    try:
        value = int(wait)
    except (TypeError, ValueError):
        return f"{d['label']}（{key}）的 wait_ms 要个整数，收到 {wait!r}"
    if not 0 <= value <= WAIT_MS_MAX:
        return f"{d['label']}（{key}）的 wait_ms 要在 0~{WAIT_MS_MAX} 之间，收到 {value}"
    return None


def validate_guard(cfg: dict) -> str | None:
    for g in GUARD_DEFS:
        key, label, kind = g["key"], g["label"], g["type"]
        if key not in cfg:
            continue
        raw = cfg[key]
        if kind == "bool":
            if not isinstance(raw, bool):
                return f"{label}（{key}）要么开要么关，收到 {raw!r}"
        else:
            try:
                value = int(raw)
            except (TypeError, ValueError):
                return f"{label}（{key}）要个整数，收到 {raw!r}"
            if not g["min"] <= value <= g["max"]:
                return (f"{label}（{key}）要在 {g['min']}~{g['max']} {g.get('unit') or ''} 之间，"
                        f"收到 {value}")
    return None


def validate_template(tpl: dict) -> str | None:
    if not isinstance(tpl, dict):
        return f"内容档应为对象，收到 {type(tpl).__name__}"
    key = str(tpl.get("key") or "").strip()
    if not key:
        return "内容档缺少 key（面板与 API 都靠它定位这一条）"
    if not re.fullmatch(r"[a-zA-Z][a-zA-Z0-9_\-]{0,31}", key):
        return f"内容档键 {key!r} 只能用字母开头的字母数字下划线短横，不超过 32 字"
    if not str(tpl.get("label") or "").strip():
        return f"{key}: 内容档要有名字（发送结果与界面上都靠它认，别留空）"
    src = str(tpl.get("source") or "text")
    if src not in SOURCES:
        return f"{key}: 内容来源只认 {' / '.join(SOURCES)}，收到 {src!r}"
    if src == "text":
        body = str(tpl.get("text") or "")
        if not body.strip():
            return f"{key}: 选了「固定文字」却没写文字 ⇒ 发出去是空的"
        if len(body) > MAX_BODY_CHARS_LIMIT:
            return (f"{key}: 固定文字 {len(body)} 字，超过单条上限 {MAX_BODY_CHARS_LIMIT} 字"
                    "—— 微信会静默截断，别存一条永远发不全的内容档")
        for seg in re.findall(r"\{[^{}]*\}", body):
            if seg not in TEXT_PLACEHOLDERS:
                return (f"{key}: 文字里有占位 {seg!r}，这里只认 "
                        f"{'、'.join(TEXT_PLACEHOLDERS)}（写错了不会报错，会原样发出去）")
    if src == "scene":
        from notifyconfig import validate_scenes
        why = validate_scenes(tpl.get("scenes") or {})
        if why:
            return f"{key}: {why}"
        if not (tpl.get("scenes") or {}):
            return f"{key}: 选了「用现成内容档」却一档都没勾 ⇒ 正文会是空的"
    if src == "script":
        script = str(tpl.get("script") or "").strip()
        if not script:
            return f"{key}: 选了「跑本地脚本」却没写脚本路径"
        if not script.endswith(".py"):
            return f"{key}: 脚本要指向仓库里的 .py 文件，收到 {script!r}"
        # 光看字符串挡不住 `/etc/x.py` 这种形状（Windows 上 `isabs` 对它回 False），
        # 所以判**解析之后还在不在仓库里** —— 这条通道不该变成「读任意文件」的口子。
        root = Path(__file__).resolve().parent
        resolved = (root / script).resolve()
        if not str(resolved).startswith(str(root) + os.sep):
            return (f"{key}: 脚本路径 {script!r} 解析后跑到了仓库外面（{resolved}）；"
                    "要写仓库内的相对路径，不给绝对路径、不给 ..")
        timeout = tpl.get("timeout_seconds", 30)
        try:
            value = int(timeout)
        except (TypeError, ValueError):
            return f"{key}: timeout_seconds 要个整数，收到 {timeout!r}"
        if not 1 <= value <= SCRIPT_TIMEOUT_MAX:
            return f"{key}: timeout_seconds 要在 1~{SCRIPT_TIMEOUT_MAX} 之间，收到 {value}"
    return None


def validate_payload(cfg: dict) -> str | None:
    """整份自检：加载后与保存前都跑一次，坏配置绝不让它进入"能发"的状态。"""
    for step in cfg.get("steps") or []:
        why = validate_step(step)
        if why:
            return why
    why = validate_guard(cfg.get("guards") or {})
    if why:
        return why
    seen: set[str] = set()
    for tpl in cfg.get("templates") or []:
        why = validate_template(tpl)
        if why:
            return why
        key = str(tpl.get("key"))
        if key in seen:
            return f"内容档键 {key} 重复了"
        seen.add(key)
    if len(cfg.get("templates") or []) > MAX_TEMPLATES:
        return f"内容档最多 {MAX_TEMPLATES} 条"
    to = str(cfg.get("default_to") or "")
    if len(to) > 60:
        return f"默认收件人 {len(to)} 字太长了（最多 60 字）：这是要打进微信搜索框的词"
    return None


# ---------------------------------------------------------------- 读写
def load() -> dict:
    """读配置。**读不到就按「这条通道是关着的」** —— 这条通道会在桌面上真按键盘，
    拿"未知"当"就绪"的代价比四个网络渠道高一个数量级，所以宁可沉默。

    缺步骤不是"少一格"：那是发送时整段流程缺一块（例如少了「回车发送」就粘着不发）。
    所以读出后用出厂值补齐，并把这件事写进 `_warning` 让界面说出口。
    """
    path = config_path()
    out = defaults()
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return out
    except (OSError, ValueError) as exc:
        out["_warning"] = (f"配置文件读不了（{type(exc).__name__}），按「微信推送关闭」运行：{path}")
        return out
    if not isinstance(raw, dict):
        out["_warning"] = f"配置内容不是对象（{type(raw).__name__}），按「微信推送关闭」运行：{path}"
        return out
    out["enabled"] = bool(raw.get("enabled", False))
    out["default_to"] = str(raw.get("default_to") or "")
    steps = raw.get("steps")
    if isinstance(steps, list) and steps:
        have = {str(s.get("key")): s for s in steps if isinstance(s, dict) and s.get("key")}
        merged, missing = [], []
        for d in STEP_DEFS:
            got = have.get(d["key"])
            if got is None:
                missing.append(d["label"])
                merged.append(default_steps()[STEP_KEYS.index(d["key"])])
            else:
                merged.append({"key": d["key"], "enabled": bool(got.get("enabled", True)),
                               "mode": str(got.get("mode") or d["default_mode"]),
                               "value": str(got.get("value") or d.get("default_value") or ""),
                               "point": [int(v) for v in (got.get("point") or
                                                          d.get("default_point") or [0, 0])][:2],
                               "wait_ms": int(got.get("wait_ms", d["wait_default"]) or 0)})
        out["steps"] = merged
        if missing:
            out["_warning"] = ("配置里缺少这些步骤，已按出厂值补上（缺了就是流程少一块）："
                               + "、".join(missing))
        unknown = [k for k in have if k not in STEP_KEYS]
        if unknown:
            out["_steps_dropped"] = unknown
    guards = raw.get("guards")
    if isinstance(guards, dict):
        for g in GUARD_DEFS:
            if g["key"] in guards:
                out["guards"][g["key"]] = guards[g["key"]]
    tpl = raw.get("templates")
    out["templates"] = [t for t in tpl if isinstance(t, dict) and t.get("key")] \
        if isinstance(tpl, list) else []
    for key in ("_changed_at", "_changed_by"):
        if raw.get(key):
            out[key] = str(raw[key])
    why = validate_payload(out)
    if why:
        out["_invalid"] = why
    return out


def save(next_state: dict, *, via: str = "") -> dict:
    payload = {"enabled": bool(next_state.get("enabled", False)),
               "default_to": str(next_state.get("default_to") or "").strip(),
               "steps": list(next_state.get("steps") or default_steps()),
               "guards": {**default_guards(), **(next_state.get("guards") or {})},
               "templates": list(next_state.get("templates") or []),
               "_changed_at": dt_now(),
               "_changed_by": via or "未知调用方"}
    why = validate_payload(payload)
    if why:
        raise ValueError(why)
    path = Path(config_path())
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with _write_lock:
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, path)
    return payload


# ---------------------------------------------------------------- 读数
def steps_view(cfg: dict) -> list[dict]:
    """步骤行：出厂含义 + 每一行的可选项 + 校验实情，面板照这一份渲染，不另抄。"""
    by = {str(s.get("key")): s for s in cfg.get("steps") or []}
    out = []
    for d in STEP_DEFS:
        got = by.get(d["key"]) or {"key": d["key"], "enabled": True,
                                   "mode": d["default_mode"],
                                   "value": d.get("default_value") or "",
                                   "point": list(d.get("default_point") or [0, 0]),
                                   "wait_ms": d["wait_default"]}
        out.append({"key": d["key"], "label": d["label"], "kind": d["kind"],
                    "enabled": bool(got.get("enabled", True)),
                    "mode": str(got.get("mode") or d["default_mode"]),
                    "mode_label": next((m["label"] for m in (d["modes"] or [])
                                        if m["value"] == got.get("mode")), None),
                    "modes": d["modes"],
                    "value": str(got.get("value") or ""),
                    "value_label": combo_label(got.get("value") or ""),
                    "point": list(got.get("point") or [0, 0]),
                    "wait_ms": int(got.get("wait_ms", d["wait_default"]) or 0),
                    "wait_default": d["wait_default"],
                    "hint": d.get("hint") or "",
                    "risky": bool(d.get("risky")),
                    "is_default": (str(got.get("mode") or d["default_mode"]) == d["default_mode"]
                                   and str(got.get("value") or "") == (d.get("default_value") or "")
                                   and int(got.get("wait_ms", d["wait_default"]) or 0)
                                   == d["wait_default"]),
                    "invalid_reason": validate_step(got)})
    return out


def guards_view(cfg: dict) -> list[dict]:
    got = cfg.get("guards") or {}
    out = []
    for g in GUARD_DEFS:
        raw = got.get(g["key"], g["default"])
        out.append({"key": g["key"], "label": g["label"], "type": g["type"],
                    "value": raw, "default": g["default"], "unit": g.get("unit"),
                    "min": g.get("min"), "max": g.get("max"), "note": g["note"],
                    "is_default": raw == g["default"]})
    return out


def templates_view(cfg: dict) -> list[dict]:
    from notifyconfig import SCENE_HINTS            # 内容档的名字与那一档同源
    out = []
    for t in cfg.get("templates") or []:
        src = str(t.get("source") or "text")
        bits = []
        if src == "text":
            bits.append(f"固定文字 {len(str(t.get('text') or ''))} 字")
        elif src == "scene":
            for key in (t.get("scenes") or {}):
                bits.append(SCENE_HINTS.get(key) or key)
        else:
            bits.append(f"脚本 {t.get('script')}")
        out.append({"key": t.get("key"), "label": str(t.get("label") or "").strip(),
                    "source": src, "source_label": SOURCE_LABELS[src],
                    "text": str(t.get("text") or ""),
                    "scenes": t.get("scenes") or {},
                    "script": str(t.get("script") or ""),
                    "timeout_seconds": int(t.get("timeout_seconds") or 30),
                    "title": str(t.get("title") or ""),
                    "enabled": bool(t.get("enabled", True)),
                    "note": str(t.get("note") or ""),
                    "summary": "、".join(bits) or "（空）",
                    "invalid_reason": validate_template(t)})
    return out


def state(*, via_hint: str = "") -> dict:
    cfg = load()
    steps = steps_view(cfg)
    guards = guards_view(cfg)
    tpls = templates_view(cfg)
    # 「用现成内容档」那一排的可选项**直接引四个渠道那一份登记表**（`notifyconfig.SCENE_KEYS`）。
    # 前端不抄第二份、这里也不再列一遍：抄的那份迟早与执行体不同，用户勾的就是个不存在的内容档。
    from notifyconfig import SCENE_HINTS, SCENE_KEYS
    return {
        "enabled": cfg["enabled"],
        "scene_choices": [{"value": k, "label": SCENE_HINTS[k]} for k in SCENE_KEYS],
        "config_path": config_path(),
        "history_path": history_path(),
        "default_to": cfg["default_to"],
        "steps": steps,
        "guards": guards,
        "templates": tpls,
        "counts": {"templates": len(tpls),
                   "templates_enabled": sum(1 for t in tpls if t["enabled"]),
                   "steps_off": sum(1 for s in steps if not s["enabled"]),
                   "steps_customized": sum(1 for s in steps if not s["is_default"]),
                   "risky": sum(1 for s in steps if s["risky"])},
        "steps_enabled": sum(1 for s in steps if s["enabled"]),
        "invalid_reason": cfg.get("_invalid"),
        "warning": cfg.get("_warning"),
        "steps_dropped": cfg.get("_steps_dropped"),
        "changed_at": cfg.get("_changed_at"), "changed_by": cfg.get("_changed_by"),
        "via_hint": via_hint or None,
        "note": ("这一条通道**不是网络请求**，是在这台机器的桌面上替你敲键盘："
                 "唤起窗口 → 搜索 → 选中会话 → 粘正文 → 回车 → 收起。"
                 "所以它有三件必须知道的事：① 只在**有人连着远程桌面**时可用（断开后会话既没"
                 "渲染面也没输入桌面，系统直接拒绝注入按键）；② 发送那几秒会**抢走键盘焦点**；"
                 "③ 「删掉输入框里的原内容」那一步会把**你没发出去的草稿**删掉且读不回来。"
                 "出厂默认是**关着的**。"),
        "explain": ("为什么不能做得更稳：微信 4.1.8 这个 Qt 客户端对 UIA **一个子控件都不暴露**"
                    "（实测前台激活＋点击后 `descendants()` 仍为 0），所以既读不到输入框、"
                    "也点不到「第一个搜索结果」，只能按键盘走到那一项或按固定坐标盲点。"
                    "因此每一步之前都会核对「前台窗口就是微信」，不是就当场停手 —— "
                    "把按键打进你正在用的程序里比不发严重得多。"
                    "联系人与正文都走剪贴板（搜索框也接受粘贴），这样绕开输入法状态。"),
        "api": ("给其他程序调用的入口：POST /api/wxpush/send "
                "{to, template | text | scenes, dry_run}。"
                "dry_run=true 只跑预检并回将要发的正文，**绝不碰桌面** —— 机器要接这条通道，"
                "先用 dry_run 看清「现在这台机器能不能发」。"),
    }


# ---------------------------------------------------------------- 变更动作
def set_enabled(on: bool, *, via: str = "") -> dict:
    cfg = load()
    cfg["enabled"] = bool(on)
    return save(cfg, via=via)


def set_default_to(value: str, *, via: str = "") -> tuple[dict, str | None]:
    cfg = load()
    cfg["default_to"] = str(value or "").strip()
    why = validate_payload(cfg)
    if why:
        return cfg, why
    return save(cfg, via=via), None


def set_step(patch: dict, *, via: str = "") -> tuple[dict, str | None]:
    """只改一步里给出的那几个字段（`enabled`/`mode`/`value`/`point`/`wait_ms`）。"""
    cfg = load()
    key = str(patch.get("key") or "").strip()
    if key not in STEP_KEYS:
        return cfg, f"没有 {key!r} 这一步（可用：{'、'.join(STEP_KEYS)}）"
    idx = STEP_KEYS.index(key)
    cur = cfg["steps"][idx]
    merged = dict(cur)
    for field in ("enabled", "mode", "value", "point", "wait_ms"):
        if field in patch:
            merged[field] = patch[field]
    d = STEP_BY_KEY[key]
    if "mode" in patch and merged["mode"] != cur["mode"]:
        # 换做法时把**这次没一起传**的组合键/等待回到该做法的出厂值 —— 留着上一种做法的键值
        # 就是"看着配了、其实按的是上一个键"。但调用方显式传了值（含空串）就不能覆盖，
        # 否则"清空快捷键"这个动作永远做不到，而空快捷键正该被校验拒掉。
        if "value" not in patch:
            merged["value"] = (d.get("default_value") or "") \
                if merged["mode"] in ("hotkey", "keys") else ""
        if "wait_ms" not in patch:
            merged["wait_ms"] = d["wait_default"]
    why = validate_step(merged)
    if why:
        return cfg, why
    cfg["steps"][idx] = merged
    return save(cfg, via=via), None


def reset_steps(*, via: str = "") -> dict:
    cfg = load()
    cfg["steps"] = default_steps()
    return save(cfg, via=via)


def set_guard(patch: dict, *, via: str = "") -> tuple[dict, str | None]:
    cfg = load()
    key = str(patch.get("key") or "").strip()
    if key not in GUARD_KEYS:
        return cfg, f"没有这道闸 {key!r}（可用：{'、'.join(GUARD_KEYS)}）"
    guards = dict(cfg["guards"])
    guards[key] = patch.get("value")
    why = validate_guard(guards)
    if why:
        return cfg, why
    cfg["guards"] = guards
    return save(cfg, via=via), None


def upsert_template(tpl: dict, *, via: str = "") -> tuple[dict, str | None]:
    cfg = load()
    tpls = list(cfg["templates"])
    idx = next((i for i, t in enumerate(tpls) if t.get("key") == tpl.get("key")), None)
    merged = dict(tpls[idx]) if idx is not None else dict(tpl)
    for field in ("label", "source", "text", "scenes", "script", "timeout_seconds",
                  "title", "enabled", "note"):
        if field in tpl:
            merged[field] = tpl[field]
    merged.setdefault("key", tpl.get("key"))
    why = validate_template(merged)
    if why:
        return cfg, why
    if idx is not None:
        tpls[idx] = merged
    else:
        if len(tpls) >= MAX_TEMPLATES:
            return cfg, f"内容档最多 {MAX_TEMPLATES} 条，先删一条再加"
        tpls.append(merged)
    cfg["templates"] = tpls
    return save(cfg, via=via), None


def remove_template(key: str, *, via: str = "") -> tuple[dict, str | None]:
    cfg = load()
    keep = [t for t in cfg["templates"] if t.get("key") != key]
    if len(keep) == len(cfg["templates"]):
        return cfg, f"没有键为 {key!r} 的内容档"
    cfg["templates"] = keep
    return save(cfg, via=via), None


def find_template(key: str, cfg: dict | None = None) -> dict | None:
    cfg = cfg or load()
    return next((t for t in cfg["templates"] if str(t.get("key")) == str(key)), None)
