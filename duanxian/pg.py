"""本地 PostgreSQL（financetool 库）P0 数据源 —— 复盘取数的**首选**通道。

设计：
  · 只读 P0/P1 表（market_data.*、reference_data.*、analytics.*），一律 fail-soft：
    PG 没配置 / 连不上 / 该场次没数据 → 返回 None，调用方回退原有的在线取数
    （akshare / 东财 / 腾讯），行为与接入前完全一致。
  · 账号固定用 ft_reader（只读，角色侧 statement_timeout 60s）；
    绝不用 ft_admin / ft_etl —— 本模块不建表、不写数。
  · **连接来自 `app/db/engine.py` 的 reader 池**（2026-10-26 起；原来每条 SQL 新建一条
    psycopg2 连接，一发握手 76~80ms，全站 66 个调用点都付一遍）。所以除了下面那组 PG_*，
    还必须有 `FT_DSN_READER` —— 两者指向**同一个 ft_reader 账号**（2026-10-26 核过），
    缺它时这一层不会抛错而是老实交 None 并在日志里写"连接配置"，让屏幕说"没读到"而不是"没有"。
  · 配置走仓库根 .env（与 fetchers 同一份）：
      VIBE_PG=1                     显式开关；不设时只要 PG_PASSWORD 在就默认开
      PG_HOST / PG_PORT / PG_DB     默认 127.0.0.1 / 5432 / financetool
      PG_USER / PG_PASSWORD         ft_reader 的凭据（`enabled()` 判的就是这两个）
"""

from __future__ import annotations

import datetime
import logging
import os

import pandas as pd

logger = logging.getLogger(__name__)

DEFAULTS = {"PG_HOST": "127.0.0.1", "PG_PORT": "5432", "PG_DB": "financetool"}

_ENV_KEYS = ("VIBE_PG", "PG_HOST", "PG_PORT", "PG_DB", "PG_USER", "PG_PASSWORD",
             "PG_ETL_USER", "PG_ETL_PASSWORD")
_env_loaded = False


def _load_env_once() -> None:
    """仓库根 .env 里补 PG 配置（fetchers 通常已加载过；standalone 引用时兜底）。

    环境变量里已有的不覆盖 —— 与 fetchers._load_env 同一约定。
    """
    global _env_loaded
    if _env_loaded:
        return
    _env_loaded = True
    env_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env")
    if not os.path.exists(env_path):
        return
    try:
        with open(env_path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                if line.startswith("export "):
                    line = line[len("export "):]
                k, v = line.split("=", 1)
                k, v = k.strip(), v.strip().strip('"').strip("'")
                if k in _ENV_KEYS and k not in os.environ:
                    os.environ[k] = v
    except Exception:
        pass


def _env(name: str) -> str:
    _load_env_once()
    return (os.environ.get(name) or DEFAULTS.get(name) or "").strip()


def enabled() -> bool:
    _load_env_once()
    flag = os.environ.get("VIBE_PG", "").strip().lower()
    if flag in ("0", "false", "off", "no"):
        return False
    return bool(_env("PG_USER") and _env("PG_PASSWORD"))


def fetch_df(sql: str, params=()) -> "pd.DataFrame | None":
    """跑一条只读 SQL 返回 DataFrame；任何失败返回 None（调用方回退在线源）。

    ⚠️ **一条 SQL 一个新连接** 这件事原来值 76~80 毫秒（2026-10-26 实测：40 发同样轻的
    只读 SQL，走 `psycopg2.connect` 平均 76.6~79.6ms 一发，借现成的池 0.7~2.7ms），
    而全站有 66 个调用点、首页一发接口动辄打九次 —— 光是握手就 ~0.7 秒。
    现在借 `app/db/engine.py` 那张**已有的** reader 池（`POOL_SIZE['reader']=12`，
    同一个 ft_reader 账号、同一句 `timezone=Asia/Shanghai`，2026-10-26 探过两边交回的
    `timestamptz` 文本一致，所以屏幕上的"这批算于几点"不会因为这次改动而变）。
    下面那两处细节不许在搬家时丢掉：
      · `if params:` 那个分支 —— 很多语句里有字面 `%`（`LIKE 'TDX\\_%%'`），
        空参数也走带参执行会被当成格式串炸掉；
      · 日期列统一收成 `str` —— 读侧到处是 `str(row['trade_date'])[:10]` 这类写法。
    """
    if not enabled():
        return None
    try:
        from app.db import engine        # 函数内导入：这一层要能在没有 app 包的环境里被整体跳过
        with engine.conn('reader') as c, c.cursor() as cur:
            if params:
                cur.execute(sql, params)
            else:
                cur.execute(sql)
            if cur.description is None:
                return None
            cols = [d.name for d in cur.description]
            rows = cur.fetchall()
        df = pd.DataFrame(rows, columns=cols)
        for c in df.columns:
            if df[c].map(lambda v: isinstance(v, datetime.date)).all() and len(df):
                df[c] = df[c].astype(str)
        return df
    except Exception as exc:
        # 配置缺失与"这条 SQL 坏了"是两件事，日志要分得开：前者会把整层的只读取数
        # 全部兜成 None，屏幕上却写"没有数据"，那就是假读数（见《05-踩坑》判据不许兜空）。
        kind = '连接配置' if 'DSN' in str(exc) else '查询'
        logger.warning("PG %s失败（回退在线源）: %s: %s", kind, type(exc).__name__, str(exc)[:200])
        return None


def trade_day_open(day, now=None) -> "bool | None":
    """日历说这天开不开市。**三种答案缺一不可**：True 开市 / False 明说不开 / None 判不了。

    `now` 只有一个用处：第四层（行情自证）要拿"此刻"和那一天比，测试得能把时钟钉住 ——
    生产调用一律不传（走真时钟）。不要在这条路上加第二份判据。

    为什么不直接用 `app/db/repo/schedule.is_trade_day()`：它按设计把"查不到那一天"也返回
    False（对它的调用方是对的：日历没同步时宁可漏跑，也不要节假日自动跑出旧页面）。
    但收盘链的宿主把 False 读成"今天不是交易日"就会**安静地永远不跑** —— 2027 年的日历
    还没同步进来那天开始，页面上一切正常、日志写着"非交易日"，谁也不会去查。
    所以这里必须把「日历说不开市」与「日历根本没有这一天」分开：后者要出声。

    这份实现只此一处：宿主（`server.py`）与 `/api/jobs`、`/api/jobs/chain` 都调它，
    两边各查各的就会出现"页面说今天是交易日、宿主说不是"。

    **日历没覆盖到那一天的自判（2026-10-07 补，第四层 2026-10-08 补）**：
    唯一的源（新浪 `tool_trade_date_hist_sina`）实测只登到 2026-12-31（8,797 行，
    与库里一模一样），2027 年的放假安排要等年底才发布 —— 本机**现在拿不到**，
    所以只能自己判能判的那一半：
      · 周六 / 周日 → `False`。交易所周末不开市与调休无关，这是可证的，不需要猜；
        不这么做的话，2027 年起的每个周末都会各记一笔"判不了"的失败，噪声会把真信号埋掉。
      · 周一至周五 → 日历本身判不了（节假日就在工作日里），但有**第四层自证**：
        交易所不开市的那天，实时行情的时间戳就停在上一个交易日，这是市场自己盖章，
        不依赖谁提前把年份排进来。见 `_market_says()`。
        要那几天照跑，用系统配置里的「只在交易日执行」关掉（宿主会照跑并说明为什么），
        而不是把"判不了"读成"是交易日"。
    """
    return _decide(day, now)[0]


def trade_day_evidence(day, now=None) -> str:
    """上面那个答案是**哪一层**给的：`calendar` / `weekend` / `quote` / `unknown`。

    界面要的是这句而不是光一个布尔：日历说"不开市"和行情时间戳说"不开市"，
    后者意味着日历已经排不住这几年了，得让人看见才会在年底去接下一年的排班。
    """
    return _decide(day, now)[1]


def _decide(day, now=None) -> "tuple[bool | None, str]":
    """判据本体：返回 `(开不开市, 依据哪一层)`。`trade_day_open` 与 `trade_day_evidence` 共用它。

    两层的**边界**（都是既有测试钉着的，别顺手合并）：
      · 日历**没有那一天**（没连上 / 查询失败 / 行不存在）→ 星期几自判可用，判不了再问行情；
      · 日历**有那一天但 `is_open` 是 NULL** = 那一天已经排了、内容是坏的 —— 这种日子
        不拿星期几补口径（该修的是那一行，不是判据），只接受行情自证，否则一律判不了。
    """
    if not enabled():
        return None, "unknown"
    df = fetch_df(
        "select is_open from reference_data.trade_calendar where calendar_date = %s", (str(day),))
    if df is None or not len(df):
        known = _weekend_means_no(day)
        if known is not None:
            return known, "weekend"
        said = _market_says(day, now)
        return (said, "quote") if said is not None else (None, "unknown")
    flag = df.iloc[0]["is_open"]
    if flag is None or (isinstance(flag, float) and flag != flag):
        said = _market_says(day, now)
        return (said, "quote") if said is not None else (None, "unknown")
    return bool(flag), "calendar"


def _parse_day(day):
    """日历里那三种写法都吃：`date` / 'YYYY-MM-DD' / 'YYYYMMDD'。认不出回 None。"""
    import re
    if isinstance(day, datetime.datetime):
        return day.date()
    if isinstance(day, datetime.date):
        return day
    text = str(day)[:10]
    try:
        return (datetime.date.fromisoformat(text) if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text)
                else datetime.datetime.strptime(text.replace("-", ""), "%Y%m%d").date())
    except (TypeError, ValueError):
        return None


def _weekend_means_no(day):
    """日历没有那一天时，只有"周末"是可以自己判出来的。判不了就照实交 None。"""
    d = _parse_day(day)
    return None if d is None else (False if d.weekday() >= 5 else None)


#: 行情时间戳最早什么时候才算"今天开市"的证据。**09:31 之前昨天那个时间戳是正常的**
#: （今天还没开盘），在那之前拿它判"今天休市"会把交易日误判成休市日、整条链一天不跑。
_PROOF_NOT_BEFORE = (9, 31)
#: 探测结果留一分钟：宿主每轮都问、页面每次刷新都问，为此每分钟发一次外部请求不值。
_PROOF_TTL_SEC = 60
_proof_cache: "tuple[float, str | None]" | None = None


def quote_trade_date() -> "str | None":
    """腾讯指数实时报价自带的行情时间戳是哪一天（`YYYY-MM-DD`），拿不到返回 None。

    用上证指数 `sh000001`，与 `duanxian/fetchers.py::fetch_indices` 同一个域名、
    同一条 `_direct_get` —— **代理策略只在 fetchers 那一处管**，这里不另起一套。
    字段实测（2026-10-08 14:39）：`f[30] = '20261008143927'`。
    """
    global _proof_cache
    if os.environ.get("PYTEST_CURRENT_TEST"):
        return None          # 测试进程绝不发外部请求（与 `jobs.loop_enabled` 同一条规矩）
    import time as _time
    now = _time.monotonic()
    if _proof_cache is not None and now - _proof_cache[0] < _PROOF_TTL_SEC:
        return _proof_cache[1]
    value: "str | None" = None
    try:
        from duanxian import fetchers as _fetchers
        raw = _fetchers._direct_get("http://qt.gtimg.cn/q=sh000001",
                                    timeout=5).content.decode("gbk", "ignore")
        parts = raw.split("~")
        stamp = (parts[30] or "").strip() if len(parts) > 30 else ""
        if len(stamp) >= 8 and stamp[:8].isdigit():
            value = "%s-%s-%s" % (stamp[:4], stamp[4:6], stamp[6:8])
    except Exception as exc:  # noqa: BLE001 — 探不到就是判不了，绝不猜
        logger.warning("行情时间戳探测失败（不猜交易日）: %s: %s",
                       type(exc).__name__, str(exc)[:120])
    _proof_cache = (now, value)
    return value


def _market_says(day, now=None) -> "bool | None":
    """第四层：日历没排到那一天时，用行情时间戳自证。**只回答"今天"。**

    这个限制是有意的：报价时间戳能证明的只有"此刻这个市场是不是在交易"，
    拿它去判一个还没到的日子等于猜。所以 2027 年起每个交易日都能自己答，
    而日历整段缺失的历史/未来仍然照实交 None。
    """
    now = now or datetime.datetime.now()
    d = _parse_day(day)
    if d is None or d != now.date():
        return None
    if (now.hour, now.minute) < _PROOF_NOT_BEFORE:
        return None                       # 今天还没开盘，昨天那个时间戳不算证据
    quoted = quote_trade_date()
    if quoted is None:
        return None
    return quoted == d.isoformat()


def calendar_coverage() -> dict:
    """日历排到哪一天、还剩几天。界面与每日链共用这一份读数。

    `running_out`（剩余不足 60 个自然日）不是故障：新浪那一路会在年底国务院发布
    次年放假安排后自己延长，而每日链的第 0 步本来就在重同步日历。它要的是**提前出声**，
    而不是等 1 月第一个工作日所有人发现链不跑。
    """
    max_day = calendar_max_day()
    days_left = None
    if max_day:
        tail = _parse_day(max_day)
        if tail is not None:
            days_left = (tail - datetime.date.today()).days
    return {"max_date": max_day, "days_left": days_left,
            "running_out": (days_left is not None and days_left < 60)}


def calendar_max_day() -> "str | None":
    """日历覆盖到哪一天。只在"判不了"时用来把话说清楚。"""
    df = fetch_df("select max(calendar_date) as d from reference_data.trade_calendar")
    if df is None or not len(df) or df["d"].iloc[0] is None:
        return None
    return str(df["d"].iloc[0])[:10]


def session_has(table: str, date: str) -> bool:
    """date（'YYYY-MM-DD'）在 table 里是否有行。False=没数据要回退；查询失败也回退。"""
    df = fetch_df(f"select exists(select 1 from {table} where trade_date = %s) as ok", (date,))
    return bool(df is not None and df["ok"].iloc[0])


def latest_session(table: str) -> "str | None":
    """table 里最大的 trade_date（YYYY-MM-DD）。"""
    df = fetch_df(f"select max(trade_date)::text as d from {table}")
    if df is None or not len(df) or df["d"].iloc[0] is None:
        return None
    return str(df["d"].iloc[0])


def ymd(date: str) -> str:
    """'YYYYMMDD' → 'YYYY-MM-DD'；已带横线则原样标准化返回。"""
    s = str(date).strip()
    if len(s) == 8 and s.isdigit():
        return f"{s[:4]}-{s[4:6]}-{s[6:8]}"
    return s
