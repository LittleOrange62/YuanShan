"""界面动作的**待人工清单** —— 系统自己动了多少次通达信的界面，哪几次没成，没成的怎么手动补。

## 为什么必须有这一份

用户 2026-10-06 20:5x 批准了"收盘时系统自己拉起并登录通达信、自己点盘后下载"，
但同一句话里加了条件：

> 并且涉及到你自己操作的步骤，最好是要保留后续我手动执行的地方，这样的话万一我当时也在操作电脑，
> 跟你的指令冲突或者误取消了，也可以我主动的去找出来，哪些没执行的我手动执行下。

这条需求的实质不是日志，是**可交接**：界面上的动作有两个手（人和自动化）会碰同一个窗口，
冲突时谁赢不保证。所以每一次"我要动界面"都必须留下三样东西 ——
我要做什么、做成了没有、**没做成时人怎么自己做到**。第三样是这份清单存在的理由：
没有它，用户只能翻源码或者猜"到底点没点上"。

## 三条规矩

1. **判"做没做成"用 verify 的结果，不用"我发了点击"。** `status='done'` 只在有证据时写
   （窗口消失了 / 登录框没了 / 条数过了门）；发出去没回音一律 `need_human`。
2. **`need_human` 不因为后面成功而被抹掉。** 人要的就是"那一次没成"这一条事实，
   抹掉它等于把冲突藏起来。同一步后来重试成功会另起一条 `done`，两条都在表上。
3. **一条不落全写会淹掉要看的那几条**，所以页面只把 `pending`（非 done）摆在前面，
   全量留在文件里可查（`gui_trail.json`，上限 `MAX_ITEMS` 条）。

落点：`<ASTOCK_DATA_HOME>\\tdx\\gui_trail.json`。
"""

from __future__ import annotations

import datetime
import json
import os
import re
import threading
from typing import Any, Optional

from .paths import data_path
from .util import atomic_write_json

FILE = "gui_trail.json"
MAX_ITEMS = 200

#: 状态只有四种，**不许合并**：
#: `done` = 有证据做成；`failed` = 做了但证据说没成（且不需要人，比如后面还有别的腿）；
#: `need_human` = 机器侧没路了，人来那一下才能补；`skipped` = 按闸门故意没做（写清为什么）。
STATUSES = ("done", "failed", "need_human", "skipped")

_LOCK = threading.Lock()

#: 每一步"人自己做要怎么做"。菜单路径与命令都来自 `app/tdx_client` 的实测注释，
#: 不是编的：菜单是自绘的，**窗口不最大化时 `TQ策略` 整项会被折叠进 `▼`**（s5 的 D 级方案）。
MANUAL = {
    "s1_launch": ("双击桌面通达信图标启动（本机是 `D:\\new_tdx64\\TdxW.exe`），"
                  "或者命令行重跑 `python -m app.tdx_client.run_agent --only s1_launch`"),
    "s2_login": ("在登录框里自己输一次账号密码登录。系统不会替你记这份口令，"
                 "所以这一步只有人能做；登录后再跑 `python -m app.tdx_client.run_agent --only s2_login` 复验"),
    "s3_popup": ("把挡在中间的公告/风险揭示/「已登录」提示逐个点掉（这些每次都长得不一样，"
                 "自动化认不全）；重跑 `python -m app.tdx_client.run_agent --only s3_popup`"),
    "s4_autosync": ("登录后客户端会自己同步一遍日线，等它写完即可 —— "
                    "判据看 `python -m duanxian.completeness --vipdoc`，不看窗口"),
    "s5_dialog": ("手动路径：**先把通达信窗口最大化**（不然菜单栏把 `TQ策略` 折叠进 `▼`），"
                  "再点 `TQ策略 → TQ数据设置 → 盘后数据下载`；"
                  "不想碰鼠标就试 `python -m app.tdx_client.tq_bridge --exec .933`（TQ 命令直接把窗叫出来）"),
    "s6_day": ("在「盘后数据下载」里勾上 **沪深京日线\\***，点「开始下载」；"
               "整段重跑用 `python -m ingest.jobs.p0_tdx_predownload --date <交易日>`"),
    "s7_minute": ("同上窗口里切到分钟线，勾 **沪深京分钟线\\*** 以及下面的"
                  "「1分钟线数据」「5分钟线数据」两个框（缺的就是这两个框没勾上），再点「开始下载」"),
    "s8_finish": ("下载完毕后点「关闭」。**别点「取消下载」** —— 那会放弃正在下的这一轮并弹"
                  "「您确定取消下载吗？」；重跑 `python -m app.tdx_client.run_agent --only s8_finish`"),
    "s9_ingest": ("命令行补：`python -m ingest.jobs.c1_day`、`python -m ingest.jobs.c3_sector`、"
                  "`python -m ingest.jobs.c16_minute --date <交易日>`"),
    "reap_sync": ("任务管理器里结束 `TPyth.exe`（或 `TPython.exe`）。"
                  "**不要结束 `TdxW.exe`** —— 那是主客户端，TQ 取数要它登录着"),
    "restart_client": ("自己关再自己开：`python -m app.tdx_client.reset` 然后 "
                       "`python -m app.tdx_client.run_agent --only s1_launch --only s2_login --only s3_popup`"),
    "ladder_wait": ("两档都没下全又不让自动重启时：自己开一次「盘后数据下载」把该勾的勾上，"
                    "跑完再看 `python -m duanxian.completeness --vipdoc`"),
    # 10-13 补：这一步从 07:25 起就一直挂着一句"这一步没有登记手动做法"，
    # 而它恰恰最需要教人点哪一格 —— 框上是「退出 / 重新登录 / 取消」三个，点错方向就是不关或重登。
    "close_client": ("通达信窗口标题栏点 X，再在弹出的「退出 / 重新登录 / 取消」里点「退出」"
                     "（点「取消」就是不关，点「重新登录」会重登一次）。窗口最小化时先在任务栏或"
                     "托盘里点回来 —— 最小化的主窗口会直接无视这个退出请求。"
                     "不想让程序在带外请退：系统配置 → 常驻循环 → 通达信进程看护，把「通达信客户端」"
                     "切成「保持运行」；只想自己答那只框：关掉「确认框由程序点退出」那一格，"
                     "程序就只发一次退出请求、框留给人点"),
    # 10-18 补：这一条从 10-16 起一直挂着「这一步没有登记手动做法」，而它恰恰是最容易让人
    # 不敢下手的 —— 桌上杵着一个黑色 cmd 窗口，谁都怕那是还在跑的东西。
    "reap_plugin_shell": ("先看清那是不是空壳：`python -X utf8 -m app.tdx_client.shells`（只读，不动任何窗口）。"
                          "它按条打出 pid 与 python 子进程数：**子进程为 0 的那几条就是脚本已经跑完、"
                          "只剩一个 conhost 的空壳，可以关**；非 0 的是还在写文件，不许关。"
                          "要关就按那一个 pid 关：`taskkill /PID <那个 pid>`，或者直接点那个窗右上角的 X。"
                          "两条红线：**绝不 `taskkill /IM cmd.exe`**（那会把你自己开着的终端一起带走），"
                          "**绝不碰 `TdxW.exe`**（那是主客户端，TQ 取数要它登录着）。"
                          "程序在判数据齐了之后自己会重试这件事（收盘链那一档），不想让它动界面："
                          "系统配置 → 常驻循环 → 通达信进程看护，关掉「收掉 AutoRun 空壳」那一格"),
}


def _plain(text: str) -> str:
    """把 `**成对粗体**` 的星号去掉，留下里面那句话。

    这一串字是直接上屏的（`TdxTrail.tsx` 里 `{x.manual}`，纯文本渲染），markdown 星号在那儿
    就是两颗挂在句子中间的星 —— 全站那条「屏幕上不许出现裸 `**`」的纪律同样管到这里。
    **只剥成对的**：连号不成对的星（比如命令里的 glob `沪深京日线*`）是数据，动了就说错话。
    """
    return re.sub(r"\*\*(.+?)\*\*", r"\1", text or "", flags=re.S)


def _now_iso() -> str:
    return datetime.datetime.now().astimezone().isoformat(timespec="seconds")


def store_path() -> str:
    d = data_path("tdx")
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, FILE)


def _load() -> list[dict]:
    try:
        with open(store_path(), encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return []
    items = data.get("items") if isinstance(data, dict) else data
    return [x for x in (items or []) if isinstance(x, dict)]


def add(*, step: str, what: str, status: str, detail: str = "",
        target: str | None = None, source: str = "server",
        supersedes: bool = False) -> dict:
    """记一条界面动作。**status 必须是 `STATUSES` 之一**，写错了宁可抛出来也不静默。

    `step` 用 `MANUAL` 里的键（s1_launch…s9_ingest / reap_sync / restart_client / ladder_wait），
    这样每条记录自动带上了"人怎么补"的那句 —— 缺了这一步的记录等于让人去猜。

    `supersedes=True`（只有 `done` 用得上）：声明「这件事后来由程序自己做完了，
    同一 `(day, step, target)` 上更早那些交人工的记录不用再麻烦人」。默认关着，
    因为默认那条例子（`pending()` 的说明）是对的：冲突事实要留着。
    """
    if status not in STATUSES:
        raise ValueError("界面动作状态只认 %s，交回的是 %r" % (list(STATUSES), status))
    item = {"at": _now_iso(), "day": datetime.date.today().isoformat(),
            "step": step, "what": what, "status": status,
            "detail": (detail or "")[:400],
            "manual": _plain(MANUAL.get(step)
                             or "这一步没有登记手动做法，见《系统配置工作指南/07-故障恢复手册》"),
            "target": target, "source": source}
    if supersedes:
        item["supersedes"] = True
    with _LOCK:
        items = _load()
        items.append(item)
        atomic_write_json(store_path(), {"saved_at": _now_iso(),
                                         "items": items[-MAX_ITEMS:]})
    return item


def items(*, limit: int = 40, days: int | None = None) -> list[dict]:
    out = _load()
    if days:
        floor = (datetime.date.today() - datetime.timedelta(days=days - 1)).isoformat()
        out = [x for x in out if str(x.get("day") or "") >= floor]
    # 读出侧两件事一起做，理由都是"旧行不能继续说旧话"：
    # ① `manual` **以登记表现为准**（`add()` 当初写的是快照，于是 10-17 那条一直念着
    #    「这一步没有登记手动做法」，而现在这一条已经登记了做法 —— 登记表才是唯一真源）；
    # ② 剥掉 markdown 星号：`TdxTrail.tsx` 用纯文本渲染，`**` 上屏就是两颗挂在句子中间的星。
    for x in out:
        step = str(x.get("step") or "")
        if step in MANUAL:
            x["manual"] = MANUAL[step]
        for key in ("manual", "detail"):
            if x.get(key):
                x[key] = _plain(str(x[key]))
    return out[-limit:][::-1]        # 最新在前：要看的永远是"刚刚发生了什么"


def pending(*, limit: int = 20, days: int = 3) -> list[dict]:
    """**待人工清单**：最近几天里所有不是 `done` 的动作，最新在前。

    同一步只留最后一次处境（`need_human` 之后又 `skipped` 说明换了原因），
    但 `done` 不算"补上了"就不抹掉前面的 `need_human` —— 那条冲突事实要留着。

    一个显式例外（10-13 加）：后来的那条 `done` 带 `supersedes` 时，说明这件事**后来由程序
    自己做完了**（现场：带外关客户端第一次没关掉、交了人工，第二次自动答了确认框并复验进程
    已退）。那种旧记录继续挂在待人工清单上是一句**假警报** —— 人会为了"补那一下"把已经关掉
    的客户端重新打开来。默认仍然保留冲突事实，只有显式带标记的才抹。
    """
    resolved = {(x.get("day"), x.get("step"), x.get("target")) for x in _load()
                if x.get("status") == "done" and x.get("supersedes")}
    seen: set[tuple] = set()
    out: list[dict] = []
    for x in items(limit=MAX_ITEMS, days=days):
        if x.get("status") == "done":
            continue
        ident = (x.get("day"), x.get("step"), x.get("target"))
        if ident in seen or ident in resolved:
            continue
        seen.add(ident)
        out.append(x)
        if len(out) >= limit:
            break
    return out


def summary() -> dict[str, Any]:
    rows = items(limit=MAX_ITEMS, days=3)
    return {"generated_at": _now_iso(), "path": store_path(),
            "count": len(rows),
            "by_status": {s: sum(1 for x in rows if x.get("status") == s) for s in STATUSES},
            "pending": pending()}
