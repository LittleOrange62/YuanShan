r"""同花顺本地自选文件解析（**纯函数，不碰库不联网**）。

源不是通达信：`D:\new_tdx64\T0002\blocknew\zxg.blk` 是**扁平自选、没有分组**
（逐行 `市场位+6位代码`，如 `1600367`=sh600367）。自选分组的唯一真源是
同花顺 `<安装目录>\<用户名>\stockblock.ini`，实测本机为
`D:\hanchen\同花顺\炒股大王哈哈\stockblock.ini`（45 组 / 成员串在
`[BLOCK_STOCK_CONTEXT]`，成本与加入日在同目录 `SelfStockInfo.json`）。

三条解析规矩（每条都是实测撞出来的，不是纸面设计）：

1. **不能用 `configparser`**。① 组名里有 `%`、`:`、中文冒号，插值会炸；
   ② **用户会把策略笔记直接写进组名并换行**（实测 `141=隔夜持股法：` 后面跟 6 行
   没有 `=` 的续行），strict 解析直接抛 `ParsingError`，非 strict 会丢组；
   ③ 同一 section 里 key 可能重复，用 dict 装会吃掉前一个。
   → 手工按段取 `(key, value)` 列表并**保留原始顺序**（那是客户端左栏的顺序，
   也就是 `display_order` 的唯一来源）。多行值**只取第一行**（组名要进表格，
   第二行往后是笔记性质），但续行必须计数，不然"组名被截短了"这件事没人知道。
2. **市场号只认 `17=SH / 33=SZ / -105=BJ`，认不出就丢弃并计数**。
   实测一份文件里要丢 140 条：`48`(板块指数) 62、`36` 31、`20` 26、`49` 12、`16` 5、`-71` 4…
   猜一个市场的代价是库里多一条永远 join 不上 `reference_data.security` 的孤行 ——
   页面上表现为"有这只票，没名字没行情"。
3. **编码 GB18030 + `errors='replace'`**（实测 utf-8 解不动）。遇到坏字节宁可出替换字符，
   也不能整份文件读不出来。
"""

from __future__ import annotations

import json
import os
import re
from collections import OrderedDict
from pathlib import Path
from typing import Any, Optional

MARKET = {"17": "SH", "33": "SZ", "-105": "BJ"}      # 只认实测对上的这三个
DEFAULT_THS_DIR = r"D:\hanchen\同花顺"

_TOKEN = re.compile(r"^(-?\d+):(\d{6})$")


def ths_dir() -> Path:
    return Path(os.environ.get("FT_THS_DIR") or DEFAULT_THS_DIR)


def find_user_dir(base: Optional[Path] = None) -> Optional[Path]:
    """用户名目录不写死：判据是"里面有 `stockblock.ini`"。"""
    base = base or ths_dir()
    if not base.is_dir():
        return None
    for p in sorted(base.iterdir()):
        if p.is_dir() and (p / "stockblock.ini").is_file():
            return p
    return None


def parse_ini(text: str) -> "OrderedDict[str, list[tuple[str, str]]]":
    """手工分段解析。返回 `{段名: [(key, value), ...]}`，**顺序即语义**。

    没有 `=` 的行当作上一个值的续行**丢弃但计数**（记在 `_continuation` 段里），
    段外出现的键忽略（实测有 `encrypt_section`、`@7` 这类我们不认的段）。
    """
    out: OrderedDict[str, list[tuple[str, str]]] = OrderedDict()
    current: Optional[str] = None
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("[") and line.endswith("]"):
            current = line[1:-1].strip()
            out.setdefault(current, [])
            continue
        if current is None or "=" not in line:
            if current is not None and "=" not in line:
                out.setdefault(current + "\0续行", []).append((line, ""))
            continue
        key, value = line.split("=", 1)
        out.setdefault(current, []).append((key.strip(), value.strip()))
    return out


def parse_members(blob: str) -> tuple[list[str], dict[str, int]]:
    """`33:301308,17:603000,-105:920599` → `(['301308.SZ', '603000.SH', '920599.BJ'], 丢弃计数)`。"""
    codes: list[str] = []
    dropped: dict[str, int] = {}
    for token in (blob or "").split(","):
        token = token.strip()
        if not token:
            continue
        m = _TOKEN.match(token)
        if not m:
            dropped["格式不认识"] = dropped.get("格式不认识", 0) + 1
            continue
        mk, code = m.group(1), m.group(2)
        if mk not in MARKET:
            dropped[mk] = dropped.get(mk, 0) + 1
            continue
        codes.append("%s.%s" % (code, MARKET[mk]))
    return codes, dropped


def parse_costs(items: Any) -> dict[str, dict]:
    """`SelfStockInfo.json`（list，元素 `{C,M,P,T}`）→ `{security_code: {cost, added}}`。

    同一个文件里混着美股（实测 `M='185'`、`C='SOXX'`），市场号认不出的一律不收录。
    `P`/`T` 都可能是空串（刚加还没成交价）→ 给 None，**不填 0**：成本 0 会让
    盈亏显示成 +∞%，日期 0 会显示成 1970。
    """
    out: dict[str, dict] = {}
    if not isinstance(items, list):
        return out
    for it in items:
        if not isinstance(it, dict):
            continue
        mk, code = str(it.get("M") or ""), str(it.get("C") or "")
        if mk not in MARKET or not re.fullmatch(r"\d{6}", code):
            continue
        cost = _to_float(it.get("P"))
        added = str(it.get("T") or "").strip()
        out["%s.%s" % (code, MARKET[mk])] = {
            "cost": cost,
            "added": _iso(added) if re.fullmatch(r"\d{8}", added) else None,
        }
    return out


def _to_float(v: Any) -> Optional[float]:
    try:
        f = float(str(v).strip())
    except (TypeError, ValueError):
        return None
    return f if f > 0 else None


def _iso(yyyymmdd: str) -> str:
    return "%s-%s-%s" % (yyyymmdd[:4], yyyymmdd[4:6], yyyymmdd[6:8])


def read_watchlist(base: Optional[Path] = None) -> dict:
    """整份读通。文件缺失/读不动 → `available=False` + `reason`，不返回半成品。"""
    udir = find_user_dir(base)
    if udir is None:
        return {"available": False,
                "reason": "找不到同花顺自选目录（FT_THS_DIR=%s 下没有含 stockblock.ini 的用户目录）"
                          % (base or ths_dir())}
    path = udir / "stockblock.ini"
    try:
        text = path.read_bytes().decode("gb18030", "replace")
    except OSError as exc:
        return {"available": False, "reason": "stockblock.ini 读不出：%s" % exc}

    sections = parse_ini(text)
    names = sections.get("BLOCK_NAME_MAP_TABLE", [])
    ctx = dict(sections.get("BLOCK_STOCK_CONTEXT", []))
    cont = len(sections.get("BLOCK_NAME_MAP_TABLE\0续行", []))
    if not names:
        return {"available": False, "reason": "stockblock.ini 里没有 [BLOCK_NAME_MAP_TABLE] 段（格式变了？）"}

    dropped: dict[str, int] = {}
    groups = []
    for order, (code, name) in enumerate(names, start=1):
        members, dr = parse_members(ctx.get(code, ""))
        for k, v in dr.items():
            dropped[k] = dropped.get(k, 0) + v
        groups.append({"code": code, "name": name.strip(), "display_order": order,
                       "members": members})
    # 重复组号会让"成员挂到哪个组"变模糊 —— 实测没有，但一旦发生必须出声而不是静默合并
    dup = len(names) - len({c for c, _ in names})

    quote: dict[str, dict] = {}
    reason_quote = "SelfStockInfo.json 不存在（成本与加入日期将为 NULL）"
    sj = udir / "SelfStockInfo.json"
    if sj.is_file():
        reason_quote = "SelfStockInfo.json 已读，但没匹配到任何 A 股条目"
        try:
            quote = parse_costs(json.loads(sj.read_bytes().decode("utf-8", "replace")))
        except (OSError, ValueError) as exc:
            quote = {}
            dropped["SelfStockInfo读不出"] = 1
            reason_quote = str(exc)
    else:
        reason_quote = "文件不存在"

    return {"available": True, "reason": None, "user": udir.name, "ini": str(path),
            "ini_mtime": _mtime(path), "groups": groups,
            "group_count": len(groups), "member_rows": sum(len(g["members"]) for g in groups),
            "unique_codes": len({c for g in groups for c in g["members"]}),
            "dropped": dropped, "multiline_name_lines": cont, "duplicate_codes": dup,
            "quote": quote, "quote_source": "SelfStockInfo.json" if quote else reason_quote}


def _mtime(path: Path) -> str:
    import datetime
    return datetime.datetime.fromtimestamp(path.stat().st_mtime).isoformat(timespec="seconds")
