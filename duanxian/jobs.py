"""作业配置与运行态。契约见 docs/系统配置工作指南/03-环境变量与作业配置.md。

两份文件，落在业务数据根（ASTOCK_DATA_HOME）下，不落在 .local/：
  jobs/config.json  —— 用户手写、可 diff 可回滚，只写要改的键
  jobs/state.json   —— 只有作业自己写，每轮覆盖
分开是硬要求：状态和配置混写，某次写状态会把用户的改动冲掉。

配置每轮重读，改完即生效、不重启进程。
"""
from __future__ import annotations

import datetime as dt
import json
import os
import threading
from pathlib import Path
from typing import Any

from .paths import data_path
from .util import atomic_write_json

CONFIG_FILE = "jobs/config.json"
STATE_FILE = "jobs/state.json"

#: 每个作业留最近多少条运行流水（用户 2026-10-10 要"这周红过几次"看得见）。
#: 上限刻意小：`/api/jobs` 一次要给 40 多行作业，每行都读一遍 state.json，
#: 文件从 5KB 涨到 100KB 就是把一页面板变成一次小文件服务器。
#: 12 条对日报型作业是 12 天，对 15 分钟心跳型是 3 小时 —— 够看清"最近怎么样的"。
RUNS_KEEP = 12
#: 错误摘要截多长（台账里留的是"为什么"，完整原文在文本日志那一份里）
RUN_ERROR_CHARS = 160


def config_path() -> str:
    return data_path(CONFIG_FILE)


def state_path() -> str:
    return data_path(STATE_FILE)


def data_home() -> str:
    """生效的业务数据根。任何面向人的输出都要带上它 —— 双击启动的 .cmd 不设
    ASTOCK_DATA_HOME，只报"写入 N 行"不报写到哪，等于没报。"""
    return data_path()


def _read(path: str) -> dict:
    try:
        with open(path, encoding="utf-8") as fh:
            value = json.load(fh)
    except (OSError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def load_config() -> dict:
    return _read(config_path())


def _hhmm(value: Any) -> bool:
    """`HH:MM` 的唯一判法（上海时区的时刻都过这一个口，别在循环里再写第二把尺）。"""
    try:
        hh, mm = str(value).split(":")
        int(hh), int(mm)
        return 0 <= int(hh) <= 23 and 0 <= int(mm) <= 59 and len(str(value)) == 5
    except (ValueError, TypeError):
        return False


def _valid(key: str, value: Any, want: Any) -> str | None:
    """返回 None 表示可用，否则返回一句人话说明为什么不用。"""
    if isinstance(want, bool):
        return None if isinstance(value, bool) else f"{key} 需要 true/false，收到 {value!r}"
    # 行情外链地址那一组（`quote_links`）：默认值本身就带 `******` 占位，所以"这是不是模板"
    # 一眼能认出来。判据只住 `app/quote_link.bad_template` 一处 ——
    # 拼不出地址的串存进去，等于全站股票名一起变死链。
    if isinstance(want, str) and "******" in want:
        try:
            from app.quote_link import bad_template
            return bad_template(value)
        except Exception:  # noqa: BLE001  出厂表读不到时至少挡住最明显的那些
            s = str(value or "").strip()
            return None if s.startswith("https://") and s.count("******") == 1 \
                else f"{key} 需要恰好含一个 ****** 的 https 地址，收到 {value!r}"
    if key == "run_at":
        return None if _hhmm(value) else f"run_at 需要 HH:MM（上海时区），收到 {value!r}"
    # 重试阶梯的时点表（用户 2026-10-08 要"每个重试时间都可设置，也可以添加和减少次数"）。
    # 判**整串**而不是逐格：一格里写坏了整条阶梯的读法就会不一致，所以在这里一次说清是哪格。
    if key == "times":
        import re
        cells = [c.strip() for c in str(value).split(",") if c.strip() != ""]
        if not cells:
            return "times 至少要留一档（不想重试请把阶梯关掉，别留个空的）"
        if len(cells) > 12:
            return f"times 最多 12 档（再多就只是在反复敲同一个上游），收到 {len(cells)} 档"
        bad = [c for c in cells if not re.fullmatch(r"([01]?\d|2[0-3]):([0-5]\d)", c)]
        if bad:
            return f"times 里这些不是 HH:MM：{'、'.join(bad)}"
        return None
    # 用户 2026-10-08 要的第二档："这个定时任务可以选择每周、每日这样的选项，时间也可以修改"。
    # 全仓原先只有 `run_at`（每日时点）与间隔型两种，没有"每周"—— 加在这里而不是某条循环里，
    # 是为了让宿主判"今天该不该跑"与面板显示节奏读的是同一份（两处各判迟早打架）。
    if key == "run_freq":
        return None if str(value) in ("daily", "weekly") else \
            f"run_freq 只接受 daily / weekly，收到 {value!r}"
    # 客户端的两档（用户 10-13）。拼错**不回落默认**：回落会把"我打错了"显示成一份
    # 看起来正常的配置，而这两档的区别是"要不要在带外把你的客户端关掉"。
    if key == "client_mode":
        return None if str(value) in CLIENT_MODES else \
            f"client_mode 只接受 {' / '.join(CLIENT_MODES)}，收到 {value!r}"
    if key == "restart_max_per_day":
        try:
            number = int(value)
        except (TypeError, ValueError):
            return f"restart_max_per_day 需要 0~10 的整数（0 = 只查不重启），收到 {value!r}"
        return None if 0 <= number <= 10 else f"restart_max_per_day 需要在 0~10 之间，收到 {number}"
    # 时间带两格（10-19 用户点名可配）：**空串是合法值**，含义是「按收盘链推导」，
    # 不是「没填」—— 所以这里不能拿"必填"那一套去拒它（同 `dossier.codes` 留空那一课）。
    if key in ("client_band_lo", "client_band_hi"):
        text = str(value or "").strip()
        if text == "":
            return None
        if not _hhmm(text):
            return f"{key} 需要 HH:MM，或留空＝按收盘链推导，收到 {value!r}"
        return None
    if key == "restart_daily_at":
        return None if _hhmm(value) else f"restart_daily_at 需要 HH:MM，收到 {value!r}"
    if key == "weekly_days":
        text = str(value).strip()
        if text == "":
            return None
        parts = [p.strip() for p in text.split(",") if p.strip() != ""]
        if not parts or any(not p.isdigit() or int(p) > 6 for p in parts):
            return f"weekly_days 需要 0~6（0=周一）用逗号分隔，收到 {value!r}"
        return None
    if key == "wait_max_sec":
        try:
            number = int(value)
        except (TypeError, ValueError):
            return f"wait_max_sec 需要 30~1800 的整数，收到 {value!r}"
        return None if 30 <= number <= 1800 else f"wait_max_sec 需要在 30~1800 之间，收到 {number}"
    # 这几个键的**合法取值包含 0**，而 0 恰好是"完全不打折 / 不提前"那一档：
    # 偏离值设成 0 = "少一条都算不全"；launch_lead_min 设成 0 = "不提前拉起，跟着链走"。
    # 所以下面这条不许统一按"正整数"判，否则用户最想试的两档反而填不进去。
    if key in ZERO_OK_KEYS:
        limit = ZERO_OK_KEYS[key]
        try:
            number = int(value)
        except (TypeError, ValueError):
            return f"{key} 需要 0~{limit} 的整数，收到 {value!r}"
        if not 0 <= number <= limit:
            return f"{key} 需要在 0~{limit} 之间，收到 {number}"
        return None
    if isinstance(want, int):
        try:
            number = int(value)
        except (TypeError, ValueError):
            return f"{key} 需要整数，收到 {value!r}"
        if number <= 0:
            return f"{key} 需要正整数，收到 {value!r}"
        return None
    # 字符串键以前一律放行（`return None`）：那等于"页面上能填任何字，代码读到什么算什么"。
    # 现在登记的这几个只认列出的取值，写错当场拒 —— 拼错一个字母就"配了个值但没人按它跑"，
    # 是这一层最难查的假生效（`sync_mode=fulll` 会静默按增量跑，然后同花顺里删掉的票删不掉了）。
    if key in ENUM_KEYS:
        choices = ENUM_KEYS[key]
        text = str(value).strip().lower()
        return None if text in choices else (
            f"{key} 只接受 {'/'.join(choices)} 之一，收到 {value!r}")
    return None


#: 只认枚举值的字符串键。值一律按小写存；含义写在用它的模块里（`watchlist_sync`）。
ENUM_KEYS = {"sync_mode": ("full", "append")}


#: 允许取 0 的数值键与各自的上限（0 的语义写在上面的注释里）。
#: 上限只防"多打一个 0"这种数量级手滑；**偏离值填到不小于基线时那道门等于没有**，
#: 这件事由 `completeness.judge()` 在结论里出声说明，不靠这里猜一个"合理上限"。
#: `sync_reap_after_min` **不在这里**：它设成 0 意味着"刚起就收进程"，正在下的那批会被打断，
#: 所以那一键仍按"正整数"判。
ZERO_OK_KEYS = {"deviation": 100000, "deviation_minute": 100000, "launch_lead_min": 240}


def news_defaults() -> dict:
    """快讯那条的默认值。**数字不住在这里**，住 `duanxian/newswire.py`（唯一真源）；
    这里只是把它整理成 `settings()` 认的那张表，让 `vr/jobsboard.py` 与 `server.py`
    两处读到同一份 —— 两处各写一个 30，迟早打架。"""
    from . import newswire as _nw

    return {"enabled": True, "interval_seconds": _nw.POLL_SECONDS,
            "backfill_minutes": _nw.BACKFILL_MINUTES}


def settings(name: str, defaults: dict) -> tuple[dict, list[str]]:
    """合并出该作业本轮生效的配置。非法值**出声并回退默认**，绝不静默采用。"""
    raw = load_config().get(name)
    if raw is None:
        return dict(defaults), []
    if not isinstance(raw, dict):
        return dict(defaults), [f"{name}: 配置值应为对象，实际 {type(raw).__name__}，全部按默认"]
    out, warns = dict(defaults), []
    for key, value in raw.items():
        if key.startswith("_"):
            continue        # `_changed_at` / `_changed_by` 是留痕，不是配置项（别报"未知配置项"）
        if key not in defaults:
            warns.append(f"{name}: 未知配置项 {key!r}，已忽略（可用项：{', '.join(sorted(defaults))}）")
            continue
        why = _valid(key, value, defaults[key])
        if why:
            warns.append(f"{name}: {why}，按默认 {defaults[key]!r} 运行")
            continue
        want = defaults[key]
        # bool 是 int 的子类：不加 `not isinstance(want, bool)`，enabled=false 会被读成 0
        out[key] = int(value) if isinstance(want, int) and not isinstance(want, bool) else value
    return out, warns


def loop_enabled(env_var: str, name: str, defaults: dict, *, pytest_block: bool = True) -> bool:
    """常驻循环"这一轮该不该干活"的**唯一读法**：测试进程硬挡 + 环境变量总闸 + 作业配置 `enabled`。

    10-07 之前这条读法在 `server.py` 里有三份拷贝（`_loop_enabled` / `_watchlist_enabled` /
    `_fundflow_enabled`），`vr/watchtower.py` 里还有第四份：改一处不会传到另三处，
    而"页面上关了、线程照跑"这类假生效正是从这种分叉长出来的。合成本函数后，
    `intraday`/`news`/`watchlist`/`fundflow`/`coverage`/`tdx_watch`/`watchtower`
    与 `daily_close` 都走这**一份**判据。

    读不到配置时**照原样跑**：观测层坏了不该把采集停掉；但也不许反过来——
    `daily_close` 那次"页面关了、宿主按默认继续跑"就是两边各读各的读法造成的。

    `pytest_block=False` 只给**另有线程洞**的循环用（`watchtower` 的洞在
    `ensure_started()` 里，测试进程硬挡那一层已经挡住了真起线程）。把两道挡同时压在
    `enabled()` 上会让"直接调 `_loop()` 喂假行情"的既有测试一轮都跑不到——
    而它们验的正是坏轮怎么出声、怎么记账（10-08 实测红了三条）。
    **洞只留一个，但必须留在起线程那一层，不是留在每一轮开头。**
    """
    if pytest_block and os.environ.get("PYTEST_CURRENT_TEST"):
        return False
    if os.environ.get(env_var, "").strip().lower() in ("0", "false", "off", "no"):
        return False
    try:
        merged, _warns = settings(name, defaults)
        return bool(merged.get("enabled", True))
    except Exception as exc:  # noqa: BLE001
        print(f"⚠️ 读 {name} 作业配置失败（照原样跑，不停循环）：{type(exc).__name__}: {exc}")
        return True


def write_setting(name: str, via: str = "", **kv: Any) -> dict:
    """写回配置。只动点名的那个作业与点名的键，其余原样保留。

    **每次写都留痕**（`_changed_at` / `_changed_by`）：`enabled` 这种键一旦被人写过
    `false`，就会一直 `false` 到有人再打开为止 —— 默认值救不回来。没有留痕的话，
    "作业为什么没跑"最后只能变成互相猜（09-28 那天同一个作业被关了三次，谁也说不清是谁）。
    `via` 由调用方给：命令行给 `cli:jobs.py disable`，接口给 `api:<路径>(<来源 IP>)`。
    """
    cfg = load_config()
    job = cfg.get(name)
    job = dict(job) if isinstance(job, dict) else {}
    for key, value in kv.items():
        if value is None or value == "":
            job.pop(key, None)        # 清空 = 交还给默认值，而不是写个空串进去
        else:
            job[key] = value
    stamp = dt.datetime.now(dt.timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S")
    job["_changed_at"] = stamp
    job["_changed_by"] = via or "未知调用方"
    cfg[name] = job
    if not atomic_write_json(config_path(), cfg):
        raise OSError(f"作业配置写入失败：{config_path()}")
    return cfg


def set_enabled(name: str, on: bool, via: str = "") -> dict:
    return write_setting(name, via=via, enabled=on)


def change_note(name: str) -> dict:
    """这个作业的键是谁、什么时候改的。没留痕就照实说没留痕（旧写入没有这两个键）。"""
    job = load_config().get(name)
    job = job if isinstance(job, dict) else {}
    return {"changed_at": job.get("_changed_at"), "changed_by": job.get("_changed_by")}


# `note` 是**人写的理由**（"谁、为什么停一天"），配合 `manage.py doctor` 的「收盘链自动开关」项：
# 停用而不写理由，页面上就只剩一个"已停用"，与被谁误关分不开。
# 它与 `_changed_at` / `_changed_by`（每次写入自动盖章）是互补的两件事：
# 留痕回答"谁在什么时候从哪条路改的"，note 回答"为什么"。
DAILY_CLOSE_DEFAULTS = {"enabled": True, "run_at": "15:59", "note": "",
                        # ① 提前几分钟把通达信拉起并登录（0 = 不提前，跟着链走）。
                        # 为什么要有这一档：客户端登录之后自己还要同步 7~10 分钟数据，
                        # 15:59 才拉起的话 ③b（TQ 直取）撞上的正是"它自己正在写 vipdoc"那一段。
                        "launch_lead_min": 0,
                        # ② 只在交易日执行（默认开）。关掉 = 每天都发一次心跳去跑，
                        # 用来处理"日历没覆盖到的年份"（例如 2027 年日历还没落库时）。
                        # ⚠️ 关掉它不会让采集变准：段脚本自己按日历拒写（`fundflow` rc 3），
                        # 非交易日只会多打一次网络请求，不会写出一场假数据。
                        "trading_days_only": True,
                        # ③ 链收工后要不要把通达信客户端一起关掉（用户 2026-10-08 要加的开关）。
                        # **默认关**：客户端保持登录着是历史决定 —— TQ 那条腿要它在、
                        # 次日自动登录也省一截；而误杀正在写 vipdoc 的客户端会把整场盘后数据掐断。
                        # 打开后仍守三条：加工段真跑完才动、有同步/下载在写不动、
                        # 只发一次"关窗"请求，收不掉就记成需要人工（绝不 /F 强杀主程序）。
                        "close_client_after": False}
FILL_ALL_DEFAULTS = {"enabled": True, "note": ""}

# 条数完整性（判"这一场的数全不全"）的两个偏离值。**默认值只住这里一处**：
# `duanxian/completeness.py` 读它，页面改它，谁都不许再写第二份数字。
# 分钟类单开一档是因为实测相邻两场的停牌波动就能到 107~143 个文件（30 会天天误报），
# 证据表在 `completeness.py` 的模块文档里。
COMPLETENESS_DEFAULTS = {"deviation": 30, "deviation_minute": 200, "note": ""}

# 采集类两条（数据获取接线）。默认值同样**只住在这里**：宿主读 `enabled`/`run_at`，
# `/api/jobs` 与 `scripts/jobs.py` 从 `CLOSE_DEFAULTS` 列键。哪里再写一份默认值，
# 就会出现"页面改得动、代码不读"——那是最难查的一类假生效。
FUNDFLOW_DEFAULTS = {"enabled": True, "run_at": "20:50"}     # 个股主力资金流（新浪，只给当天）
# `sync_mode`（用户 2026-10-07 要的一档）：
#   full   —— **默认**，以同花顺本机 `stockblock.ini` 为准：ini 里删掉的组/成员，库里也删。
#   append —— 增量添加：只把 ini 里有的加进来，库里已有的同花顺已经不要的那些**留着不删**。
# 两条都是"以那份 ini 为源"，差别只在删不删。选 append 的代价要看得见：
# 库里会慢慢积累"同花顺已经删掉但这里还在"的票，自选 ⊃ 持仓 这条包含关系看起来成立，
# 其实那份真源已经不管它们了 —— 所以每次跑完都会把"这一轮没删掉几条"记进状态。
WATCHLIST_DEFAULTS = {"enabled": True, "run_at": "20:40", "sync_mode": "full", "note": ""}


def valid(key: str, value: Any) -> str | None:
    """校验一个作业/配置键的取值，返回 None 表示可用、否则一句人话。

    公开这一层是给接口/页面用的：校验规则只许住在 `_valid` 一处，
    调用方各写一份正则，就会出现"CLI 收的值接口拒、接口收的值宿主不读"。

    默认值从**所有在册表**里找（收盘链每一段 + 条数完整性），所以新加一张表不会
    莫名变成"这个键我永远说它非法"。
    """
    want: Any = True
    for spec in list(CLOSE_DEFAULTS.values()) + [COMPLETENESS_DEFAULTS,
                             QUOTE_LINK_DEFAULTS]:
        if key in spec:
            want = spec[key]
            break
    return _valid(key, value, want)


def set_setting(name: str, via: str = "", **kv: Any) -> tuple[str | None, dict]:
    """页面/CLI 改一条作业的任意在册键（时点之外的数值与开关走这里）。

    先整体校验再落盘：**一个键非法就整笔不写**，否则半改完的状态最难查
    （`launch_lead_min=500` 与 `trading_days_only=false` 一起提交时，
    只写进去后半句会让页面上显示的数字与实际跑的不是同一份）。
    返回 `(错误或 None, 新状态)`。
    """
    registry = dict(CLOSE_DEFAULTS)
    registry["completeness"] = COMPLETENESS_DEFAULTS
    registry["quote_links"] = QUOTE_LINK_DEFAULTS      # 行情外链地址（一组参数，不是作业）
    defaults = registry.get(name)
    if defaults is None:
        return (f"未知配置项「{name}」：在册的是 " + "、".join(sorted(registry)), {})
    for key, value in kv.items():
        if key not in defaults:
            return (f"「{name}」没有键 {key!r}：可用的是 "
                    + "、".join(k for k in defaults if not k.startswith("_")), {})
        if key == "note":
            continue                       # 长度与措辞由 set_note 那侧统一管
        if value is None or value == "":
            continue        # 清空＝交还默认，下面 `write_setting` 会把这个键弹出；
                            # 这一档不许被取值校验当成"不是整数"拒掉，否则"改坏了想撤回"撤不了
        why = _valid(key, value, defaults[key])
        if why:
            return (f"{why}；这一笔整条没写入，仍按原值运行", {})
    # 时间带是**一对**，单键校验看不出方向：`lo >= hi` 会被读成"整天都不在带内"，
    # 屏上「带外主动关掉」那句照样通顺，而客户端再也不开着 —— 这种"看着对其实错"必须在写面拒。
    # 只有一格填了另一格空着不判（空＝那一侧仍按收盘链推导，方向本来就说不死）。
    if name == "tdx_watch" and any(k in kv for k in ("client_band_lo", "client_band_hi")):
        cur, _ = settings(name, defaults)
        lo = str(kv.get("client_band_lo", cur.get("client_band_lo") or "")).strip()
        hi = str(kv.get("client_band_hi", cur.get("client_band_hi") or "")).strip()
        if lo and hi and lo >= hi:
            return (f"时间带要求开始早于结束（现在 {lo} ~ {hi}）；填反了会被读成「整天都不在带内」，"
                    f"客户端就再也不会开着。这一笔整条没写入，仍按原值运行", {})
    write_targets = {k: (None if v == "" else v) for k, v in kv.items()}
    merged_before, _ = settings(name, defaults)
    write_setting(name, via=via, **write_targets)
    merged, warns = settings(name, defaults)
    changed = {k: merged.get(k) for k in kv if merged.get(k) != merged_before.get(k)}
    return (None, {"name": name, "changed": changed, "merged": merged, "warnings": warns,
                   "defaults": {k: v for k, v in defaults.items() if not k.startswith("_")}})


def run_at(name: str = "daily_close", env_var: str = "DAILY_CLOSE_AT",
           defaults: dict | None = None) -> tuple[str, str]:
    """收盘链该跑的时刻，返回 `(HH:MM, 这个值是从哪来的)`。

    **优先级只有这一份**：作业配置 `run_at` > 环境变量 `DAILY_CLOSE_AT` > 默认。
    宿主和 `/api/jobs` 都调它 —— 两边各读各的，页面就会显示一个时刻、实际按另一个跑。

    读的是 `load_config()` 的**原值**而不是 `settings()` 的合并值：合并值里
    "没配"与"配了默认"长得一样，那样环境变量永远被默认的 15:59 挡在前面，
    等于这条覆盖通道存在却永远不生效。

    第二个返回值必须一起交出去：时点是配出来的，"现在是多少、谁说了算"是页面上
    要看的事实；不然出错只能靠人猜是哪一层盖了哪一层。
    """
    base = defaults or DAILY_CLOSE_DEFAULTS
    raw = load_config().get(name)
    knob = raw.get("run_at") if isinstance(raw, dict) else None
    if knob is not None:
        if _valid("run_at", knob, base["run_at"]) is None:
            return str(knob), f"作业配置 {name}.run_at"
        # 非法值**出声并回落**，绝不静默采用：`"25:99" <= hhmm` 恒假 =
        # 配了个值、链从此永不触发，而页面看着像"今天还没到点"。
        env = os.environ.get(env_var, "").strip()
        if env and _valid("run_at", env, base["run_at"]) is None:
            return env, f"环境变量 {env_var}（{name}.run_at={knob!r} 非法，已忽略）"
        return str(base["run_at"]), f"默认值（{name}.run_at={knob!r} 非法，已忽略）"
    env = os.environ.get(env_var, "").strip()
    if env:
        if _valid("run_at", env, base["run_at"]) is None:
            return env, f"环境变量 {env_var}"
        return str(base["run_at"]), f"默认值（{env_var}={env!r} 非法，已忽略）"
    return str(base["run_at"]), "默认值（没配 run_at）"


def state() -> dict:
    """读 `jobs/state.json`。**同一份文件没变就别重解析**（按 mtime+size 缓存）。

    `/api/jobs` 一次要给 40 多个作业，每个都调这里；自从 10-10 把运行流水（`runs`）
    也记进这份文件，文件从 5KB 变成几十 KB，不缓存就是每次打开面板多做几十遍全量解析。
    ⚠️ 交出去的是**浅拷贝**：`record()` 会改返回值再写盘，直接把缓存对象给它，
    万一那次写盘失败（状态写失败按设计不抛），缓存里就留着"盘上没有的那一笔"了。
    """
    import os
    path = state_path()
    try:
        st = os.stat(path)
        key = (st.st_mtime_ns, st.st_size)
    except OSError:
        _STATE_CACHE.clear()
        return _read(path)
    if _STATE_CACHE.get('key') == key:
        return dict(_STATE_CACHE['data'])
    data = _read(path)
    _STATE_CACHE['key'], _STATE_CACHE['data'] = key, data
    return dict(data)


_STATE_CACHE: dict = {}


# ---------------------------------------------------------------- 收盘链的形状与互斥
# 这份表住在数据层而不是宿主里，因为**两个入口都要读它**：常驻宿主的 15:59 循环、
# `/api/jobs` 的手动触发。各存一份的话，页面按 A 的超时/模块跑、宿主按 B 跑。
#
# ⚠️ **不变量：段超时必须大于段内任何一步自己的预算**，否则会出现最坏的组合 ——
# 某一步正按它自己的合法预算慢慢跑（`d1_indicators --recent 5` 实测 1002 秒），
# 宿主先到点把它掐了，然后按 `hold` 在下一档**从头再跑一遍整段**。那既浪费一轮，
# 又会把"其实正在正常完成"记成一次失败。2026-10-06 加固单步预算时顺手把这一条写死，
# 回归见 `tests/test_auto_execution_hardening.py::test_段超时容得下单步预算`。
CLOSE_CHAIN: dict[str, dict] = {
    "daily_close": {
        "label": "收盘即时链：判时机→查库→vipdoc/TQ→盘后下载→c1/c12/c3/c16 入库→成员快照→回验",
        "module": "ingest.jobs.daily_close_sync", "timeout": 5400,
        # `daily_close_sync` 的 3 = "数据没到位且自动手段用尽，**要人来**"
        # 4 = "数据还没到，但**今天还没绝望**"（vipdoc 与 TQ 两条都没到、且没去点窗口）。
        #    分两码是因为两者的处置完全不同：3 要人到场，4 只要等下一档再看一次。
        #    合成一个 3 的后果这两天天天在演：宿主以为"要人来"就不再回来，而通达信
        #    的盘后文件 17:2x 才落盘 —— 数据其实会到，只是比链的耐心晚。
        "rc": {0: ("run", True), 2: ("stop", True), 3: ("stop", False), 4: ("hold", False)},
    },
    "fill_all": {
        "label": "追平与加工链：网络源补齐→因子→评分→板块趋势→点火→宽度→倍量→质量审计→验收",
        "module": "ingest.jobs.e1_fill_all", "timeout": 7200,
        # `e1_fill_all` 的 3 **语义相反**：骨干（日线）是好的，只有两融/龙虎榜这类
        # 辅助源抖了。合成一个 `rc in (0, 3)` 必错一边，所以按段列表。
        "rc": {0: ("run", True), 3: ("run", True), 2: ("stop", False)},
    },
}

# 每种 stop 在页面上要说的那句话（措辞按各自的退出码文档，不美化）
CLOSE_STOP_TEXT = {
    ("daily_close", 2): "按设计跳过（未到收盘线，或它自己判定今天不是交易日）—— 与宿主的日历判据不一致，去看一眼",
    ("daily_close", 3): "数据没到位且自动手段用尽，要人来处理（输出的「要做的事」里有具体一句）",
    ("daily_close", 4): "数据还没到（vipdoc 与 TQ 两条都没到）—— 今天晚些时候还会自动再看一次",
    ("fill_all", 2): "vipdoc 还没覆盖目标日、又没开 --allow-web，按设计停在入库前 —— 要人去下载",
}

#: 收盘链每一档尝试的**最早发生时刻**（分钟，相对 `run_at`）。
#: 为什么要这么一张表，而且是硬事实不是偏好：2026-09-28/29 两天实测，通达信的盘后文件
#: 要到 **17:2x** 才落盘，而老逻辑 `hold` 是宿主循环里"下一轮(60 秒)再敲一次、上限 3 次" ——
#: 三次在两分钟内烧完，之后 `_daily_close_next` 返回 None、今天就此不再回来。
#: 换句话说：**上游晚到一天，就注定缺这一天**。这张表把"晚到"变成"等到"。
#: 最后一档 150 分 = 18:29，仍在宿主 3 小时窗口内；再晚就该由体检红灯说话（见 manage.py）。
DAILY_CLOSE_RETRY_MIN: tuple[int, ...] = (0, 25, 60, 100, 150)

#: 链自己那一轮里，**时点后这么多分钟内不去点通达信窗口**（`daily_close_sync.gui_allowed_now`）。
#: 理由不是"怕麻烦"，是 2026-09-28 那次「通达信跑到一半死机」的形状：15:59 前后客户端的
#: 登录后自动同步正往 vipdoc 写文件，这时 `reset` + 重开等于把正在跑的下载打断，
#: 反而把那一天彻底弄缺。前面几档由便宜的腿（vipdoc/TQ）顶着，到点才轮到最贵的一条。
#: 人工跑（早上、或 `--date` 补历史）不受这条限制 —— 那时没有自动同步在写。
DAILY_CLOSE_GUI_AFTER_MIN = 60


def close_attempt_due(attempt: int, minutes_since_slot: int) -> bool:
    """第 `attempt` 次尝试（1 起）现在该不该发生。超出表长 = 今天不再试。

    纯函数、只住这一处：宿主的调度循环和 `/api/jobs` 上"今天还会不会再跑"那一格
    必须给同一个答案，两份实现就会出现"页面说还会试、宿主其实已经放弃"。
    """
    if attempt < 1 or attempt > len(DAILY_CLOSE_RETRY_MIN):
        return False
    return minutes_since_slot >= DAILY_CLOSE_RETRY_MIN[attempt - 1]


def close_attempts_max() -> int:
    """今天最多试几次 —— 上限就是这张表的长度，不再另有一个魔法数字。"""
    return len(DAILY_CLOSE_RETRY_MIN)


def close_next_attempt_min(minutes_since_slot: int) -> int | None:
    """**下一次**尝试最早落在时点后第几分钟；今天不再试了返回 `None`。

    给转等待那句输出和 `/api/jobs` 的"今天还会不会再跑"共用：两处各算一份，
    就会出现页面说还会试、宿主其实已经收工（或反过来）。
    """
    for offset in DAILY_CLOSE_RETRY_MIN:
        if offset > minutes_since_slot:
            return offset
    return None

# 每段的旋钮默认值。宿主读开关、`/api/jobs` 读数、`scripts/jobs.py` 给页面列可用键，
# 三处都从这里取；各写一份 `{"enabled": True}` 的话，加一个键就要改三个地方。
#
# 2026-10-06 补两条**常驻只读循环**进册（《自动执行机制自查表》判据：不在册 = 看不见也改不动）：
# - `coverage`：18:20 重算覆盖体检。原先时点只住在 `server.py::_coverage_slot()` 的字面量里，
#   这正是"页面显示一个时刻、实际按另一个跑"的那个坑。
# - `tdx_watch`：探测通达信进程/窗口/同步子进程 + 用户点名的三件事（收赖着的 `TPyth*`、
#   下载阶梯、**收盘时自动拉起并登录**）。原先写的是"`heal()`（启动客户端、抢前台、
#   发真实键鼠）默认关、必须用户点头才接"—— **10-06 20:5x 用户点了这个头**，
#   所以上面那条红线现在由 `auto_launch`/`auto_restart` 两个开关承担，
#   代价是每一次动界面都要进 `duanxian/gui_trail.py` 的待人工清单。
COVERAGE_DEFAULTS = {"enabled": True, "run_at": "18:20", "note": ""}
# 探测循环不是"几点跑一次"，是"每隔两分钟看一眼"，所以**故意没有 run_at**
# （给它配时点 = 页面上多一个永远改不动也不生效的框）。间隔住在 `server.py` 的常量里。
#
# `sync_reap_after_min`：**用户 2026-10-06 点名要的那件事**。
# 盘后下载跑完（或跑到一半死了）之后，`TPyth*`（窗口标题是
# 「管理员: TPyth-可以获取各类字段」那个进程）经常还赖在那里不走 —— 它握着 vipdoc 的
# 文件句柄，于是"文件到底写完没有"这件事永远判不干净，而 `step_complete` 那道条数门
# 只能等它退出。用户的要求是：**它结束了你帮我关掉，或者定时去关，时间设置长一点**。
# 所以给 40 分钟（远长于一轮分钟线 7′41″ + 转档），并且只在**没有链在跑**的时候动手。
# ⚠️ 只收 `TPyth*`，**绝不动 `TdxW.exe`**：TQ 那条腿（`c12_tq_day`）就是靠主客户端登录着
# 才能取数（`tq_bridge.available()` 的判据就是 `TdxW.exe` 在不在），杀错一个就把第二腿弄瘫了。
#
# `auto_launch`：**2026-10-06 20:5x 用户明确点头**（"这个必须要你自动执行啊！太需要你去动界面了，
# 帮我搞定就行"）—— 收盘时客户端没开着就自己拉起并登录，好让 ③b（TQ 直取）有数可取。
# 同一段话里还有一条同等硬的条件：**每个自己动界面的步骤都要留下人能补的地方**，
# 所以每一次动作都进 `duanxian/gui_trail.py` 的待人工清单（页面 `/api/jobs/chain` 的 `tdx_trail`）。
# 关掉它 = 只做"查数据 + 关进程"，不碰键盘。
TDX_WATCH_DEFAULTS = {"enabled": True, "sync_reap_after_min": 40,
                      "auto_restart": True, "auto_launch": True,
                      # **客户端的两档（用户 2026-10-13 点名要的那个开关）**：
                      #   on_demand（默认）= 只在"归这条链管的时间带"里开着，带外由程序**主动关掉**；
                      #   keep_alive       = 全天候开着，每轮探测判「假死 / 停在登录框 / 连不上服务器」，
                      #                      判坏就按进程树收掉再拉起并登录（每天最多 `restart_max_per_day` 次）。
                      # 为什么默认给 on_demand：10-12 实测 `TdxW.exe` 从 15:59:22 一直挂到深夜，
                      # 而链早就收工了 —— 挂着的原因是 `close_client_after` 那一档默认关，
                      # 没人去关它。旧那一档现在只是 on_demand 的实现细节（见 `CLIENT_MODE_MEANING`）。
                      "client_mode": "on_demand",
                      # keep_alive 那一档的重启上限：夜里连着判坏会形成"收→起→登录→又判坏"的死循环，
                      # 每轮都动界面。用完就停手并写待人工清单（与阶梯"档期用完就放弃"同形）。
                      "restart_max_per_day": 3,
                      # `on_demand` 的**收尾那一步**：点 X 之后通达信会挂出「退出 / 重新登录 / 取消」
                      # （10-13 实测），只发请求就住手 ⇒ 每天都要人对着那个框点一次，"带外自动关 +
                      # 后台静默"就还是半真的。开着它 = 允许程序**只点全文等于「退出」**那一个按钮
                      # （框里出现任何 `NEVER_CLICK` 文字就不点，也绝不点「重新登录」「取消」）。
                      # 关掉 = 回到"只请退、确认框交人工"。走的是同一个温和退出路径，**不是 /F 强杀**。
                      "exit_confirm_click": True,
                      # 收了通达信 AutoRun 留下的**空壳控制台**（`cmd /k ... PYPlugins\user\Get_DayEnd.py`）。
                      # 用户 2026-10-08 点名："这个是通达信客户端的定时任务自动起来的，
                      # 你发现数据全了之后，帮我关闭进程即可。" 四道闸见 `app/tdx_client/shells.py`。
                      "reap_script_shells": True,
                      # `on_demand` 那个「时间带」现在**可以在界面上拨**了（用户 2026-10-19 点名
                      # 「加到系统配置里面去，并且允许 9 点到 19 点都可以保持在线」）。
                      # 留空＝按收盘链推导（`daily_close.run_at` 前 10 分 → 后 180 分，实测 15:49–18:59），
                      # 填了就以填的为准 —— 带内保持开着、带外才主动关，所以 09:00/19:00 就把盘中包进来了。
                      # 为什么要留"空＝推导"这个默认：这台机器的旧口径是「盘中不许长开、客户端归收盘链管」，
                      # 默认值一改就等于替用户决定盘中一直挂着行情端。
                      "client_band_lo": "", "client_band_hi": "",
                      # **每日定时重启通达信**（用户 2026-10-19 同一轮点名：「再加一个策略，
                      # 定时自动重启通达信，默认时间是下午 15 点 38 分」）。走的是唯一出口 `_restart_tdx`
                      # （链忙让路 + 下载没停不关 + 每天去重一次），不是 `watchman` 里那条没接线的旧路。
                      "restart_daily_enabled": True, "restart_daily_at": "15:38",
                      "note": ""}

#: 两档的中文措辞**只住 `vr/jobsboard.py::CLIENT_MODE_MEANING`**（面板、CLI、文档都引那一份）。
CLIENT_MODES = ("on_demand", "keep_alive")

# 盘中盯盘的 3 秒轮询（`vr/watchtower.py`，线程名 `watchtower`）。
# 它原先**不在任何在册清单里**：懒启动（第一次有人打开盯盘页才起线程）、自己往
# `state.json` 记 `watchtower`，但既不显示在常驻循环面板上、也没有开关 ——
# 10-07 梳理"到底有多少定时任务"时才发现这条：它是第 8 条循环，且是全仓唯一一条
# 每 3 秒发一次外部请求的。在册之后才能"看得见 + 关得掉"。
WATCHTOWER_DEFAULTS = {"enabled": True, "note": ""}
# 盘中快照的六个时点（`server.py::_intraday_scheduler`，线程名 `intraday`）。
# 同理：它在 `LOOP_JOBS` 里挂了名、却不在 `CLOSE_DEFAULTS` 里，面板上那条一直是
# `switchable=false`（"看不见也关不掉"）。**没有 run_at** —— 它的六个时点是常量
# `intraday.SNAPSHOT_SLOTS`，给它配一个框等于骗人。
INTRADAY_DEFAULTS = {"enabled": True, "note": ""}

# 同花顺"云端 → 本机"自选同步（用户 2026-10-08 点名）。⚠️ **默认关**：这条会动用户桌面上的客户端窗口，
# 第一次必须人在键盘前手动演练一遍（`python -m ingest.jobs.c23_ths_cloud_sync --no-close`），
# 确认它停在"起进程 / 等文件 / 收自己起的窗口"这三件事上、没有多点任何东西，再把 enabled 打开。
# `run_at` 定在 15:30：收盘链 15:59 才起，而 20:40 的 `watchlist` 读的就是这份 ini —— 同步必须在前。
THS_SYNC_DEFAULTS = {"enabled": False, "run_at": "15:30", "run_freq": "daily",
                     "weekly_days": "", "wait_max_sec": 240, "close_after": True, "note": ""}

# 加工段失败单步的**重试阶梯**（用户 2026-10-08 点名：21/22/23/隔日 1/2 点多/4 点多，
# 用完就算了等下一次收盘链）。默认开：它只碰"台账里真失败的那些步"，
# 每一档先重读台账（成功的不重跑），并与 15:59 那条链抢同一把 `fill_all` 锁。
# `times` 是逗号分隔的 HH:MM，**界面上逐条可改可增删**；早于 12:00 的算次日（跨午夜）。
STEP_RETRY_DEFAULTS = {"enabled": True, "times": "21:00,22:00,23:00,01:00,02:30,04:20",
                       "note": ""}

# 每日数据库备份（`app/ops/backup.py` 早就齐了：dump → 恢复验证 → 轮转 → 记质量规则），
# **缺的从来不是能力，是调度** —— 2026-10-12 实测 daily/weekly 两份都停在 148 小时前，
# 而 doctor 只能红着说"最近备份 148 小时"。库里是这台机器唯一的真资产，重灌要按小时计。
# 时点定在 18:40：盘后链 16:00~17:25 已经收工、18:20 的覆盖体检刚过、20:40/20:50 那两条还没到，
# 而 pg_dump 期间库是只读一致的，不挡任何人。
# 三种档由循环自己按日期选：**每月 1 号 monthly（全库 + globals + basebackup）、周六周日 weekly
# （全库）、其余 daily（核心 schema）** —— 全库那两天覆盖了月档以外的大多数兜底需求。
BACKUP_DEFAULTS = {"enabled": True, "run_at": "18:40", "note": ""}

# 消息推送（渠道与计划住 `<data home>/notify/config.json`，判据在 `notifyconfig.py`）。
# **这条循环没有 `run_at`**：它每 60 秒扫一遍计划表，"几点发"是**每条计划自己**的字段
# （`plan.run_at` + `run_freq`/`weekly_days`），给它再配一个时点就是页面上多一个
# 改了也不生效的框 —— 与 `tdx_watch` 同一条理由。间隔住在 `server.py` 的常量里。
# 默认开：一条计划都没配时它什么都不做，成本是每分钟一次读文件；
# 真要停的人可以在系统配置里关，或 `VIBE_NOTIFY=0`。
NOTIFY_PUSH_DEFAULTS = {"enabled": True, "note": ""}

# 行情外链地址（用户 2026-10-08：「针对不同市场的股票，在系统配置里做一个超链接配置，
# 除了代码以外的网址写在这里，代码用 ****** 替换」）。**这是一组参数，不是一条作业**
# （没有开关、不自己跑、不记账，同 `completeness`）：每行一个市场，值就是模板本身。
# 默认值由 `app/quote_link.py` 那一份给（判据与真值表也住那里），这里只登记可改的键 ——
# 在两处各写一遍出厂地址，就是"改了哪边都不一定生效"的老形状。
try:
    from app.quote_link import DEFAULT_TEMPLATES as _QUOTE_LINK_DEFAULTS
except Exception:  # noqa: BLE001 — 读不到出厂表就用空表：宁可这一格不在册，也不要写死一份假的
    _QUOTE_LINK_DEFAULTS = {}
QUOTE_LINK_DEFAULTS = {**{k: v for k, v in _QUOTE_LINK_DEFAULTS.items()}, "note": ""}

CLOSE_DEFAULTS = {"daily_close": DAILY_CLOSE_DEFAULTS, "fill_all": FILL_ALL_DEFAULTS,
                  "fundflow": FUNDFLOW_DEFAULTS, "watchlist": WATCHLIST_DEFAULTS,
                  "coverage": COVERAGE_DEFAULTS, "tdx_watch": TDX_WATCH_DEFAULTS,
                  "watchtower": WATCHTOWER_DEFAULTS, "intraday": INTRADAY_DEFAULTS,
                  "ths_sync": THS_SYNC_DEFAULTS, "step_retry": STEP_RETRY_DEFAULTS,
                  "backup": BACKUP_DEFAULTS, "notify_push": NOTIFY_PUSH_DEFAULTS}


def freq_allows(merged: dict, day) -> bool:
    """`run_freq` 这一档今天该不该跑：`daily` 天天到点就跑；`weekly` 只看 `weekly_days`
    （0=周一 … 6=周日，ISO 序）。**判不了就返回 True** —— 配置写坏了不该让一条循环静默停产，
    宿主会照跑，页面按 `warnings` 出声（与 `settings()` 那套"非法值出声并按默认"同一个读法）。"""
    if str(merged.get("run_freq") or "daily") != "weekly":
        return True
    raw = str(merged.get("weekly_days") or "").strip()
    if not raw:
        return True
    try:
        wanted = {int(p) for p in raw.split(",") if p.strip() != ""}
    except ValueError:
        return True
    return int(day.weekday()) in wanted

_claims: set[str] = set()
_claim_lock = threading.Lock()


def close_verdict(key: str, rc: int) -> tuple[str, bool]:
    """这段的退出码该怎么收尾：`(verdict, 是不是记成成功)`。

    verdict ∈ run（放行下一段）/ stop（今天到此为止）/ hold（失败，窗口内还要再试）。
    查不到就按最保守的读法：非零 = 没成、还要再试。124（宿主自己掐的超时）从来不在
    表里，走的正是这条 —— 它不是段脚本的语义，更不该被读成"按设计跳过"。
    """
    table = (CLOSE_CHAIN.get(key) or {}).get("rc", {})
    if rc in table:
        return table[rc]
    return ("run", True) if rc == 0 else ("hold", False)


def claim(key: str) -> bool:
    """占住这个作业的执行权；已经被占返回 False。

    **为什么要有它**：定时宿主与页面上的「立即触发」是两个入口，各跑同一个
    `python -m ingest.jobs.daily_close_sync`。那个脚本会点通达信的盘后下载窗口 ——
    两个进程同时点同一个窗口，结果是两边都判"数据没到位"，而日志看着像源坏了。
    """
    with _claim_lock:
        if key in _claims:
            return False
        _claims.add(key)
        return True


def release(key: str) -> None:
    with _claim_lock:
        _claims.discard(key)


def stage_busy() -> str | None:
    """现在有没有一段收盘链正在跑（返回正在跑的那个键，没有则 `None`）。

    给"定时收 `TPyth*` 后台进程"那件事用：**链在跑的时候不许收** ——
    那个进程就是它叫起来的，收了等于把正在下的盘后数据掐断。
    （`_claims` 是进程内的集合，所以这条只对同一个进程里的调用方有意义；
    另一个进程要判，得看 `state.json` 的 `last_run_at` 距今多久。）
    """
    with _claim_lock:
        return next(iter(sorted(_claims)), None)


def _spawn_stage(module: str, timeout: int, argv: tuple[str, ...] = ()) -> tuple[int | str, str]:
    """起一段链并等它，返回 `(退出码, 末尾一行)`。**这是测试唯一该打的洞。**

    ⚠️ **为什么要有这个洞，而不是让测试去 monkeypatch `subprocess.run`**：
    2026-10-06 实测到一次红线越界 —— 我把执行体从 `subprocess.run` 改成 `Popen`（为了能按
    进程树收孤儿）之后，`tests/test_daily_close_host.py` 里那个假 `subprocess.run` **就不再被
    调用了**，于是那套"宿主循环"测试真的起了 `ingest.jobs.daily_close_sync`，
    再往下是 `p0_tdx_predownload`，**在用户的桌面上把通达信启动了起来**
    （`TdxW.exe` pid 14800，CreationDate 17:39:19，正好是那次 pytest 的时间）。
    测试改一个执行体的内部实现，就不该有权限去动桌面。所以：
    ① 起真链的入口收成一个函数，测试打它就够；
    ② **在 pytest 进程里硬挡**，就算有人忘了打这个洞也不会去点界面。
    """
    import subprocess
    import sys

    if os.environ.get("PYTEST_CURRENT_TEST"):
        return 127, ("测试进程里不许起真实的收盘链（它会启动/点击通达信窗口）："
                     "请 monkeypatch `duanxian.jobs._spawn_stage`")
    
    # Windows 上不闪黑框
    NO_WINDOW = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
    
    proc = subprocess.Popen([sys.executable, "-m", module, *[str(a) for a in argv]],
                            cwd=str(Path(__file__).resolve().parents[1]),
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, encoding="utf-8", errors="replace",
                            creationflags=NO_WINDOW)
    lines: list[str] = []

    def _drain():
        # 读线程的存在只有一个理由：管道写满会**把子进程卡在 write 上**，
        # 那时它看起来像"卡死"，其实是没人读。
        try:
            for line in proc.stdout or []:
                lines.append(line)
        except (OSError, ValueError):
            pass

    reader = threading.Thread(target=_drain, daemon=True, name="close-stage-drain")
    reader.start()
    from duanxian.proc import kill_tree
    try:
        rc: int | str = proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        # ⚠️ 不能只杀直接子进程：段脚本里面还有一层（`c1/c3/c16/p0/run_agent`），
        # 那层变孤儿继续写库，而宿主已经记成"这段失败了" —— 第二天的对账谁也说不清是谁写的。
        note = kill_tree(proc.pid)
        rc = 124
    reader.join(timeout=5)
    tail = (("".join(lines)).strip().splitlines() or [""])[-1]
    if rc == 124:
        tail = (f"超过 {timeout} 秒未完成 → 已按进程树收掉（{note}）；"
                f"已输出 {len(lines)} 行，末尾：{tail}")[:400]
    return rc, tail


def exec_close_stage(key: str, extra: dict | None = None) -> dict:
    """跑一段收盘链的**执行体**：起子进程 → 超时按进程树收 → 按 `close_verdict` 记账。
    **调用前必须已经持有 `claim(key)`** —— 拆成两层就是因为 `/api/jobs` 要先同步回答
    409（抢不到锁就不该起后台线程），而宿主和 CLI 直接"抢到就跑"。

    真正起进程的是 `_spawn_stage`（测试打那个洞；pytest 进程里硬挡真链）。
    """
    spec = CLOSE_CHAIN[key]
    try:
        rc, tail = _spawn_stage(spec["module"], spec["timeout"])
    except OSError as exc:           # 起不来进程（路径/权限）同样是"这段没成"，不能漏出去弄死调用方
        rc, tail = 127, f"子进程起不来：{type(exc).__name__}: {exc}"
    verdict, ok = close_verdict(key, rc)
    reason = CLOSE_STOP_TEXT.get((key, rc)) or tail[:200]
    detail = {"rc": rc, "verdict": verdict, "reason": reason, "last_line": tail[:200]}
    detail.update(extra or {})
    record(key, ok=ok, detail=detail)
    return {"ran": True, "rc": rc, "verdict": verdict, "ok": ok,
            "reason": reason, "last_line": tail[:200]}


def run_close_stage(key: str, extra: dict | None = None) -> dict:
    """抢锁 + 跑一段 + 还锁。三个入口里不需要同步回答 409 的那两个走这里。

    **三个入口共用这一个执行体**（15:59 的常驻宿主、`/api/jobs` 的手动触发、
    `scripts/jobs.py run-now`）：各写一份的话，退出码的读法就有三套，
    而页面只看得见其中一套。
    """
    if not claim(key):
        return {"ran": False, "rc": None, "verdict": "busy", "ok": False,
                "reason": f"{key} 正在运行（另一个入口占着），本次未触发", "last_line": ""}
    try:
        return exec_close_stage(key, extra)
    finally:
        release(key)


#: 「只拉起并登录」这一次的参数与上限。它跑在 15:59 那条链**之前**，
#: 抢的是同一把 `daily_close` 锁 —— 两个入口同时点通达信窗口会比"没人点"更糟
#: （窗口叠两层，下一轮 s5 点的是最上面那一层）。
LAUNCH_ONLY_MODULE = "ingest.jobs.daily_close_sync"
LAUNCH_ONLY_TIMEOUT_SEC = 900


def run_tdx_launch_only(date: str = "", via: str = "host") -> dict:
    """按 `daily_close.launch_lead_min` 提前那一次：**只拉起并登录，不查库不取数**。

    不往 `state.json` 记 `daily_close` 的账 —— 那一格说的是"这段链跑没跑成"，
    拉起只是它的前置动作。这一次动过什么界面，证据在 `gui_trail`（页面上的待人工清单），
    判"成没成"用的是探针复验，不是"我发了命令"。
    """
    if not claim("daily_close"):
        return {"ran": False, "reason": "收盘链另一个入口正占着锁，这一次不碰界面"}
    try:
        argv = ("--launch-only",) + (("--date", date) if date else ())
        rc, tail = _spawn_stage(LAUNCH_ONLY_MODULE, LAUNCH_ONLY_TIMEOUT_SEC, argv)
    except OSError as exc:
        rc, tail = 127, f"子进程起不来：{type(exc).__name__}: {exc}"
    finally:
        release("daily_close")
    return {"ran": True, "rc": rc, "via": via, "last_line": (tail or "")[:200]}


def record(name: str, *, ok: bool, detail: dict | None = None) -> dict:
    """记一轮结果。**只有 ok=True 才刷新 last_ok_at**，连续失败计数由此累计。

    第三种结果：`detail['skipped']` 有值 ⇒ 这一轮是**按口径主动跳过**（未开市、
    补不了历史日、非交易日拒写……）。它既不是成功（不刷新 `last_ok_at`），
    也不是失败（`consecutive_failures` 不动）—— 否则一条按设计停下来的循环
    会在休市两天后显示"连败 200+"，那个数字就失去了意义，而真故障混在里面看不出来。
    原因照旧写进 `last_error`，让面板上还能解释这一轮为什么没动。
    """
    stamp = dt.datetime.now(dt.timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S")
    all_state = state()
    job = all_state.get(name) if isinstance(all_state.get(name), dict) else {}
    fails = int(job.get("consecutive_failures") or 0)
    skipped = bool((detail or {}).get("skipped"))
    entry = {
        "last_run_at": stamp,
        "last_ok_at": job.get("last_ok_at"),
        "last_status": "skipped" if skipped else ("ok" if ok else "fail"),
        "consecutive_failures": fails if skipped else (0 if ok else fails + 1),
        "last_error": None if (ok and not skipped) else (detail or {}).get("reason"),
    }
    if ok and not skipped:
        entry["last_ok_at"] = stamp
    if detail:
        entry["last_detail"] = detail
    # ---- 运行流水（用户 2026-10-10："台账只留最近一次，看不出这周红过几次"）
    # 字段名对齐 `/api/jobs` 的 `run_history`（前端 `HistoryBlock` 直接吃 started_at/outcome/error），
    # 所以这里存的是**接口形状**，不是内部形状 —— 少一层翻译，也就少一处会漏翻译的地方。
    # 上限 RUNS_KEEP：留的是"最近这几次怎么样"，不是档案库；要长历史请看文本日志那一层。
    runs = list(job.get("runs") or [])
    one = {"started_at": stamp, "outcome": entry["last_status"],
           "error": (str(entry["last_error"])[:RUN_ERROR_CHARS] if entry.get("last_error") else None)}
    if isinstance((detail or {}).get("written"), int):
        one["rows_written"] = int(detail["written"])
    runs.append(one)
    entry["runs"] = runs[-RUNS_KEEP:]
    all_state[name] = entry
    atomic_write_json(state_path(), all_state)   # 状态写失败不抛：它只是观测，不能反过来弄死作业
    return entry


def summary(names: dict[str, dict]) -> list[dict]:
    """给 jobs.py status 与 doctor 用的一揽子视图。"""
    st = state()
    rows = []
    for name, defaults in names.items():
        merged, warns = settings(name, defaults)
        job = st.get(name) if isinstance(st.get(name), dict) else {}
        rows.append({
            "name": name,
            "enabled": merged.get("enabled", True),
            "settings": merged,
            "warnings": warns,
            "last_run_at": job.get("last_run_at"),
            "last_ok_at": job.get("last_ok_at"),
            "last_status": job.get("last_status"),
            "consecutive_failures": job.get("consecutive_failures") or 0,
            "last_error": job.get("last_error"),
        })
    return rows
