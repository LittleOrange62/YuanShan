"""界面状态的落库位置（`ys.ui_pref`）：页面"上次选的是什么"存在库里，不再只靠 localStorage。

用户 2026-09-28 的原话是「默认最近一次看过的票，不要总是变来变去」——
浏览器本地存储换台机器、清一次缓存就回到默认值，而在这种页面上"回到默认值"等于
**悄悄换了一只票**：用户看到的表和他上次看的不是同一只，却没有任何地方说明这件事。

判据只住这一处：
  · 键必须先在这张单子上登记（`PREFS`）。未登记的键写入直接拒 ——
    不然界面每加一个"记住上次"就自己造一个键名，库里慢慢长出一堆没人认领的行；
  · **取值按各键自己的判据校验**。代码就是 6 位数字，空串表示"没有选择"。
    存进去一个错形状的值，读回来的是"选中了一只不存在的票"，页面会显示空表，
    看上去像那只票没有数据。
  · 这张表存的是界面状态，不是用户手写的记录：清库随便清；
    真正不能丢的在 `ys.review_note`（见 `duanxian/review_notes.py`）。
"""
from __future__ import annotations

import re
from typing import Any, Optional

TABLE = "ys.ui_pref"
_KEY = re.compile(r"^[a-z][a-z0-9_]{0,30}(\.[a-z][a-z0-9_]{0,30})?$")

#: 允许出现的键与各自的取值判据。`pattern=None` 表示只限长度、不做格式校验。
PREFS: dict[str, dict[str, Any]] = {
    "scores.last_code": {"label": "趋势评分页最近选中的个股", "pattern": re.compile(r"^\d{6}$"),
                         "max": 6, "empty_ok": True},
    # 分组自选（`/grouped`）手输的那一只（用户 2026-10-14：档案上方也要能自己输代码选股，
    # 并且下次进这一页要带回这一只）。键与 `scores.last_code` **分开**：两个入口共用一个键，
    # 谁后写谁就把对方的选择悄悄换掉了 —— 那正是这个仓里"看起来有数据其实是另一只票"的老毛病。
    # 值收 6 位裸码或带交易所后缀的全长码：明细行交回的是 `600519.SH`，手输的多半是 `600519`。
    "grouped.dossier_code": {"label": "分组自选页最近要分析的那一只",
                             "pattern": re.compile(r"^\d{6}(\.(SH|SZ|BJ))?$"),
                             "max": 16, "empty_ok": True},
    # 重点关注（持仓）的显示范围（用户 2026-10-06 要"记住上次选择，存数据库"；
    # 2026-10-14 用户补一句「这里能选分组，不能选个股，你这个功能没做好」→ 加 code:<6 位裸码>）：
    # all = 全部持仓；watchlist = 只看库里的自选；group:<id> = 只看某个自选分组；
    # code:<代码> = 只看某一只。
    # 值必须带得上分组 id / 六位代码的形状，读回来一个不存在的分组只会给一张空表。
    "positions.scope": {"label": "重点关注（持仓）上次选的显示范围",
                        "pattern": re.compile(
                            r"^(all|watchlist|group:[1-9][0-9]{0,5}|code:[0-9]{6})$"),
                        "max": 20, "empty_ok": True},
}


class PrefError(ValueError):
    """键没登记、值形状不对 —— 都是调用方的错，接口按 400 交回。"""


def _check(key: str, value: str) -> str:
    if not _KEY.match(key or ""):
        raise PrefError(f"键名要形如 页面.用途（小写字母/下划线），收到「{key}」")
    meta = PREFS.get(key)
    if not meta:
        raise PrefError(f"这个键没在 duanxian/ui_pref.py 登记：「{key}」；已登记的是 "
                        + "、".join(sorted(PREFS)))
    text = (value or "").strip()
    if not text:
        if meta["empty_ok"]:
            return ""
        raise PrefError(f"「{key}」不接受空值")
    if len(text) > meta["max"]:
        raise PrefError(f"「{key}」最长 {meta['max']} 个字符，收到 {len(text)} 个")
    pattern = meta.get("pattern")
    if pattern is not None and not pattern.match(text):
        raise PrefError(f"「{key}」的取值形状不对，收到「{text}」（要求形如 {pattern.pattern}）")
    return text


def get(key: str, default: str = "") -> str:
    """读一个键；没这一行或库里读不到都交回 `default`，**不抛**（界面开局不该被状态读取拦住）。"""
    from app.db import engine

    try:
        row = engine.query_one(f"select value from {TABLE} where key = %s", (key,), role="reader")
    except Exception:  # noqa: BLE001  连不上库就按"没记过"处理，页面照常能用
        return default
    if not row:
        return default
    return str(row.get("value") or "")


def snapshot() -> dict[str, dict[str, Any]]:
    """一次交出全部已登记键的当前值（前端开局只要一个往返）。"""
    from app.db import engine

    try:
        rows = engine.query(f"select key, value, note, updated_at::text as updated_at from {TABLE}",
                            role="reader") or []
    except Exception:  # noqa: BLE001
        rows = []
    by_key = {str(r["key"]): r for r in rows}
    return {key: {"value": (by_key.get(key) or {}).get("value") or "",
                  "note": (by_key.get(key) or {}).get("note") or "",
                  "updated_at": (by_key.get(key) or {}).get("updated_at"),
                  "label": meta["label"]}
            for key, meta in PREFS.items()}


def set_value(key: str, value: str, note: str = "") -> dict[str, Any]:
    from app.db import engine

    text = _check(key, value)
    engine.execute(
        f"""insert into {TABLE} (key, value, note, updated_at)
            values (%s, %s, %s, now())
            on conflict (key) do update set value = excluded.value, note = excluded.note,
                                            updated_at = now()""",
        (key, text, (note or "").strip()[:200]), role="app")
    row = engine.query_one(
        f"select key, value, note, updated_at::text as updated_at from {TABLE} where key = %s",
        (key,), role="reader")
    if not row:
        raise RuntimeError("写完读不回这一行，不假定已生效")
    return row


def registered() -> list[dict[str, Any]]:
    """给接口/测试看：现在登记了哪些键、各自的判据。

    **按键名字典序交**，不按 `PREFS` 的字面量顺序 —— 一个对外列清单的函数
    不该因为谁在中间插了一个新键就换行序（页面/测试都按这个列表对行）。
    """
    return [{"key": k, "label": v["label"], "max": v["max"],
             "pattern": v["pattern"].pattern if v.get("pattern") else None,
             "empty_ok": v["empty_ok"]}
            for k, v in sorted(PREFS.items())]
