"""`GET /api/jobs` 的数据底。契约：docs/页面展示工作指南/03-任务调度页契约.md

三条 shaping 决定（都写进契约 §3/§4，别只留在这里）：

1. **顶层 `scheduler` 只描述本进程宿主（yuanshan-uvicorn）**。FinanceTool 登记的 39 条不走它 ——
   它们 `available=false` + `reason`，由 §4 第 2 级接住。否则会出现"本进程的作业在跑，
   却因为 FT 的开关是关的而被整页判成未调度"。
2. **`consecutive_fail=0` 不是健康**，是"从没跑过所以没失败过"。这句原样进 `reason`。
3. **`last_ok_at` 只在有真实成功证据时才给**。`operations.schedule_state.last_success_date`
   是**日期**不是墙钟时刻，硬拼一个时间就是造假 → 它进 `last_data_date`，`last_ok_at` 留 null。
"""
from __future__ import annotations

import datetime as dt
import json
import os
import subprocess
import sys
import threading
from pathlib import Path

from duanxian import jobs as jstore
from duanxian import pg as _pg
from duanxian.util import china_now

BACKUP_DIR = os.environ.get("VIBE_BACKUP_DIR") or r"D:\finance\TataPG_backup"
# 可恢复性标记（manage.py 的 doctor 写）。做成模块变量是为了测试能注入，别让它指向真文件。
READCHECK_MARKER = Path(__file__).resolve().parents[1] / ".local" / "backup_readcheck.json"

# 本进程**应该**有的常驻循环：`(线程名, 记账键, 中文名, 关掉之后会怎样)`。
# 线程名以 `server.py` 的 `threading.Thread(name=…)` 为准，记账键以
# `duanxian/jobs.py::CLOSE_DEFAULTS` / `state.json` 为准 —— **两边必须写成对**：
# 原先这里只有一份名字清单，于是"这条循环活着、但它一次账都没记过"没人能回答
# （10-06 实测 `intraday` 就是这个形状：接线在所有快照时点之后，`state.json` 里至今零笔）。
# `manage.py::declared_loops` 读的就是这张表，页面与体检因此是同一份清单。
LOOP_JOBS = (
    ("intraday", "intraday", "盘中快照（分时/榜单槽位）",
     "过了那个点就永远没有这张快照，补不回来"),
    ("newswire", "news", "快讯采集",
     "30 秒一轮的轮询停掉；已落盘的 jsonl 不受影响"),
    ("watchlist-sync", "watchlist", "同花顺自选 ini → 库内镜像",
     "自选改动不再同步进库（含周末），页面上看到的是旧镜像"),
    ("fundflow-sina", "fundflow", "个股主力资金流排行（新浪）",
     "当天不采就永久少一天（报文没有日期字段，补不回来）"),
    ("coverage-precompute", "coverage", "覆盖体检预计算",
     "首屏那 50 秒要现场等；数据本身不受影响"),
    ("daily-close", "daily_close", "15:59 收盘链（获取段 + 加工段）",
     "当天不采就永久少一天，且不会自己恢复（要人再打开）"),
    ("tdx-watch", "tdx_watch", "通达信看护（探测 + 收进程 + 下载阶梯）",
     "停了就没人收赖着的 `TPyth*`，也没人按阶梯判「全不全」之后关窗/重启；"
     "收盘链自己那三条腿照跑"),
    # 第 8 条（10-07 梳理定时任务时发现：它一直在跑、一直记账，却不在任何清单里）
    ("watchtower", "watchtower", "盘中盯盘轮询（3 秒一帧的实时快照与异动）",
     "盯盘页的实时帧不再更新、异动不再落盘；已存的历史不受影响"),
    # 第 9 条（用户 2026-10-08 点名）：启动同花顺等云端自选落盘，再收掉自己起的窗口。
    # **默认关** —— 它动的是桌面上的客户端窗口，第一次要人在键盘前手动演练过才开。
    ("ths-cloud-sync", "ths_sync", "同花顺自选云端同步（启动→等 ini 落盘→关闭）",
     "云端改的分组不会主动落到本机 ini，20:40 那条自选同步读到的就是旧 ini（页面上看不出区别）"),
    # 第 10 条（用户 2026-10-08 点名）：加工段**失败那几步**的夜间重试阶梯。
    # 没有它的话，"17:21 收工时报了失败"就是今天最后一次尝试 —— 等明天 15:59 才有人再碰。
    ("step-retry", "step_retry", "失败单步重试阶梯（默认 21:00 / 22:00 / 23:00 / 01:00 / 02:30 / 04:20）",
     "当天失败的那几步今晚不会自己再试，要等明天 15:59 那条链顺手补（或人记得去敲 --only）"),
    # 第 11 条（2026-10-12 接线）：**每日数据库备份**。`app/ops/backup.py` 的 dump→恢复验证→轮转
    # 早就写全了，缺的只有调度 —— 当天实测 daily/weekly 两份都停在 148 小时前，
    # 而 doctor 只能红着说这一句，没有人（也没有程序）去补。库里是这台机器唯一的真资产。
    ("pg-backup", "backup", "数据库备份（每月 1 号全库+basebackup、周六周日全库、其余核心 schema）",
     "本地库一旦坏掉就没有可回滚的那一份；重灌日线/分钟线要按小时计，而**已经采过的那些场补不回来**"),
    # 第 12 条（用户 2026-10-15 点名）：消息推送。渠道与计划住 `<data home>/notify/config.json`，
    # 内容一律复用页面上那些读数块（共振/筛选/自选/个股档案/持仓/市场概览），发送走 apprise。
    # **它没有 run_at**：几点发是"每条计划自己"的字段，给循环再配一个时点就是多一个改了不生效的框。
    ("notify-push", "notify_push", "消息推送（每 60 秒扫一遍定时计划，到点按选中的内容发飞书/钉钉/企微/邮件）",
     "配好的定时推送不再发出去（手机上看不到当天那份），网站与库里数据本身不受影响"),
)
HOST_LOOPS = tuple(name for name, _key, _label, _off in LOOP_JOBS)

#: **按需起**的循环：线程不是开机就起，而是第一次有人订阅盯盘时才起
#: （`vr/watchtower.py::ensure_started()`，调用点在 `vr/app.py` 的盯盘接口里）。
#: 这一条不许混进"缺"：没人盯盘的夜里那条线程**本来就不该在**，把它报成
#: 「缺 1 条 ⇒ 今天不会自己跑」是一条天天误报的红 —— 而天天误报的体检项等于没有体检项
#: （`CHAIN_WORDS`、`phase="startup"` 两次同样的教训）。但也不能一句"不算缺"糊过去：
#: 起没起要在 note 里说出来，"从没起过"那种真故障由 `manage.py::loop_inventory` 拿台账验
#: （这一格记过账 ⇒ 它确实起来过；从没记过 ⇒ 接线断了，那边照样转红）。
ON_DEMAND_LOOPS = ("watchtower",)
#: 名字必须还在 `HOST_LOOPS` 里（改名/删条会让上面那句"不许混进缺"悄悄失效）——
#: 这条由 `tests/test_jobsboard.py::test_按需起的循环在册且不许混进缺` 钉住。

#: **每轮开头重读开关**的循环。⚠️ 下面的 `server.py:行号` 只是导览，**改过 `server.py` 就会整体漂移**
#: （10-15 实测：中段插入 `notify-push` 那段之后 `:2449→:2629`、`:2401→:2581`、`:2903→:3777`）。
#: 要说谁即时生效，**以本常量与 `DEAD_UNTIL_RESTART_LOOPS` 这两份集合为准**，
#: 一致性由 `tests/test_self_heal_inventory.py`（反查线程体 `_thread_target` + `_reads_master_switch`）钉住：
#: `intraday` `:1639`、`newswire` `:1820`、`daily-close` `:2338`+`:2364`（每轮重读 `settings`，
#: 第二段还逐段重读作业开关）、`backup` `:2091`、`ths-cloud-sync` `:2629`、`step-retry` `:2709`、
#: `notify-push` `:2170`、`watchtower` `vr/watchtower.py` 的 `_loop()` 里 `enabled()`。
#: 只有这些"在面板上关掉就真的停了"。其余四条的 `_*_enabled()` **只出现在 `_start_*`**：
#: `watchlist-sync` `:1928`、`fundflow-sina` `:2047`、`coverage-precompute` `:2581`、
#: `tdx-watch` `:3777` —— 关掉之后**当前这个进程照跑到重启 8910 为止**。
#: 这是 10-07 那条"假开关"修复的残留形状：`enabled` 写进 `config.json` 是真的，
#: "下一个心跳生效"那句话对它们是假的。
LIVE_SWITCH_LOOPS = ("intraday", "newswire", "daily-close", "ths-cloud-sync", "watchtower",
                     # 第 10 条：每一轮都重新问 `_step_retry_enabled()`，面板上关掉就停手
                     # （档期与待补名单由 `ingest/jobs/step_retry.py` 判，线程只管时机）。
                     "step-retry",
                     # 第 11 条：备份每小时都不该抢在关掉之后还继续跑，所以每轮重读开关。
                     "pg-backup",
                     # 第 12 条（消息推送）：`server.py` 的循环体第一行就是
                     # `if not _notify_enabled(): sleep; continue` ⇒ 面板关掉下一个心跳就停手。
                     # ⚠️ 别照 `tdx-watch` 那样误判：它每轮读的是 `client_mode` 那个子开关，
                     # **总开关仍然只在 `_start_tdx_watch()` 里读一次**，所以它留在"要重启"那一档。
                     "notify-push")
DEAD_UNTIL_RESTART_LOOPS = tuple(n for n in HOST_LOOPS if n not in LIVE_SWITCH_LOOPS)


#: 自选同步的两档含义（`watchlist.sync_mode`，用户 2026-10-07 要的可配置项）。
#: 措辞只住这一处 + `watchlist_sync.run()` 的报告里引用同一份，别两处各写一遍。
SYNC_MODE_MEANING = {
    "full": "全量更新（默认）：以同花顺本机 stockblock.ini 为准 —— ini 里删掉的组和成员，库里也删。"
            "持仓里的票若不在 ini 里，20:40 同步后就不在自选中了（页面会点名，不静默）。",
    "append": "增量添加：只把 ini 里有的加进来，库里那些 ini 已经没有的留着不删。"
              "代价是库里会慢慢积累「同花顺已经不要但这里还在」的票，镜像就不再等于那份真源。",
}
#: 通达信客户端的两档（用户 2026-10-13 点名：「保持运行（定时检查是否假死 / 连不上服务器，
#: 连不上就重启一下程序）or 需要时才运行（有数据获取和加工的任务时才在后台运行，否则就运行…
#: 你要主动检查并关闭进程）」）。措辞只住这一处，`server.py` 的判据与面板都引这同一份。
#: ⚠️ 两档互斥，默认 `on_demand`：选 keep_alive 就等于同意"夜里也允许程序去点我的客户端"。
CLIENT_MODE_MEANING = {
    "on_demand": "需要时才运行（默认）：只在通达信归收盘链管的时间带里开着"
                 "（**带子可以在下面那两格拨**；留空＝按链推导：链前 launch_lead_min+10 分 → 链后 180 分，"
                 "实测 15:49–18:59。拨成 09:00 / 19:00 就把整个盘中包进来，用户 2026-10-19 点名要的就是这个）。"
                 "带外如果它还在跑、"
                 "链与下载阶梯都不忙、而你也有一阵没碰键盘鼠标，程序**主动请它退出**"
                 "（温和点标题栏 X，绝不 /F 强杀）。点 X 之后通达信会挂出"
                 "「退出 / 重新登录 / 取消」那只确认框（10-13 实测）——"
                 "要不要由程序替你点那一个「退出」，是下面那个开关。"
                 "⚠️ 代价要说清：带内它一直挂着（拨成 09:00–19:00 就是盘中长开），"
                 "带外你手动打开看行情，空闲够长之后它同样会被请退出"
                 "（一天只动一次，且你正在敲键盘鼠标时不动）；想让客户端全天挂着随时能用，"
                 "切「保持运行」那一档。",
    "keep_alive": "保持运行：全天候不关，每 2 分钟探一次「进程在不在 / 有没有停在登录框 / "
                  "窗口还不响应 / 交易时段里 vipdoc 还在不在往前写」，判坏就按进程树收掉 → "
                  "重新拉起 → 登录 → 清弹窗 → 复验真能取数才算成。一天最多重启 "
                  "`tdx_watch.restart_max_per_day` 次（默认 3），用完停手并写待人工清单"
                  "（否则会形成「收→起→又判坏」的死循环，每轮都动界面）。",
}
#: 时间带那一格（`tdx_watch.client_band_lo/hi`，用户 2026-10-19 点名可配）。措辞只住这一处。
CLIENT_BAND_MEANING = (
    "「需要时才运行」那档保持开着的时间带（上海时区 HH:MM）。**两格都留空＝按收盘链推导**"
    "（链前 launch_lead_min+10 分 → 链后 180 分）；只拨一格就只替换那一头。"
    "例：09:00 / 19:00 ＝ 从开盘前一小时到晚上七点一直开着（含整个盘中），带外才主动请退。"
    "⚠️ 开始必须早于结束，填反了会在保存时就拒掉（那样会被读成「整天都不在带内」，客户端再也不开）。"
    "**生效时机：这一格下一轮探测（≤2 分钟）就按新带子判，不用重启 8910** —— "
    "要重启才生效的是这条循环的总开关，不是它。")
#: 每日定时重启那一格（`tdx_watch.restart_daily_*`，用户 2026-10-19 点名，默认 15:38）。
DAILY_RESTART_MEANING = (
    "每天到这个时刻把通达信**整个重启一次**（收进程 → 重新拉起 → 登录 → 清弹窗 → 复验），"
    "走的是与下载阶梯、keep_alive 判坏**同一个出口**，不是第二套执行体。"
    "默认 15:38：排在收盘链（15:59）之前，让链拿到一台刚登录好、窗口状态干净的客户端，"
    "又排在 15:00 收盘之后，不打断盘中看盘。"
    "闸有三道：① 链正在跑就让路（不抢同一个窗口）；② 不开市的日子不空转；③ 一天只做一次。"
    "关掉这一格＝完全不做，只做看护判坏那种被动重启。"
    "**生效时机：时刻与开关都是每轮现读，改完 ≤2 分钟按新的判，不用重启 8910。**")
#: 确认框那一格（`tdx_watch.exit_confirm_click`）。措辞只住这一处，面板与文档都引这份。
#: 为什么默认开：用户 10-13 的原话是"需要时才运行…你要主动检查并关闭进程，
#: 定时任务不要有弹窗，后台静默执行"。只发一次"点 X"就住手，等于每天要人点一次「退出」，
#: 那条模式就还是半真的。为什么仍要留在界面上：这是程序**替人点按钮**，代价是"某天你正
#: 想让客户端开着、它却在你不在键盘前时自己退了"—— 关掉就回到"只请退、交人工"。
EXIT_CLICK_MEANING = (
    "自动答确认框（默认开）：请退之后如果挂着「退出 / 重新登录 / 取消」，程序只点文字全文"
    "等于「退出」的那一个按钮（绝不用子串匹配，绝不点「重新登录」「取消」；框里出现"
    "「取消下载」这类等于放弃正在下的数据的文字时一次都不点），点完重探进程确认真的退了"
    "才算成。走的仍是温和退出，不是 /F 强杀；在同步、vipdoc 还在往前写、键鼠空闲不够长时"
    "根本不会走到这一步。关掉它 = 程序只发退出请求，那只框交给你自己点。")
#: 关掉之后会怎样（面板上必须说，不然"关一下试试看"的代价没人知道）。
LOOP_OFF_EFFECT = {_key: off for _n, _key, _l, off in LOOP_JOBS}
LOOP_LABELS = {_key: label for _n, _key, label, _off in LOOP_JOBS}
#: 每条循环的时点由哪个环境变量兜底（`run_at` 的三层读法：作业配置 > 环境变量 > 默认）。
#: **没有这一项的键就是"没有时点"**（间隔型或跟段跑），页面上不许给它一个改了也不生效的框。
LOOP_SLOT_ENV = {"daily_close": "DAILY_CLOSE_AT", "fundflow": "FUNDFLOW_AT",
                 "watchlist": "WATCHLIST_SYNC_AT", "coverage": "COVERAGE_PRECOMPUTE_AT",
                 "ths_sync": "THS_SYNC_AT", "backup": "BACKUP_AT"}

#: 频率档（`run_freq`）与"每周哪几天"（`weekly_days`，0=周一…6=周日）。
#: 用户 2026-10-08 要的就是这一档：「可以选择每周、每日这样的选项，时间也可以修改」。
RUN_FREQ_CHOICES = {"daily": "每日：到点就跑（周末也跑，节假日由各自的判据拒写）",
                    "weekly": "每周：只在下面勾选的那几天跑（不勾 = 每天都跑）"}
WEEKDAY_CHOICES = [(0, "周一"), (1, "周二"), (2, "周三"), (3, "周四"), (4, "周五"),
                   (5, "周六"), (6, "周日")]

#: 节奏的文字。**有节奏表/槽位常量的条目必须写在这里**，不能让"有没有 run_at"反推节奏：
#: 反出来的那句"跟随获取段，成功才起"会贴到盯盘轮询和盘中快照身上（10-07 就是这么个错，
#: 这两条都 `has_slot=false`，面板差点把它们说成跟收盘链走）。
LOOP_CADENCE = {
    "news": "间隔型：每 30 秒一轮（`newswire.POLL_SECONDS`），没有「几点」这一说",
    "tdx_watch": "间隔型：每 2 分钟探测一次 + 下载阶梯每 10 分钟一档",
    "intraday": "槽位型：09:25 / 09:35 / 10:00 / 11:30 / 14:00 / 15:00 六个时点，到点后 6 分钟内补抓"
                "（槽位是常量 `intraday.SNAPSHOT_SLOTS`，没有单独的时刻配置项）",
    "watchtower": "间隔型：盘中每 3 秒一帧（`watchtower.POLL_SECONDS`）、非交易时段每 20 秒；"
                  "线程懒启动 —— 第一次有人打开盯盘页才起，之后常驻",
    "fill_all": "跟随获取段，段1 真跑成才起",
    "ths_sync": "到点跑（默认 15:30，收盘链 15:59 之前先把云端自选拉下来）；频率可选每日/每周，等 ini 落盘最多 wait_max_sec 秒；默认关闭 —— 要先人在键盘前演练一次再开",
    "step_retry": "多档到点跑（times 那一串 HH:MM，默认 21:00/22:00/23:00/01:00/02:30/04:20；"
                  "早于 12:00 的算次日）。每档先重读逐步台账：**只跑还挂着 fail 的那几步**，"
                  "按口径拒写（refused）与成功（ok）都不跑；到点后认 90 分钟内的迟到；"
                  "最后一档用完仍有失败就记放弃，等下一次收盘链一起补",
    "backup": "到点跑（默认 18:40，盘后链与覆盖体检都收工之后），到点后认 120 分钟内的迟到，"
              "当天背成一次就不再重跑；档由日期定：1 号 monthly（全库 + globals + basebackup）、"
              "周六周日 weekly（全库）、其余 daily（核心 schema）。每一档都带恢复验证与过期轮转",
    # ⚠️ 这条**必须写**：`cadence` 那一格没有兜底就落进 `到点跑（{slot}）/跟随获取段` 那两句，
    # 而这条循环既没有自己的 `run_at`、也不跟获取段 —— 那句会是一行编出来的节奏。
    "notify_push": "间隔型：每 60 秒扫一遍计划表（`server.NOTIFY_SCAN_SEC`）；**循环自己没有时刻**——"
                   "「几点发 / 每日还是每周几 / 只在交易日」是每条计划自己的字段。到点后认 "
                   "15 分钟内的迟到（`notifyconfig.LATE_WINDOW_MIN`），当天这一档发过就不再重跑"
                   "（发失败也算发过；要重发用面板上的「立刻发一次」）",
}


def _int_or_none(value) -> int | None:
    try:
        return None if value is None else int(value)
    except (TypeError, ValueError):
        return None


def _hhmm_plus(hhmm: str, minutes: int) -> str:
    """`HH:MM` 之后 N 分钟，跨零点也算对（页面上要写成时刻，不是"第几分钟"）。"""
    total = int(hhmm[:2]) * 60 + int(hhmm[3:5]) + minutes
    return "%02d:%02d" % (total // 60 % 24, total % 60)


def _coverage_slot_for_page() -> str:
    """覆盖体检重算的时点。**读法只有 `duanxian/jobs.py::run_at` 一份**：
    宿主按 `coverage.run_at` 跑，页面也必须按同一个值说 —— 原先这里没这一格，
    而宿主自己从 `COVERAGE_PRECOMPUTE_AT` + 字面量 `18:20` 拼，两边迟早分叉。"""
    return jstore.run_at(name="coverage", env_var="COVERAGE_PRECOMPUTE_AT",
                         defaults=jstore.COVERAGE_DEFAULTS)[0]


def _records(df) -> list[dict]:
    """DataFrame → dict 列表，**顺手把 NaN/NaT 换成 None**。

    pandas 读 NULL 会给成 `NaN`（float），`json.dumps` 遇到它就抛
    `Out of range float values are not JSON compliant` → 整个 `/api/jobs` 变 500。
    页面有 `—` 的约定，所以规范化成 null，而不是 `str(nan)` 糊成 "nan" 显示出来。
    """
    if df is None or not len(df):
        return []
    import pandas as pd

    return df.astype(object).where(pd.notna(df), None).to_dict("records")


def _text(value) -> str | None:
    """`str()` 之前先挡 NaN/NaT —— 否则页面上会出现 "nan" 这种看着像数据的字符串。
    `_json_safe` 救不了已经拼进文案的情况，所以必须在转字符串这一步就拦。"""
    if value is None:
        return None
    if isinstance(value, float) and value != value:
        return None
    text = str(value)
    return None if text in ("nan", "NaN", "NaT", "<NA>", "None", "") else text


def _json_safe(value):
    """兜底：非 DataFrame 来源（meta jsonb、文件 mtime 等）里冒出来的 NaN/Inf 也一并清掉。"""
    if isinstance(value, float) and (value != value or value in (float("inf"), float("-inf"))):
        return None
    if isinstance(value, dict):
        return {k: _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    return value


def _iso(moment: dt.datetime) -> str:
    return moment.strftime("%Y-%m-%dT%H:%M:%S+08:00")


def _mtime_iso(path: str) -> str | None:
    try:
        return _iso(dt.datetime.fromtimestamp(os.path.getmtime(path)))
    except OSError:
        return None


def _empty_counters() -> dict:
    return {"scope": "per_run", "unit": "条", "since": None, "total_seen": None,
            "written": None, "already_had": None, "dropped": None}


def _preview(**kw) -> dict:
    base = {"outbound_hosts": [], "writes": [], "touches_db": False, "est_seconds": None}
    base.update(kw)
    return base


def _expected_trade_day(now: dt.datetime) -> str | None:
    """按交易日历 + `trade_day_cutoff`（默认 15:00）算"这次数据该到哪一天"。

    切日规则必须有：盘中快照 15:00 之前当天的场次还没跑完，拿今天当基准会把
    每个上午都显示成"落后 1 个交易日"。
    """
    if not _pg.enabled():
        return None
    cutoff = "15:00"
    got = _pg.fetch_df("select value#>>'{}' as v from config.setting where key='trade_day_cutoff'")
    if got is not None and len(got) and got.iloc[0]["v"]:
        cutoff = str(got.iloc[0]["v"])
    try:
        hh, mm = (int(x) for x in cutoff.split(":")[:2])
    except (ValueError, AttributeError):
        hh, mm = 15, 0
    reference = now.date()
    if (now.hour, now.minute) < (hh, mm):
        reference = reference - dt.timedelta(days=1)
    df = _pg.fetch_df(
        "select calendar_date::text as d from reference_data.trade_calendar "
        "where is_open and calendar_date <= %s order by calendar_date desc limit 1", (str(reference),))
    return _text(df.iloc[0]["d"]) if df is not None and len(df) else None


def _days_behind(last_data_date: str | None, expected: str | None) -> int | None:
    """两个日期之间隔了几个交易日。算不出就 None，**不许折成 0**。"""
    if not last_data_date or not expected or not _pg.enabled():
        return None
    df = _pg.fetch_df(
        "select count(*)::int as n from reference_data.trade_calendar "
        "where is_open and calendar_date > %s and calendar_date <= %s",
        (last_data_date, expected))
    if df is None or not len(df):
        return None
    n = int(df.iloc[0]["n"])
    return n if n >= 0 else None


def _file_job(name: str, label: str, newest_prefix: str, dir_name: str,
              interval_seconds: int | None, spec: str) -> dict:
    """从文件侧证据算一个采集作业的状态（news 的 poller 还没写 state.json）。"""
    root = Path(jstore.data_path(dir_name))
    newest_date, newest_mtime = None, None
    if root.is_dir():
        candidates = sorted([p for p in root.iterdir() if p.name.startswith(newest_prefix)],
                            key=lambda p: p.name, reverse=True)
        if candidates:
            digits = "".join(ch for ch in candidates[0].name if ch.isdigit())
            newest_date = f"{digits[:4]}-{digits[4:6]}-{digits[6:8]}" if len(digits) >= 8 else None
            newest_mtime = candidates[0].stat().st_mtime
    state_file = Path(jstore.data_path("jobs", "state.json"))
    stored = jstore.state().get(name) if state_file.is_file() else None
    expected = _expected_trade_day(china_now()) if interval_seconds is None else None
    behind = _days_behind(newest_date, expected) if expected else None
    last_run = (stored or {}).get("last_run_at") or (
        _iso(dt.datetime.fromtimestamp(newest_mtime)) if newest_mtime else None)
    basis = ("jobs/state.json" if stored
             else f"文件侧证据（{dir_name}/ 下最新产物的 mtime；采集循环未写 state.json）")
    return {
        "key": name, "name": label, "entry": f"scripts/jobs.py run-now {name}",
        "origin": "yuanshan-ingest", "runner": "yuanshan-uvicorn",
        "state": "运行中" if (interval_seconds and newest_mtime and
                             (dt.datetime.now().timestamp() - newest_mtime) < interval_seconds * 3)
                 else "正常",
        "enabled": True, "available": True, "reason": None,
        "schedule_kind": "interval" if interval_seconds else "trade_day_after_close",
        "schedule_spec": spec, "trade_day_aware": interval_seconds is None,
        "last_run_at": last_run, "last_ok_at": last_run,
        "consecutive_failures": (stored or {}).get("consecutive_failures"),
        "last_data_date": newest_date or (stored or {}).get("last_data_date"),
        "expected_data_date": expected, "days_behind": behind,
        "next_due_at": None, "overdue_seconds": None,
        "last_error": (stored or {}).get("last_error"), "last_error_at": None,
        # 10-10 起常驻循环也写 `runs` 流水（`jobs.record()`）：这里直接透出，
        # 前端 `HistoryBlock` 原来只能显示"跑了而没留历史"，现在能列出最近 12 次。
        "run_history": (stored or {}).get("runs") or [], "counters": _empty_counters(),
        "source_health": [], "sink_health": {"writable": None, "last_err": None, "targets": []},
        "run_preview": _preview(outbound_hosts=["zx.cj.sina.cn", "np-listapi.eastmoney.com"],
                                writes=[f"data_path('{dir_name}')"], est_seconds=5),
        "basis": basis,
    }


def _backup_runnable() -> str | None:
    """本地那份 `app/ops/backup.py` 到底能不能在 YuanShan 进程里跑完。

    **能 import ≠ 能跑**：它要 `FT_DSN_ADMIN`（pg_dump 用 admin 角色）与 `FT_PG_SUPERPASS`
    （globals/basebackup/恢复验证用超管）。实测这两个键 `YuanShan/.env` 里都没有 →
    `dsn_parts('admin')` 当场抛 RuntimeError。所以这一项**不能摆成"可触发"**。
    """
    missing = [k for k in ("FT_DSN_ADMIN", "FT_PG_SUPERPASS") if not os.environ.get(k)]
    if not missing:
        return None
    return "、".join(missing)


def _backup_job(now: dt.datetime) -> dict:
    """备份作业：判据是 `pg_restore -l` 能不能列出 TOC，不是文件存在与 mtime。"""
    rows, newest, newest_kind = [], None, None
    for kind in ("daily", "weekly", "monthly"):
        d = Path(BACKUP_DIR) / kind
        files = sorted(d.glob("*.dump"), key=lambda p: p.stat().st_mtime, reverse=True) if d.is_dir() else []
        if not files:
            rows.append("monthly＝无整簇 basebackup（只有 globals.sql）" if kind == "monthly"
                        else f"{kind}＝无备份")
            continue
        f = files[0]
        if newest is None or f.stat().st_mtime > newest:
            newest, newest_kind = f.stat().st_mtime, kind
        rows.append(f"{kind}={_readcheck(f)}")
    blocked = _backup_runnable()
    return {
        "key": "backup", "name": "数据库备份与恢复验证",
        "entry": "python -m app.ops.backup daily",
        "origin": "yuanshan-ingest",
        "runner": "none" if blocked else "yuanshan-uvicorn",
        "state": "正常" if not blocked else None,
        "enabled": True, "available": blocked is None,
        "reason": (None if not blocked else
                   f"YuanShan/.env 缺 {blocked}，本地那份 app/ops/backup.py 连 "
                   f"`dsn_parts('admin')` 都过不去 —— 当前 dump 是外部进程产出的，不摆按钮。"),
        "schedule_kind": "cron", "schedule_spec": "22:30 daily / 周日 23:00 weekly",
        "trade_day_aware": False,
        "last_run_at": _iso(dt.datetime.fromtimestamp(newest)) if newest else None,
        "last_ok_at": _iso(dt.datetime.fromtimestamp(newest)) if newest else None,
        "consecutive_failures": None, "last_data_date": None, "expected_data_date": None,
        "days_behind": None, "next_due_at": None, "overdue_seconds": None,
        "last_error": None if newest else "三档备份一个都没有", "last_error_at": None,
        "run_history": [], "counters": {"scope": "cumulative", "unit": "行", "since": None,
                                        "total_seen": None, "written": None,
                                        "already_had": None, "dropped": None},
        "source_health": [], "sink_health": {"writable": None, "last_err": None,
                                             "targets": [f"{BACKUP_DIR}/{newest_kind or 'daily'}"]},
        "run_preview": _preview(outbound_hosts=[], writes=[BACKUP_DIR], touches_db=True,
                                est_seconds=180),
        "basis": "；".join(rows) + "（可恢复性判据 = 实跑 pg_restore -l，不是文件存在与 mtime）",
    }


def _readcheck(dump: Path) -> str:
    """备份**可恢复性**的结论来自 manage.py 的实跑 `pg_restore -l`（标记文件），不在这里重跑 ——
    608MB 全库列一次 TOC 要几秒，页面每次刷新都付这个钱不值。没标记就老实说"未验"。"""
    try:
        data = json.loads(READCHECK_MARKER.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return "未验"
    entry = data.get(str(dump)) if isinstance(data, dict) else None
    if not isinstance(entry, dict):
        return "未验"
    age_h = (dt.datetime.now().timestamp() - float(entry.get("checked_at") or 0)) / 3600
    verdict = "可读" if entry.get("ok") else f"读不开（{entry.get('why')}）"
    return f"{verdict}·{age_h:.0f}h前实跑pg_restore -l"


def _financetool_jobs(ft_state: dict | None = None) -> list[dict]:
    """FT 登记的作业：本进程没有执行体，一律 available=false + reason（§4 第 2 级接住）。"""
    if not _pg.enabled():
        return []
    df = _pg.fetch_df(
        "select j.job_key, j.name, j.kind, j.enabled, j.trigger_kind, j.cron_expr, "
        "j.only_trade_day, s.last_fire_at, s.next_fire_at, s.last_success_date, "
        "s.last_result, s.consecutive_fail, s.missed_count, s.note "
        "from config.job j left join operations.schedule_state s using (job_key) "
        "order by j.job_key")
    if df is None or not len(df):
        return []
    logs = _pg.fetch_df(
        "select job_key, occurred_at, action, reason from operations.schedule_log "
        "order by occurred_at desc limit 400")
    history: dict[str, list] = {}
    if logs is not None:
        for r in _records(logs):
            bucket = history.setdefault(r["job_key"], [])
            if len(bucket) < 10:
                bucket.append({"started_at": _text(r.get("occurred_at")), "finished_at": None,
                               "outcome": "skipped", "rows_written": None,
                               "error": _text(r.get("reason"))})
    ft_state = ft_state or {}
    if ft_state.get("known"):
        host = (f"它的登记表由本机另一个进程维护并在发心跳"
                f"（pid {ft_state.get('pid')}、enabled={ft_state.get('enabled')}、"
                f"dry_run={ft_state.get('dry_run')}、fired={ft_state.get('fired')} "
                f"⇒ 每个 tick 只发心跳、不入队）")
    else:
        host = "它的登记表由本机另一个进程维护，但当前读不到心跳（那个进程可能已停）"
    out = []
    for r in _records(df):
        note = _text(r.get("note")) or "无 state 行（该作业还没进调度器视野）"
        out.append({
            "key": r["job_key"], "name": _text(r.get("name")) or r["job_key"], "entry": "config.job",
            "origin": "financetool", "runner": "ft-scheduler",
            "state": None, "enabled": bool(r.get("enabled")), "available": False,
            "reason": (f"YuanShan 内尚未覆盖该项：本进程没有它的执行体，故不摆触发按钮。"
                       f"{host}。该作业自己的 state：{note}。"
                       f"⚠ consecutive_fail=0 是\"从没跑过所以没失败过\"，不等于健康。"),
            "schedule_kind": {"cron": "cron", "manual": "manual",
                              "chain": "event", "on_ready": "event"}.get(
                                  str(r.get("trigger_kind")), "manual"),
            "schedule_spec": _text(r.get("cron_expr")),
            "trade_day_aware": bool(r.get("only_trade_day")),
            "last_run_at": _text(r.get("last_fire_at")),
            "last_ok_at": None,          # 只有日期没有墙钟时刻，拼时间就是造假
            "consecutive_failures": r.get("consecutive_fail"),
            "last_data_date": _text(r.get("last_success_date")),
            "expected_data_date": None, "days_behind": None,
            "next_due_at": _text(r.get("next_fire_at")),
            "overdue_seconds": None,
            "last_error": None, "last_error_at": None,
            "run_history": history.get(r["job_key"], []),
            "counters": _empty_counters(), "source_health": [],
            "sink_health": {"writable": None, "last_err": None, "targets": []},
            "run_preview": _preview(), "basis": "config.job + operations.schedule_state",
        })
    return out


def _ft_scheduler() -> dict:
    """FinanceTool 侧调度器的真相，从它自己的心跳表读 —— 别说"从未跑过"，那是错的。

    实测（2026-09-25 13:28）：`role=scheduler` 心跳每 ~3 秒推进一次，但 meta 里
    `enabled=false` + `dry_run=true` + `fired=0` ⇒ **进程活着、每 tick 只发心跳不入队**。
    """
    out = {"known": False, "alive": None, "heartbeat_at": None, "pid": None,
           "enabled": None, "dry_run": None, "fired": None, "jobs_seen": None, "target": None}
    df = _pg.fetch_df(
        "select role, pid, heartbeat_at, meta from operations.scheduler_heartbeat "
        "where role = 'scheduler' order by heartbeat_at desc limit 1") if _pg.enabled() else None
    if df is None or not len(df):
        return out
    row = df.iloc[0]
    meta = row["meta"] if isinstance(row.get("meta"), dict) else {}
    out.update(known=True, heartbeat_at=_text(row["heartbeat_at"]),
               pid=_int_or_none(row.get("pid")),
               enabled=meta.get("enabled"), dry_run=meta.get("dry_run"),
               fired=meta.get("fired"), jobs_seen=meta.get("jobs"), target=_text(meta.get("target")))
    if out["heartbeat_at"] is None:
        out["known"] = False          # 心跳时间读成空 = 等于不知道，别拿 "nan" 当真值
        return out
    try:
        beat = dt.datetime.fromisoformat(out["heartbeat_at"].replace("+00", "+00:00"))
        out["alive"] = (dt.datetime.now(dt.timezone.utc) - beat.astimezone(dt.timezone.utc)
                        ).total_seconds() < 120
    except ValueError:
        pass
    return out


def _recorded_job(name: str, label: str, schedule_spec: str, est_seconds: int,
                  writes: list[str], after: str | None = None,
                  due_at: str | None = None, now: dt.datetime | None = None) -> dict:
    """有 `jobs/state.json` 运行记录的作业（收盘链两段就是这种）。

    与 `_file_job` 的区别就一条：**这里的时间是真记录，不是从文件 mtime 猜的**。
    所以 `basis` 明写 `jobs/state.json`，页面上"上次成功"和"从未运行"才分得开。

    `after` 是顺序依赖（段2 的 `fill_all` 跟在段1 后面）。上游今天按设计停下时，
    下游**一次都没跑**，这里现读现说成「未起」，但**绝不给它写 `last_run_at`** ——
    没跑过的段那个字段必须是 null（契约 03 §3），编一个时刻出来就是把"没干"
    显示成"干过了没成"，而后者会让人去查一个不存在的失败。
    """
    all_state = jstore.state()
    st = all_state.get(name) if isinstance(all_state.get(name), dict) else {}
    merged, warns = jstore.settings(name, jstore.CLOSE_DEFAULTS[name])
    # 「没成功过」有两种，处置完全不同，不能都写成「从未运行」：
    # 跑过 91 次全挂（09-28 那条自选同步就是这种）要人去查归因，
    # 一次都没跑过只是还没到点/刚接入。判据只看 `jobs/state.json` 里有没有运行记录。
    runs, fails = st.get("last_run_at"), (st.get("consecutive_failures") or 0)
    if st.get("last_ok_at"):
        state_text = "正常"
    elif runs or fails:
        state_text = f"从未成功（跑过 {runs or '至少一次'}，连败 {fails}）"
    else:
        state_text = "从未运行"
    last_error = st.get("last_error")
    if after:
        up = all_state.get(after) if isinstance(all_state.get(after), dict) else {}
        up_detail = up.get("last_detail") if isinstance(up.get("last_detail"), dict) else {}
        up_today = str(up.get("last_run_at") or "")[:10] == str((now or china_now()))[:10]
        if up_today and up_detail.get("verdict") and up_detail["verdict"] != "run":
            state_text = f"未起（{after} 今天停在 {up_detail['verdict']}）"
            last_error = up_detail.get("reason") or last_error
    return {
        "key": name, "name": label, "entry": f"scripts/jobs.py run-now {name}",
        "origin": "yuanshan-ingest", "runner": "yuanshan-uvicorn",
        "state": state_text,
        "enabled": bool(merged.get("enabled", True)), "available": True,
        "reason": "；".join(warns) or None,
        "schedule_kind": "trade_day_after_close", "schedule_spec": schedule_spec,
        "trade_day_aware": True,
        "last_run_at": st.get("last_run_at"), "last_ok_at": st.get("last_ok_at"),
        "consecutive_failures": st.get("consecutive_failures"),
        "last_data_date": None, "expected_data_date": None, "days_behind": None,
        # 该跑的时刻由宿主说出来（`DAILY_CLOSE_AT`，默认 15:59）：没有它，
        # 第一天还没跑过的作业会撞上 §4 第 7 级"从未运行"的最重样式，
        # 而那与"今天还没到点"在页面上长得一模一样。
        # ⚠ 但**开关为关时不许再给 next_due_at**：关掉之后还显示"明天 15:59 要跑"，
        # 读的人只会得出两个错结论之一 —— "关了没用" 或 "到点它还会自己动桌面"。
        # 关着就是关着，这一格交回 null，页面按"已关闭"那一级渲染。
        "next_due_at": (f"{(now or china_now()).date().isoformat()} {due_at}"
                        if due_at and merged.get("enabled", True) else None),
        "overdue_seconds": None,
        "last_error": last_error, "last_error_at": None,
        "run_history": st.get("runs") or [], "counters": _empty_counters(), "source_health": [],
        "sink_health": {"writable": None, "last_err": None, "targets": writes},
        "run_preview": _preview(outbound_hosts=["q.gtimg.cn", "push2.eastmoney.com",
                                                "np-listapi.eastmoney.com"],
                                writes=writes, touches_db=True, est_seconds=est_seconds),
        "basis": "jobs/state.json（收盘链自己记的运行结果）",
    }


def _disabled_loop_names() -> set[str]:
    """按开关停用的那些循环的**线程名**。10-08 起这个集合会非空：第九条 `ths_sync` 默认关
    （它动桌面上的客户端窗口，要先人工演练）。不摘出来的话，面板会永远显示"缺一条"，
    而"缺"和"人关的"是两件事 —— 前者要去查，后者不用。"""
    out: set[str] = set()
    for loop, key, _label, _off in LOOP_JOBS:
        defaults = jstore.CLOSE_DEFAULTS.get(key)
        if not defaults:
            continue
        try:
            merged, _warns = jstore.settings(key, defaults)
        except Exception:  # noqa: BLE001  读不出配置就别替人下结论，交给 alive/missing 那套原判据
            continue
        if not bool(merged.get("enabled", True)):
            out.add(loop)
    return out


def _live_loops() -> tuple[list[str], list[str]]:
    """本进程**真的**活着哪几条常驻循环，以及缺了哪几条。

    为什么要交这个：改完宿主之后"重启了没有"只能靠猜，而 `/api/jobs` 的作业行
    是新代码渲染的、看不出进程里跑的是哪一份代码。缺 `daily-close` 意味着
    今天的 15:59 不会自己跑 —— 这必须是一条能读出来的话，不是一个静默的空档。

    ⚠️ "缺"只算**该活着却没有**的那些：被开关明确关掉的（`_disabled_loop_names`）不算缺，
    按需起的（`ON_DEMAND_LOOPS`）也不算缺 —— 两者都会把缺勤表永久涂红，
    而那条涂红的格子会盖住真出事那一次。
    """
    names = {t.name for t in threading.enumerate()}
    alive = [n for n in HOST_LOOPS if n in names]
    off = _disabled_loop_names()
    return alive, [n for n in HOST_LOOPS
                   if n not in names and n not in off and n not in ON_DEMAND_LOOPS]


def switched_off_loops() -> list[str]:
    """按开关停用的线程名（面板另列一格用，别混进"缺"）。"""
    names = {t.name for t in threading.enumerate()}
    return sorted(n for n in _disabled_loop_names() if n not in names)


def chain_status(now: dt.datetime | None = None) -> dict:
    """收盘链的**开关面板**读数：每段的开关、时点与它的来源、最近结果、谁改的。

    为什么单独给一个端点：`enabled=false` 是会一直留着的持久状态（默认值救不回来），
    而它落在 `~/.duanxian-agents/jobs/config.json` 里，界面上原先只有一个小灰标签
    「已停用」—— 那种"看着像用户自己不想跑"的措辞，分不出是被谁误关的。
    09-28 那天同一个作业被关了三次，谁也答不上来是谁、什么时候、从哪儿改的。
    """
    now = now or china_now()
    slot, slot_from = jstore.run_at()
    alive, missing = _live_loops()
    out = []
    for key, spec in jstore.CLOSE_CHAIN.items():
        merged, warns = jstore.settings(key, jstore.CLOSE_DEFAULTS[key])
        st = jstore.state().get(key)
        st = st if isinstance(st, dict) else {}
        note = jstore.change_note(key)
        row = {
            "key": key, "name": spec["label"], "enabled": bool(merged.get("enabled", True)),
            "default_enabled": bool(jstore.CLOSE_DEFAULTS[key].get("enabled", True)),
            "run_at": merged.get("run_at"), "run_at_source": slot_from if key == "daily_close" else None,
            "last_run_at": _text(st.get("last_run_at")), "last_ok_at": _text(st.get("last_ok_at")),
            "consecutive_failures": _int_or_none(st.get("consecutive_failures")),
            # `last_status` 是三态（ok / fail / **skipped**）：休市两天里一条按口径停住的
            # 循环不该显示成"连败 200+"——真故障会埋进噪音里看不出来。
            "last_status": _text(st.get("last_status")),
            "last_error": _text(st.get("last_error")),
            "changed_at": _text(note.get("changed_at")), "changed_by": _text(note.get("changed_by")),
            # `note` 是人写的理由；留痕是自动盖章的"谁/何时/从哪条路"。两者互补，都要上页面
            "note": _text(merged.get("note")),
            "warnings": warns,
        }
        if key == "daily_close":
            # **「今天还会不会再试」必须和宿主给同一个答案**（同一个函数、同一张表）。
            # 09-28 那次"16:31 判 stop、17:22 数据才到"之所以没人发现，就是因为页面上
            # 只剩一句失败，没人知道它到底还会不会回来 —— 而现在它真的会回来。
            detail = st.get("last_detail") if isinstance(st.get("last_detail"), dict) else {}
            mins = (now.hour * 60 + now.minute) - (int(slot[:2]) * 60 + int(slot[3:5]))
            nxt = (jstore.close_next_attempt_min(mins)
                   if detail.get("verdict") == "hold" else None)
            row["attempts_used"] = _int_or_none(detail.get("attempt"))
            row["attempts_max"] = jstore.close_attempts_max()
            row["next_attempt_at"] = _hhmm_plus(slot, nxt) if nxt is not None else None
            # 用户 2026-10-07 要的两格：**提前几分钟把通达信拉起并登录**、以及
            # **是否只在交易日执行**。两个都改得动、都下一个心跳生效，所以这里必须
            # 交出"算出来的实际拉起时刻"，不能让人自己减（减错了页面看不出来）。
            lead = int(merged.get("launch_lead_min") or 0)
            row["launch_lead_min"] = lead
            row["launch_at"] = _hhmm_plus(slot, -lead) if lead else slot
            row["trading_days_only"] = bool(merged.get("trading_days_only", True))
            # 「链收工后自动关客户端」（用户 10-08 要加的那一档）。**默认关**，
            # 而"关着"的含义必须写在旁边：保持登录是历史决定，不是漏配。
            row["close_client_after"] = bool(merged.get("close_client_after", False))
            row["close_client_default"] = bool(
                jstore.DAILY_CLOSE_DEFAULTS.get("close_client_after", False))
        out.append(row)
    trade = _pg.trade_day_open(now.date())
    # 通达信那一格（`server.py::_tdx_probe_round` 每两分钟只探测一次写进来的）。
    # **没有记录必须写成 null，不许回退成"在跑"** —— 2026-10-06 之前的真实状态就是
    # "监控代码存在但全仓没人调用"，界面上什么都没有才看得出什么都没有。
    tw = jstore.state().get("tdx_watch")
    tw = tw if isinstance(tw, dict) else {}
    txd = tw.get("last_detail") if isinstance(tw.get("last_detail"), dict) else {}
    tdx = {
        "loop_alive": "tdx-watch" in alive,
        "last_run_at": _text(tw.get("last_run_at")),
        "ok": (None if not tw else bool(tw.get("last_status") == "ok")),
        "running": txd.get("running"), "login_box": txd.get("login_box"),
        "sync": _text(txd.get("sync")), "reason": _text(txd.get("reason")),
        "heal": _text(txd.get("heal")) or "watchman.heal 未接；拉起并登录由收盘链 ③b 前置做",
        "consecutive_failures": _int_or_none(tw.get("consecutive_failures")),
    }
    # **待人工清单**（用户 10-06 的条件："万一我当时也在操作电脑，跟你的指令冲突或者误取消了，
    # 也可以我主动的去找出来，哪些没执行的我手动执行下"）。
    # 每一次系统自己动界面的动作都在 `duanxian/gui_trail.py` 留一条，非 `done` 的都算"待人工"，
    # 每条自带"人怎么做"那一句（菜单路径 + 可重跑的命令）。
    from duanxian import gui_trail

    trail = gui_trail.summary()
    # 「今天会不会跑」的三个条件分开列，其中**交易日那一条现在是可以关掉的**
    # （`daily_close.trading_days_only`，用户 10-07 要的可选项）。关掉的含义必须写在数旁边：
    # 那是"每天都发一次心跳"，不是"每天都会有数" —— 段脚本自己仍按日历拒写。
    only_trade = next((bool(r.get("trading_days_only")) for r in out
                       if r["key"] == "daily_close"), True)
    # 关掉「只在交易日执行」= **每天都发一次**，所以日历说"不是交易日"也不能把它算成"今天不跑"；
    # 那句"段脚本自己仍按日历拒写"由 `is_trade_day` 与卡片文案说清，不靠这里替它保守。
    day_ok = trade is True if only_trade else True
    return {
        "generated_at": _iso(now), "slot": slot, "slot_source": slot_from,
        "stages": out,
        "host_loop_alive": "daily-close" in alive,
        "host_loop_missing": missing,
        "tdx": tdx,
        "tdx_trail": trail,
        "is_trade_day": trade,
        # 今天这个"是不是交易日"是**哪一层**给的：`calendar`（日历明说）/ `weekend`（周末自判）/
        # `quote`（行情时间戳自证）/ `unknown`（判不了）。页面必须把它交出去 —— 读数一旦
        # 来自 `quote`，就说明日历已经排不住今天了，这是要人在年底接下一年的信号，
        # 而不是等 1 月发现链安静地不跑。
        "trade_day_evidence": _pg.trade_day_evidence(now.date()),
        # 日历覆盖到哪天：**这句要能在页面上读出日期**，否则"日历只登到 2026-12-31"
        # 就成了写死在文案里的事实，明年它自己会变成一句假话。
        "calendar_through": _pg.calendar_max_day(),
        **_pg.calendar_coverage(),
        "completeness": completeness_status(),
        "today_will_run": bool(day_ok and "daily-close" in alive
                               and any(s["enabled"] for s in out)),
        "explain": (
            "开关关着到点也不会跑，且「不会自己恢复」——会一直关到有人再打开；"
            "改时点/开关与改绑定地址不同，这里是「下一个心跳就生效」，不用重启。"
        ),
    }


def tdx_watch(now: dt.datetime | None = None) -> dict:
    """通达信看护那一屏的读数 —— `/api/tdx/watch`。

    这个端点原先**根本不存在**（`/system` 上那一格写着"接口 /api/tdx/watch 尚未实现（HTTP 404）"），
    而看护循环 10-06 已经真的在跑了：接口不在，就等于"看着有其实看不见"。
    判据现问现答（`watchman.probe()` 只枚举窗口，不动界面），拿不到才退回
    `jobs/state.json` 里那条最近记录，并**交出它的年龄** —— 几小时前的探测不能当"现在"。

    `process_running` / `login_dialog` 这些是**三态**：`null` = 没探测到，
    绝不能把 `null` 读成 `false`（那等于"客户端没在跑"是一个结论而不是一个未知）。
    """
    from duanxian import gui_trail, jobs as jstore

    now = now or china_now()
    st = jstore.state().get("tdx_watch")
    st = st if isinstance(st, dict) else {}
    detail = st.get("last_detail") if isinstance(st.get("last_detail"), dict) else {}
    alive, missing = _live_loops()

    probe, source, probe_at = None, None, None
    try:
        from app.tdx_client import watchman
        probe = watchman.probe()
        source, probe_at = "live", _iso(now)
    except Exception as exc:  # noqa: BLE001  探测不了就用上一次的记录，并说清是旧的
        probe_err = "%s: %s" % (type(exc).__name__, str(exc)[:120])
        if st:
            source, probe_at = "cached", _text(st.get("last_run_at"))
            detail = {**detail, "probe_error": probe_err}
        else:
            return {"available": False,
                    "reason": f"现问探测失败（{probe_err}），而且一条看护记录都没有 —— "
                              "这一屏没有数据来源，页面不放示例数字",
                    "loop_alive": "tdx-watch" in alive}
    else:
        source = "live"

    running = None if probe is None else bool(probe.get("running"))
    login = None if probe is None else bool(probe.get("login_box"))
    minimized = None if probe is None else probe.get("minimized")
    age = None
    if probe_at:
        try:
            age = max(0, int((now - dt.datetime.fromisoformat(probe_at.replace(" ", "T"))
                              .astimezone()).total_seconds()))
        except (ValueError, TypeError):
            age = None
    trail = gui_trail.summary()
    tw_cfg, _cfg_warns = jstore.settings("tdx_watch", jstore.TDX_WATCH_DEFAULTS)

    # ⚠️ `note` 只准由**这一次探测**说话（10-13 实测的假读数）：`watchman.probe()` 根本不附
    # `reason`，而从前这行是 `probe.reason or detail.reason` —— 于是永远一路兜到**上一轮记账**
    # 里那句"在跑（pid 40672）"。08:09 客户端被自动关掉之后，同一个响应里
    # `process_running=false` 与 `note=在跑（pid 40672）` 同时挂在屏上。
    # 只有探测本身失败（`source=cached`）才允许引用旧记录，而且要说清那是旧的。
    if source == "live":
        note = _text((probe or {}).get("reason")) or (
            "在跑（pid %s）· 同步子进程：%s" % ((probe or {}).get("pid"),
                                              _text((probe or {}).get("sync")) or "没读到")
            if running else "没在跑（现问探测：进程表里没有 TdxW）")
    else:
        # 到这一支时 `probe` 一定是 None（现问探测抛了）。旧记录可以引用，但必须当场说清是旧的。
        old = _text(detail.get("reason"))
        note = old + ("（上一次记录的读数，本轮现问探测失败）" if old else
                      "本轮现问探测失败，而且上一条记录里也没有读数")

    return {
        "available": True, "reason": None, "source": source,
        "loop_alive": "tdx-watch" in alive, "loop_missing": missing,
        "last_probe_at": probe_at, "age_seconds": age,
        "process_running": running, "pid": (probe or {}).get("pid") if probe else detail.get("pid"),
        "login_dialog": login,
        "window_visible": (None if minimized is None else not minimized),
        "window": _text((probe or {}).get("window") if probe else detail.get("window")),
        "syncing": bool(str((probe or {}).get("sync") or detail.get("sync") or "").strip()
                        and "没有" not in str((probe or {}).get("sync") or detail.get("sync"))),
        "sync": _text((probe or {}).get("sync") if probe else detail.get("sync")),
        # 界面自动化这两条开关现在**真的起作用**了（10-06 用户点头）：
        # `auto_restart`=阶梯两档不全时全重启客户端；`auto_launch`=收盘链 ③b 前置会拉起并登录。
        "auto_restart": bool(tw_cfg.get("auto_restart", True)),
        "auto_launch": bool(tw_cfg.get("auto_launch", True)),
        "sync_reap_after_min": tw_cfg.get("sync_reap_after_min"),
        "note": note,
        "consecutive_failures": _int_or_none(st.get("consecutive_failures")),
        "trail": {"count": trail["count"], "by_status": trail["by_status"],
                  "pending": trail["pending"], "path": trail["path"]},
    }


def tdx_status(now: dt.datetime | None = None) -> dict:
    """`/api/tdx/status` 的读数 —— 通达信控制页那一屏（原先这个端点根本不存在）。

    只答**探得到的**那些：进程/登录框/同步子进程/vipdoc 目录/文件到哪一天/该到哪一天，
    加上 TQ 这条路现在能不能走。探不到的（主站连接、后台同步开关、TQ 是否被占住）
    一律给 `null` 并说明为什么 —— 这三样要客户端界面里的开关状态，
    后台服务账号读不到自绘控件，**拿"看起来像"的值顶上是这一页最容易造假的地方**。
    """
    from app.tdx_client import tq_bridge, watchman
    from app.tdx_client import readiness

    now = now or china_now()
    try:
        st = watchman.probe()
    except Exception as exc:  # noqa: BLE001
        return {"available": False,
                "reason": "探不到通达信状态：%s: %s（不是「没在跑」，是没探到）"
                          % (type(exc).__name__, str(exc)[:150])}
    running = bool(st.get("running"))
    login_box = bool(st.get("login_box"))
    tq_ok, tq_why = (False, "TQ 未探测")
    try:
        tq_ok, tq_why = tq_bridge.available()
    except Exception as exc:  # noqa: BLE001
        tq_why = "TQ 可用性探测自己坏了：%s" % str(exc)[:120]
    try:
        v = readiness.Readiness().day_ready()
        dates = [p.last_date for p in v.probes if p.last_date]
        last_file = str(max(dates)) if dates else None
        expected = str(v.target)
    except Exception:  # noqa: BLE001  文件侧读不出就把这两格留空，不编日期
        last_file = expected = None
    from app import config as app_config
    from duanxian import gui_trail

    return {
        "available": True, "reason": None,
        "trail": gui_trail.summary(),
        "client_running": running,
        # "logged_in" 的判据是**没停在登录框上**，不是"窗口标题像登录了"
        "logged_in": (running and not login_box) if running else None,
        "login_dialog": login_box,
        "background_sync": None, "server_connected": None,
        "not_probed": "「后台同步」开关与「主站连接」在客户端自绘界面里，"
                      "后台服务账号读不到 —— 这两格恒为空，不是「关」也不是「没连」",
        "tq_ready": tq_ok, "tq_reason": tq_why,
        "tq_endpoint": None, "tq_busy": None,
        "last_probe_at": _iso(now), "vipdoc_dir": str(app_config.VIPDOC_DIR),
        "last_file_date": last_file, "expected_data_date": expected,
        "sync": _text(st.get("sync")), "window": _text(st.get("window")),
    }


def _news_defaults() -> dict:
    """快讯那条的默认值。**数字与拼装都只住 `duanxian/jobs.py::news_defaults()` 一份**
    （宿主 `server.py` 读的就是它）；这里再拼一次就会出现"面板显示的间隔与线程实际用的不是一份"。"""
    try:
        return jstore.news_defaults()
    except Exception:  # noqa: BLE001  读不出默认值也只是"这行按开关=默认显示"，不该拖垮整个面板
        return {"enabled": True}


def _e1():
    """加工段模块的**懒导入**（步骤清单 + 台账读写都在它那里，别在 jobsboard 里抄第二份）。
    导入失败时交回 None，调用方要说"读不到"而不是"没有失败步骤"。"""
    if _E1[0] == "unset":
        try:
            from ingest.jobs import e1_fill_all as mod
            _E1[0] = mod
        except Exception:  # noqa: BLE001
            _E1[0] = None
    return _E1[0]


_E1: list = ["unset"]


def _retry():
    """重试阶梯模块的懒导入（档期与待补名单都住在它那里，别在 jobsboard 抄第二份判据）。"""
    try:
        from ingest.jobs import step_retry as mod
        return mod
    except Exception:  # noqa: BLE001
        return None


def chain_steps() -> dict:
    """加工段**最近一次的逐步结果**（谁没成、为什么、多久），以及"能单独补跑哪一步"。
    为什么单独开一个读数而不是塞进 `/api/jobs/chain`：那个端点讲"今天会不会跑、跑到哪一段"，
    这里讲"上一次跑完每一步的结果"。合在一起的话，一次成功的追平会把三天前的失败一起盖掉。
    """
    import re
    e1 = _e1()
    led = (e1.read_ledger() if e1 else {}) or {}
    steps = led.get("steps") or []
    return {
        "available": bool(led),
        "reason": None if led else "还没有台账：加工段至少跑过一次才会有（`python -m ingest.jobs.e1_fill_all`）",
        "target": led.get("target"), "verdict": led.get("verdict"), "line": led.get("line"),
        "written_at": led.get("written_at"), "started_at": led.get("started_at"),
        "ran_only": led.get("ran_only"), "lagging_tables": led.get("lagging_tables") or [],
        "steps": steps,
        "merged": led.get("merged"),
        "rerunnable": [n for n, _w, _c in e1.STAGES] if e1 else [],
        # **三态**：`fail` 才是要重试/要补救的；`refused` 是按口径拒写（归属日不可证明时
        # 写进去才是错），把它算进"失败"会让阶梯天天白跑一次、也让面板把正常收工报成故障。
        "failed_steps": [s["name"] for s in steps if s.get("status") == "fail"],
        "refused_steps": [s["name"] for s in steps if s.get("status") == "refused"],
        "retry": self_retry_status(),
    }


def self_retry_status(now: dt.datetime | None = None) -> dict:
    """重试阶梯的读数（档期 / 待补名单 / 下一档 / 是否已放弃）。读不出来要说"读不到"。"""
    mod = _retry()
    if mod is None:
        return {"available": False, "reason": "读不到阶梯模块（ingest.jobs.step_retry 导入失败）"}
    try:
        out = mod.status(now)
        out["available"] = True
        return out
    except Exception as exc:  # noqa: BLE001 — 观测层坏了不该把面板弄红
        return {"available": False, "reason": f"阶梯读数异常：{type(exc).__name__}: {str(exc)[:120]}"}


def rerun_step(name: str, *, via: str = "") -> tuple[int, dict]:
    """单独补跑**一步**（页面上那句"补救一次"）。与 15:59 那条链抢同一把锁。

    ⚠️ 必须走 `jobs.claim('fill_all')`：手动补跑和到点的链子是同一个写入方，
    两边各判各的"没在跑"会同时起两个 `c11`/`c21`，源侧看到的是一分钟内两笔同样的请求 ——
    然后一起被判成"网络故障"，谁都不知道为什么。
    """
    e1 = _e1()
    if e1 is None:
        return 503, {"reason": "读不到加工段的步骤清单（ingest.jobs.e1_fill_all 导入失败）"}
    names = {n for n, _w, _c in e1.STAGES}
    if name not in names:
        return 404, {"reason": f"没有这一步 {name!r}（可用：{'、'.join(sorted(names))[:180]}…）"}
    if not jstore.claim("fill_all"):
        return 409, {"reason": "加工段正在跑（15:59 那条链或另一次手动补跑），本次未触发"}

    def _go():
        try:
            _spawn_step(name, e1.step_timeout(name))
        except Exception as exc:  # noqa: BLE001  补跑线程不能把异常漏出去
            print(f"⚠️ 单步补跑异常：{type(exc).__name__}: {str(exc)[:160]}")
        finally:
            jstore.release("fill_all")

    threading.Thread(target=_go, name=f"chain-step-{name}", daemon=True).start()
    return 202, {"status": "queued", "step": name,
                 "reason": f"只补跑 {name} 这一步；跑完这一格的台账会更新（约 1 分钟后再刷新）"}


def _spawn_step(name: str, timeout: int) -> int:
    """起**那一个**补跑子进程 —— 全仓唯一会因"页面上点了一步"而拉起作业的地方，
    所以它同时是测试的洞：`PYTEST_CURRENT_TEST` 里直接返回，绝不真起进程。
    （同一条规矩见 `ingest.jobs.c23_ths_cloud_sync.spawn` 与 `jobs.loop_enabled`。）
    """
    if os.environ.get("PYTEST_CURRENT_TEST"):
        # 这一句**不许 print**：补跑是在后台线程里调它的，线程往 pytest 的全局捕获缓冲区写字节，
        # 会撞上捕获收尾时的那次 read —— 10-08 实测 1/7 的概率把整条用例判成
        # `UnicodeDecodeError: invalid start byte`（用例本身什么都没做错）。
        # "洞生效了"的证据由测试直接断言返回值（`== 0`），不靠出声。
        return 0
    return subprocess.run([sys.executable, "-X", "utf8", "-u", "-m", "ingest.jobs.e1_fill_all",
                           "--only", name], cwd=str(Path(__file__).resolve().parents[1]),
                          timeout=timeout).returncode


def loop_switches(now: dt.datetime | None = None) -> dict:
    """**常驻循环的开关面板**：`LOOP_JOBS` 那 7 条，逐条给开关、时点与来源、最近记账、留痕。

    为什么不并进 `/api/jobs/chain`：那个端点的标题是"收盘链"，把探测与采集塞进去
    会让标题说谎；但**写入面必须共用**（`set_switch/set_note/set_slot` 认整张在册表），
    否则又出现"两份开关读法、一份留痕"—— 09-28 被关三次就是这么来的。

    三条纪律：
      · `alive` 只说线程在不在，`last_run_at` 只说有没有记账 —— 两者**分开交**，
        合在一起就藏住"活着但从不记账"那一类；
      · 没有记录就给 null，不回退成"正常"；
      · 关掉会失去什么（`off_effect`）必须写出来：`fundflow`/`daily_close` 是
        **当天不采永久少一天**，`coverage` 只是首屏要现场等 —— 同一个小开关，代价差一个量级。
    """
    now = now or china_now()
    alive, missing = _live_loops()
    all_state = jstore.state()
    rows = []
    for loop, key, label, off in LOOP_JOBS:
        defaults = (jstore.CLOSE_DEFAULTS.get(key)
                    or (jstore.FILL_ALL_DEFAULTS if key == "fill_all" else None)
                    or (_news_defaults() if key == "news" else None))
        merged, warns = (jstore.settings(key, defaults) if defaults else ({}, []))
        st = all_state.get(key) if isinstance(all_state.get(key), dict) else {}
        note = jstore.change_note(key) if defaults else {}
        has_slot = bool(defaults and "run_at" in defaults)
        slot, slot_from = (jstore.run_at(name=key, env_var=LOOP_SLOT_ENV.get(key, ""),
                                        defaults=defaults) if has_slot else (None, None))
        rows.append({
            "key": key, "name": label, "loop": loop,
            "alive": loop in alive,
            "switchable": bool(defaults),
            "unswitchable_reason": (None if defaults else
                                    "还没有在册默认值：加进 `duanxian/jobs.py::CLOSE_DEFAULTS` 就能在这里关"),
            "enabled": bool(merged.get("enabled", True)) if defaults else None,
            "default_enabled": bool((defaults or {}).get("enabled", True)),
            "run_at": slot, "run_at_source": slot_from,
            # 没有 run_at 的键**不许给时点**（间隔型/跟段跑），页面据此不渲染那个框
            "has_slot": has_slot,
            # `watchlist` 那一格多一个同步模式（用户 2026-10-07 要"全量/增量可配置"）。
            # 只在**这张在册表里真有这个键**时才交，别的键交 null ——
            # 给一个没人读的键配下拉框 = 页面上多一个改了也不生效的框。
            "sync_mode": (str(merged.get("sync_mode")) if "sync_mode" in (defaults or {}) else None),
            "sync_mode_choices": SYNC_MODE_MEANING if "sync_mode" in (defaults or {}) else None,
            # 客户端两档（`tdx_watch.client_mode`，10-13 用户点名）。同样**只在本键真在册时才交**。
            # `restart_max_per_day` 刻意**不交**：面板上没有能改它的控件，
            # 交一个改了也不生效的键正是《12-定时任务全景》列过的洞 —— 它走 CLI（`jobs.py set`）。
            "client_mode": (str(merged.get("client_mode")) if "client_mode" in (defaults or {}) else None),
            "client_mode_choices": CLIENT_MODE_MEANING if "client_mode" in (defaults or {}) else None,
            # 确认框那一格（10-13）：同样只在本键真在册时才交，且**措辞引 `EXIT_CLICK_MEANING` 一份**。
            "exit_confirm_click": (bool(merged.get("exit_confirm_click"))
                                   if "exit_confirm_click" in (defaults or {}) else None),
            "exit_confirm_click_help": (EXIT_CLICK_MEANING
                                        if "exit_confirm_click" in (defaults or {}) else None),
            # 时间带两格 + 每日定时重启（用户 2026-10-19 点名）。同样**只在本键真在册时才交**：
            # 空串在这里是合法值（＝按收盘链推导），所以交 `str(... or "")`，不折成 null 再猜。
            "client_band_lo": (str(merged.get("client_band_lo") or "")
                               if "client_band_lo" in (defaults or {}) else None),
            "client_band_hi": (str(merged.get("client_band_hi") or "")
                               if "client_band_hi" in (defaults or {}) else None),
            "client_band_help": (CLIENT_BAND_MEANING
                                 if "client_band_lo" in (defaults or {}) else None),
            "restart_daily_enabled": (bool(merged.get("restart_daily_enabled", True))
                                      if "restart_daily_enabled" in (defaults or {}) else None),
            "restart_daily_at": (str(merged.get("restart_daily_at") or "")
                                 if "restart_daily_at" in (defaults or {}) else None),
            "restart_daily_help": (DAILY_RESTART_MEANING
                                   if "restart_daily_at" in (defaults or {}) else None),
            # 频率档与"每周哪几天"（用户 2026-10-08 要的第二档）。同样**只在本键真在册时才交**：
            # 给一条间隔型循环配"每日/每周"下拉，等于又造一个改了也不生效的框。
            "run_freq": (str(merged.get("run_freq") or "daily") if "run_freq" in (defaults or {}) else None),
            "run_freq_choices": RUN_FREQ_CHOICES if "run_freq" in (defaults or {}) else None,
            "weekly_days": (str(merged.get("weekly_days") or "") if "weekly_days" in (defaults or {}) else None),
            "weekday_choices": WEEKDAY_CHOICES if "weekly_days" in (defaults or {}) else None,
            # 重试阶梯的时点表（第 10 条循环专有；同样**只在本键真在册时才交**）。
            # 交默认值是为了页面上"删了一格"之后还能一键退回出厂那串。
            "times": (str(merged.get("times")) if "times" in (defaults or {}) else None),
            "times_default": (str((defaults or {}).get("times")) if "times" in (defaults or {}) else None),
            "retry": self_retry_status() if "times" in (defaults or {}) else None,
            "cadence": LOOP_CADENCE.get(key) or (
                f"到点跑（{slot}）" if has_slot else "跟随获取段，成功才起"),
            "off_effect": off,
            "last_run_at": _text(st.get("last_run_at")), "last_ok_at": _text(st.get("last_ok_at")),
            "consecutive_failures": _int_or_none(st.get("consecutive_failures")),
            # 三态（ok / fail / **skipped**）：休市里一条按口径停住的循环不该被显示成
            # "连败 N"——那会把真故障埋进噪音里（10-08 实测 intraday 6、tdx_watch 117）。
            "last_status": _text(st.get("last_status")),
            "last_error": _text(st.get("last_error")),
            # `never_recorded` 不看"有没有在册默认值"：**活着却零笔记账**正是"看着有其实没跑"
            # 最省事的藏法（10-06 的 `intraday`：线程在，`state.json` 里连键都没有）。
            "never_recorded": not bool(st),
            # **这一格关不关得动，当场就说清**：`live_switch=false` 的那几条，开关写进
            # `config.json` 是真的，但本进程的循环体只在启动时读过它 —— 不重启就照跑。
            "live_switch": loop in LIVE_SWITCH_LOOPS,
            "switch_effect": (
                "下一个心跳就生效，不用重启" if loop in LIVE_SWITCH_LOOPS else
                "开关已写入，但这条循环只在启动时读一次开关 ⇒ 要重启 8910 才真停下来"),
            # **频度**（10-08 新增，`ths_sync` 那一档用户点名要能选"每日/每周"）：
            # 只有默认值里真有 `run_freq` 的键才交这两个字段，别的键页面上不渲染那排控件。
            "has_freq": bool(defaults and "run_freq" in defaults),
            "run_freq": (_text(merged.get("run_freq")) if defaults and "run_freq" in defaults else None),
            "weekly_days": (_text(merged.get("weekly_days"))
                            if defaults and "weekly_days" in defaults else None),
            # `weekly` 而 `weekly_days` 空 ⇒ `freq_allows` 按"判不了就放行"天天跑，
            # 与用户选的"每周"完全相反。这一格必须当场说出来，不能等人自己发现。
            "freq_warning": (
                "选了每周却一个周几都没勾 ⇒ 现在实际是天天跑。勾上周几，或改回每日"
                if defaults and "run_freq" in defaults and str(merged.get("run_freq")) == "weekly"
                and not str(merged.get("weekly_days") or "").strip() else None),
            "changed_at": _text(note.get("changed_at")), "changed_by": _text(note.get("changed_by")),
            "note": _text(merged.get("note")) if defaults else None,
            "warnings": warns,
        })
    return {
        "generated_at": now.strftime("%Y-%m-%dT%H:%M:%S+08:00"),
        "loops": rows,
        "alive_count": len(alive), "declared_count": len(LOOP_JOBS),
        "missing_loops": missing,
        # 按开关停用的另列一格：面板上"缺"要去查，"人关的"不用（10-08 的 ths_sync 默认关）
        "switched_off_loops": switched_off_loops(),
        "note": ("改的是 `jobs/config.json`，且 `enabled=false` 会一直留着、不会自己恢复 —— "
                 "关之前看清「关掉之后会怎样」那一列。"
                 "⚠️ **生效时机不是一条规则，是逐条的事实**：只有 `live_switch=true` 的那几条"
                 "是「下一个心跳就生效」，其余几条本进程的循环体只在启动时读过开关，"
                 "关掉后**照跑到重启 8910 为止**（这一列就写在每行上，别说成统一口径）。"
                 "`alive` 只回答线程在不在，有没有记账看那格的 `最近记账`。"),
        "live_switch_count": len(LIVE_SWITCH_LOOPS),
        "needs_restart_count": len(DEAD_UNTIL_RESTART_LOOPS),
        "needs_restart_loops": list(DEAD_UNTIL_RESTART_LOOPS),
        "explain": ("环境变量总闸（`VIBE_TDX_WATCH=0` 等）对非技术用户等于没有，"
                    "所以每一条都要能在这里关；关掉的代价逐条写在 `off_effect`，"
                    "关了到底停不停逐条写在 `switch_effect`。"),
    }


#: 能被开关的键 = 整张在册表，不只是收盘链那两段。
#: 10-06 的形状是"页面上看得见 7 条循环，其中 5 条没有开关"，只剩环境变量总闸 ——
#: 那对非技术用户等于没有；而 `coverage`/`tdx_watch`/`fundflow`/`watchlist` 都有"关掉会失去什么"的代价，
#: 代价大的那条（`fundflow` 当天不采永久少一天）恰恰最需要人能在界面上决定。
def _switchable(key: str) -> bool:
    return key in jstore.CLOSE_DEFAULTS or key == "news"


def _loop_of(key: str) -> str | None:
    return next((n for n, k, _l, _o in LOOP_JOBS if k == key), None)


def _switch_timing(key: str) -> tuple[bool, str]:
    """这条循环的开关**当场到底生效不生效** —— 一句话答完，别再套统一口径。"""
    loop = _loop_of(key)
    if loop is None:
        return True, ""
    if loop in LIVE_SWITCH_LOOPS:
        return True, "（下一个心跳就生效，不用重启）"
    return False, ("（⚠ 这条循环只在启动时读一次开关：**当前进程照跑，要重启 8910 才停下来**；"
                   "配置已经写进去了，重启后按它判）")


def set_switch(key: str, enabled: bool, via: str) -> tuple[int, dict]:
    """页面改开关。未知键给 404，成功回 200 + 新读数。"""
    if not _switchable(key):
        return 404, {"reason": f"未知作业 {key!r}：不在册（可用：{', '.join(sorted(jstore.CLOSE_DEFAULTS))}, news）。"
                               "要让它可关，先加进 `duanxian/jobs.py::CLOSE_DEFAULTS`"}
    jstore.set_enabled(key, bool(enabled), via=via)
    live, timing = _switch_timing(key)
    if not enabled:
        return 200, {"reason": f"{key} 已关闭：{'到点/下一轮都不再自动跑' if live else '到点后本进程还会再跑这一轮'}，"
                              f"且不会自己恢复{timing}"
                              f"（关掉会失去：{LOOP_OFF_EFFECT.get(key, '见作业说明')}）"}
    return 200, {"reason": f"{key} 已开启{timing or '（下一个心跳就按它跑，不用重启）'}"}


def set_note(key: str, note: str, via: str) -> tuple[int, dict]:
    """写"为什么停/为什么开"那句理由。留痕已经自动记了谁与何时，这里只补原因。"""
    if not _switchable(key):
        return 404, {"reason": f"未知作业 {key!r}"}
    text = str(note or "").strip()
    if len(text) > 120:
        return 400, {"reason": "理由请写在 120 字以内（这一格是给人快速看的，不是文档）"}
    jstore.write_setting(key, via=via, note=text or None)
    return 200, {"reason": "理由已记下" if text else "理由已清空"}


def set_slot(key: str, run_at: str, via: str) -> tuple[int, dict]:
    """页面改时点。**只有默认值里真有 `run_at` 的键才收**：
    给一个没人读的键配时刻 = 页面上多一个改了也不生效的框（`tdx_watch` 就是故意没有时点，
    它是"每两分钟看一眼"，不是"几点跑一次"；`fill_all` 跟在前一段后面）。"""
    if not _switchable(key):
        return 404, {"reason": f"未知作业 {key!r}"}
    defaults = jstore.CLOSE_DEFAULTS.get(key) or (jstore.FILL_ALL_DEFAULTS if key == "fill_all" else None)
    if not defaults or "run_at" not in defaults:
        why = {"fill_all": "加工段跟在获取段后面，给它配时刻等于配一个没人读的键 —— 改 `daily_close.run_at` 才有用",
               "tdx_watch": "探测循环没有时点：它是「每隔几分钟看一眼」，给它配时刻那个框改了也不生效"}.get(key,
                "这一条按自己的节奏轮询，没有「几点跑一次」这一说")
        return 400, {"reason": f"{key} 没有自己的时点：{why}"}
    why = jstore.valid("run_at", run_at)
    if why:
        return 400, {"reason": why + "；已按原值运行，没有采纳这次输入"}
    jstore.write_setting(key, via=via, run_at=run_at)
    return 200, {"reason": f"下一个心跳起按 {run_at} 判（不用重启）"}


def set_knob(key: str, field: str, value: Any, via: str) -> tuple[int, dict]:
    """页面上那两个"新格子"（`launch_lead_min` / `trading_days_only`）走的写入面。

    为什么不复用 `set_slot`：那条只认 `run_at`，而"给一个没人读的键配时刻 = 页面上多一个
    改了也不生效的框"这条警告仍然成立。注册表、取值范围（含 0 是否合法）、整笔校验
    全在 `jobs.set_setting` 一处，这里只把它翻译成 HTTP 语义。
    """
    if key not in jstore.CLOSE_DEFAULTS:
        return 404, {"reason": f"未知作业 {key!r}：不在册（可用：{', '.join(sorted(jstore.CLOSE_DEFAULTS))}）"}
    if field in ("enabled", "run_at", "note"):
        return 400, {"reason": f"{field} 请走它自己的那条路（enabled / run_at / note 各一个端点动作）"}
    why, resp = jstore.set_setting(key, via=via, **{field: value})
    if why:
        return 400, {"reason": why}
    return 200, {"reason": "已写入，下一个心跳就按新值判（不用重启）：%s.%s=%s"
                          % (key, field, value), "merged": resp.get("merged")}


def completeness_status() -> dict:
    """「条数完整性」那一格：判据、两个偏离值（各自从哪层取的）、各口径最新一场的基线。

    为什么值得单开一格：判"这一场的数全不全"决定收盘链要不要再去重下一遍，
    而它的阈值现在是**人可以在页面上改的条数**（用户 2026-10-07 的要求）。
    改了不显示 = 下一次误判时没人知道是按哪个数判的。
    """
    from duanxian import completeness

    s = completeness.summary()
    kinds = [{
        "kind": r["kind"], "label": r["label"], "unit": r.get("unit"),
        "latest": r.get("latest"), "count": r.get("count"),
        "baseline": r.get("baseline"), "baseline_date": r.get("baseline_date"),
        "baseline_basis": r.get("baseline_basis"), "sessions_back": r.get("sessions_back"),
        "deviation": r.get("deviation"), "deviation_from": r.get("deviation_from"),
        "sessions": r.get("sessions"), "floor": r.get("floor"),
        "measured": r.get("measured"),
    } for r in s.get("kinds") or []]
    return {
        "available": True,
        "rule": "本场条数 ≥ 基线（前一天的条数）− 偏离值；偏离值是条数，不是百分比",
        "deviation": s.get("deviation"), "deviation_from": s.get("deviation_from"),
        "deviation_minute": s.get("deviation_minute"),
        "deviation_minute_from": s.get("deviation_minute_from"),
        "kinds": kinds,
        "explain": ("分钟类单独一档不是宽松：2026-10-07 逐日量相邻两场的真实回退，"
                    "日线文件（.day）最大 −11 条，而 1/5 分钟线文件（.lc1/.lc5）最大 −107 条"
                    "（PG 分钟线只数最大 −143 条）—— 停牌那天就是没有那些文件。"
                    "把分钟类也压成 30，这种正常日子会天天判成不全，收盘链就天天去重启通达信重下。"
                    "设成 0 = 少一条都算不全。"),
    }


def set_completeness(via: str, *, deviation: int | None = None,
                     deviation_minute: int | None = None,
                     note: str | None = None) -> tuple[int, dict]:
    """页面改偏离值。**两个数一次只改一件事**（与 `enabled/run_at/note` 同一套规矩），
    非法值整笔不写入并把原因端回去。"""
    touched = {"deviation": deviation, "deviation_minute": deviation_minute, "note": note}
    given = {k: v for k, v in touched.items() if v is not None}
    if not given:
        return 400, {"reason": "没有要改的项（可用：deviation / deviation_minute / note）"}
    if len(given) > 1:
        return 400, {"reason": "一次只改一件事，收到 " + "、".join(given)}
    why, resp = jstore.set_setting("completeness", via=via, **given)
    if why:
        return 400, {"reason": why}
    return 200, {"reason": "已写入，下一场就按新值判（不用重启）：%s"
                          % "、".join("%s=%s" % (k, v) for k, v in given.items()),
                 "merged": resp.get("merged")}


def build() -> dict:
    now = china_now()
    # 时点与它的来源都只有一份读法（`jobs.run_at`）：页面上报的时刻必须就是宿主
    # 真正在用的那个，两边各读各的就会出现"显示 15:59、实际按别的跑"
    slot, slot_from = jstore.run_at()
    news = _file_job("news", "快讯采集", "news-", "news", 30, "30s")
    intraday = _file_job("intraday_snapshot", "盘中快照", "", "intraday", None, "SNAPSHOT_SLOTS")
    jobs = [news, intraday, _backup_job(now),
            _recorded_job("daily_close", "收盘即时链（获取段）", f"{slot}（{slot_from}）", 2400,
                          ["market_data.stock_daily", "market_data.index_daily",
                           "market_data.sector_daily", "market_data.stock_minute"],
                          due_at=slot, now=now),
            _recorded_job("fill_all", "追平与加工链（加工段）", "跟随获取段，成功才起", 5400,
                          ["analytics.*", "market_data.*", "qd.*"],
                          after="daily_close", now=now)]
    # ⚠️ 这一行以前是一段**手写死的假读数**：`available=false` + "YuanShan 内还没有资金流
    # 采集的执行体…`fund_flow_rank` 现空表"。而实际上 `c21_fundflow_sina` 早就接进了常驻
    # 循环（`fundflow-sina`），2026-09-30 20:50 还在成功记账，表里也有两天各约 5,570 行。
    # 那就是"看着有其实没跑"的镜像版本 —— **跑着的东西被页面报成没接**，谁也不会去查它。
    # 现在与收盘链同一份读法：时间来自 `jobs/state.json`，开关来自 `jobs/config.json`。
    ff_slot, _ff_from = jstore.run_at(name="fundflow", env_var="FUNDFLOW_AT",
                                      defaults=jstore.FUNDFLOW_DEFAULTS)
    ff = _recorded_job("fundflow", "个股主力资金流采集（新浪，只给当天）",
                       f"{ff_slot}（作业配置 / 默认）", 900,
                       ["market_data.fund_flow_rank"], due_at=ff_slot, now=now)
    ff["reason"] = ("新浪即时截面，报文里没有日期字段 ⇒ 一天不采就永久少一天，补不回来；"
                    "同一天不重采（要靠 `--rerun` 显式覆盖）")
    jobs.append(ff)
    # 两条常驻只读/重算循环同样要"看得见"：不在册 = 缺了也没人报（2026-10-06 自查）。
    cov_slot = _coverage_slot_for_page()
    jobs.append(_recorded_job("coverage", "覆盖体检每日重算（首屏靠它）",
                              f"{cov_slot}（作业配置 / 默认）", 120,
                              ["只读：32 张表的新鲜度快照（写缓存文件，不写库）"],
                              due_at=cov_slot, now=now))
    tw = _recorded_job("tdx_watch", "通达信看护（探测 + 收进程 + 下载阶梯）",
                       "每 2 分钟一次（无时点）", 5,
                       ["枚举进程/窗口 → `jobs/state.json`；`TPyth*` 赖着不走且过了年龄阈值 → "
                        "`kill_tree` 收掉（绝不动 `TdxW.exe`）；下载进行中每 10 分钟判一次条数，"
                        "全了关窗收进程、两档不全就全重启客户端"], now=now)
    tw["next_due_at"] = None       # 它没有"下一次到点的时刻"，别给页面编一个
    tw["reason"] = ("开着但停在登录框上 = TQ 那条腿取不到数，这一格会记成 not-ok；"
                    "它会动界面（关窗、收进程、必要时重启客户端）—— 每一次都进"
                    "`/api/jobs/chain` 的 `tdx_trail`，没做成的那条写着人怎么补。"
                    "不想让它动界面就关 `auto_restart`（阶梯只查不重启）")
    jobs.append(tw)
    ft_state = _ft_scheduler()
    ft_rows = _financetool_jobs(ft_state)
    # **同 key 不许出现两行**。FT 的 `config.job` 里也有 `fill_all`（"一键追平到最新收盘日"），
    # 与本进程的加工段是同一件事：两行并存时 `POST /api/jobs/<key>/run` 不知道触发哪一个，
    # 页面也拿同一个 key 当两个 React 项的标识。取代关系要说出来，不能悄悄删一行 ——
    # 少了的那行是"另一个进程登记过这件事"的事实。
    mine = {j["key"] for j in jobs}
    replaced = [r["key"] for r in ft_rows if r["key"] in mine]
    for row in jobs:
        if row["key"] in replaced:
            row["basis"] = (f"{row['basis']}；FT 的 config.job 里同名项 {row['key']} "
                            "已由本进程的执行体取代，不再单独列一行")
    jobs.extend([r for r in ft_rows if r["key"] not in mine])
    if ft_state["known"]:
        ft_note = (f"FinanceTool 调度器：进程{'活着' if ft_state['alive'] else '没在跑'}"
                   f"（心跳 {ft_state['heartbeat_at']}，pid {ft_state['pid']}），"
                   f"但 enabled={ft_state['enabled']}、dry_run={ft_state['dry_run']}、"
                   f"fired={ft_state['fired']} ⇒ 每个 tick 只发心跳、不入队；"
                   f"它眼里有 {ft_state['jobs_seen']} 个作业，目标日 {ft_state['target']}")
    else:
        ft_note = ("FinanceTool 调度器：operations.scheduler_heartbeat 里没有可用心跳"
                   "（表为空、行缺失，或心跳时间不可解析）")
    alive_loops, missing_loops = _live_loops()
    always_loops = [n for n in HOST_LOOPS if n not in ON_DEMAND_LOOPS]
    live_always = [n for n in alive_loops if n in always_loops]
    loop_note = (f"本进程常驻循环 {len(live_always)}/{len(always_loops)} 条活着"
                 f"（{'、'.join(live_always)}）" if not missing_loops else
                 f"⚠ 本进程常驻循环缺 {len(missing_loops)} 条："
                 f"{'、'.join(missing_loops)} —— 缺 daily-close 就是"
                 "「今天 15:59 不会自己跑」，改完宿主要重启才生效")
    # 按需起的那几条单列一句：起没起都要说出口，但不许混进上面那个"缺"字。
    on_demand_state = [{"name": n, "alive": n in alive_loops} for n in ON_DEMAND_LOOPS]
    if on_demand_state:
        loop_note += ("；按需起 " + "、".join(
            "%s（%s）" % (r["name"], "已起" if r["alive"] else "还没起：没人订阅盯盘，这是设计")
            for r in on_demand_state))
    return _json_safe({
        "generated_at": _iso(now),
        "scheduler": {"enabled": True, "dry_run": False, "host": "yuanshan-uvicorn",
                      "pid": os.getpid(), "started_at": None, "heartbeat_at": None,
                      # 页面现在不读这两个数组（契约 §2 里没有它们），但它们是
                      # 唯一能证明"重启真的把新宿主装上了"的读数 —— 加进 note 里
                      # 人就看得见，不用去猜进程跑的是哪份代码
                      "host_loops": alive_loops, "host_loops_missing": missing_loops,
                      # 按需起的单独一档（名字 + 此刻起没起）：它们不在上面两个数组的"缺"里，
                      # 但没有这一格，界面就无法区分"按设计没起"与"接线断了"
                      "host_loops_ondemand": on_demand_state,
                      "note": f"本字段只描述本进程宿主（快讯/盘中快照为常驻线程）。"
                              f"{loop_note}。{ft_note}。"
                              f"FinanceTool 登记的作业不走这个开关，见各自 available/reason。"},
        "scheduler_available": True, "scheduler_reason": None,
        "jobs": jobs, "jobs_available": True, "jobs_reason": None,
        "truncated": False,
    })


def _run_backup() -> tuple[bool, str]:
    """在本项目路径下跑本项目那份备份（不是去调旧项目）。约 1~2 分钟，含恢复验证。"""
    root = str(Path(__file__).resolve().parents[1])
    p = subprocess.run([sys.executable, "-X", "utf8", "-m", "app.ops.backup", "daily"],
                       cwd=root, capture_output=True, timeout=3600)
    out = (p.stdout or b"").decode("utf-8", "replace").strip().splitlines()
    return p.returncode == 0, (out[-1] if out else "退出码 %d" % p.returncode)


def trigger(key: str) -> tuple[int, dict]:
    """§5：只有真有执行体的才给触发。同作业在跑 → 409。

    互斥走 `jobs.claim`，**与 15:59 的常驻宿主同一个锁**。两边各判各的"没在跑"
    时会同时起两个 `daily_close_sync`，而它点的是同一个通达信下载窗口 ——
    结果是两边都判"数据没到位"，日志看着像源坏了。
    """
    if key not in ("news", "backup") and key not in jstore.CLOSE_CHAIN:
        return 403, {"reason": "本进程没有该作业的执行体，本次未触发（有真执行体才给按钮）"}
    if not jstore.claim(key):
        return 409, {"reason": "该作业正在运行（定时宿主或另一次手动触发），本次未触发"}

    def _go():
        try:
            if key in jstore.CLOSE_CHAIN:
                # 锁已经在上面抢到了，这里走"只跑不抢"的那一层；
                # 读法与宿主完全同一份（`jobs.exec_close_stage`）
                jstore.exec_close_stage(key, {"manual": True})
                return
            if key == "backup":
                ok, detail = _run_backup()
                jstore.record(key, ok=ok, detail={"output": detail})
                return
            from duanxian import newswire as nw
            res = nw.collect_once(nw.read_cursors())
            nw.persist(res)
            jstore.record(key, ok=bool(res.get("available")),
                          detail={"rows": len(res.get("rows") or []), "reason": res.get("reason")})
        except Exception as exc:      # noqa: BLE001  后台线程不能把异常漏出去
            jstore.record(key, ok=False, detail={"reason": f"{type(exc).__name__}: {exc}"})
        finally:
            jstore.release(key)

    threading.Thread(target=_go, name=f"job-{key}", daemon=True).start()
    return 202, {"run_id": None, "status": "queued"}


# ---------------------------------------------------------------- 行情外链地址（按市场可配置）
# 用户 2026-10-08：「针对不同市场的股票，在系统配置里做一个超链接配置的功能点：
# 除了代码以外的网址写在这里，代码用 ****** 替换；北证写 https://quote.eastmoney.com/bj/******.html」
# 判据与出厂表只住 `app/quote_link.py` 一处，这里做的是"读出来 + 改得动 + 立刻生效"。


def quote_links_status() -> dict:
    """每一行 = 一个市场/品种，值就是那条含 `******` 的模板，并给出该行的样例地址。

    样例不是装饰：`sample_url` 是后端**真的拼出来**的地址，页面上点一下就知道改对没有。
    非法值不静默回落出厂——那会让人以为"我改的那一格生效了"；`bad` 里带回原因。
    """
    from app import quote_link as ql
    from duanxian import jobs as jstore

    merged, warns = jstore.settings("quote_links", dict(ql.DEFAULT_TEMPLATES, note=""))
    active = ql.configured_templates()
    rows = []
    for rid, label, default, sample in ql.LINK_ROUTES:
        raw = merged.get(rid)
        rows.append({
            "id": rid, "label": label, "default": default, "template": active.get(rid, default),
            "configured": raw is not None, "changed": (raw is not None and raw != default),
            "bad": ql.bad_template(raw) if raw is not None else None,
            "sample_code": sample, "sample_url": ql.em_url(sample, templates=active),
        })
    note = jstore.change_note("quote_links") or {}
    return {"placeholder": ql.PLACEHOLDER, "routes": rows, "warnings": list(warns or []),
            "changed_any": any(r["changed"] or r["bad"] for r in rows),
            "note": merged.get("note") or "",
            "changed_at": note.get("changed_at"), "changed_by": note.get("changed_by"),
            "explain": ("改一处全站生效（股票名、榜单、深链页面上的外链都读这一张表），"
                        "不用重启；每行右边的样例地址就是按当前模板拼出来的，点开验一次最省事。")}


def set_quote_link(rid: str, value: str, via: str = "") -> tuple[int, dict]:
    """改一行（一次只改一行，与所有配置同一份写入面：校验 + 留痕 + 整笔拒）。"""
    from app import quote_link as ql
    from duanxian import jobs as jstore

    if rid not in ql.ROUTE_IDS:
        return 404, {"reason": f"没有这一行 {rid!r}（可用：{'、'.join(ql.ROUTE_IDS)}）"}
    why, _resp = jstore.set_setting("quote_links", via=via, **{rid: str(value or "").strip()})
    if why:
        return 400, {"reason": why}
    ql.reset_template_cache()        # 15 秒的 TTL 缓存立刻作废，别让人等
    st = quote_links_status()
    row = next((r for r in st["routes"] if r["id"] == rid), {})
    mark = "已恢复出厂值" if row.get("default") == row.get("template") else "已保存"
    return 200, {"data": st,
                 "reason": f"{mark}：{rid} 现在拼出来的是 {row.get('sample_url') or '（拼不出，检查占位符）'}"}
