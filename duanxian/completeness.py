"""条数完整性 —— 判「这一场的数全不全」，基线由系统自己每天记下来的条数长出来。

## 为什么换掉抽样比例

原先两处判据都是**拍下来的固定比例**：`server.py` 的下载阶梯看"随机抽样 40 只里
有 90% 尾部日期到了这天"，`duanxian/coverage.py` 看"最近一场的只数不少于上一场的 60%"。
用户 2026-10-06 把这条否了，理由是要一个**硬性、能自己跟着市场长**的规则：

> 要设置个硬性规则……因为会有新股上市，后面只会更多——最好是能动态变化，
> 每次记录总条数，近一个交易日的条数是必须大于等于前第 2 个交易日的。

拆开就是三件事，本模块一件一件落地：

1. **数整目录，不抽样**。抽样会碰上"抽到的恰好都停牌"；整目录的条数才是"今天有多少只
   被写进了这一场"的直接读数。实测 2026-09-30 那场：`.day` 命中 9,490 个文件
   （sh 4,813 / sz 4,327 / bj 350）。
2. **每判一次就把这一场的条数记进盘**（`counts.json`），下一场才有基线可比 —— 基线不是
   写死的常数，所以新股上市、退市出清都会自动跟着走。
3. **比较对象是"前一天的数量"**（用户 2026-10-07 改的：从"前第 2 个交易日"改回**前一交易日 T-1**）。
   原先跳过一场是为了防"昨天只下了半截、今天跟它比双双通过"，而这件事**下面那条
   "只登记判为全的场"已经挡住了**：半截那天进不了基线池，于是 T-1 自动回落到
   "最近一个被判为全的场次"。两条规则做同一件活，留一条就够 —— 留两条反而会
   在"连续两天事故"时把基线推到太远，比较对象都不像今天了。

## 基线不能被打毒

只数进表不等于只数能当基线：**半截那一天的条数一旦进基线池，之后每天拿它比都会"通过"**
（09-23 分钟线只有 351 只，跟它比的话连 200 只都算全）。所以本模块只认
**过了绝对下线 `FLOOR` 的那些场**当基线候选，池子里**最新那一场**就是比较对象
（2026-10-07 起；10-06 那一版取的是次新一场，为的是跳过"昨天只下了半截"，
而那一件事由"只登记判为全的场"挡住，不需要跳过一场 —— 见上面第 3 条）。
绝对下线是量级门（缺一大截必输），不是精确判据 —— 精确判据是那条 `>=`。
池子里最新一场离今天隔了几场，随结论交出 `sessions_back`：**这个数本身就是
"基线多久没更新过"的读数**，隔得久说明中间那些场都被判成半截了。

## 允许回退多少：偏离值是**条数**，不是百分比（2026-10-07 用户改判）

用户 2026-10-06 的原话是"必须大于等于"，我按口径加了一点百分比容差；
2026-10-07 他把它换成一句更好执行的规则：

> 我之前说的方法没考虑退市、停牌的，所以那个计算方法可能是不行的。
> 你就按照前一天的数量，偏离值在 30 以内就算全了（这个偏离值可以在系统配置中手动设置和修改）。

于是判据变成 `本场条数 ≥ 基线条数 − 偏离值`，**偏离值是绝对条数**，默认 30，
在「系统配置 → 条数完整性」里能改（也留了 CLI 与接口两条路，默认值只住
`duanxian/jobs.py::COMPLETENESS_DEFAULTS` 一处）。

**但 30 这一档不能全场通用 —— 分钟线扛不住。** 2026-10-07 拿本机 vipdoc 实数逐日量的：

| 口径 | 相邻两场的条数差（近 10 场实测） | 最大回退 | 给的偏离值 |
|---|---|---|---|
| `.day`（沪深京整目录） | 0 / 0 / −1 / −1 / 0 / −1 / 0 / **−7** / **−11** | −11 | **30**（余量 2.7 倍） |
| `.lc1` / `.lc5` 分钟线 | −1 / 0 / −2 / −1 / −1 / **−107** / −3 / −17 / **−25** | **−107** | **200** |
| PG `stock_minute` 只数 | 9,531→9,388（既有实测） | **−143** | 200 |
| PG `index_daily` 行数 | 174↔172 来回 | −2 | 30 |

那 107/143 只不是事故，是**当天停牌**：停牌股的 `.day` 尾部日期会停在停牌前
（`readiness.py` 那条铁律），`.lc1` 更是直接没有那天的文件。所以 09-24 那种
"分钟线少 107 个文件"是完整的一场。把分钟线的偏离值也压成 30，这一天就会判成不全
→ 收盘链天天去重启通达信重下，比半截数据更烦人。**页面与 doctor 都会把这两个数分开显示**，
改了哪一个、按哪一个判的，都在结论里说出来。

要完全不打折就把两个数都改成 0，其余逻辑一行都不用动。
"""

from __future__ import annotations

import datetime
import json
import os
import threading
from typing import Any, Iterable, Optional

from .paths import data_path
from .util import atomic_write_json

CACHE_FILE = "session_counts.json"

#: 相邻场次允许回退的**条数**（不是比例）。用户 2026-10-07 定的默认值就是 30；
#: 分钟类单独一档，因为实测停牌那天能少 107~143 个文件（见模块文档那张表）。
#: 这两个数都只是**默认** —— 真正生效的值走 `duanxian/jobs.py::COMPLETENESS_DEFAULTS`，
#: 页面/CLI 改的是那份，本模块只读它（默认值不许住两处）。
DEVIATION_DEFAULT = 30
DEVIATION_MINUTE_DEFAULT = 200

#: 基线池里最多留多少场。全市场一天一场也才 250 场/年，留 400 条够跨一年回看，
#: 又能防止这份文件无限膨胀（它每 10 分钟被写一次）。
MAX_SESSIONS_PER_KIND = 400

_LOCK = threading.Lock()

#: kind → 说明。`floor` 是"这一场的条数量级对不对"的下线，用来
#: ① 没有基线可比的头几天兜底，② 把半截那天挡在基线池外面。
#: `deviation` 是这个口径自己的**默认偏离值（条）**，可被系统配置里的
#: `completeness.deviation` / `completeness.deviation_minute` 覆盖。
#: 两列数值**全部来自实测**，不是估的：
#:   · `.day` 整目录 2026-09-30 命中 9,490（sh 4,813 / sz 4,327 / bj 350），
#:     目录内总共 9,564 个文件 —— 那天有 74 只（0.77%）尾部停在更早一天（停牌/退市）；
#:   · 10-07 逐日量了近 10 场的相邻差：`.day` 最大回退 **−11**、`.lc1/.lc5` 最大 **−107**；
#:   · PG 分钟线只数 9527 / 9531 / 9388 / 9417 / 9394 / 9398（09-22..09-30），
#:     最大回退 **−143**：停牌股那天根本没有分钟线，这是常态不是事故；
#:   · 指数 172..174 来回（最多 −2）；板块 1114..1119（最多 −1）；日线 5553..5561 只增不减。
#: **要把打折全去掉就把两个数改成 0**，其余逻辑一行都不用动（代价见模块文档）。
KINDS: dict[str, dict[str, Any]] = {
    "vipdoc_day": {"label": "通达信 .day（沪深京整目录）", "unit": "个文件", "floor": 8500,
                   "deviation": 30, "family": "day",
                   "measured": "2026-09-30 实数 9,490/9,564（sh 4,813 / sz 4,327 / bj 350）；"
                               "相邻场最大回退 −11（10-07 实测 10 场）"},
    "vipdoc_lc1": {"label": "通达信 1 分钟线 .lc1（沪深京整目录）", "unit": "个文件",
                   "floor": 8500, "deviation": 200, "family": "minute",
                   "measured": "2026-09-30 实数 9,398（sh 4,813 / sz 4,235 / bj 350）；"
                               "相邻场最大回退 −107（09-24 那天停牌多）"},
    "vipdoc_lc5": {"label": "通达信 5 分钟线 .lc5（沪深京整目录）", "unit": "个文件",
                   "floor": 8500, "deviation": 200, "family": "minute",
                   "measured": "与 .lc1 逐场同数（10-07 实测）"},
    # ---- PG 侧：与 `duanxian/coverage.py` 的 ENTITY_DAY_TABLES 同一批表 ----
    "stock_daily": {"label": "全市场日线（PG 行）", "unit": "行", "floor": 5000,
                    "deviation": 30, "family": "day",
                    "measured": "2026-09-21..09-30 为 5553/5554/5556/5557/5557/5559/5561，只增不减"},
    "stock_snapshot": {"label": "收盘快照（PG 行）", "unit": "行", "floor": 5000,
                       "deviation": 30, "family": "day",
                       "measured": "同期 5575→5578，只增不减"},
    "index_daily": {"label": "指数日线（PG 行）", "unit": "行", "floor": 150,
                    "deviation": 30, "family": "day",
                    "measured": "2026-08-01..09-30 在 172~174 之间来回（2 只指数时有时无）"},
    "sector_daily": {"label": "板块日线（PG 行）", "unit": "行", "floor": 1000,
                     "deviation": 30, "family": "day",
                     "measured": "同期 1114~1119，最大回退 1 行"},
    "stock_minute_1m": {"label": "1 分钟线（PG 只数）", "unit": "只", "floor": 8500,
                        "deviation": 200, "family": "minute",
                        "measured": "09-22..09-30 = 9527/9531/9388/9417/9394/9398，最大回退 −143"},
    "stock_minute_5m": {"label": "5 分钟线（PG 只数）", "unit": "只", "floor": 8500,
                        "deviation": 200, "family": "minute",
                        "measured": "与 1 分钟逐场相同（只数一样，行数 1/5）"},
}

#: coverage 的表 key → 本模块的 kind（两边必须用同一个判据实现，不许各写一份 `>=`）
COVERAGE_KINDS = {"stock_daily": "stock_daily", "stock_snapshot": "stock_snapshot",
                  "index_daily": "index_daily", "sector_daily": "sector_daily",
                  "stock_minute_1m": "stock_minute_1m", "stock_minute_5m": "stock_minute_5m"}


def _read_deviation(key: str, fallback: int) -> tuple[int, str]:
    """读一个偏离值，连同"这个数是从哪一层取的"。**读配置这一件事只此一处。**

    出处必须分得开"没人改过"与"有人改过"：`jobs.settings()` 是按默认补齐后交回来的，
    只看它就分不清 30 是"用户在系统配置里填的 30"还是"压根没填"。这一格存在的意义
    就是说清阈值从哪来 —— 判错一场时按"系统配置里有人写了 30"去查，会白找一个不存在的改动。
    """
    try:
        from . import jobs
        cfg, warns = jobs.settings("completeness", jobs.COMPLETENESS_DEFAULTS)
        raw = jobs.load_config().get("completeness")
        written = isinstance(raw, dict) and key in raw
    except Exception as exc:  # noqa: BLE001  配置层的问题不该让"数据全不全"跟着判不出来
        return fallback, "模块默认（作业配置读不到：%s: %s）" % (type(exc).__name__, str(exc)[:60])
    value = cfg.get(key)
    if value is None:
        return fallback, "模块默认（作业配置没这一项）"
    note = "系统配置 completeness.%s" % key if written else "默认（系统配置里没改过这一项）"
    if warns:
        note += "（有非法值已回落：%s）" % "；".join(warns)
    return int(value), note


def effective_deviations() -> dict[str, Any]:
    """当前**生效**的两个偏离值与各自出处。`summary()` 的顶层两个数走这里。

    不许退回模块常量：用户在系统配置里把 30 改成 15 之后页面上还印 30，这一格就
    自己变成了误导（10-07 写页面测试时实测到原来那份读的就是常量）。
    """
    dev, dev_from = _read_deviation("deviation", DEVIATION_DEFAULT)
    mdev, mdev_from = _read_deviation("deviation_minute", DEVIATION_MINUTE_DEFAULT)
    return {"deviation": dev, "deviation_from": dev_from,
            "deviation_minute": mdev, "deviation_minute_from": mdev_from}


def deviation_for(kind: str) -> tuple[int, str]:
    """这个口径当前生效的**偏离值（条）**，连同"这个值是从哪来的"一起交。

    默认值只住 `duanxian/jobs.py::COMPLETENESS_DEFAULTS` 一处（分钟类与其余分开，理由见模块
    文档那张实测表）；页面/CLI 改的是作业配置里的 `completeness.deviation*`，本模块只读它。
    读配置这件事不许把判据带着炸：作业配置读不到就回落到本模块默认，并把回落原因写进来源串，
    让人看得见"这一场是按哪个数判的"。
    """
    spec = KINDS.get(kind) or {}
    minute = spec.get("family") == "minute"
    key = "deviation_minute" if minute else "deviation"
    fallback = int(spec.get("deviation")
                   or (DEVIATION_MINUTE_DEFAULT if minute else DEVIATION_DEFAULT))
    return _read_deviation(key, fallback)


def _now_iso() -> str:
    return datetime.datetime.now().astimezone().isoformat(timespec="seconds")


def store_path() -> str:
    return os.path.join(data_path("completeness"), CACHE_FILE)


def _load() -> dict:
    try:
        with open(store_path(), encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def record(kind: str, day: "str | datetime.date", count: Optional[int],
           *, source: str = "vipdoc", note: str = "") -> None:
    """把这一场的总条数记进盘 —— 用户要的「每次记录总条数」。

    只记 `count` 是整数的那些次（`None` = 压根没数出来，不许进表当成 0）。
    """
    if count is None:
        return
    if isinstance(day, datetime.date):
        day = day.isoformat()
    with _LOCK:
        data = _load()
        entry = data.setdefault(kind, {})
        entry[str(day)] = {"count": int(count), "at": _now_iso(),
                           "source": source, "note": note}
        if len(entry) > MAX_SESSIONS_PER_KIND:
            for old in sorted(entry)[:len(entry) - MAX_SESSIONS_PER_KIND]:
                entry.pop(old, None)
        atomic_write_json(store_path(), data)


def history(kind: str) -> dict[str, int]:
    raw = _load().get(kind) or {}
    return {d: int(v["count"]) for d, v in raw.items()
            if isinstance(v, dict) and v.get("count") is not None}


def baseline(kind: str, day: str, *, floor: Optional[int] = None) -> dict:
    """可比的那一场 = 池子里**过了绝对下线**的最新一场（前一交易日 T-1，用户 2026-10-07 改的）。

    "半截那天当基线"这件事不靠跳过一场来防，靠的是**只登记判为全的场**（见 `judge`）：
    半截那天进不了池子，于是"最新一场"自然回落到最近一个被判为全的场次。
    所以这里取 `pool[-1]`，而 `sessions_back` 交出去的是"实际隔了几场"（正常为 1，
    出事没登记的那几天会 >1 —— 这个数本身就是"基线多久没更新过"的读数）。

    返回 `basis`：`baseline` = 有可比场次；`floor` = 头几次还没历史，只能用绝对下线。
    """
    spec = KINDS.get(kind) or {}
    floor = spec.get("floor") if floor is None else floor
    day = str(day)
    pool = sorted(d for d, n in history(kind).items()
                  if d < day and floor is not None and n >= floor)
    if not pool:
        return {"basis": "floor", "count": floor, "date": None, "sessions_back": None}
    used = pool[-1]
    # 隔了几场：按日历上"这中间应该有多少个交易日"算太贵，直接数池子里的登记场次差
    back = len([d for d in pool if d > used]) + 1
    return {"basis": "baseline", "count": history(kind)[used], "date": used,
            "sessions_back": back}


def judge(kind: str, day: "str | datetime.date", count: Optional[int],
          *, record_it: bool = True, source: str = "vipdoc") -> dict:
    """这一场到底算不算"全" —— 判据只有一条：**本场条数 ≥ 基线条数 − 偏离值（条）**。

    偏离值默认 30（用户 2026-10-07 定的），分钟类默认 200（停牌那天实测能少 107~143 个文件），
    两个数都能在「系统配置 → 条数完整性」改；取哪一个、从哪层取的，随结论一起交出。
    """
    if isinstance(day, datetime.date):
        day = day.isoformat()
    spec = KINDS.get(kind) or {}
    label, unit = spec.get("label", kind), spec.get("unit", "条")
    dev, dev_from = deviation_for(kind)
    base = baseline(kind, day)
    out: dict[str, Any] = {"kind": kind, "label": label, "unit": unit, "date": day,
                           "count": count, "baseline": base["count"],
                           "baseline_date": base["date"],
                           "baseline_basis": base["basis"],
                           "sessions_back": base["sessions_back"],
                           "deviation": dev, "deviation_from": dev_from}
    if count is None:
        out.update(complete=False, why="没数出来（读目录/查库本身失败），不当成 0 也不当成全")
        return out
    if base["count"] is None:
        out.update(complete=False,
                   why="%s 这一场 %s %s：这个口径还没登记过绝对下线，判不了" % (label, day, f"{count:,}"))
        return out
    need = base["count"] if base["basis"] == "floor" else max(0, base["count"] - dev)
    out["min_required"] = need
    if base["basis"] == "floor":
        ok = count >= need
        out["complete"] = ok
        out["why"] = ("%s %s：%s %s ≥ 绝对下线 %s —— 还没有可比基线（这个口径一条都没登记过），"
                      "只能按量级判" % (label, day, f"{count:,}", unit, f"{need:,}")
                      if ok else
                      "%s %s：%s %s < 绝对下线 %s（还没有可比基线，先按量级判）"
                      % (label, day, f"{count:,}", unit, f"{need:,}"))
    else:
        ok = count >= need
        out["complete"] = ok
        gap = base["count"] - count
        back = base["sessions_back"]
        # 「基线那天隔了几场」必须写在句子里：半截那天不进池，于是隔久了的那一场
        # 已经不是"昨天"。不说隔几场，人会把"三天前"当成"昨天"来读。
        whom = "基线 %s（隔 %s 场）" % (base["date"], back) + ("" if back == 1 else "，中间那些场没登记")
        # **偏离值填到不小于基线 = 这一项静默失效**（`need` 被 max(0,…) 压成 0，天天判"全"）。
        # 上限那一档只防手滑数量级，真要说"这道门现在等于没有"必须在这里出声。
        dead = ("｜⚠ 偏离值 %s 条已经不小于基线 %s 条，这个口径**等于不再判不全**，"
                "要恢复就把它调小" % (f"{dev:,}", f"{base['count']:,}")) if dev >= base["count"] else ""
        out["why"] = ("%s %s：%s %s，比%s 的 %s %s了 %s 条"
                      "（偏离值 %s 条，取自%s；要求 ≥%s）%s"
                      % (label, day, f"{count:,}", unit, whom, f"{base['count']:,}",
                         "多" if gap <= 0 else "少", f"{abs(gap):,}", dev, dev_from, f"{need:,}", dead)
                      if ok else
                      "%s %s：%s %s 比%s 的 %s 少了 %s 条，超出偏离值 %s 条"
                      "（取自%s；要求 ≥%s）—— **少这么多按半截处理**"
                      % (label, day, f"{count:,}", unit, whom, f"{base['count']:,}",
                         f"{gap:,}", dev, dev_from, f"{need:,}"))
    if record_it and out["complete"]:
        # **只登记判为"全"的那些场**。半截那天（09-23 的 351 只）一旦进了基线池，
        # 之后每天跟它比都会"通过"，这条规则就自己废了。绝对下线是第二道保险，
        # 这一道才是按语义筛。
        record(kind, day, count, source=source, note="complete")
    return out


# ---- vipdoc 侧：整目录计数 -------------------------------------------------

def vipdoc_counts(day: "str | datetime.date", *,
                  kinds: Iterable[str] = ("vipdoc_day", "vipdoc_lc1", "vipdoc_lc5"),
                  markets: tuple[str, ...] = ("sh", "sz", "bj"),
                  workers: int = 8) -> dict[str, int]:
    """数整个 vipdoc：**尾部记录日期已到这一天**的文件有几个。

    为什么是"尾部日期"而不是文件大小/修改时间：一只票只有真的写到那天，尾部那条才是那天
    （`readiness.py` 的同一条铁律）。半截下载的文件尾部还停在上一天，正好被排除在计数外。

    整目录一个文件一次 seek+32 字节。实测（本机、9,564 个 `.day`）：
    **冷文件缓存 ~49 秒**（sh 27.5 / sz 18.5 / bj 2.9），**热了以后 ~2.8 秒**（sh 1.45 / sz 1.22 / bj 0.1）；
    `.lc1` 冷跑一遍另加 ~50 秒。刚下完那一轮正好是"文件一直在被写、多半还在缓存里"的情形，
    但仍然要给冷跑留余地 —— 所以这里开线程并发（Windows 下文件调用释放 GIL），
    并且调用方只在 10 分钟一档的阶梯上跑它，不放进 2 分钟一次的探测循环。
    """
    from concurrent.futures import ThreadPoolExecutor

    import app.config as config
    from app.ingest import tdx_binary as tb
    from app.tdx_client.readiness import last_day_date, last_minute_dt

    target = day if isinstance(day, datetime.date) else \
        datetime.date.fromisoformat(str(day))

    def hit_day(p):
        d, _ = last_day_date(p)
        return d is not None and d >= target

    def hit_minute(p):
        stamp, _ = last_minute_dt(p)
        return stamp is not None and stamp.date() >= target

    jobs = {}
    kinds = set(kinds)
    if "vipdoc_day" in kinds:
        jobs["vipdoc_day"] = (list(tb.iter_day_files(config.VIPDOC_DIR, markets)), hit_day)
    for period, key in ((1, "vipdoc_lc1"), (5, "vipdoc_lc5")):
        if key in kinds:
            jobs[key] = (list(tb.iter_minute_files(config.VIPDOC_DIR, period, markets)),
                         hit_minute)
    out: dict[str, int] = {}
    for key, (paths, pred) in jobs.items():
        if not paths:
            out[key] = 0
            continue
        with ThreadPoolExecutor(max_workers=max(1, workers),
                                thread_name_prefix=f"count-{key}") as ex:
            out[key] = sum(1 for ok in ex.map(pred, paths) if ok)
    return out


def check_vipdoc(day: "str | datetime.date", *, record_it: bool = True,
                 markets: tuple[str, ...] = ("sh", "sz", "bj")) -> dict:
    """数一遍 + 逐口径判一遍。返回 `{"counts"、"verdicts"、"complete"、"missing":[...]}`。"""
    counts = vipdoc_counts(day, markets=markets)
    verdicts = {k: judge(k, day, counts.get(k), record_it=record_it, source="vipdoc")
                for k in counts}
    missing = [v["why"] for v in verdicts.values() if not v["complete"]]
    return {"date": str(day), "counts": counts, "verdicts": verdicts,
            "complete": bool(verdicts) and not missing, "missing": missing}


# ---- PG 侧：把基线补上，别等两天才开始判 -------------------------------------

PG_SEED = {
    # kind → (表, 日期列, 额外 where, 数量表达式)
    "stock_daily": ("market_data.stock_daily", "trade_date", "", "count(*)"),
    "stock_snapshot": ("market_data.stock_snapshot", "trade_date", "", "count(*)"),
    "index_daily": ("market_data.index_daily", "trade_date", "", "count(*)"),
    "sector_daily": ("market_data.sector_daily", "trade_date", "", "count(*)"),
    "stock_minute_1m": ("market_data.stock_minute", "dt", "and period = 1",
                        "count(distinct security_code)"),
    "stock_minute_5m": ("market_data.stock_minute", "dt", "and period = 5",
                        "count(distinct security_code)"),
}


def seed_from_pg(days: int = 8) -> dict:
    """**没有基线的头几天**从库里把最近若干场的条数捞出来登记，避免"要等两天才有基线"。

    只在某个 kind 完全没有历史时才去查它 —— 常规判定的成本不因此上升。分钟线一场
    `count(distinct)` 实测 0.65~3.7 秒，days=8 就是一次窗口查询，交给调用方在
    后台线程里跑，不放下载阶梯的等待循环里。
    """
    from . import pg

    out = {}
    for kind, (table, date_col, where, expr) in PG_SEED.items():
        if history(kind):
            out[kind] = "已有登记，跳过"
            continue
        rows = pg.fetch_df(
            "select %s::date as d, %s as n from %s t where true %s "
            "group by 1 order by 1 desc limit %s"
            % (date_col, expr, table, where, int(days)))
        if rows is None:
            out[kind] = "查库失败（库没连上或超时），基线仍为空"
            continue
        for r in rows.to_dict("records"):
            if r["d"] is None or r["n"] is None:
                continue
            record(kind, str(r["d"]), int(r["n"]), source="pg-seed", note="seed")
        out[kind] = "已登记 %d 场" % len(rows)
    return out


def summary() -> dict:
    """给页面/doctor 看的总览：每个口径最新一场、基线、生效的偏离值与结论。"""
    out = []
    for kind, spec in KINDS.items():
        h = history(kind)
        dev, dev_from = deviation_for(kind)
        if not h:
            out.append({"kind": kind, "label": spec["label"], "unit": spec["unit"],
                        "deviation": dev, "deviation_from": dev_from,
                        "latest": None, "count": None, "has_baseline": False,
                        "floor": spec.get("floor"), "baseline": None,
                        "baseline_date": None, "baseline_basis": None,
                        "sessions_back": None, "sessions": 0,
                        "measured": spec.get("measured"), "registered_note": None,
                        "note": "还没有任何一次条数登记（跑过收盘链或体检后会有）"})
            continue
        latest = max(h)
        b = baseline(kind, latest)
        out.append({"kind": kind, "label": spec["label"], "unit": spec["unit"],
                    "latest": latest, "count": h[latest], "floor": spec.get("floor"),
                    "baseline": b["count"], "baseline_date": b["date"],
                    "baseline_basis": b["basis"], "sessions_back": b["sessions_back"],
                    "sessions": len(h), "deviation": dev, "deviation_from": dev_from,
                    "measured": spec.get("measured"),
                    "registered_note": (_load().get(kind, {}).get(latest, {}) or {}).get("note")})
    # 顶层这两个数是"页面要能一眼看出默认档与分钟档不是一回事"，不是再一处判据 ——
    # 而且必须是**生效值**：读常量的那份会让"改了系统配置但页面还显示 30"变成日常（10-07 实测）。
    eff = effective_deviations()
    return {"generated_at": _now_iso(), **eff, "kinds": out}


def main(argv: Optional[list[str]] = None) -> int:
    """命令行：`python -m duanxian.completeness --summary | --seed | --vipdoc [--day 2026-09-30]`。

    为什么要有 CLI：判据一旦只活在代码里，出问题时就只能读源码。这条命令让人（和
    《故障恢复手册》）能直接问一句"今天这场到底按哪个数判的、基线是哪天"。
    """
    import argparse

    ap = argparse.ArgumentParser(description='条数完整性：登记/查看每一场的总条数')
    ap.add_argument('--summary', action='store_true', help='每个口径最新一场、基线、结论')
    ap.add_argument('--seed', action='store_true',
                    help='PG 侧没有历史的口径，从库里把最近若干场的条数补登记一次')
    ap.add_argument('--vipdoc', action='store_true',
                    help='数一遍整目录并按规则判一次（判为全才写进登记表）')
    ap.add_argument('--day', default=None,
                    help='目标交易日；默认取 readiness.expected_trade_date()')
    args = ap.parse_args(argv)
    rc = 0

    if args.seed:
        for kind, msg in seed_from_pg().items():
            print('%-16s %s' % (kind, msg))
    if args.vipdoc:
        import app.tdx_client.readiness as readiness

        day = args.day or str(readiness.expected_trade_date())
        r = check_vipdoc(day)
        for kind, v in r["verdicts"].items():
            print('%-12s %s' % (kind, v["why"]))
        print('整目录判据：%s' % ('全了' if r["complete"] else '不全'))
        rc = 0 if r["complete"] else 1
    if args.summary or not (args.seed or args.vipdoc):
        s = summary()
        print('登记落点 %s（偏离值默认 %s 条、分钟类 %s 条，改的地方是系统配置里的「条数完整性」）'
              % (store_path(), s["deviation"], s["deviation_minute"]))
        for row in s["kinds"]:
            if row["latest"] is None:
                tail = "还没有任何登记（绝对下线 %s）" % row["floor"]
            elif row["baseline_basis"] == "floor":
                tail = ("最新 %s = %s%s｜没有可比场次，按绝对下线 %s 判"
                        % (row["latest"], f"{row['count']:,}", row["unit"],
                           f"{row['baseline']:,}" if row["baseline"] else "—"))
            else:
                tail = ("最新 %s = %s%s｜基线 %s（%s，隔 %s 场；偏离值 %s 条，取自%s）"
                        % (row["latest"], f"{row['count']:,}", row["unit"],
                           f"{row['baseline']:,}", row["baseline_date"], row["sessions_back"],
                           row["deviation"], row["deviation_from"]))
            print('%-16s %-28s %s' % (row["kind"], row["label"], tail))
    return rc


if __name__ == '__main__':
    raise SystemExit(main())
