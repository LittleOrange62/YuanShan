"""消息推送的配置层：渠道与定时计划的**唯一来源**（只用标准库）。

为什么单独一个文件、还只用标准库：照 `netconfig.py` 的规矩 ——
`scripts/manage.py` 在依赖还没装的时候也要能读出"配了几个渠道、有没有密钥没填"，
而发送侧（`duanxian/notify.py`）要拉 pandas/psycopg/apprise。两边共用这一份判据，
才不会出现"页面说配好了、发送时才发现是坏值"。

配置落在业务数据根下（与 `jobs/config.json`、`net/config.json` 同一套规矩，
见 `docs/系统配置工作指南/03-环境变量与作业配置.md`），**不落 PG**：

    <ASTOCK_DATA_HOME>/notify/config.json

    {"enabled": true,
     "channels": [{"key": "feishu-main", "kind": "feishu", "label": "主通知群",
                   "url": "feishu://<token>", "url_env": "", "enabled": true, "note": ""}],
     "plans":    [{"id": "p1", "name": "收盘后·自选与共振", "run_at": "16:20",
                   "run_freq": "daily", "weekly_days": "", "trade_days_only": true,
                   "enabled": true, "channels": ["feishu-main"], "scenes": {...}, "note": ""}]}

四条硬规矩，错了都会变成"看着配好了其实没发"：

1. **密钥不落屏幕**。渠道 URL 里带 token/密码，所以**任何读数接口默认只交掩码**
   （`masked_channels()`），要真串只能在本进程内拿（`real_url()`）。
   留空 `url` 不等于"删掉密钥"—— 编辑时留空表示"这一项不改"，与 `jobs.py set` 的
   "清空=交还默认"故意不同：那是配置键，这是凭据。
2. **推荐把真串放 `.env`，配置里只写变量名**（`url_env=NOTIFY_FEISHU_URL`）。
   这样 config.json 被复制/截图/贴进会话时不会泄密；两条都填时 `url_env` 优先。
3. **每次写入都盖章**（`_changed_at/_changed_by`）—— 与作业开关、白名单同一套留痕。
   09-28 那三次"为什么被关了"就是因为开关有三份读法、留痕只有一份。
4. **渠道种类只有四种**（邮件 / 企业微信群助手 / 钉钉群助手 / 飞书群助手，用户 10-08 圈的口径）。
   短信、syslog、SNMP TRAP **本程没有这条通道**，界面上不许出现"能填但没人读"的空框。
"""
from __future__ import annotations

import json
import os
import re
import threading
from datetime import datetime
from pathlib import Path

CONFIG_FILE = "notify/config.json"
HISTORY_FILE = "notify/history.jsonl"
MAX_CHANNELS = 12
MAX_PLANS = 20
SECRET_KEEP = 4          # 掩码时保留的尾部字符数（够认出是哪条，不够拿去用）
#: 到点之后还认多久的"没发过"（`due_minute`/`slot_state` 同一份）。
#: 宿主是"处理完再睡 60 秒"，所以没有这个窗口时，一条慢发送或一次重启就能把那一分钟整片错过。
LATE_WINDOW_MIN = 15

#: 支持的四种渠道 → `(apprise 的 URL 前缀, 中文名, 这一类必须有什么)`。
#: `must` 里的名字是**校验用的字段名**，不是 apprise 的参数名：
#: `token`=机器人 webhook 里的密钥段，`to`=收件人，`host`=SMTP 服务器。
KINDS = {
    "feishu": ("feishu", "飞书群助手", ("token",)),
    "dingtalk": ("dingtalk", "钉钉群助手", ("token",)),
    "wecom": ("wecombot", "企业微信群助手", ("token",)),
    "mail": ("mailto", "邮件（SMTP）", ("host", "to")),
}
KIND_LABELS = {k: v[1] for k, v in KINDS.items()}

#: **用户从飞书/钉钉/企微后台复制到的是一条完整 https 地址**，不是我们内部那种
#: `feishu://<token>`（10-16 用户就是卡在这里："加不进去"）。两种形状都收，存下也照原样存，
#: 发的时候由 `duanxian/notify.py::_bot_target` 拆 —— 让用户改抄送地址的成本比我们自己多写一条分支高。
OFFICIAL_URLS = {
    "feishu": "https://open.feishu.cn/open-apis/bot/v2/hook/",
    "dingtalk": "https://oapi.dingtalk.com/robot/send",
    "wecom": "https://qyapi.weixin.qq.com/cgi-bin/webhook/send",
}
#: 哪一格该带「安全设置 / 加密密钥」（签名校验）。企微的群机器人只有 key、没有加签。
SECRET_KINDS = ("feishu", "dingtalk")

#: 可多选的内容块（"发什么"）。**判据/读数一律不在这个文件里**，
#: 这里只登记"有这一档、面板要渲染它"，真组装住 `duanxian/notify.py::SCENE_BUILDERS`。
#: 两边不同步就会长出"面板能勾、发出来是空的"那种假功能 —— 由测试钉住。
SCENE_KEYS = ("confluence", "screen", "watchlist", "dossier", "positions", "market",
              "voldbl", "breakout", "ambush", "quadrant", "abnormal", "spike",
              "trendstock", "sector", "groupanalytics", "news", "allocator")
#: 每一档能配的那些格子见下面的 `SCENE_FIELDS`（原先这里是"勾选了就必须给参数"的三行写死名单，
#: 加一档就得再抄一行，而且挡不住"面板能填、执行体读不到"的那种半截功能）。

#: 面板上「这一档还能怎么调」的**唯一登记表**（用户 10-17 要的"内容根据各个点来配置，
#: 再多一些、再具体一点"就落在这张表上）。一条描述符 = 一个可填的格子：
#:
#:   key/label/type   —— 写进 `scenes.<档>.<key>` 的键、面板上的中文、控件形状
#:   min/max/default/unit —— 数字格的上下限、留空时后端执行体用的那个默认、单位
#:   choices / choices_from —— 静态候选（写在这里）或从 `scene_catalog()` 现读的那份清单
#:   empty_means —— 留空的**含义**（不是"必填"）。这一格是 10-16 那次教训的形状：
#:                   留空有含义的字段被当必填拒掉，后端那条兜底就永远走不到
#:   empty_error —— 留空就是发空白 ⇒ 校验当场拦
#:   bit_prefix/bit_suffix —— 列表行摘要怎么把这一格说成人话（"≥3"、"只发其中 2 个策略"）
#:
#: ⚠️ 每一格的取值都**必须是那份读层接口自己就接受的参数**（`limit`/`min_energy`/`signal_type`…），
#: 不在这里新造判据：阈值归各页管（比如筛选规则的阈值去「条件筛选」页改），
#: 推送只负责"选哪一档、按现成接口已有的开关取多少"。加一档内容 = 后端加一行执行体 + 这张表加几行描述符，
#: 界面（勾选、参数框、摘要、校验）全部自动跟上，不需要再动前端。
SCENE_FIELDS: dict[str, tuple[dict, ...]] = {
    "confluence": (
        {"key": "min_hits", "label": "共振门槛（几个策略同日选中）", "type": "int",
         "min": 1, "max": 6, "default": 2, "unit": "个", "bit_prefix": "≥",
         "note": "上限＝在册策略个数，面板上那一格按 catalog 现读"},
        {"key": "sources", "label": "只发这些策略选中的票", "type": "multi",
         "choices_from": "confluence_sources", "empty_means": "全部策略",
         "empty_error": "勾了「只发指定策略」却一个策略都没选 ⇒ 要么勾几个，要么整项留空（＝全部策略）",
         "bit_suffix": "只发其中 %s 个策略",
         "note": "多选＝取并集；票被别的策略命中时那些策略照样列在行里"},
    ),
    "screen": (
        {"key": "rules", "label": "筛选规则", "type": "multi", "choices_from": "screen_rules",
         "as_objects": True, "required": True, "empty_error": "一条规则都没选 ⇒ 这一块会发出空白",
         "bit_suffix": "%s 条规则"},
        {"key": "universe", "label": "票池", "type": "select",
         "choices": [{"value": "watchlist", "label": "自选池"}, {"value": "market", "label": "全市场"}],
         "default": "watchlist"},
    ),
    "watchlist": (
        {"key": "group_id", "label": "自选分组", "type": "select", "numeric": True,
         "choices_from": "watchlist_groups", "empty_means": "全部分组", "bit_prefix": "分组 "},
        {"key": "pct_min", "label": "只报涨幅不低于", "type": "num", "min": -20, "max": 20,
         "default": None, "unit": "%", "bit_prefix": "涨幅≥", "bit_suffix": "%",
         "note": "留空＝全部照列。这是对已读出那几行的收窄，不是新判据"},
    ),
    "dossier": (
        {"key": "codes", "label": "重点个股", "type": "codes",
         "empty_means": "按自选前几只", "bit_suffix": "指定 %s 只"},
        {"key": "blocks", "label": "档案里带哪几块", "type": "multi",
         "choices_from": "dossier_blocks", "empty_means": "六块全带", "bit_suffix": "带 %s 块",
         "note": "正文太长会被渠道上限截掉（截掉的那一段看不见），少勾几块比截断好"},
    ),
    "positions": (
        {"key": "only_loss", "label": "只列亏损的持仓", "type": "bool",
         "bit_suffix": "只报亏损"},
    ),
    "market": (
        {"key": "limit", "label": "连板天梯列几层", "type": "int"},
    ),
    # ---- 以下 11 档是 10-17 加的：每一档都调本页已在用的那份读层，参数就是接口自己的参数
    "voldbl": (
        {"key": "min_energy", "label": "近 40 场量能加权分不少于", "type": "num",
         "min": 0, "max": 40, "default": 0, "unit": "分", "bit_prefix": "加权分≥"},
        {"key": "quadrants", "label": "限定四维象限", "type": "multi",
         "choices_from": "quadrant_options", "empty_means": "不限象限",
         "bit_suffix": "限定 %s 个象限"},
    ),
    "breakout": (
        {"key": "signal_type", "label": "只要哪一类突破", "type": "select",
         "choices_from": "breakout_types", "empty_means": "三类都要", "bit_prefix": ""},
    ),
    "ambush": (
        {"key": "types", "label": "只要哪几类埋伏", "type": "multi",
         "choices_from": "ambush_types", "empty_means": "五类都要", "bit_suffix": "%s 类埋伏"},
        {"key": "min_weekly_ups", "label": "近 6 周里上涨周数不少于", "type": "int",
         "min": 0, "max": 10, "default": 0, "unit": "周", "bit_prefix": "周涨≥"},
        {"key": "only_strict", "label": "只要严格通过的", "type": "bool", "bit_suffix": "严格档"},
    ),
    "quadrant": (
        {"key": "min_x", "label": "X 个股质量分不少于", "type": "num",
         "min": -100, "max": 100, "default": None, "bit_prefix": "X≥"},
        {"key": "min_y", "label": "Y 环境共振分不少于", "type": "num",
         "min": -100, "max": 100, "default": None, "bit_prefix": "Y≥"},
    ),
    "abnormal": (
        {"key": "recent", "label": "近期严重异动往回看几场", "type": "select",
         "choices_from": "abnormal_recent_days", "default": 10},
        {"key": "hot_limit", "label": "涨幅偏离榜列几只", "type": "int",
         "min": 1, "max": 100, "default": 20, "unit": "只", "bit_suffix": "偏离榜 %s 只"},
    ),
    "spike": (
        {"key": "tags", "label": "只要哪几类冲高", "type": "multi",
         "choices_from": "spike_tags", "empty_means": "全部类型", "bit_suffix": "%s 类形态"},
        {"key": "min_hi", "label": "盘中冲高幅度不少于", "type": "num",
         "min": 0, "max": 30, "default": None, "unit": "%", "bit_prefix": "冲高≥", "bit_suffix": "%"},
    ),
    "trendstock": (
        {"key": "lookback", "label": "连续站上 5 日线看几天", "type": "select",
         "choices_from": "trend_lookbacks", "default": 5},
    ),
    "sector": (
        {"key": "days", "label": "轮动往回列几日", "type": "int",
         "min": 1, "max": 5, "default": 1, "unit": "日", "bit_prefix": "近", "bit_suffix": " 日"},
    ),
    "groupanalytics": (
        {"key": "window", "label": "分组趋势按近几场算", "type": "int",
         "min": 3, "max": 60, "default": 10, "unit": "场", "bit_prefix": "近", "bit_suffix": " 场"},
        {"key": "only_focus", "label": "只列标了重点的分组", "type": "bool", "bit_suffix": "只看重点组"},
    ),
    "news": (
        {"key": "keyword", "label": "标题里要出现这几个字", "type": "text",
         "max_len": 20, "empty_means": "不过滤", "bit_prefix": "含「", "bit_suffix": "」"},
    ),
    "allocator": (
        {"key": "parts", "label": "带哪几块榜", "type": "multi",
         "choices": [{"value": "toll", "label": "收费站榜"}, {"value": "crowding", "label": "拥挤度榜"},
                     {"value": "stages", "label": "阶段分布"}],
         "empty_means": "三块都带", "bit_suffix": "%s 块榜",
         "note": "两张榜各自的排法由后端定（收费分 / 拥挤温度），这里不另排一遍"},
    ),
}

#: 面板"发什么"那一排后面的长说明。**每个 `SCENE_KEYS` 里的人都得有一条** ——
#: 少了就只剩短名，用户看不出这一档与隔壁那档差在哪（由测试钉住同集合）。
SCENE_HINTS = {
    "confluence": "策略共振榜（几个策略同时选中）",
    "screen": "条件筛选结果（自选池/全市场，可多条规则）",
    "watchlist": "自选股当日明细（可按分组、可按涨幅收窄）",
    "dossier": "个股量化档案（内容最全，逐票，可选带哪几块）",
    "positions": "持仓与浮动盈亏（可只报亏损）",
    "market": "市场概览（情绪周期/连板天梯）",
    "voldbl": "倍量形态榜（近 40 场倍量次数 × 贴 MA60，可按象限筛）",
    "breakout": "突破信号（缩量收敛后首破：首破/放量续攻/仍在收敛）",
    "ambush": "埋伏票榜（均线汇集、横盘蓄势那五类）",
    "quadrant": "四维象限榜（X 个股质量 × Y 环境共振，可给两轴设门槛）",
    "abnormal": "异动筛查（偏离值口径的涨幅榜/前瞻榜/近期严重异动）",
    "spike": "盘中冲高异动（急拉、触板、开板回封那几类形态）",
    "trendstock": "趋势股榜（连续站上 5 日线且今日未封板）",
    "sector": "板块轮动（今日领涨板块与连日重复度）",
    "groupanalytics": "自选分组分析（分组等权分、涨跌家数、振幅）",
    "news": "快讯要闻（当天采集的财经快讯，可按标题关键词筛）",
    "allocator": "分配者视角（板块收费站分、拥挤温度、阶段）",
}

_write_lock = threading.Lock()
_TIME_RE = re.compile(r"^([01]?\d|2[0-3]):[0-5]\d$")


def dt_now() -> str:
    return datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S")


def data_home() -> Path:
    """与 `duanxian/paths.py`、`scripts/manage.py::data_home` 同一条规则（这里不能 import 它们）。"""
    return Path(os.environ.get("ASTOCK_DATA_HOME") or (Path.home() / ".duanxian-agents"))


def config_path() -> str:
    """配置位置。`VIBE_NOTIFY_CONFIG` 只给测试注入用，日常不要设。"""
    override = os.environ.get("VIBE_NOTIFY_CONFIG", "").strip()
    if override:
        return override
    return str(data_home() / CONFIG_FILE)


def history_path() -> str:
    return os.environ.get("VIBE_NOTIFY_HISTORY") or str(data_home() / HISTORY_FILE)


def defaults() -> dict:
    return {"enabled": True, "channels": [], "plans": []}


# ---------------------------------------------------------------- 读写
def load() -> dict:
    """读配置。**读不到就按"一个渠道都没配"** —— 推送这道口宁可沉默，
    也不能拿"未知"当"已就绪"（那会让人以为手机收不到是网络的错）。

    坏文件必须出声（`_warning`）：静默回落会让用户以为"我配了但软件不认"，
    而实际是文件写坏了，两种说法对应的动作完全不同。
    """
    path = config_path()
    out = defaults()
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return out
    except (OSError, ValueError) as exc:
        out["_warning"] = f"配置文件读不了（{type(exc).__name__}），按「未配置渠道」运行：{path}"
        return out
    if not isinstance(raw, dict):
        out["_warning"] = f"配置内容不是对象（{type(raw).__name__}），按「未配置渠道」运行：{path}"
        return out
    out["enabled"] = bool(raw.get("enabled", True))
    chans = raw.get("channels")
    out["channels"] = [c for c in chans if isinstance(c, dict) and c.get("key")] \
        if isinstance(chans, list) else []
    plans = raw.get("plans")
    out["plans"] = [p for p in plans if isinstance(p, dict) and p.get("id")] \
        if isinstance(plans, list) else []
    for key in ("_changed_at", "_changed_by"):
        if raw.get(key):
            out[key] = str(raw[key])
    if isinstance(raw.get("channels"), list) and chans and not out["channels"]:
        out["_warning"] = "channels 里没有任何一条是带 key 的对象，按「未配置渠道」运行"
    return out


def save(next_state: dict, *, via: str = "") -> dict:
    """整份覆盖写入（原子：tmp + fsync + replace），并盖章"谁、什么时候"。"""
    payload = {"enabled": bool(next_state.get("enabled", True)),
               "channels": list(next_state.get("channels") or []),
               "plans": list(next_state.get("plans") or []),
               "_changed_at": dt_now(),
               "_changed_by": via or "未知调用方"}
    if len(payload["channels"]) > MAX_CHANNELS:
        raise ValueError(f"渠道最多 {MAX_CHANNELS} 条")
    if len(payload["plans"]) > MAX_PLANS:
        raise ValueError(f"定时计划最多 {MAX_PLANS} 条")
    path = Path(config_path())
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with _write_lock:
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, path)
    return payload


# ---------------------------------------------------------------- 校验
def validate_kind(kind) -> str | None:
    if kind not in KINDS:
        return (f"渠道种类只支持 {'、'.join(KIND_LABELS[k] for k in KINDS)}"
                f"（收到 {kind!r}）。短信/syslog/SNMP TRAP 本程没有这条通道，"
                "不是填不了，而是本程没有它的执行体，界面上也不给那个框")
    return None


def validate_channel(ch: dict, *, allow_blank_secret: bool = False) -> str | None:
    """一条渠道配置能不能用。返回 None 表示可用，否则一句人话。"""
    if not isinstance(ch, dict):
        return f"渠道应为对象，收到 {type(ch).__name__}"
    key = str(ch.get("key") or "").strip()
    if not key:
        return "渠道缺少 key（页面靠它定位这一条）"
    if not re.fullmatch(r"[a-zA-Z][a-zA-Z0-9_\-]{0,31}", key):
        return f"渠道键 {key!r} 只能用字母开头的字母数字下划线短横，不超过 32 字"
    why = validate_kind(ch.get("kind"))
    if why:
        return f"{key}: {why}"
    prefix, label, must = KINDS[ch["kind"]]
    url = str(ch.get("url") or "").strip()
    env = str(ch.get("url_env") or "").strip()
    if allow_blank_secret and not url and not env:
        return None                      # 编辑时留空 = 不改这一条的凭据
    if not url and not env:
        return f"{key}（{label}）既没填 URL 也没指环境变量 ⇒ 这条发不出去"
    if env and not re.fullmatch(r"[A-Z][A-Z0-9_]{2,63}", env):
        return f"{key} 的 url_env={env!r} 不像个变量名（要全大写字母/数字/下划线）"
    if env and not url:
        # **变量指向的那条地址也查**：只查变量名形状会放过"种类选了飞书、而 .env 里那条还是
        # 钉钉地址"这种组合 —— 面板标着「可用」、计划算成 sendable，实发时被对面一句拒掉，
        # 而那是最难受的一类错（一切看起来都绿，只有手机上一片安静）。
        resolved = str(env_value(env) or "").strip()
        if resolved:
            why = validate_channel({"key": key, "kind": ch["kind"], "url": resolved})
            if why:
                return f"{why}（这条来自变量 {env}）"
    if url:
        official = OFFICIAL_URLS.get(ch["kind"], "")
        if official and url.startswith(official):
            # 用户从群机器人设置页复制到的就是这一条 —— 必须收，不能让他改成我们的内部写法
            from urllib.parse import parse_qs, urlsplit
            tail = urlsplit(url)
            q = {k: v[0] for k, v in parse_qs(tail.query).items()}
            seg = tail.path.rstrip("/").rsplit("/", 1)[-1] if ch["kind"] == "feishu" else ""
            token = seg or q.get("access_token") or q.get("key") or ""
            if not token:
                return (f"{key}（{label}）粘贴的地址里没找到 webhook 密钥段"
                        f"（飞书看路径最后一段，钉钉看 access_token=，企微看 key=）")
            if "test" in token:
                return f"{key}（{label}）的 webhook 看着还是占位串（含 'test'），换成真地址再存"
            return None
        if not url.startswith(f"{prefix}://"):
            return (f"{key}（{label}）的 URL 前缀不对：应以 {prefix}:// 开头，"
                    f"或直接粘贴群机器人设置页那条 {official}…；收到 {url.split('://')[0]!r}")
        body = url[len(prefix) + 3:]
        if not body.strip("/"):
            return f"{key}（{label}）的 URL 里没有凭据/收件人，只有前缀 ⇒ 发了也是空响"
        if ch["kind"] == "mail":
            if "@" not in body:
                return f"{key}（邮件）的 URL 里要有发件域与收件人，形如 mailto://用户:密码@域名/收件人"
            if "/" not in body.split("@", 1)[1]:
                return f"{key}（邮件）没写收件人（@域名 之后那一段），一条都送不到"
        if "test" in body and ch["kind"] != "mail":
            return f"{key}（{label}）的 URL 看着还是占位串（含 'test'），换成真 webhook 再存"
    sec_env = str(ch.get("secret_env") or "").strip()
    if sec_env and not re.fullmatch(r"[A-Z][A-Z0-9_]{2,63}", sec_env):
        return f"{key} 的 secret_env={sec_env!r} 不像个变量名（要全大写字母/数字/下划线）"
    return None


def validate_time(value) -> str | None:
    text = str(value or "").strip()
    if not _TIME_RE.match(text or ""):
        return f"时刻要写成 HH:MM（24 小时制），收到 {value!r}"
    return None


def validate_freq(run_freq, weekly_days) -> str | None:
    """沿用 `duanxian/jobs.py` 那一层的口径：`daily` / `weekly` + 0~6 逗号分隔。

    ⚠️ 但**这里比宿主更严**：宿主 `freq_allows` 是"判不了就放行"（配置写坏了不该让循环静默停产），
    所以 `weekly` + 空周几在宿主那边等于天天跑。写入面不能把这种"用户以为每周、其实天天"放过去。
    """
    freq = str(run_freq or "daily").strip()
    if freq not in ("daily", "weekly"):
        return f"频度只支持 daily（每日）/ weekly（每周），收到 {run_freq!r}"
    days = str(weekly_days or "").strip()
    if freq == "weekly" and not days:
        return "选了每周就必须勾至少一个周几 —— 不勾的话按「判不了就放行」会天天发，与你的选择相反"
    if days:
        parts = [p.strip() for p in days.split(",") if p.strip() != ""]
        if not parts or any(not p.isdigit() or int(p) > 6 for p in parts):
            return f"周几要写成 0~6（0=周一）用逗号分隔，收到 {weekly_days!r}"
    return None


def _scalar(value) -> bool:
    return isinstance(value, (str, int, float, bool))


def _validate_field(name: str, fd: dict, present: bool, value) -> str | None:
    """一格参数的形状与范围。文案一律带得上档名与键名 —— 报错要说的是"哪一档的哪一格"，
    只说"不合法"等于让人从头猜。"""
    key, label = fd["key"], fd.get("label") or fd["key"]
    kind = fd["type"]
    if not present and not fd.get("required"):
        # **整项不填**与"填了个空"是两件事：不填＝用后端默认/那句 `empty_means`（合法），
        # 填了个空列表＝"勾了这一格却一个都没选"（下面按 `empty_error` 拦）。
        # 10-16 就是被这两件事混起来判死过：`confluence` 只想设门槛时不该被要求选策略。
        return None
    blank = value is None or (isinstance(value, str) and not value.strip()) or value == []
    if blank:
        # 留空**有含义**的那些格子（`empty_means`）在这里放行 —— 10-16 的教训：
        # 把"留空＝按自选前几只"当必填拒掉，后端那条兜底就永远走不到。
        # `required` 的那些（`screen.rules`）在这一格就会被 `empty_error` 挡下：
        # 没填与填了个空列表是同一件坏事。
        if fd.get("empty_error"):
            return f"{name}：{fd['empty_error']}"
        if present and kind in ("multi", "codes") and not fd.get("empty_means"):
            return f"{name} 的 {key}（{label}）空了：要么勾几个，要么整项不填"
        return None
    if kind in ("int", "num"):
        try:
            number = int(value) if kind == "int" else float(value)
        except (TypeError, ValueError):
            return (f"{name} 的 {key}（{label}）要一个{'整数' if kind == 'int' else '数字'}，"
                    f"收到 {value!r}")
        lo, hi = fd.get("min"), fd.get("max")
        if lo is not None and hi is not None and not lo <= number <= hi:
            note = f"（{fd['note']}）" if fd.get("note") else ""
            return f"{name} 的 {key} 要在 {lo}~{hi} 之间{note}，收到 {value!r}"
        return None
    if kind == "bool":
        if isinstance(value, bool):
            return None
        if str(value).strip().lower() in ("1", "0", "true", "false", "yes", "no", "on", "off"):
            return None
        return f"{name} 的 {key}（{label}）要么勾要么不勾，收到 {value!r}"
    if kind == "text":
        if not isinstance(value, str):
            return f"{name} 的 {key}（{label}）要一段文字，收到 {type(value).__name__}"
        cap = int(fd.get("max_len") or 40)
        if len(value.strip()) > cap:
            return f"{name} 的 {key}（{label}）太长（最多 {cap} 字），收到 {len(value.strip())} 字"
        return None
    if kind == "select":
        if not _scalar(value):
            return f"{name} 的 {key}（{label}）要从清单里选一个，收到 {type(value).__name__}"
        if fd.get("numeric") and not str(value).strip().isdigit():
            return (f"{name} 的 {key}（{label}）要么留空（{fd.get('empty_means') or '用默认'}）"
                    f"要么是个数字，收到 {value!r}")
        allowed = _choice_values(fd)
        if allowed and str(value).strip() not in allowed:
            return (f"{name} 的 {key}（{label}）只认 {'、'.join(allowed)}，收到 {value!r}"
                    "（候选项是后端现读的，改过之后重新打开面板会看到新清单）")
        return None
    if kind == "multi":
        items = value if isinstance(value, (list, tuple)) else (
            [x for x in str(value).replace("，", ",").split(",") if x.strip()]
            if isinstance(value, str) else None)
        if items is None:
            return (f"{name} 的 {key}（{label}）要一组值（或一条逗号分隔的文字），"
                    f"收到 {type(value).__name__}")
        for one in items:
            if isinstance(one, dict):
                # `screen.rules` 的形状是 `[{rule_key, params}]`（执行体与 `screen_view` 都要这个形状）
                if not fd.get("as_objects") or not str(one.get("rule_key") or "").strip():
                    return (f"{name} 的 {key}（{label}）里有一项不是可选项"
                            + ("（规则要写成 {rule_key, params}）" if fd.get("as_objects") else ""))
            elif not _scalar(one) or fd.get("as_objects"):
                return (f"{name} 的 {key}（{label}）里混进了不能认的一项：{str(one)[:24]!r}"
                        + ("" if not fd.get("as_objects") else "（规则要写成 {rule_key, params}）"))
        allowed = _choice_values(fd)
        if allowed:
            bad = [str(x.get("rule_key") if isinstance(x, dict) else x).strip()
                   for x in items if str(x.get("rule_key") if isinstance(x, dict) else x).strip() not in allowed]
            if bad:
                return (f"{name} 的 {key}（{label}）里有清单上不存在的项：{'、'.join(bad)}"
                        "（候选项来自后端现读，改过之后重新打开面板即可看到新清单）")
        return None
    if kind == "codes":
        if not isinstance(value, (list, tuple)):
            # 字符串必须拒：执行体是 `for c in codes` —— 传 "600519" 进去会被**逐字符**当成六只票，
            # 发出来是一行垃圾而不是一句报错（10-16 实测抓到过这个形状）。
            return (f"{name} 的 {key}（{label}）要一组代码（**留空＝{fd.get('empty_means') or '用默认'}**），"
                    f"收到 {type(value).__name__}：{str(value)[:40]!r}")
        if [c for c in value if not _scalar(c)]:
            return f"{name} 的 {key}（{label}）里每一项都得是代码字符串"
        return None
    return f"{name} 的 {key} 登记了不认识的种类：{kind}"


def _choice_values(fd: dict) -> list[str]:
    """静态候选的取值清单。`choices_from`（库里现读的那些：分组、规则、策略源）**这里不判** ——
    校验时不查库，而且那些清单本来就允许被删改：删了分组只会让那一档发成"读不到"。"""
    return [str(c.get("value")) for c in (fd.get("choices") or []) if isinstance(c, dict)]


def validate_scenes(scenes) -> str | None:
    """内容块与每一格的形状。**规则只有一处：`SCENE_FIELDS` 那张登记表**。
    原先这里是五段按档名写死的 if —— 加一档内容就得再抄一段，而抄漏的那一档
    会长成"面板能填、发出来是垃圾"（10-16 的 `dossier.codes` 就是这么被卡住的）。
    """
    if not isinstance(scenes, dict):
        return f"scenes 应为对象（内容块 → 参数），收到 {type(scenes).__name__}"
    unknown = [k for k in scenes if k not in SCENE_KEYS]
    if unknown:
        return (f"不认识的内容块：{'、'.join(map(str, unknown))}"
                f"（可用：{'、'.join(SCENE_KEYS)}；想加新的一档要先在 duanxian/notify.py 里有执行体）")
    for name, params in scenes.items():
        pr = params if isinstance(params, dict) else {}
        if params is not None and not isinstance(params, dict):
            return f"{name} 的参数应为对象（格子名 → 值），收到 {type(params).__name__}"
        # 「条数上限」每档都有一格：值由面板按 `catalog.scenes[].default_limit / max_limit` 渲染，
        # 上下限本身住在 `duanxian/notify.py::{DEFAULT_LIMITS,MAX_LIMITS}` —— 这里只判形状，
        # 不在这里再抄一份数字（抄了就等于两处各说一遍"最多能发多少条"）。
        if "limit" in pr:
            raw = pr.get("limit")
            try:
                value = int(raw)
            except (TypeError, ValueError):
                return f"{name} 的 limit 要个整数（面板上是数字框），收到 {raw!r}"
            if not 1 <= value <= 500:
                return f"{name} 的 limit 要在 1~500 之间（再大的正文会被渠道上限切掉，见 14 那节），收到 {value}"
        for fd in SCENE_FIELDS.get(name, ()):
            why = _validate_field(name, fd, fd["key"] in pr, pr.get(fd["key"]))
            if why:
                return why
    return None


#: 一条计划怎么落地成手机上的消息（用户 2026-10-19：「按照对应的点，分条推送。不要合并推送。」）。
#: 措辞只住这一处，面板的选项文字与 `duanxian/notify.py` 的分支都引这份；
#: **后端缺省 `merged`**（旧计划的既有形状，别在没人要求时把一条变六条）；
#: 面板上新建/编辑的表单**默认勾 `per_scene`**（用户 2026-10-19 要的"按各个点分条推送"），
#: 所以新建的就是分条，`merged` 留给想要一条的人。
#: 因为有人只想要一条（渠道少、内容少的时候一条更省事）。
SEND_MODES = ("per_scene", "merged")
SEND_MODE_MEANING = {
    "per_scene": "分条推送（面板新建时的默认）：勾中的每一档各发一条，标题写清是哪块内容。"
                 "手机上一眼定位，不用在一屏里翻六块；代价是渠道多时消息条数变多，"
                 "企微那种 2048 字节硬墙是**每一条单独判**（超长的那一条自己声明被截断，"
                 "不会连累别的那几条）。",
    "merged": "合并成一条：所有勾选内容拼成一条消息一起发（**后端缺省**，也是 10-19 之前的唯一行为）。"
              "整条按这批渠道里最小的上限切，超长就在末尾声明「已截断」。",
}


def validate_plan(p: dict) -> str | None:
    if not isinstance(p, dict):
        return f"计划应为对象，收到 {type(p).__name__}"
    pid = str(p.get("id") or "").strip()
    if not pid:
        return "计划缺少 id"
    if not str(p.get("name") or "").strip():
        return f"{pid}: 计划要有名字（历史与面板都靠它认，别留空）"
    why = validate_time(p.get("run_at"))
    if why:
        return f"{pid}: {why}"
    why = validate_freq(p.get("run_freq"), p.get("weekly_days"))
    if why:
        return f"{pid}: {why}"
    chans = p.get("channels")
    if not isinstance(chans, list) or not chans:
        return f"{pid}: 没选发往哪些渠道 ⇒ 到点无处可发"
    why = validate_scenes(p.get("scenes"))
    if why:
        return f"{pid}: {why}"
    # 分条/合并这一格**拼错不许悄悄回落默认**：它决定手机上收到一条还是六条，
    # 把打错读成"按默认走"就是让用户以为配好了、实际发的是另一种形状。
    mode = p.get("send_mode")
    if mode is not None and str(mode) not in SEND_MODES:
        return f"{pid}: send_mode 只接受 {' / '.join(SEND_MODES)}，收到 {mode!r}"
    return None


# ---------------------------------------------------------------- 真值与掩码
def env_value(name: str) -> str:
    """`.env` 或环境变量里的凭据。**环境变量优先**（与 `duanxian/pg.py` 同一约定）。"""
    if not name:
        return ""
    got = os.environ.get(name)
    if got:
        return got.strip()
    env_path = Path(__file__).resolve().parent / ".env"
    try:
        for line in env_path.read_text(encoding="utf-8").splitlines():
            text = line.strip()
            if not text or text.startswith("#") or "=" not in text:
                continue
            if text.startswith("export "):
                text = text[len("export "):]
            k, v = text.split("=", 1)
            if k.strip() == name:
                return v.strip().strip('"').strip("'")
    except OSError:
        return ""
    return ""


def real_secret(ch: dict) -> str:
    """这条渠道的**加签密钥**（飞书「签名校验」/钉钉「加签」）。`secret_env` 优先。

    为什么单独一格而不是塞在 URL 的 `?secret=` 里：用户在群机器人设置页看到的就是
    两个分开的东西（一条 webhook 地址 + 一串密钥），塞一起等于让他自己拼。
    """
    env = str(ch.get("secret_env") or "").strip()
    if env:
        got = env_value(env)
        if got:
            return got
    return str(ch.get("secret") or "").strip()


def real_url(ch: dict) -> str:
    """这条渠道真正要交给 apprise 的 URL。`url_env` 优先，读不到就回空串（调用方必须出声）。"""
    env = str(ch.get("url_env") or "").strip()
    if env:
        got = env_value(env)
        if got:
            return got
    return str(ch.get("url") or "").strip()


def mask(text: str) -> str:
    """把 URL 里的凭据遮掉，只留"认得出是哪条"的那点尾巴。

    为什么不用固定长度前缀：`mailto://用户:密码@域/收件人` 与
    `feishu://<32 位 token>` 的敏感段位置完全不同，逐段解析会漏。
    """
    if not text:
        return ""
    tail = text[-SECRET_KEEP:] if len(text) > SECRET_KEEP else ""
    return f"******{tail}"


def masked_channel(ch: dict) -> dict:
    """交出去的形状：**永远不带真凭据**，但要说清这条能不能发。"""
    url = str(ch.get("url") or "").strip()
    env = str(ch.get("url_env") or "").strip()
    resolvable = bool(env and env_value(env)) or bool(url)
    kind = ch.get("kind")
    sec_env = str(ch.get("secret_env") or "").strip()
    signed = bool(real_secret(ch))
    return {
        "key": ch.get("key"), "kind": kind,
        "kind_label": KIND_LABELS.get(kind, kind),
        "label": str(ch.get("label") or "").strip() or ch.get("key"),
        "url_masked": mask(real_url(ch)) if resolvable else "",
        "url_env": env or None,
        "has_secret": resolvable,
        # 这一格专治"配了个指向不存在变量的 url_env"：不说清就是"页面绿、发送空"
        "secret_source": ("环境变量 " + env if env and env_value(env) else
                          "配置文件内联" if url else None),
        # 加签密钥**只交"有没有"，一个字都不交**：群机器人那边开了「签名校验」而这里没填，
        # 对面会回一串错误码 —— 与其让人去猜，不如这一格先说清。
        "kind_supports_sign": kind in SECRET_KINDS,
        "has_sign": signed,
        # **变量名不是凭据**（`sign_source` 本来就把 "环境变量 FEISHU_SECRET" 写在屏幕上）。
        # 这一格必须交出来，否则界面上"编辑已有渠道"时表单里是空的，而 `upsert_channel`
        # 收到空串就会把这条指回 `.env` 的引用清掉 —— 用户只是想改个备注。
        "secret_env": sec_env or None,
        "sign_source": ("环境变量 " + sec_env if sec_env and env_value(sec_env) else
                        "配置文件内联" if str(ch.get("secret") or "").strip() else None),
        "sign_warning": (None if kind not in SECRET_KINDS or signed else
                         "这一格没填「加密密钥」—— 如果你的群机器人开了「签名校验 / 加签」，"
                         "对面会直接拒绝这条消息（错误码会在发送结果里原样报出来）。"
                         "没开签名校验就不用填"),
        "missing_reason": (None if resolvable else
                           (f"配置里写的是 {env}，但 .env 与环境变量里都没有这个变量"
                            if env else "既没填 URL 也没指环境变量")),
        "enabled": bool(ch.get("enabled", True)),
        "note": str(ch.get("note") or "").strip() or None,
        "invalid_reason": validate_channel(ch),
    }


def masked_channels(cfg: dict | None = None) -> list[dict]:
    cfg = cfg or load()
    return [masked_channel(c) for c in cfg["channels"]]


def plan_view(p: dict, channels: list[dict]) -> dict:
    """计划的读数：把 `channels` 里的键换成"能不能发"的实情。

    为什么必须现算：渠道被删掉或被禁用之后，计划还留着那个键 ——
    到点就是一条"计划说发了、其实无处可发"的静默失败（本项目最常见的那类假成功）。
    """
    by = {c["key"]: c for c in channels}
    refs = []
    for key in (p.get("channels") or []):
        c = by.get(str(key))
        refs.append({"key": key,
                     "label": (c or {}).get("label") or key,
                     "kind_label": (c or {}).get("kind_label"),
                     "exists": c is not None,
                     "usable": bool(c and c["enabled"] and c["has_secret"] and not c["invalid_reason"])})
    return {"id": p.get("id"), "name": str(p.get("name") or "").strip(),
            "enabled": bool(p.get("enabled", True)),
            "run_at": str(p.get("run_at") or "").strip() or None,
            "run_freq": str(p.get("run_freq") or "daily"),
            "weekly_days": str(p.get("weekly_days") or "").strip(),
            "trade_days_only": bool(p.get("trade_days_only", True)),
            "scenes": p.get("scenes") if isinstance(p.get("scenes"), dict) else {},
            # 分条/合并：**把默认值算出来再交**，不交 null。
            # 交 null 的话面板就显示不出"现在到底是几条"，而用户看到的正是"手机上收了六条"。
            "send_mode": (str(p.get("send_mode")) if p.get("send_mode") in SEND_MODES
                          else "merged"),
            "channels": refs,
            "note": str(p.get("note") or "").strip() or None,
            "invalid_reason": validate_plan(p),
            "sendable": any(r["usable"] for r in refs)}


def plans_view(cfg: dict | None = None, chans: list[dict] | None = None) -> list[dict]:
    """`chans` 可以传现成的掩码结果：同一个请求里两处都要用它，别读两遍配置。"""
    cfg = cfg or load()
    chans = masked_channels(cfg) if chans is None else chans
    return [plan_view(p, chans) for p in cfg["plans"]]


def state(*, via_hint: str = "") -> dict:
    """给 `/api/notify` 与体检用的整份读数（一律掩码）。"""
    cfg = load()
    chans = masked_channels(cfg)
    plans = plans_view(cfg, chans)
    return {
        "enabled": cfg["enabled"],
        "config_path": config_path(),
        "channels": chans,
        "plans": plans,
        "kind_choices": [{"value": k, "label": KIND_LABELS[k], "must": KINDS[k][2],
                          # 面板按这两格决定"粘贴整条 webhook 也可以"与"要不要多一个密钥框"，
                          # 而不是在前端另抄一份各家规则（抄的那份一定会有人改漏）。
                          "official": OFFICIAL_URLS.get(k), "supports_sign": k in SECRET_KINDS}
                         for k in KINDS],
        # 「发什么」那一排的清单**从 `SCENE_KEYS` + `SCENE_HINTS` 生成**（原先这里手写六行，
        # 加一档内容的人只要忘一处，面板就永远看不见它 —— 10-17 一次加 11 档，这种漏必出）。
        "scene_choices": [{"value": k, "label": SCENE_HINTS[k]} for k in SCENE_KEYS],
        # 分条/合并那一排的选项与措辞（面板不抄第二份；措辞只住 `SEND_MODE_MEANING`）
        "send_mode_choices": [{"value": k, "label": SEND_MODE_MEANING[k].split("：")[0],
                               "hint": SEND_MODE_MEANING[k]} for k in SEND_MODES],
        "counts": {"channels": len(chans),
                   "usable_channels": sum(1 for c in chans
                                          if c["enabled"] and c["has_secret"] and not c["invalid_reason"]),
                   "plans": len(plans), "plans_enabled": sum(1 for p in plans if p["enabled"]),
                   "plans_sendable": sum(1 for p in plans if p["enabled"] and p["sendable"])},
        "warning": cfg.get("_warning"),
        "changed_at": cfg.get("_changed_at"), "changed_by": cfg.get("_changed_by"),
        "via_hint": via_hint or None,
        "late_window_min": LATE_WINDOW_MIN,
        "note": ("推送这件事**只有四种渠道**（用户 10-15 圈的口径）：邮件 / 企业微信群助手 / 钉钉群助手 / "
                 "飞书群助手。短信、syslog、SNMP TRAP 本程没有执行体，界面上也不给框 —— "
                 "能填但没人读的框比没有框更坏。"),
        "explain": ("计划是**本进程里的第 12 条常驻循环 `notify-push`** 在发：每 60 秒扫一遍，"
                    f"到点之后的 {LATE_WINDOW_MIN} 分钟内都算「这一档还没发」——因为宿主是"
                    "**处理完再睡 60 秒**，一条慢发送或一次重启就能把正好那一分钟错过。"
                    "窗口只补「错过那一分钟」，**不是自动重试**：发失败也记当天一笔，"
                    "要重发用「立刻发一次」。"
                    "总开关是**每轮重读**的：面板上关掉，下一个心跳就停手，不用重启 8910。"
                    "每次发送都往 " + HISTORY_FILE + " 追加一条，成败都记（不含任何凭据）。"),
    }


# ---------------------------------------------------------------- 变更动作
def upsert_channel(ch: dict, *, via: str = "") -> tuple[dict, str | None]:
    cfg = load()
    chans = list(cfg["channels"])
    idx = next((i for i, c in enumerate(chans) if c.get("key") == ch.get("key")), None)
    if idx is None:
        if len(chans) >= MAX_CHANNELS:
            return cfg, f"渠道最多 {MAX_CHANNELS} 条，先删一条再加"
        merged = dict(ch)
    else:
        merged = dict(chans[idx])
        # **留空不覆盖凭据**：编辑面板只回显掩码，用户改了别的字段时不该把密钥抹掉
        incoming = str(ch.get("url") or "").strip()
        if incoming:
            merged["url"] = incoming
        # 加签密钥同理：界面上那一格是 password 且**永不回显**，留空只可能是"不改"
        sec_in = str(ch.get("secret") or "").strip()
        if sec_in:
            merged["secret"] = sec_in
        for field in ("kind", "label", "url_env", "secret_env", "enabled", "note"):
            if field in ch:
                merged[field] = ch[field]
        why = validate_channel(merged)
        if why:
            return cfg, why
        chans[idx] = merged
    why = validate_channel(merged)
    if why:
        return cfg, why
    if idx is None:
        chans.append(merged)
    cfg["channels"] = chans
    return save(cfg, via=via), None


def remove_channel(key: str, *, via: str = "") -> tuple[dict, str | None]:
    cfg = load()
    keep = [c for c in cfg["channels"] if c.get("key") != key]
    if len(keep) == len(cfg["channels"]):
        return cfg, f"没有键为 {key!r} 的渠道"
    # 删之前先看有没有计划还指着它 —— 有就是"计划到点无处可发"，必须当场说
    orphans = [p.get("id") for p in cfg["plans"] if key in (p.get("channels") or [])]
    cfg["channels"] = keep
    saved = save(cfg, via=via)
    return saved, (None if not orphans else
                   f"已删除，但还有 {len(orphans)} 条计划指着它：{'、'.join(map(str, orphans))}"
                   "（这些计划现在会被面板标成「无处可发」）")


def upsert_plan(plan: dict, *, via: str = "") -> tuple[dict, str | None]:
    cfg = load()
    plans = list(cfg["plans"])
    idx = next((i for i, p in enumerate(plans) if p.get("id") == plan.get("id")), None)
    merged = dict(plans[idx]) if idx is not None else dict(plan)
    for field in ("name", "enabled", "run_at", "run_freq", "weekly_days",
                  "trade_days_only", "channels", "scenes", "note", "send_mode"):
        if field in plan:
            merged[field] = plan[field]
    merged.setdefault("id", plan.get("id"))
    why = validate_plan(merged)
    if why:
        return cfg, why
    if idx is not None:
        plans[idx] = merged
    else:
        if len(plans) >= MAX_PLANS:
            return cfg, f"定时计划最多 {MAX_PLANS} 条"
        plans.append(merged)
    cfg["plans"] = plans
    return save(cfg, via=via), None


def remove_plan(pid: str, *, via: str = "") -> tuple[dict, str | None]:
    cfg = load()
    keep = [p for p in cfg["plans"] if p.get("id") != pid]
    if len(keep) == len(cfg["plans"]):
        return cfg, f"没有 id 为 {pid!r} 的计划"
    cfg["plans"] = keep
    return save(cfg, via=via), None


def set_enabled(on: bool, *, via: str = "") -> dict:
    cfg = load()
    cfg["enabled"] = bool(on)
    return save(cfg, via=via)


# ---------------------------------------------------------------- 到点判定
def _norm_time(value: str) -> str:
    """`9:05`/`09:05` 归一成 `HH:MM`。不归一就会让"面板显示 9:05、宿主按 09:05 判"
    这种偏差有机会出现（本项目最恨的一类假生效）。"""
    text = str(value or "").strip()
    if validate_time(text):
        return ""
    hour, minute = text.split(":")
    return f"{int(hour):02d}:{minute}"


def slot_minutes(plan: dict) -> int | None:
    """这条计划今天的时刻（从 00:00 起的分钟数）。没配或形状不合法 ⇒ `None`（不是 0）。"""
    want = _norm_time(plan.get("run_at"))
    if not want:
        return None
    hour, minute = want.split(":")
    return int(hour) * 60 + int(minute)


def day_applies(plan: dict, now: datetime) -> bool:
    """今天（星期这一层）在不在这一档里。
    ⚠️ 频度写坏（`weekly` 而周几空）时**按每日处理** —— 与宿主 `duanxian/jobs.freq_allows`
    同一口径：配置坏了不该让推送静默停；但这句话必须被看见，所以 `validate_freq`
    在写入面就把它拒了，`slot_state` 也会把它读成「不合法」而不是「今天不跑」。
    """
    if str(plan.get("run_freq") or "daily") != "weekly":
        return True
    wanted = {int(x) for x in str(plan.get("weekly_days") or "").split(",")
              if x.strip().isdigit()}
    return True if not wanted else now.weekday() in wanted


def due_minute(plan: dict, now: datetime) -> bool:
    """这一分钟该不该发这一条计划（**只判时刻与星期，不判"发没发过"**）。

    "发过没有"由发送侧按 `计划 id + 日期` 去重：判在这里会让重发与漏发都说不清
    （15:59 收盘链那次的教训就是"宿主自己判完了当天不再回来"）。

    ⚠️ **认 `LATE_WINDOW_MIN` 分钟内的迟到**（10-15 补）。原先只认"正好那一分钟"，
    而宿主是"处理完再睡 60 秒"：一条发送慢一点、或进程在 09:04 重启一次，
    `09:05` 那一分钟就整片错过，**而"没执行"什么都不会留下**（比"发失败"难查一个数量级）。
    有了窗口，同一条计划在这一档里会被反复问到，但**去重只放过第一次**：
    `mark_sent` 失败也记 ⇒ 窗口只补"错过那分钟"，**不会**变成自动重试（那仍交给「立刻发一次」）。
    """
    if not plan.get("enabled", True):
        return False
    slot = slot_minutes(plan)
    if slot is None or not day_applies(plan, now):
        return False
    return 0 <= now.hour * 60 + now.minute - slot <= LATE_WINDOW_MIN


def slot_state(plan: dict, now: datetime, *, sent: bool = False,
               trade_day: bool | None = True) -> dict:
    """今天这一档现在处于什么状态 —— 专治"到点却没发，而这件事在界面上不存在"。

    读数与执行走的是**同一份**判据（`slot_minutes`/`day_applies`/`LATE_WINDOW_MIN`），
    不是另写一套日历；所以面板说的"错过了"与宿主真的错过是同一件事。
    `trade_day=None` 是**判不了**：不降级成"今天不跑"（与发送侧同一取舍）。
    """
    def out(state, label):
        return {"state": state, "label": label}

    if not plan.get("enabled", True):
        return out("off", "这条计划是关着的")
    why = validate_plan(plan)
    if why:
        return out("bad", f"配置不合法：{why}")
    want = _norm_time(plan.get("run_at"))
    slot = slot_minutes(plan)          # 上面 `validate_plan` 已过 ⇒ 这里不可能是 None
    if not day_applies(plan, now):
        return out("not_today", f"今天不在这一档的星期里（每周 {want}，周几 {plan.get('weekly_days')}）")
    if plan.get("trade_days_only", True) and trade_day is False:
        return out("not_trade_day", "今天不是大 A 交易日（这条勾了只在交易日发）")
    if sent:
        return out("sent", f"{want} 那一档今天已经发过")
    lag = now.hour * 60 + now.minute - slot
    if lag < 0:
        return out("waiting", f"还没到点（{want}，还有 {-lag} 分钟）")
    if lag <= LATE_WINDOW_MIN:
        return out("in_window", f"到点了、还没发（{lag} 分钟前那一档）⇒ 下一轮心跳就补")
    return out("missed", f"错过：{want} 那一档已过补发窗口（{lag} 分钟前）而今天没发过 "
                         f"—— 要补用「立刻发一次」，宿主不会自己追")
