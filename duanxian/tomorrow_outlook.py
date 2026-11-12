# -*- coding: utf-8 -*-
"""明日展望 · 读侧（用户 2026-11-05 点名：「在量化选股中增加一个明日展望，
把任务推送 ⑩ 里面的内容展示出来，并针对提到的个股把这些个股的各类指标全部展示出来」）。

这一块答的问题：19:00 那一炉（`ingest/jobs/push/t10_tomorrow_outlook.py`）发出去的两张短名单，
**屏上能不能自己摆出来，并且每一只票的 35 项技术指标当场摊开看**。三条口径：

1. **判据只住本文件一份**。两张榜（多指标靠前／趋势最正）的 SQL 原先长在 t10 里，
   现在搬到这里，t10 反过来 import 这里的别名 —— 于是「推送出去的名单」与「屏上的名单」
   不可能是两把尺（同 `duanxian/momentum_follow.py` 引 `quant.rank_momentum` 的写法：
   这里没有第二份名次 SQL，那边也没有第二份取数）。
2. **只读库里已落地的加工结果**，一个判据都不新算：`analytics.indicator_value`（35 项因子）、
   `analytics.xy_rank_daily`、`analytics.stock_character_daily`（stockchar-1）、
   `analytics.trend_stock_daily`、`analytics.limit_up_pool_daily`（lup-1）。
3. **缺数交 null，不补 0**。指标那一族当日没有整行（次新股没有 `ma250`）⇒ 那一格 `—`；
   两榜的输入表那天没有场次 ⇒ 那一节点名「缺数」并说清最新到哪天。

---- 逐票全部指标那一块：为什么 15 项不给名次 ----

`config.indicator.output_unit` 决定这一项能不能跨票比：

| 单位 | 项数 | 给什么 |
|---|---|---|
| `pct` / `ratio` / `none` | 19 | 值 + 全场名次（按数值从大到小）+ 该项当日母体只数 |
| `yuan`（MA 族、MACD 族、均价/最高/最低/ATR） | 15 | **只给值**。绝对价格是量纲，不是强弱：`ma5` 排全场第 8 名只说明它贵 |
| `count`（`chg_rank` 涨幅排名） | 1 | 只给值。它本身就是一名，再排一次是把名次当数值排 |

名次用 `rank()`（同分同名、下一名顺延），分母是**该项当日有值的只数**（各不一样：
2026-11-05 实测 `amp` 5,571 只、`ma250` 5,393 只），所以每一行自己带 `family_n`，
界面不许拿一个统一母体去解释所有名次（同 `yuanshan-filter-placement` 那条「0 只 ≠ 没读到」）。

---- 与推送那一炉的关系 ----

屏上还摆**今天真正发出去的那一条原文**（读 `analytics.intraday_broadcast` 的 `task='tomorrow'` 行），
连同发没发、发到哪些渠道、几点发的 —— 那样「屏上的名单」和「群里那条」能当场对得上，
而不是各算一遍再互相怀疑。原文没有（今天没跑/没内容）⇒ 那一块交 `null` 并说清是哪种。
"""
from __future__ import annotations

import datetime as dt
import json
import math
import time
from pathlib import Path
from typing import Any, Iterable, Optional

from app.db import engine

from . import ranking as RANKING

ROOT = Path(__file__).resolve().parents[1]
CFG_FILE = ROOT / 'app' / 'intraday' / 'broadcast_config.json'

#: 五张输入表（都在 `analytics`，2026-11-05 现查都有当天行）
IND_TABLE = 'analytics.indicator_value'
XY_TABLE = 'analytics.xy_rank_daily'
CHAR_TABLE = 'analytics.stock_character_daily'
TREND_TABLE = 'analytics.trend_stock_daily'
POOL_TABLE = 'analytics.limit_up_pool_daily'
BROADCAST_TABLE = 'analytics.intraday_broadcast'
CHAR_SPEC = 'stockchar-1'        # 与 `app/compute/stock_character.SPEC_VERSION` 同一条
POOL_SPEC = 'lup-1'              # 与 `app/compute/limit_up_pool.SPEC_VERSION` 同一条

#: 默认家族与门槛 —— **只有配置文件读不到时才走这里**，那种时候屏上要出声（见 `params`）。
#: 「为什么是这 9 项、为什么没有 macd_hist」的理由写在 `ingest/jobs/push/t10_tomorrow_outlook.py`
#: 的家族注释里（量纲跨票可比才谈得上"靠前"），这边不另立一套。
FAMILY_DEFAULT = ('chg10', 'chg20', 'slope20', 'pos250', 'rel_sector',
                  'chg10_avg', 'chg20_avg', 'amt_rank', 'vol_ratio')
PARAM_DEFAULTS: dict[str, Any] = {
    't10_family': list(FAMILY_DEFAULT),
    't10_top_n': 100,
    't10_min_hits': 4,
    't10_window_sessions': 3,
    't10_hits_n': 8,
    't10_trend_streak_min': 5,
    't10_trend_n': 6,
    't10_char_keep': ['strong'],
}

#: 一屏最多摊开拓扑几只票的全部指标（两榜并集今天实测 13 只，留一倍余量；
#: 真被 `hits_n`/`trend_n` 调大到超限时交 `truncated` 声明，不静默丢人）。
MAX_EXPANDED = 40

#: 整份读数的进程内缓存（一天只动一次的榜，没必要每次请求重扫 0.9 秒的窗口函数）。
#: ⚠️ 缓存必须自己声明自己：交 `cache_age_seconds` 与 `cached`，界面写着"X 秒前实算"，
#: 并留 `refresh=True` 这条路 —— 刚回算完场次时那一小时的旧值要能当场追平。
_CACHE: dict[str, tuple[float, Any]] = {}
_CACHE_TTL = 900.0


# ---------------------------------------------------------------- 出口清洗

def _num(v: Any, nd: Optional[int] = None) -> Any:
    """把库里的 Decimal/日期/NaN 收进 JSON 能装的样子。

    ⚠️ `pandas.isna` 那道闸排在日期判断**之前**：`pd.NaT` 是 `datetime` 的实例，
    顺序反了就带着 NaT 进 JSON，`json.dumps` 产出非法 JSON、浏览器 `JSON.parse` 直接炸。
    """
    if v is None or isinstance(v, (bool, str, int)):
        return v
    if isinstance(v, float):
        return None if not math.isfinite(v) else (round(v, nd) if nd is not None else v)
    try:
        import pandas as pd
        if pd.isna(v):
            return None
    except Exception:  # noqa: BLE001  没装 pandas 时按原值往下走
        pass
    if isinstance(v, (dt.date, dt.datetime)):
        return v.isoformat()
    try:
        f = float(v)
    except (TypeError, ValueError):
        return str(v)
    return None if not math.isfinite(f) else (round(f, nd) if nd is not None else f)


def _cav(code: str, text: str, label: str, hard: bool = False) -> dict:
    """一条声明。`label` 就是屏幕上那一格的中文（chip 读 `label ?? code`），每条都得配。"""
    row = {'code': code, 'label': label, 'text': text}
    if hard:
        row['hard'] = True
    return row


def _dash(date: str) -> str:
    s = str(date or '').strip()
    return s[:10]


# ---------------------------------------------------------------- 参数（配置文件是唯一真源）

def load_config() -> tuple[dict, str | None]:
    """读 `app/intraday/broadcast_config.json` 里那 8 个 `t10_*` 键。

    返回 `(参数, 读不到时的原因)`。读不到**不静默用默认值**：默认值只在代码里，
    配置文件是用户能改的那一份，两边悄悄分叉就是"屏上摆的和推送发的不是同一档门槛"。
    """
    try:
        raw = json.loads(CFG_FILE.read_text(encoding='utf-8'))
    except (OSError, ValueError) as e:  # noqa: BLE001
        return dict(PARAM_DEFAULTS), f'{type(e).__name__}: {e}'
    cfg = {}
    for k, default in PARAM_DEFAULTS.items():
        v = raw.get(k)
        cfg[k] = default if v in (None, '', []) else v
    return cfg, None


def params_block(cfg: dict, unread: str | None) -> dict:
    """把这一屏用的参数原样交出去（界面「说明」里念的就是这几格，不许前端自己抄一份）。"""
    fam = list(cfg.get('t10_family') or FAMILY_DEFAULT)
    names = indicator_names(fam)
    return {
        'family': fam,
        'family_names': [names.get(k, k) for k in fam],
        'family_size': len(fam),
        'top_n': int(cfg.get('t10_top_n') or PARAM_DEFAULTS['t10_top_n']),
        'min_hits': int(cfg.get('t10_min_hits') or PARAM_DEFAULTS['t10_min_hits']),
        'window_sessions': int(cfg.get('t10_window_sessions') or PARAM_DEFAULTS['t10_window_sessions']),
        'hits_n': int(cfg.get('t10_hits_n') or PARAM_DEFAULTS['t10_hits_n']),
        'trend_streak_min': int(cfg.get('t10_trend_streak_min') or PARAM_DEFAULTS['t10_trend_streak_min']),
        'trend_n': int(cfg.get('t10_trend_n') or PARAM_DEFAULTS['t10_trend_n']),
        'char_keep': list(cfg.get('t10_char_keep') or PARAM_DEFAULTS['t10_char_keep']),
        'config_file': 'app/intraday/broadcast_config.json',
        'config_read': unread is None,
        'config_unread_reason': unread,
    }


def indicator_names(keys: Iterable[str]) -> dict[str, str]:
    """指标键 → 中文名（真源 `config.indicator.name`，本仓不许在界面或本文件里另写一份）。"""
    ks = [str(k) for k in keys]
    if not ks:
        return {}
    out = {}
    for r in engine.query('select indicator_key, name from config.indicator '
                          'where indicator_key = any(%s::text[])', (ks,), role='reader') or []:
        out[str(r['indicator_key'])] = str(r['name'] or r['indicator_key'])
    return out


# ---------------------------------------------------------------- 两张榜（判据只在这里）

def family(cfg: dict) -> list[str]:
    fam = list(cfg.get('t10_family') or [])
    return fam or list(FAMILY_DEFAULT)


def hits_shortlist(day: dt.date, cfg: dict) -> tuple[list[dict] | None, dt.date | None]:
    """一、多指标靠前：近 N 场里，家族中每项指标各自 top{top_n}，数每只票命中几项、几场。

    返回 (短名单, 窗口最新场次)；短名单 None ⇒ 库里最新场次不是今天（缺数）。
    """
    fam = family(cfg)
    top_n = int(cfg.get('t10_top_n', 100))
    min_hits = int(cfg.get('t10_min_hits', 4))
    win = int(cfg.get('t10_window_sessions', 3))
    n = int(cfg.get('t10_hits_n', 8))
    keep = list(cfg.get('t10_char_keep') or []) or ['strong']
    latest = engine.scalar('SELECT max(trade_date) FROM analytics.indicator_value'
                           ' WHERE trade_date <= %s', (day,), role='reader')
    if latest != day:
        return None, latest
    rows = engine.query(
        """
        WITH d AS (
            SELECT distinct trade_date FROM analytics.indicator_value
             WHERE trade_date <= %s ORDER BY trade_date DESC LIMIT %s
        ),
        fam AS (
            SELECT v.security_code, v.trade_date, v.indicator_key,
                   rank() OVER (PARTITION BY v.trade_date, v.indicator_key
                                ORDER BY v.value DESC) rk
              FROM analytics.indicator_value v
             WHERE v.trade_date IN (SELECT trade_date FROM d)
               AND v.indicator_key = ANY(%s::text[])
        )
        SELECT f.security_code, s.name, s.board, c.char_level,
               count(*) filter (where f.trade_date = (select max(trade_date) from d)) hits_today,
               count(distinct f.trade_date) days_in_top,
               x.rank_sum
          FROM fam f
          LEFT JOIN reference_data.security s ON s.security_code = f.security_code
          LEFT JOIN analytics.xy_rank_daily x
            ON x.security_code = f.security_code AND x.trade_date = (select max(trade_date) from d)
          LEFT JOIN analytics.stock_character_daily c
            ON c.security_code = f.security_code AND c.trade_date = (select max(trade_date) from d)
           AND c.spec_version = %s
         WHERE f.rk <= %s
           AND (c.char_level = ANY(%s::text[]) OR c.char_level IS NULL)
         GROUP BY f.security_code, s.name, s.board, c.char_level, x.rank_sum
        HAVING count(*) filter (where f.trade_date = (select max(trade_date) from d)) >= %s
         ORDER BY hits_today DESC, days_in_top DESC, x.rank_sum NULLS LAST
         LIMIT %s
        """, (day, win, fam, CHAR_SPEC, top_n, keep, min_hits, n), role='reader') or []
    if not rows:
        return [], latest
    # 上榜的**是哪几项**（只念前 3 个中文名，不堆满 9 个）＋今日涨停池的封板/题材
    codes = [r['security_code'] for r in rows]
    # 上榜的**是哪几项**：按当日**排名**取前 3（"它最靠前的是哪几项"，不是随便三行）
    inds: dict[str, list[str]] = {}
    for r in engine.query(
            """
            SELECT f.security_code, f.indicator_key, c.name
              FROM (
                SELECT v.security_code, v.indicator_key,
                       rank() OVER (PARTITION BY v.indicator_key ORDER BY v.value DESC) rk
                  FROM analytics.indicator_value v
                 WHERE v.trade_date = %s AND v.indicator_key = ANY(%s::text[])
                   AND v.security_code = ANY(%s::text[])
              ) f
              LEFT JOIN config.indicator c ON c.indicator_key = f.indicator_key
             ORDER BY f.security_code, f.rk
            """, (day, fam, codes), role='reader') or []:
        inds.setdefault(r['security_code'], []).append(r['name'] or r['indicator_key'])
    zt = {}
    for r in engine.query(
            "SELECT l.security_code, l.consec, l.reason"
            " FROM analytics.limit_up_pool_daily l"
            " WHERE l.trade_date = %s AND l.sealed AND l.security_code = ANY(%s::text[])"
            "   AND l.spec_version = %s",
            (day, codes, POOL_SPEC), role='reader') or []:
        zt[r['security_code']] = r
    for r in rows:
        r['inds'] = (inds.get(r['security_code']) or [])[:3]
        r['zt'] = zt.get(r['security_code'])
    return rows, latest


def trend_shortlist(day: dt.date, cfg: dict) -> tuple[list[dict] | None, dt.date | None]:
    """二、趋势最正：趋势票管线当天 `selected5` 且收盘在 5 日线上方的票，按站上天数排。

    返回 (短名单, 最新场次)；短名单 None ⇒ 缺场次（缺数）。
    """
    min_streak = int(cfg.get('t10_trend_streak_min', 5))
    n = int(cfg.get('t10_trend_n', 6))
    keep = list(cfg.get('t10_char_keep') or []) or ['strong']
    latest = engine.scalar('SELECT max(trade_date) FROM analytics.trend_stock_daily'
                           ' WHERE trade_date <= %s', (day,), role='reader')
    if latest != day:
        return None, latest
    rows = engine.query(
        """
        SELECT t.security_code, s.name, s.board, t.pct_change, t.ma5_streak,
               t.nh_20, t.sealed, i.value pos250, c.char_level
          FROM analytics.trend_stock_daily t
          LEFT JOIN reference_data.security s ON s.security_code = t.security_code
          LEFT JOIN analytics.indicator_value i
            ON i.security_code = t.security_code AND i.trade_date = t.trade_date
           AND i.indicator_key = 'pos250'
          LEFT JOIN analytics.stock_character_daily c
            ON c.security_code = t.security_code AND c.trade_date = t.trade_date
           AND c.spec_version = %s
         WHERE t.trade_date = %s AND t.selected5 AND t.above_ma5
           AND t.ma5_streak >= %s
           AND (c.char_level = ANY(%s::text[]) OR c.char_level IS NULL)
         ORDER BY t.ma5_streak DESC, t.nh_20 DESC, t.sealed DESC
         LIMIT %s
        """, (CHAR_SPEC, day, min_streak, keep, n), role='reader') or []
    return rows, latest


def top_boards(day: dt.date) -> list[dict]:
    """今日封住票里的最高标（≤3 只，按封单排）——"明日观察清单"那一格念它们。"""
    return engine.query(
        "SELECT s.name, l.consec FROM analytics.limit_up_pool_daily l"
        " LEFT JOIN reference_data.security s ON s.security_code = l.security_code"
        " WHERE l.trade_date = %s AND l.sealed"
        "   AND l.consec = (SELECT max(consec) FROM analytics.limit_up_pool_daily"
        "                    WHERE trade_date = %s AND sealed AND spec_version = %s)"
        "   AND l.spec_version = %s"
        " ORDER BY l.order_amount DESC NULLS LAST LIMIT 3",
        (day, day, POOL_SPEC, POOL_SPEC), role='reader') or []


# ---------------------------------------------------------------- 逐票全部指标

#: 按数值从大到小给名次的那三族单位（`yuan` 是量纲不是强弱、`count` 本身就是名次）
RANKABLE_UNITS = ('pct', 'ratio', 'none')

#: 单位清单从上面那份常量拼出来 —— SQL 里再抄一遍字面量，就是"改一处、另一处悄悄不跟"的形状。
#: 这三个值是 `config.indicator.output_unit` 的在册取值，不接受任何外部输入。
_UNITS_SQL = ', '.join(f"'{u}'" for u in RANKABLE_UNITS)

INDICATOR_SQL = f"""
with day as (
  select v.security_code, v.indicator_key, v.value::float8 as value,
         i.name, coalesce(i.output_unit, '') as unit, i.decimals,
         rank()  over (partition by v.indicator_key order by v.value desc nulls last) as rk,
         count(v.value) over (partition by v.indicator_key) as family_n
    from analytics.indicator_value v
    left join config.indicator i using (indicator_key)
   where v.trade_date = %s
)
select security_code, indicator_key, name, unit, decimals, value,
       case when unit in ({_UNITS_SQL}) and value is not null then rk end as rank,
       case when unit in ({_UNITS_SQL}) then family_n end as family_n
  from day
 where security_code = any(%s::text[])
 order by security_code, indicator_key
"""


def indicators_for(codes: list[str], day: str, refresh: bool = False) -> dict[str, list[dict]]:
    """这几只票这一场的**全部**技术指标（值 + 能跨票比的那 19 项给全场名次）。

    连不上库那一趟不钉进缓存（`{}` 会被下一次请求当成"这批票一个指标都没有"念出去）。
    缺那一行的票那一项交不出键，由 `_build()` 换算成"这只票缺哪几项"，界面是 `—` 而不是 0。
    """
    if not codes:
        return {}
    key = f'ind|{day}|{",".join(sorted(codes))}'
    hit = _CACHE.get(key)
    now = time.monotonic()
    if hit is not None and not refresh and now - hit[0] < _CACHE_TTL:
        return hit[1]
    rows = engine.query(INDICATOR_SQL, (day, list(codes)), role='reader')
    if rows is None:
        return {}                       # 读不到库：交空并由调用方出声，不拿上一次的值顶
    out: dict[str, list[dict]] = {}
    for r in rows:
        out.setdefault(str(r['security_code']), []).append({
            'key': str(r['indicator_key']),
            'name': str(r['name'] or r['indicator_key']),
            'unit': str(r['unit'] or ''),
            'rankable': str(r['unit'] or '') in RANKABLE_UNITS,
            'decimals': None if r['decimals'] is None else int(r['decimals']),
            'value': _num(r['value'], 4),
            'rank': None if r['rank'] is None else int(r['rank']),
            'family_n': None if r['family_n'] is None else int(r['family_n']),
        })
    if out:
        _CACHE[key] = (now, out)
    return out


def all_indicator_keys() -> list[str]:
    """在册指标（`config.indicator` 按 enabled 过滤后按键排序）——缺哪几项要跟这份清单比。"""
    return [str(r['indicator_key']) for r in (engine.query(
        'select indicator_key from config.indicator where enabled order by 1', (),
        role='reader') or [])]


# ---------------------------------------------------------------- 今天发出去的那一条原文

def pushed(day: str) -> dict | None:
    """`analytics.intraday_broadcast` 里今天那一栏的正文与发送结果（`task='tomorrow'`）。

    只读，不重算：`--resend` 用的就是这一条正文，屏上摆的也必须是它，否则"屏上名单"与
    "群里那条"各算一遍、对不上时谁都不知道该信谁。今天没跑 ⇒ 交 None（调用方点名是哪种没有）。
    """
    row = engine.query_one(
        "SELECT body, at_ts, counts, channel_key, ok, delivered, err"
        f" FROM {BROADCAST_TABLE}"
        " WHERE trade_date = %s AND task = 'tomorrow' AND what = 'tomorrow'"
        "   AND body IS NOT NULL"
        ' ORDER BY at_ts DESC LIMIT 1', (day,), role='reader')
    if not row:
        return None
    sent = engine.query_one(
        "SELECT channel_key, ok, delivered, err FROM " + BROADCAST_TABLE +
        " WHERE trade_date = %s AND task = 'tomorrow' AND what = 'sent'"
        ' ORDER BY at_ts DESC LIMIT 1', (day,), role='reader') or {}
    at = row.get('at_ts')
    return {
        'body': row.get('body'),
        'at': at.isoformat() if isinstance(at, dt.datetime) else _num(at),
        'sent_ok': sent.get('ok') if sent else None,
        'delivered': sent.get('delivered') if sent else None,
        'channels': sent.get('channel_key') if sent else row.get('channel_key'),
        'send_err': sent.get('err') if sent else None,
    }


# ---------------------------------------------------------------- 组装

def view(date: str = '', expand: bool = True, refresh: bool = False) -> dict:
    """这一屏的全部读数（带 15 分钟进程内缓存）。

    `expand=False` 只交两张榜、不摊 35 项指标；`refresh=True` 跳过缓存重扫一次。
    两份都自带 `cache_age_seconds`/`cached` —— 让人一眼看出这一屏是几秒前实算的。
    """
    key = f'view|{_dash(date) or "latest"}|{int(bool(expand))}'
    now = time.monotonic()
    hit = _CACHE.get(key)
    rebuilt = True
    if hit is not None and not refresh and now - hit[0] < _CACHE_TTL:
        data, built, rebuilt = hit[1], hit[0], False
    else:
        data = _build(date, expand, refresh)
        built = now
        if data.get('available'):       # 不可用的那一趟不钉进缓存（库修好了要能立刻重读）
            _CACHE[key] = (built, data)
    out = dict(data)
    out['cache_age_seconds'] = round(max(0.0, time.monotonic() - built), 1)
    # `cached` 说的是**本次请求**有没有重扫 —— 拿"年龄 > 1 秒"当判据会在连打两次时
    # 对着一份真的走了缓存的读数说"这是刚算的"，反过来也一样。重扫与否是个事实，不是阈值。
    out['cached'] = not rebuilt
    out['ttl_seconds'] = _CACHE_TTL
    return out


def _build(date: str = '', expand: bool = True, refresh: bool = False) -> dict:
    """这一屏的全部读数（不含缓存）。`expand=False` 只交两张榜、不摊 35 项指标。

    不可用时只交 `{available:false, reason}`，**不带 rows**；reason 说清是库不通、
    这一场还没有，还是两榜的输入表那天都没场次。
    """
    cfg, unread = load_config()
    want = _dash(date)
    if want:
        day = dt.date.fromisoformat(want)
    else:
        day = engine.scalar(f'select max(trade_date) from {IND_TABLE}', (), role='reader')
    if day is None:
        return {'available': False,
                'reason': '指标表 `analytics.indicator_value` 一行都没有 —— 加工链还没跑到因子那一步。'}
    day = day if isinstance(day, dt.date) else dt.date.fromisoformat(str(day)[:10])
    d = day.isoformat()

    hits, ind_latest = hits_shortlist(day, cfg)
    trends, tr_latest = trend_shortlist(day, cfg)
    if hits is None and trends is None:
        return {'available': False,
                'date': d,
                'reason': (f'两张榜的输入表都没有 {d} 这一场（指标最新到 {ind_latest or "无"}、'
                           f'趋势管线最新到 {tr_latest or "无"}）—— 缺数就不摆榜，'
                           '也不拿上一场那张榜冒充今天。')}

    params = params_block(cfg, unread)
    caveats: list[dict] = []
    if unread:
        caveats.append(_cav(
            'params_unread',
            f'配置文件 `app/intraday/broadcast_config.json` 读不到（{unread}），'
            '这一屏的家族与门槛用的是代码里的默认值 —— 它和 19:00 那一炉实际用的可能不是一份，'
            '对不上名单时先修配置。', '参数用的兜底默认值', hard=True))

    def _row(r: dict) -> dict:
        return {k: _num(v) for k, v in r.items() if k not in ('zt', 'inds')}

    hit_rows = []
    for i, r in enumerate(hits or [], 1):
        zt = r.get('zt') or {}
        hit_rows.append({**_row(r), 'no': i,
                         'hits_family': params['family_size'],
                         'window_sessions': params['window_sessions'],
                         'top_inds': list(r.get('inds') or []),
                         'consec': None if not zt else _num(zt.get('consec')),
                         'reason': (zt.get('reason') or None) if zt else None})
    trend_rows = []
    for i, r in enumerate(trends or [], 1):
        trend_rows.append({**_row(r), 'no': i})

    codes = []
    for row in hit_rows + trend_rows:
        c = row.get('security_code')
        if c and c not in codes:
            codes.append(c)
    truncated = len(codes) > MAX_EXPANDED
    board_codes = codes[:MAX_EXPANDED] if truncated else codes

    stocks: dict[str, dict] = {}
    missing_keys: set[str] = set()
    if expand and board_codes:
        catalogue = all_indicator_keys()
        by_code = indicators_for(board_codes, d, refresh)
        names = {c: '' for c in board_codes}
        for row in hit_rows + trend_rows:
            names[row['security_code']] = row.get('name') or row['security_code']
        for c in board_codes:
            rows = by_code.get(c) or []
            got = {r['key'] for r in rows}
            miss = [k for k in catalogue if k not in got]
            missing_keys.update(miss)
            stocks[c] = {
                'code': c, 'name': names.get(c) or c,
                'in_hits': any(r['security_code'] == c for r in hit_rows),
                'in_trends': any(r['security_code'] == c for r in trend_rows),
                'indicators': sorted(rows, key=lambda r: r['key']),
                'indicator_count': len(rows),
                'missing_indicators': miss,
            }
        if catalogue and any(not s['indicators'] for s in stocks.values()):
            empty = [s['name'] for s in stocks.values() if not s['indicators']]
            caveats.append(_cav(
                'indicator_rows_absent',
                f'这几只票在 {d} 这一场的指标表里一行都没有：{("、".join(empty[:8]))}'
                f'{"…" if len(empty) > 8 else ""} —— 那一格是 `—`（没有读数），不是 0，'
                '也不是"所有指标都垫底"。', '有票没指标行', hard=True))

    # 缺项点名：不是每只票都有 35 项 —— 次新股没有年线（ma250 当日母体就只有 5,393 只）。
    if missing_keys:
        nk = indicator_names(sorted(missing_keys))
        caveats.append(_cav(
            'indicator_partial_missing',
            '这一批里有 ' + '、'.join(f'{nk.get(k, k)}（{k}）' for k in sorted(missing_keys))
            + ' 某些票没有行 —— 那些格屏上是 `—`（判不了），不是 0。', '有指标项缺行'))
    if truncated:
        caveats.append(_cav(
            'expanded_truncated',
            f'两榜并集有 {len(codes)} 只，超过一屏摊开指标的上限 {MAX_EXPANDED} 只，'
            f'这一份只把前 {MAX_EXPANDED} 只的全部指标交出来（榜单本身还是全的）。'
            '要调那一族上限见 `duanxian/tomorrow_outlook.py::MAX_EXPANDED`。',
            '摊开的票被上限截断', hard=True))
    if hits is None:
        caveats.append(_cav(
            'hits_session_missing',
            f'一、多指标靠前缺数：指标表最新只到 {ind_latest or "无"}，今天（{d}）这一场还没有。',
            '指标榜缺场次', hard=True))
    if trends is None:
        caveats.append(_cav(
            'trends_session_missing',
            f'二、趋势最正缺数：趋势管线最新只到 {tr_latest or "无"}，今天（{d}）这一场还没有。',
            '趋势榜缺场次', hard=True))
    na = [r['name'] or r['security_code'] for r in (hits or []) + (trends or [])
          if 'char_level' in r and r['char_level'] is None]
    if na:
        caveats.append(_cav(
            'char_na_kept',
            '这些票的股性判不了（上市不足 120 场或档位价缺），按 11-04 定的口径照留在榜上并点名：'
            + '、'.join(sorted(set(na))) + ' —— `—` 不是"股性差"。', '股性判不了的照留'))

    both = None if (hits is None or trends is None) else sorted(
        {r['security_code'] for r in hits} & {r['security_code'] for r in trends})
    name_of = {r['security_code']: (r.get('name') or r['security_code'])
               for r in (hits or []) + (trends or [])}

    return {
        'available': True,
        'date': d,
        'requested_date': want or None,
        'generated_at': dt.datetime.now(dt.timezone.utc).astimezone().isoformat(timespec='seconds'),
        'params': params,
        'hits': hit_rows,
        'trends': trend_rows,
        'overlap': [{'code': c, 'name': name_of.get(c, c)} for c in (both or [])],
        'top_boards': [{'name': r.get('name'), 'consec': _num(r.get('consec'))}
                       for r in (top_boards(day) if hits is not None or trends is not None else [])],
        'expanded': expand,
        'stocks': stocks,
        'rank_note': RANKING.RULE_NOTE_SHORT,
        'pushed': pushed(d),
        'universe': {
            'indicator_codes_day': _one_n(f'select count(distinct security_code) n from {IND_TABLE}'
                                          f" where trade_date = '{d}'"),
            'character_scored_day': _one_n(
                f"select count(*) n from {CHAR_TABLE} where trade_date = '{d}'"
                f" and spec_version = '{CHAR_SPEC}' and char_level is not null"),
        },
        'caliber': (
            f'两张榜的判据只住 duanxian/tomorrow_outlook.py 一份，19:00 那一炉 import 同一份 —— '
            f'屏上和群里必然是一张榜。家族 {params["family_size"]} 项各取当日前 {params["top_n"]} 名，'
            f'今日命中 ≥{params["min_hits"]} 项者进一、，近 {params["window_sessions"]} 场重复上榜的次数'
            f'并列排序；二、取趋势管线当日 selected5 且收盘在 5 日线上方、站上 ≥{params["trend_streak_min"]} '
            f'日者。两榜都只留股性{"、".join(params["char_keep"])}（判不了的照留并点名）。'
            '逐票全部指标读 analytics.indicator_value 当日行，绝对价格那 15 项（单位 yuan）只给值不给名次。'),
        'caveats': caveats,
    }


def _one_n(sql: str) -> Optional[int]:
    """一条计数。读不到交 None —— 交 0 会被界面念成"母体 0 只"。"""
    try:
        v = engine.scalar(sql, (), role='reader')
    except Exception:  # noqa: BLE001
        return None
    return None if v is None else int(v)


# ---------------------------------------------------------------- 6 个月回溯（用户 2026-11-05 点名：
# 「列出策略最近 6 个月每天算出来的个股，往后 1/2/3 个交易日涨跌、冲高率、整体高点涨幅，
#   算整体平均值，判断这个策略的有效性」）
#
# 数据住 `analytics.outlook_pick_daily` / `analytics.outlook_baseline_daily`（写侧
# `app/compute/outlook_backtest.py`，判据 import 的就是本文件这两张榜）。本函数只做两件事：
#   1) **懒回填**：库里最新信号场次落后于"已有隔日"的最新场次 ⇒ 只补中间差的那天（一天约 10 秒），
#      空表才从头补全窗（约 8 分钟 —— 那是冷启动，界面上"重新取数"按钮会转很久，说明写在那一格）。
#   2) 读行汇总：每榜每指标的均值/中位/胜率/±3% 占比 + 全市场等权基线对照 + 逐场逐票明细。
# 缺失纪律照旧：哪列没数哪列 NULL（分母各算各的），界面是 `—` 不是 0。

BT_PICK_TABLE = 'analytics.outlook_pick_daily'
BT_BASE_TABLE = 'analytics.outlook_baseline_daily'
BT_SPEC = 'outlook-pick-1'   # 与 `app/compute/outlook_backtest.SPEC_VERSION` 同一条
_BT_CACHE: dict[str, tuple[float, Any]] = {}


def _bt_latest_complete_day() -> Optional[dt.date]:
    """最近一个"第 1 个隔日已经到"的信号场次（更早的场次 2/3 日也可能缺，由行内 NULL 自己说）。

    定义就一句：**指标表里严格早于"日线最新场次"的最大那一天** —— 那天之后至少还有一场，
    所以它的隔日表现已经能算。两个标量聚合，毫秒级。

    两处踩过的坑都在这：① 原来写成 `WHERE exists (SELECT 1 FROM market_data.stock_daily s
    WHERE s.trade_date > d.trade_date)`，那是逐行对着千万行日线做半连接，实测把请求拖到
    statement timeout（60 秒）才被杀；② 中途我还加过一个 `complete` 开关（"指标表最新场 <
    日线最新场才算有隔日"），那个判据是错的 —— 指标表与日线天天同步往前，两个 max 相等是常态，
    于是它永远交回 None，回填会**静默停在最后一天**不再前进。
    """
    r = engine.query_one(
        'SELECT (select max(trade_date) from analytics.indicator_value'
        '           where trade_date < (select max(trade_date) from market_data.stock_daily)) as n',
        (), role='reader') or {}
    return r.get('n')


def bt_ensure_backfilled() -> dict:
    """回测表落后于最新完整场次就补差。返回 {filled_days, from, to, note}（没补就是空差）。"""
    have = engine.scalar(f"SELECT max(trade_date) FROM {BT_PICK_TABLE}"
                         f" WHERE spec_version = %s", (BT_SPEC,), role='reader')
    need = _bt_latest_complete_day()
    if need is None:
        have_ind = engine.scalar('SELECT max(trade_date) FROM analytics.indicator_value', (), role='reader')
        return {'filled_days': 0, 'from': None, 'to': None,
                'note': ('指标表还没有任何场次' if have_ind is None else
                         f'最新一场（{have_ind}）的隔日还没到 —— 不是缺数，明天这一场会自动补')}
    if have is not None and have >= need:
        return {'filled_days': 0, 'from': None, 'to': None}
    from app.compute import outlook_backtest as OB
    # 起点用 `have + 1 天`：原来加 4 天是想跨过周末，可"have 是周五"那种日子会把周一整场跳过 ——
    # 于是那一场的名单永远不在表里，而界面上看不出来。多试一场空的日子不花钱（`window_days` 会滤掉）。
    start = dt.date.fromisoformat(str(have)[:10]) + dt.timedelta(days=1) if have else dt.date(2026, 6, 2)
    res = OB.run(start=start, end=need)
    return {'filled_days': int(res.get('sample_days') or 0), 'from': res.get('from'),
            'to': res.get('to'), 'note': '本次请求触发了懒回填（冷启动补全窗要几分钟，等这一趟）'
            if have is None else None}


def backtest_view(refresh: bool = False) -> dict:
    """6 个月回溯那一块的全部读数（15 分钟进程内缓存；`refresh=True` 重扫）。"""
    key = 'bt'
    now = time.monotonic()
    hit = _BT_CACHE.get(key)
    rebuilt = True
    if hit is not None and not refresh and now - hit[0] < _CACHE_TTL:
        data, built = hit[1], hit[0]
        rebuilt = False
    else:
        data = _backtest_build()
        built = now
        if data.get('available'):
            _BT_CACHE[key] = (built, data)
    out = dict(data)
    out['cache_age_seconds'] = round(max(0.0, time.monotonic() - built), 1)
    out['cached'] = not rebuilt
    out['ttl_seconds'] = _CACHE_TTL
    return out


def _stats_rows() -> list[dict]:
    """每榜 × 每指标的均值/中位/胜率/±3%（SQL 一次聚合；`missing_days` 数分母各算各的）。

    `both`（两榜都进）按**场次×票去重**：一只票同一天在两榜都有，原来会被 `case` 折进
    `both` 两次，`n` 直接翻倍（均值不变，但"样本 8 笔"其实是 4 只票）。`hits`/`trend` 
    两桶仍按各自榜的行数算 —— 那两桶本来就是"这个榜的表现"。
    """
    return engine.query(
        f"""
        with b as (select section, in_both, trade_date, security_code,
                          gap_pct, hi_pct, cl_pct, cl2_pct, cl3_pct, hi_max_3d_pct, up3d,
                          row_number() over (partition by trade_date, security_code
                                             order by section) rn
                     from {BT_PICK_TABLE}
                    where spec_version = %s),
             p as (select section as grp, gap_pct, hi_pct, cl_pct, cl2_pct, cl3_pct,
                          hi_max_3d_pct, up3d
                     from b
                   union all
                   select 'both' as grp, gap_pct, hi_pct, cl_pct, cl2_pct, cl3_pct,
                          hi_max_3d_pct, up3d
                     from b where in_both and rn = 1)
        select grp,
               count(*) n,
               avg(cl1)  mean_cl1,  percentile_cont(0.5) within group (order by cl1)  med_cl1,
               100.0*sum((cl1 > 0)::int) filter (where cl1 is not null)
                 /nullif(count(*) filter (where cl1 is not null), 0) win_cl1,
               100.0*sum((cl2 > 0)::int) filter (where cl2 is not null)
                 /nullif(count(*) filter (where cl2 is not null), 0) win_cl2,
               avg(cl2) mean_cl2, percentile_cont(0.5) within group (order by cl2) med_cl2,
               100.0*sum((cl3 > 0)::int) filter (where cl3 is not null)
                 /nullif(count(*) filter (where cl3 is not null), 0) win_cl3,
               avg(cl3) mean_cl3, percentile_cont(0.5) within group (order by cl3) med_cl3,
               100.0*sum((hi  > 0)::int) filter (where hi  is not null)
                 /nullif(count(*) filter (where hi  is not null), 0) win_hi,
               avg(hi) mean_hi,
               100.0*sum((hi3 > 0)::int) filter (where hi3 is not null)
                 /nullif(count(*) filter (where hi3 is not null), 0) win_hi3,
               avg(hi3) mean_hi3, percentile_cont(0.5) within group (order by hi3) med_hi3,
               100.0*sum((gap > 0)::int) filter (where gap is not null)
                 /nullif(count(*) filter (where gap is not null), 0) win_gap,
               avg(gap) mean_gap,
               100.0*sum((cl1 >= 3)::int) filter (where cl1 is not null)
                 /nullif(count(*) filter (where cl1 is not null), 0) ge3_cl1,
               100.0*sum((cl1 <= -3)::int) filter (where cl1 is not null)
                 /nullif(count(*) filter (where cl1 is not null), 0) le3_cl1,
               100.0*sum((cl3 >= 3)::int) filter (where cl3 is not null)
                 /nullif(count(*) filter (where cl3 is not null), 0) ge3_cl3,
               100.0*sum((cl3 <= -3)::int) filter (where cl3 is not null)
                 /nullif(count(*) filter (where cl3 is not null), 0) le3_cl3,
               100.0*sum((hi3 >= 3)::int) filter (where hi3 is not null)
                 /nullif(count(*) filter (where hi3 is not null), 0) ge3_hi3,
               100.0*sum((up3d)::int) filter (where up3d is not null)
                 /nullif(count(*) filter (where up3d is not null), 0) up3d_rate,
               count(up3d) filter (where up3d is not null) up3d_n
          from (select *, cl_pct as cl1, cl2_pct as cl2, cl3_pct as cl3,
                         hi_pct as hi, hi_max_3d_pct as hi3, gap_pct as gap
                  from p) t
         group by grp
        """, (BT_SPEC,), role='reader') or []


def _backtest_build() -> dict:
    filled = bt_ensure_backfilled()
    stamp_now = None
    drift = False
    try:
        from app.compute import outlook_backtest as OB
        stamp_now = OB.algo_stamp()
    except Exception:  # noqa: BLE001
        stamp_now = None
    # 取"最后一批写进去的那一行"的参数与指纹。原来这句是 `select params, algo_stamp,
    # max(calculated_at)` —— 两个裸列没进 group by，PG 直接报错，于是**这个接口每次 502**
    # （11-06 实测：60 秒后回 `column "outlook_pick_daily.params" must appear in the GROUP BY clause`）。
    params = engine.query_one(
        f"select params, algo_stamp, calculated_at at from {BT_PICK_TABLE}"
        f" where spec_version = %s order by calculated_at desc limit 1", (BT_SPEC,), role='reader')
    if not params:
        return {'available': False,
                'reason': (f'回测表 `analytics.outlook_pick_daily` 还没有 {BT_SPEC} 的行'
                           f'（懒回填也没补出来：{filled.get("note") or "场次输入缺"}）—— '
                           '先跑 `python -m app.compute.outlook_backtest --sessions 120`。')}
    try:
        drift = bool(stamp_now and params.get('algo_stamp') and
                     params['algo_stamp'] != stamp_now)
    except Exception:  # noqa: BLE001
        pass

    rng = engine.query_one(
        f"select min(trade_date) mn, max(trade_date) mx, count(distinct trade_date) n"
        f" from {BT_PICK_TABLE} where spec_version = %s", (BT_SPEC,), role='reader') or {}
    base = engine.query_one(
        f"""
        select avg(gap_pct) b_gap, avg(hi_pct) b_hi, avg(cl_pct) b_cl,
               avg(cl2_pct) filter (where cl2_pct is not null) b_cl2,
               avg(cl3_pct) filter (where cl3_pct is not null) b_cl3,
               avg(hi_max_3d_pct) filter (where hi_max_3d_pct is not null) b_hi3,
               avg(hi_win_3d_pct) filter (where hi_win_3d_pct is not null) b_hi3_win,
               count(*) bn
          from {BT_BASE_TABLE} where spec_version = %s
        """, (BT_SPEC,), role='reader') or {}

    stats = _stats_rows()
    stat_of = {str(r['grp']): r for r in stats}

    def _blk(g: str) -> dict:
        r = stat_of.get(g) or {}
        return {
            'n': None if r.get('n') is None else int(r['n']),
            'cl1': {'mean': _num(r.get('mean_cl1')), 'median': _num(r.get('med_cl1')),
                    'win_pct': _num(r.get('win_cl1')), 'ge3_pct': _num(r.get('ge3_cl1')),
                    'le3_pct': _num(r.get('le3_cl1'))},
            'cl2': {'mean': _num(r.get('mean_cl2')), 'median': _num(r.get('med_cl2')),
                    'win_pct': _num(r.get('win_cl2'))},
            'cl3': {'mean': _num(r.get('mean_cl3')), 'median': _num(r.get('med_cl3')),
                    'win_pct': _num(r.get('win_cl3')), 'ge3_pct': _num(r.get('ge3_cl3')),
                    'le3_pct': _num(r.get('le3_cl3'))},
            'hi1': {'mean': _num(r.get('mean_hi')), 'win_pct': _num(r.get('win_hi'))},
            'gap': {'mean': _num(r.get('mean_gap')), 'win_pct': _num(r.get('win_gap'))},
            'hi3': {'mean': _num(r.get('mean_hi3')), 'median': _num(r.get('med_hi3')),
                    'win_pct': _num(r.get('win_hi3')), 'ge3_pct': _num(r.get('ge3_hi3'))},
            'up3d_rate_pct': _num(r.get('up3d_rate')),
            'up3d_n': None if r.get('up3d_n') is None else int(r['up3d_n']),
        }

    days = []
    for r in engine.query(
            f"""
            select p.trade_date, p.section, p.security_code, p.security_name, p.board,
                   p.rank_in_section, p.in_both, p.gap_pct, p.hi_pct, p.cl_pct,
                   p.cl2_pct, p.cl3_pct, p.hi_max_3d_pct, p.up3d,
                   count(*) over (partition by p.trade_date, p.security_code) same_code_n
              from {BT_PICK_TABLE} p
             where p.spec_version = %s
             order by p.trade_date desc, p.section, p.rank_in_section
            """, (BT_SPEC,), role='reader') or []:
        d = str(r['trade_date'])[:10]
        cur_day = days[-1] if days and days[-1]['date'] == d else None
        if cur_day is None:
            cur_day = {'date': d, 'picks': []}
            days.append(cur_day)
        # 两榜都进的票在 pick 表里是两行（各榜一份名次）：合成一行、rank 用两榜各自的
        for i, ex in enumerate(cur_day['picks']):
            if ex['code'] == r['security_code']:
                if r['section'] == 'trend' and ex.get('sec') == 'hits':
                    ex['in_trend'] = True
                    ex['rank_trend'] = _num(r['rank_in_section'])
                break
        else:
            cur_day['picks'].append({
                'code': r['security_code'], 'name': _num(r['security_name']),
                'board': _num(r['board']),
                'sec': r['section'], 'in_trend': bool(r['in_both']) and r['section'] == 'hits',
                'rank': _num(r['rank_in_section']), 'rank_trend': None,
                'gap': _num(r['gap_pct']), 'hi': _num(r['hi_pct']), 'cl': _num(r['cl_pct']),
                'cl2': _num(r['cl2_pct']), 'cl3': _num(r['cl3_pct']),
                'hi3': _num(r['hi_max_3d_pct']), 'up3d': _num(r['up3d']),
            })

    caveats: list[dict] = []
    if drift:
        caveats.append(_cav('algo_drift',
                            '库里的脚本指纹与当前判据不一致 —— 这组行是**上一版判据**重算的，'
                            '判据改版后旧行留着对照（主键含 spec_version 正是为这个）。'
                            '要把这段场次按新版重算，跑 `python -m app.compute.outlook_backtest` '
                            '再 `refresh=True`。', '判据已改版', hard=True))
    if filled.get('note'):
        caveats.append(_cav('lazy_backfill', str(filled['note']), '本次触发了懒回填'))
    cfg, unread = load_config()
    params_json = None
    if params.get('params'):
        try:
            params_json = json.loads(params['params'])
        except (TypeError, ValueError):
            params_json = None
    if params_json is None:
        params_json = dict(cfg)

    return {
        'available': True,
        'window': {'from': _num(rng.get('mn')), 'to': _num(rng.get('mx')),
                   'sessions': _num(rng.get('n')), 'filled_this_request': filled},
        'params': params_block(cfg, unread),
        'params_snapshot': params_json,
        'algo_stamp': _num(params.get('algo_stamp')),
        'drift': drift,
        'summary': {g: _blk(g) for g in ('hits', 'trend', 'both')},
        'baseline': {'sessions': _num(base.get('bn')),
                     'gap': _num(base.get('b_gap')), 'hi': _num(base.get('b_hi')),
                     'cl': _num(base.get('b_cl')), 'cl2': _num(base.get('b_cl2')),
                     'cl3': _num(base.get('b_cl3')), 'hi3': _num(base.get('b_hi3')),
                     'hi3_win_pct': _num(base.get('b_hi3_win'))},
        'days': days,
        'computed_at': _num(params.get('at')),
        'caliber': (
            '名单＝每天 19:00 那一炉判据（duanxian/tomorrow_outlook.py 两份 SQL）重算历史场次；'
            '涨幅一律后复权价（价 × adjustment_factor）÷ 信号日收价 − 1，不是未复权 pct_change。'
            '往后 1/2/3 个交易日按**市场场次**数（停牌不拉长）；"冲高率"= 3 日内最高价 > 信号日收盘的占比；'
            '"整体高点涨幅"= 3 日内最高价 ÷ 信号日收价 − 1。NULL＝那一天它没有行或还没发生（—，不是 0%）。'
            '基线＝同批场次全市场等权同尺均值，没有对照那几个数没有意义。'
            '用今天这版判据回算历史 ≠ 当时的 19:00 真选出的票（判据还在改，algo_stamp 对得上才是一版）。'),
        'caveats': caveats,
    }
