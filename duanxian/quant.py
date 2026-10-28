"""量化选股轨的只读取数 —— 把 `analytics.score` 家族搬到页面上。

为什么要这一份：用户 2026-09-25 点名「导航栏一个都没增加，给了你这么多参考项目你都学到哪里去了，
把这么多的数据全部体现出来」。`analytics.score` 有 194 万行、新鲜到 09-23、8 个 capability 齐全，
**却一个页面都没读过它** —— 数据在库里躺着等于没做。

四条口径纪律（每条都有实测依据，不是风格偏好）：

1. **象限只在 `capability_key='oper_score'` 的行上有值**，其余 7 个 capability 恒为 NULL 是
   **设计**（`compute/scoring.py` 就这么定的），不是"七项指标没算出来"。这个区分随数据交出，
   否则页面会被读成数据缺失。
2. **象限标签从库里读**：`config.setting.quadrant_labels` 确实存在
   （2026-09-25 实测：`updated_at = 2026-08-31 20:38:51`，随 05_seed.sql 那批一起入库）。
   读不到才用兜底 map，且 `labels_source` 必须说清这次走的是哪条路 —— 别假装它一直是数据驱动的。
3. **权重与口径随数据交出**。评分页要能回答"凭什么是 72 分"，所以分量级带
   `weight/contribution/script_version`，并和 `config.view_effective_binding` 的注册表权重交叉核对。
   只给一个综合分等于把结论当事实卖。
4. **各表起点差别很大，窗口长度是 caveat 不是缺数**。起点与场数**不许背在这里**：
   它由 `_coverage(tables)` 现算（`min/max/count(distinct trade_date)`，走 `_agg` 缓存并交出缓存年龄），
   随同一份返回体的 `coverage` 一起发给界面。这里写死一份就会过期：10-11 那次倍量表回算
   205 场之后，注释里那句「只 19 场」就成了屏幕上的假话（2026-10-14 实测 208 场）。

另外两条从 qd 轨学来的教训，这里同样适用：
  · **算不出是 None，不是 0**。PG 的 numeric 能存 NaN，psycopg 会交回 `float('nan')`；
    它不是合法 JSON，也绝不能折成 0 —— 0 是一个真实分值。统一交 None，由界面显示 `—`。
  · **不静默换窗口/换场次**。请求 `window=20` 而库里只有 10，直接返回不可用并列出实有窗口。
"""

from __future__ import annotations

import datetime as dt
import hashlib
import math
import re
import time
from typing import Any

from app.compute import startup_volume as SV      # 五档倍量区间的判据只住那一处（本文件不再自己数）
from . import pg
from . import pg_sources
from . import day_returns
from . import em_board_link
from . import ranking          # 名次（并列同名、顺延）这件事的唯一实现 —— 公用组件
from .pg_sources import full_code as _full_code

CAPABILITIES = ('oper_score', 'temp_score', 'env_resonance', 'stock_quality',
                'sector_heat', 'market_mood', 'vol_stage', 'vol_temp')

# 兜底标签，**只在 config.setting 读不到时使用**，且使用时如实写在 labels_source 里。
_FALLBACK_LABELS = {'Q1': '买入', 'Q2': '持有', 'Q3': '试探', 'Q4': '回避'}
QUADRANT_MEANING = {
    'Q1': 'X 质量正、Y 环境正 —— 个股与板块同时向上',
    'Q2': 'X 正 Y 负 —— 票本身可以但环境不配合，持有不加仓',
    'Q3': 'X 负 Y 正 —— 环境热但票自身不够硬，只算试探',
    'Q4': 'X 负 Y 负 —— 回避',
}
# 人读的轴语义与合成式；**分量权重以 config.view_effective_binding 实测为准**
# （一起交在 axis_weights 里，两边不一致要暴露而不是取其一）。
QUADRANT_AXES = {
    'x': {'capability_key': 'stock_quality',
          'composition': {'形态': .25, '趋势': .25, '位置': .20, '筹码': .20, '量能': .10}},
    'y': {'capability_key': 'env_resonance',
          'composition': {'板块热度': .65, '大盘情绪': .35}},
    'oper_formula': 'oper_score = 0.40*X + 0.60*Y',
}
#: 象限的四个档 + 「当天没取到 oper_score」那一档（倍量榜的象限筛选要用，用户 2026-10-13）。
#: `NO_QUADRANT` 必须是**可选的一档**：榜上确实有票在 score 里没有行
#: （实测 2026-10-12：5,526 只里有 12 只没有象限）。少这一档，多选框就成了"筛不到的就消失"。
QUADRANT_CODES = ('Q1', 'Q2', 'Q3', 'Q4')
NO_QUADRANT = 'none'


def _quadrant_tokens(raw: str) -> tuple[list[str], list[str]]:
    """把 `Q2,Q4,none` 拆成 `(认得的档, 认不得的原样)`。

    认不得的**不悄悄丢掉**：交回给调用方出一条 caveat —— 拼错的人要当场看见自己拼错了，
    而不是看到一份"看起来筛过了"的榜（与 `jobs._valid` 对 `client_mode` 的处理同一口径）。
    """
    ok: list[str] = []
    bad: list[str] = []
    for cell in str(raw or '').replace('，', ',').split(','):
        t = cell.strip()
        if not t:
            continue
        key = NO_QUADRANT if t.lower() == NO_QUADRANT else t.upper()
        if key in QUADRANT_CODES or key == NO_QUADRANT:
            if key not in ok:
                ok.append(key)
        elif t not in bad:
            bad.append(t)
    return ok, bad
_NOTE = {
    'quadrant_design': '`quadrant` 只在 capability_key=oper_score 的行上有值，其余 7 个 capability '
                       '恒为 NULL 是设计而非缺数',
    'entry_quadrant': '`quadrant` 是**当日**象限；`window_entry_quadrant` 是**进 10 日窗口第一天**的象限'
                      '（producer 侧 `(array_agg(quadrant ORDER BY rn))[1]`）。两者分布本就不同，'
                      '不是数据打架，界面别去"对齐"它们',
    'stale': '`is_stale=true` 表示这一行的输入已不是该场最新数据，读数仍可用但要标注',
    'coverage': '各表起点不同，短历史是 caveat 不是缺数。这一张表实有覆盖（起止与场数）'
                '看同一份返回里的 `coverage`，它现算自库里的 `min/max(count distinct trade_date)`，'
                '一小时内复用同一份、并由缓存条声明年龄 —— 本注释与界面都不背这些数字，背了就会过期。',
    'zero_axis': 'X/Y 的中位数都落在负区（2026-09-23 实测 stock_quality 中位 -12.9、'
                 'env_resonance -24.6），所以"切在 0 上"会把多数票推进 Q4；'
                 '象限是相对零点的强弱，不是排名分位',
    'sector_breadth': '行业板块的 `up_count` 大面积为空 ⇒ 广度项算不出 ⇒ 点火判据永远不达标。'
                      '库里 `ignition.term_count` 列注释写的是"230 个行业板块…故永远不会判为 ignite"，'
                      '方向对但数字与机理都不准：实测 **296** 个行业板块、229,221 行里只有 **1,086** 行'
                      '有广度值，且这 1,086 行只覆盖 **12 个场次**（2026-08-27 前仅 18 个板块有值、'
                      '9 月起 103 个、09-23 因灌入三级行业才 293 个）；行业 `stage=ignite` **0 次**。'
                      '同期概念点火 330 次（138 个板块）、风格 318 次（64 个板块）。'
                      '所以行业不上点火榜是**算不出**，不是那天没轮动。'
                      '另：行业有 406 行凑满 5 项，"最多 4 项"也已不成立 —— 别引用注释里的常数，'
                      '要当下的数发 `stats=full` 看 `breadth_reality`',
    'heat_two_calibers': '`heat`（板块热度：涨幅/广度/动力/成交四分项）与 `score`'
                         '（点火：五要素加权）**不是同一个榜的两列**，是两套判据，排序会分叉。'
                         '另外 `member_scored_count` 只在已废弃的 `source=member_agg'
                         '` 口径上有值，现行 sector 口径整列 NULL 是设计不是缺数；'
                         '要判断某行热度可不可信，看 `part_count`（四分项里有值的个数 1~4）',
    'quadrant_null_share': '象限为空的票**不进任何一格**，所以四格相加不等于全市场票数。'
                           '具体比例见同一份返回里的 `null_coverage`（实测数据，不是估计）。',
    'entry_quadrant_null': '`window_entry_quadrant`（库里 `first_quadrant`）为空时那一格显示 `—`：'
                           '窗口首日恰好没有象限行。它为空不代表斜率算不出 —— 两者互不相干。'
                           '全区间占比见 `null_coverage`。',
    # 方向这句话的原文住 `duanxian/ranking.py`（名次这件事的唯一实现）：
    # 组热度表的"近 N 场名次"那一列也要念它，两处各写一遍迟早有一处反过来。
    'rank_direction': ranking.DIRECTION_NOTE,
    'env_rank_levels': 'Y 轴（env_resonance）的分数本身是档位化的，全市场几千只票只落在几十个不同取值上 ⇒ '
                       '它的 `rank_in_market` 大面积并列（一档里几百只票同名次）。'
                       'Y 轴路径里「数字相同」是并列、不是没动；具体档位数随本次读数交出（`universe`），'
                       '别背这里的描述当现值',
    'relative_not_absolute': '名次前进只说明「相对全场的位置变好了」，不等于这只票本身变好：'
                             '全场都在退时原地不动也是前进。绝对量看同一行的 `oper_value`（综合分原值）'
                             '与 X/Y 原值，两者一起读才分得清「它变强了」与「别人变弱了」',
}

#: caveat 那枚小徽标在屏幕上念的**中文**（用户 2026-10-22 指着整屏说「完全看不懂」，
#: 首要原因就是徽标念的是 `stale`、`cached_aggregates` 这类机器码，而不是那句话本身）。
#: `code` 照旧原样交出 —— 测试、推送与 AI 读它；界面只读 `label`。
#: 没登记的 code 一律退回中性词「口径说明」，**机器码不再上屏**（要对照就悬停，`title` 给的是 code）。
_CAV_LABEL = {
    'quadrant_design': '象限只在操作分那一行',
    'entry_quadrant': '当日象限与入口象限是两件事',
    'stale': '有些行是上一场的值',
    'coverage': '各表起点不同',
    'zero_axis': '零点不是中位',
    'sector_breadth': '行业广度算不出',
    'heat_two_calibers': '两套热度口径',
    'quadrant_null_share': '象限为空的票不进格',
    'entry_quadrant_null': '入口象限为空',
    'rank_direction': '名次方向',
    'env_rank_levels': 'Y 轴名次是档位化的',
    'relative_not_absolute': '相对位置不是绝对好坏',
    'cached_aggregates': '这几个数是缓存里的',
    'no_detail': '没有分量明细',
    'weight_mismatch': '权重与注册表不一致',
    'component_name_unmatched': '有分量没对上中文名',
    'rank_degenerate': '这一项的名次没有横截面意义',
    'rank_coarse': '这一项的名次挤在少数档位里',
    'no_rank': '有几行库里没写名次',
    'labels_fallback': '象限标签用的兜底那份',
    'y_axis_levels': 'Y 轴档位分辨率',
    'caliber_break': '这一窗口径不齐',
    'stats_columns_missing': '历史分布里有几列没数',
    'cohort_empty_window': '这一窗没人走完三条',
    'cohort_sessions_unread': '市场场次表没读到',
    'cohort_inputs_unread': '有几张输入表没读到',
    'cohort_conf_unmatched': '有几行在交叉榜里没行',
    'cohort_conf_day_missing': '有几天交叉榜没算',
    'cohort_stale_score': '有些档位是沿用旧场的',
    'cohort_edge': '后 5 场还没到齐',
    'cohort_self_check': '这一块自检没过',
}
_CAV_LABEL_FALLBACK = '口径说明'


def _cav(code: str, text: str) -> dict:
    """一条 caveat：机器码 + 上屏的中文 + 原文。构造只准走这一处。"""
    return {'code': code, 'label': _CAV_LABEL.get(code, _CAV_LABEL_FALLBACK), 'text': text}


# ---------------------------------------------------------------- 基础工具

def _plain(v):
    if v is None or isinstance(v, (bool, int, str)):
        return v
    # jsonb 列（config.setting.value、score_detail 的 params、sparkline 数组）
    # psycopg 直接交回 dict/list。**必须原样放过**：一旦掉进下面的 float() 分支，
    # 会被 str() 成 "{'Q1': '买入'}" 这种 Python repr，JSON 解析不了，
    # 于是"库里有的配置键"会变成"读不到"并悄悄走兜底。
    #
    # ⚠️ 但别把这段当成本模块那次误判的原因 —— 那是另一个错，而且更难防：
    # 当时探针用的是原始 fetch_df + to_string，输出里那一行**明明白白打印着**，
    # 是我把自己跑出来的结果读成了"0 行"。技术陷阱能靠代码挡，读错输出只能靠
    # "结论与被引用的那行输出一起贴出来"。
    if isinstance(v, (dict, list, tuple)):
        return list(v) if isinstance(v, tuple) else v
    if isinstance(v, float):
        return None if (math.isnan(v) or math.isinf(v)) else v
    if hasattr(v, 'isoformat'):
        return v.isoformat()
    try:
        f = float(v)
    except (TypeError, ValueError):
        return str(v)
    return None if (math.isnan(f) or math.isinf(f)) else f


def _rows(sql, params=()):
    df = pg.fetch_df(sql, params)
    if df is None:
        return None
    return [{k: _plain(v) for k, v in r.items()} for r in df.to_dict('records')]


def _one(sql, params=()):
    r = _rows(sql, params)
    return r[0] if r else None


# 全库级聚合与"看哪一天"无关：象限切分规则的实证、quadrant 为空的占比、各表起点。
# 它们一天最多变一次，可每次请求重算要 3.3 秒（194 万行扫描 + 两次自连接），
# 而这个数本来就是要不要给页面加 caveat 的依据 —— 进程内缓存 1 小时。
# 缓存不是免费的：它交出的是"一小时内的库"，所以年龄必须随数据一起交出（`_agg_age`），
# 否则回填之后读者会以为看到的是此刻的库。
_AGG_TTL = 3600.0
_AGG: dict[str, tuple[float, object]] = {}


def _agg(key: str, sql: str, params=(), many: bool = False):
    """全库级聚合的进程内缓存。`many=True` 用于多行聚合（如按板块类型分组）。

    慢的从来不是榜单本身（09-23 当日榜 0.32 秒），是这几类"一天最多动一次"的全表扫描：
    `analytics.sector_trend` 545 万行的覆盖度统计单独要 7.8 秒。
    """
    now = time.monotonic()
    hit = _AGG.get(key)
    if hit is not None and now - hit[0] < _AGG_TTL:
        return hit[1]
    value = _rows(sql, params) if many else _one(sql, params)
    if value is not None:   # 连不上库时别把 None 钉在缓存里一小时 —— 那样重连也不会再试
        _AGG[key] = (now, value)
    return value


def _agg_call(key: str, fn):
    """`_agg` 的函数版：同一份进程内缓存（`_AGG`、同一个 TTL），跑的是一条 SQL 之外的取数函数。

    给那些"慢但不是一条 SQL 能包住"的取数（`day_returns.series_by_code` 取回后还要在 Python
    里逐票串起算日）。存 None 的规矩照搬 `_agg`：连不上库那一趟不许钉在缓存里一小时。
    """
    now = time.monotonic()
    hit = _AGG.get(key)
    if hit is not None and now - hit[0] < _AGG_TTL:
        return hit[1]
    value = fn()
    if value is not None:
        _AGG[key] = (now, value)
    return value


def _agg_age(key: str) -> float | None:
    """该聚合距上次真算过了多少秒；没算过返回 None。"""
    hit = _AGG.get(key)
    return None if hit is None else round(time.monotonic() - hit[0], 1)


def _agg_lazy(key: str, sql: str, params=(), many: bool = False):
    """**有缓存就返回、没有就返回 None 而不是现算**。

    给"口径解释类"的全库聚合用：它们决定不了今天谁领涨，却各要 2~3 秒
    （`sector_trend` 545 万行的 distinct 2.3 秒、广度全历史分组 2.6 秒）。
    默认首屏只查当日（0.4 秒内），这几项等谁愿意付那几秒时算一次、之后一小时白送。
    返回 `(值, 是不是现算的)`；值为 None 时接口会写清 `stats=full` 才能拿到。
    """
    hit = _AGG.get(key)
    if hit is not None and time.monotonic() - hit[0] < _AGG_TTL:
        return hit[1], False
    return None, False



def clear_agg_cache(prefix: str = '') -> int:
    """丢掉缓存。回填刚跑完要看新数时用；`prefix` 只清相关项，别一锅端别的接口。"""
    keys = [k for k in _AGG if not prefix or k.startswith(prefix)]
    for k in keys:
        _AGG.pop(k, None)
    return len(keys)


def _ratio(part, whole):
    whole = _int(whole)
    part = _int(part)
    if not whole or part is None:
        return None
    return _num(part * 100.0 / whole, 1)


def _num(v, nd=None):
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if (math.isnan(f) or math.isinf(f)) else (f if nd is None else round(f, nd))


def _int(v):
    f = _num(v)
    return None if f is None else int(f)


def jsonable(v: Any) -> Any:
    """只读接口出口清洗（一处实现，各视图共用）：Decimal→float、日期→ISO 串、NaN/NaT→None。

    为什么需要它：`_num` 会把日期串变成 None（它只认能转 float 的东西），
    而整份 payload 里日期、文本、嵌套 dict/list 混着来，逐字段包 `_num` 迟早有人漏一个
    ——漏一个的后果不是报错，是 `json.dumps` 写出裸 `NaN`/`Infinity` 或直接把 `Decimal`
    抛给序列化层，浏览器 `JSON.parse` 炸在这一屏，页面只会说"取数异常"。
    ⚠️ 判"自己不等于自己"要排在日期之前：`pd.NaT` 是 `datetime` 的实例，
    先判日期就会带着 NaT 进 JSON。
    """
    if v is None or isinstance(v, (bool, int, str)):
        return v
    try:
        if v != v:
            return None
    except Exception:                          # 数组类比较返回的是向量，不是标量 ⇒ 不是我们要清洗的东西
        pass
    if isinstance(v, float):
        return v if math.isfinite(v) else None
    if hasattr(v, 'as_tuple'):                 # decimal.Decimal（numeric 列全到这里）
        f = float(v)
        return f if math.isfinite(f) else None
    if isinstance(v, (dt.date, dt.datetime, dt.time)):
        return v.isoformat()
    if isinstance(v, dict):
        return {k: jsonable(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [jsonable(x) for x in v]
    return str(v)


def _latest(table: str, spec: str | None = None) -> str | None:
    """这张表最近有数的一场。给了 `spec` 就只认那个判据版本。

    为什么要给：判据改版后旧行留着对账，不带版本条件的 `max(trade_date)` 可能挑到一个
    只剩旧版本的日子 —— 页面于是报「这一场没有信号」，而实情是新版本还没算到那一天（回算
    分批跑的时候天天如此）。那是把"还没算"读成"没有"。
    """
    if spec:
        r = _one(f"select max(trade_date)::text d from {table} where spec_version = %s", (spec,))
        return (r or {}).get('d') or None
    return pg.latest_session(table)


def _alive() -> bool:
    """区分"库连不上/查询失败"与"库里真没这一场"。

    `pg.fetch_df` 两种都返回 None。不探针就会把连接故障报成"还没有已算好的场次"，
    而后者读起来像"再等等就回填了" —— 那种措辞会把人带向错误结论。
    """
    return bool(_one('select 1 as ok'))


def _dash(date: str) -> str:
    d = (date or '').strip()
    return f'{d[:4]}-{d[4:6]}-{d[6:8]}' if len(d) == 8 and d.isdigit() else d


def _reject(what: str, date: str | None, why: str) -> dict:
    """**参数本身不对**时的不可用 —— 不许借 `_fail` 那句"这一场没有数据"。

    两者读起来完全不同：一个是"你给的条件我按不了"，一个是"库里没这场"。
    混成一句会让人去查回填，而真正要改的是请求参数（10-07 写新高这一格时实测到：
    `window=999` 被回成"2026-09-30 这一场没有数据"，看着像数据坏了）。
    """
    return {'available': False, 'date': date, 'reason': f'{what}：{why}'}


def _fail(what: str, table: str, date: str | None, extra: str = '') -> dict:
    if not pg.enabled():
        reason = f'{what}要读本地库 `{table}`；未配置 .env 的 PG_USER/PG_PASSWORD 时没有这一项'
    elif not _alive():
        reason = (f'本地库连不上或查询失败（**不是"没有数据"**）：{what} 读不到 `{table}`。'
                  '先确认 PostgreSQL 在跑、PG_USER/PG_PASSWORD 有效')
    elif not date:
        reason = f'`{table}` 里没有任何已算好的场次，本地计算尚未覆盖该项'
    else:
        reason = (f'`{table}` 里 {date} 这一场没有数据（未回填或当日非交易日），'
                  '不回落到邻近交易日')
    return {'available': False, 'date': date, 'reason': reason if not extra else f'{reason}；{extra}'}


def _empty_after_filter(what: str, table: str, date: str, how: str) -> dict:
    """**这一场有数据、只是筛完是 0 行** —— 与 `_fail`（那一场根本没有行）分开说。

    原先这两件事共用一句"`{table}` 里 {date} 这一场没有数据（未回填或当日非交易日）"，
    于是「倍量次数≥12」这种高门槛筛空了也会被告知"今天的数还没回填"（10-17 推送那一侧实测抓到：
    正文里明明写着这场没数据，而同一分钟页面能列出 191 只）。库连不上/查询失败时**不说这句**，
    交回 `_fail` —— 那句"有数据"在库不通时本身就是假话。
    """
    if not pg.enabled() or not _alive():
        return _fail(what, table, date, f'{how} ⇒ 0 只')
    return {'available': False, 'date': date,
            'reason': f'`{table}` 里 {date} 这一场是有数据的，但{how} ⇒ 0 只'
                      '（不是没回填，也不是库连不上）'}


def _labels() -> tuple[dict | None, str]:
    row = _one("select value from config.setting where key = 'quadrant_labels'")
    v = row.get('value') if row else None
    if isinstance(v, str):
        import json
        try:
            v = json.loads(v)
        except ValueError:
            v = None
    if isinstance(v, dict) and v:
        return v, "config.setting.quadrant_labels（实测该键存在，updated_at 随 2026-08-31 那批 seed 入库）"
    return dict(_FALLBACK_LABELS), ('兜底：config.setting 里读不到 quadrant_labels，'
                                   '本次用的是代码内兜底标签，与库里那份可能已经分叉')


def _axis_weights() -> dict:
    """从注册表取两轴的实际分量权重，和 QUADRANT_AXES 的说明式权重并排交出。"""
    rows = _rows("""select capability_key, script_key, script_name, raw_weight, eff_weight
                      from config.view_effective_binding
                     where capability_key in ('stock_quality','env_resonance','oper_score')
                     order by capability_key, display_order""") or []
    out: dict[str, list] = {}
    for r in rows:
        out.setdefault(r['capability_key'], []).append(
            {'script_key': r['script_key'], 'script_name': r['script_name'],
             'raw_weight': _num(r['raw_weight'], 4), 'eff_weight': _num(r['eff_weight'], 4)})
    declared = {'stock_quality': [v for v in QUADRANT_AXES['x']['composition'].values()],
                'env_resonance': [v for v in QUADRANT_AXES['y']['composition'].values()],
                'oper_score': [.40, .60]}
    drift = []
    for cap, want in declared.items():
        got = sorted(b['raw_weight'] for b in out.get(cap, []) if b['raw_weight'] is not None)
        if got and [round(w, 4) for w in sorted(want)] != got:
            drift.append({'capability_key': cap, 'doc_weights': sorted(want), 'registry_weights': got})
    return {'registry': out, 'doc_weights': declared, 'drift': drift}


def _caveats(*keys: str, extra: str | None = None) -> list[dict]:
    out = [_cav(k, _NOTE[k]) for k in keys if _NOTE.get(k)]
    if extra:
        out.append(_cav('note', extra))
    return out


def _coverage(tables: tuple[str, ...]) -> dict:
    out = {}
    for t in tables:
        # 194 万行的 count(distinct trade_date) 没有索引可走，单次 ~0.7 秒；
        # 它是全库事实，与本次看哪一天无关，所以走 _agg 缓存（缓存年龄由 _cache_caveat 交出）。
        r = _agg(f'coverage:{t}',
                 f'select min(trade_date) a, max(trade_date) b, count(distinct trade_date) n '
                 f'from analytics.{t}')
        out[t] = {'from': r and r['a'], 'to': r and r['b'], 'sessions': r and r['n']}
    return out


def _agg_label(key: str) -> str:
    """caveat 是给读页面的人看的，别把内部缓存键名原样印上去。"""
    if key.startswith('coverage:'):
        return f'`{key.split(":", 1)[1]}` 表的覆盖起点'
    if key.startswith('first_session:'):
        return '本机日线的第一天'
    if key.startswith('new_high:'):
        return '自选股新高（按所选分组与窗口）'
    if key.startswith('new_high_peaks:'):
        return '自选股新高的峰日回捞'
    if key.startswith('rank_momentum_stats:'):
        # 缓存键里带着"看哪一天、哪一档"，直接印上屏幕就是一串内部标识；这里只留人能读的那半句。
        parts = key.split(':')
        which = {'xy': 'X+Y 两轴合名次', 'oper': '总分名次'}.get(parts[-1], parts[-1])
        return f'名次稳步前进榜的窗口与母体（看 {parts[1]} 的「{which}」这一档）'
    if key.startswith('step_gap_pool:'):
        parts = key.split(':')
        return (f'倍量次数较前窗增减的全场分布（{parts[1]} 那场、倍量次≥{parts[2]} 的母体，'
                f'阈值 {STEP_GAP_MIN}）')
    return _AGG_LABELS.get(key, key)


_AGG_LABELS = {'quadrant_rule': '象限切分规则的逐场实证',
               'quadrant_null_share': '象限为空的比例',
               'entry_quadrant_null_share': '入口象限为空的比例'}


def _cache_caveat(keys: tuple[str, ...]) -> list[dict]:
    """缓存必须自己声明自己：全库聚合一小时算一次，回填之后看到的会是旧值。

    只报"本次请求之前就算好的"那些（年龄 > 1 秒）—— 刚在同一个请求里算出来的项
    也列进去，等于对着新鲜数喊"这是缓存"，反而误导。
    """
    known = {k: v for k, v in ((k, _agg_age(k)) for k in keys) if v is not None and v > 1.0}
    if not known:
        return []
    return [_cav('cached_aggregates',
                 '这几项全库级聚合取自进程内缓存（一小时重算一次），不是此刻重扫：'
                 + '、'.join(f'{_agg_label(k)}（{v:.0f} 秒前实算）' for k, v in known.items())
                 + '。刚回填过场次时这一小时的旧值要留心；要立刻看新数，请求带 `refresh=1`。')]


# 象限/入口象限为空的行"不进任何一格"，是分布读数的头号陷阱，所以把空值比例当数据交出。
# 这两条都是全库聚合（无 trade_date 可过滤），一次 ~0.3~0.5 秒，故与 coverage 同走 _agg。

def _null_coverage(date: str, session_nulls: int | None = None) -> dict:
    """`null_coverage`：象限为空的占比，分"全场历史"与"所选这一场"两个分母。

    只给一个数会被读错：历史上 27.6% 的 `oper_score` 行没有象限（ producer 早期口径），
    而最近这些场一场都不空 —— 页面若只说"27.6% 为空"就是吓唬人，只说"本场 0%"是掩盖历史。
    """
    allrows = _agg('quadrant_null_share',
                   "select count(*) tot, count(*) filter (where quadrant is null) n "
                   "from analytics.score where capability_key = 'oper_score'")
    trend = _agg('entry_quadrant_null_share',
                 "select count(*) tot, count(*) filter (where first_quadrant is null) n "
                 "from analytics.score_trend")
    out = {
        'score_oper_rows_all_sessions': _int(allrows and allrows.get('tot')),
        'score_quadrant_null_all_sessions': _int(allrows and allrows.get('n')),
        'score_quadrant_null_pct_all_sessions': _ratio(allrows and allrows.get('n'),
                                                       allrows and allrows.get('tot')),
        'score_selected_session': {'date': date,
                                   'quadrant_null': _int(session_nulls),
                                   'note': '所选这一场里 quadrant 为空的行数（不进任何一格）'},
        'score_trend_rows': _int(trend and trend.get('tot')),
        'score_trend_first_quadrant_null': _int(trend and trend.get('n')),
        'score_trend_first_quadrant_null_pct': _ratio(trend and trend.get('n'),
                                                     trend and trend.get('tot')),
        'why': '分布四格相加不等于全市场票数：象限为空的票一格都不进。空是 producer 那行没算出'
               '象限（早期口径），不是被筛掉的票',
    }
    return out


def _comments(table: str) -> dict:
    """把列注释当数据交出，界面就不必把字段含义写死在组件里。

    传裸表名（`pg_class.relname` 不含 schema 前缀），带前缀传进来这里剥掉 ——
    否则静默返回空字典，看起来像"这张表没写注释"。
    """
    table = table.split('.')[-1]
    rows = _rows("""select a.attname as col, col_description(c.oid, a.attnum) as comment
                      from pg_class c join pg_namespace n on n.oid = c.relnamespace
                      join pg_attribute a on a.attrelid = c.oid and a.attnum > 0 and not a.attisdropped
                     where n.nspname = 'analytics' and c.relname = %s
                       and col_description(c.oid, a.attnum) is not null""", (table,)) or []
    return {str(r['col']): ' '.join(str(r['comment']).split()) for r in rows}


# ================================================================ 1. 四维象限

def quadrant(date: str = '', min_x=None, min_y=None, limit: int = 200,
             refresh: bool = False) -> dict:
    """四象限选票：X=个股质量、Y=环境共振，`oper = 0.40X + 0.60Y`。

    象限的切分规则**不靠宣称**：本函数逐场比对"存储的 quadrant"与"按 sign(X)/sign(Y)
    推出的 quadrant"，把一致率与映射表一起交出（实测 199,525 行全对得上，映射为
    Q1=X+Y+ / Q2=X+Y- / Q3=X-Y+ / Q4=X-Y-）。对不上就把差异暴露成 caveat，
    不偷偷改成"看起来一致"。

    分布按**未过滤的全场**给（`distribution`），过滤后的条数单独放在 `filter` 里 ——
    两者混成一个数会让"筛完只剩 3 只"被读成"市场只有 3 只"。

    `refresh=True`：清掉本接口碰到的全库聚合（规则实证、空值占比、覆盖起点）重算。
    不刷新时这些一小时算一次，年龄随 `cached_aggregates` caveat 交出，不拿旧值冒充此刻。
    """
    if refresh:
        clear_agg_cache('quadrant')
        clear_agg_cache('entry_quadrant_null_share')
        clear_agg_cache('coverage:')
    table = 'analytics.score'
    d = _dash(date) or _latest(table) or ''
    if not d or not pg.session_has(table, d):
        return _fail('四象限选票', table, d or None)

    # 不按 quadrant is not null 过滤掉空象限：那一格恰好是分布要交代的东西，
    # 拆出来单独放，四格相加就不等于全市场票数（见 null_coverage）。
    counts = _rows("""select quadrant, count(*)::int as n from analytics.score
                       where trade_date = %s and capability_key = 'oper_score'
                       group by 1 order by 1""", (d,))
    session_nulls = next((r['n'] for r in (counts or []) if r['quadrant'] is None), 0)
    counts = [r for r in (counts or []) if r['quadrant'] is not None]
    if not counts:
        return _fail('四象限选票', table, d, '该场没有任何带象限的综合分')

    lim = max(1, min(int(limit or 200), 1000))
    pick = _rows(
        """select s.security_code as code, sec.name, s.quadrant,
                  s.value as oper_value, s.rank_in_market, s.percent_rank, s.is_stale,
                  x.value as x_quality, y.value as y_env,
                  t.slope, t.up_streak_days, t.window_days, t.point_count,
                  t.first_quadrant,
                  -- 象限趋势变化：窗口期内每天的象限串成 Q1→Q2→Q3 形式
                  (select string_agg(sq.quadrant, '→' order by sq.trade_date)
                     from analytics.score sq
                    where sq.security_code = s.security_code
                      and sq.capability_key = 'oper_score'
                      and sq.trade_date <= s.trade_date
                      and sq.trade_date > s.trade_date - make_interval(days => coalesce(t.window_days, 10) * 7 / 5)
                      and sq.quadrant is not null) as quadrant_trend
             from analytics.score s
             join reference_data.security sec using (security_code)
             left  join analytics.score x on x.trade_date = s.trade_date
                                          and x.security_code = s.security_code
                                          and x.capability_key = 'stock_quality'
             left  join analytics.score y on y.trade_date = s.trade_date
                                          and y.security_code = s.security_code
                                          and y.capability_key = 'env_resonance'
             left  join analytics.score_trend t on t.trade_date = s.trade_date
                                                and t.security_code = s.security_code
            where s.trade_date = %s and s.capability_key = 'oper_score'
              and s.quadrant is not null
              and (%s::numeric is null or x.value >= %s::numeric)
              and (%s::numeric is null or y.value >= %s::numeric)
            order by s.value desc nulls last, s.security_code
            limit %s""", (d, _num(min_x), _num(min_x), _num(min_y), _num(min_y), lim))

    labels, labels_source = _labels()
    dist = {}
    for r in counts:
        q = r['quadrant']
        dist[q] = {'count': r['n'], 'label': labels.get(q, '未入库标签'),
                   'meaning': QUADRANT_MEANING.get(q)}

    # 规则的实证：全库逐场比对存储象限与按正负推出的象限。
    # 这条要扫 194 万行自连接（实测 ~2.1 秒），且答案与"看哪一天"无关 —— 缓存一小时，
    # 年龄由下面的 cached_aggregates caveat 自己交代。
    agree = _agg('quadrant_rule', """select count(*) n,
                          count(*) filter (where s.quadrant =
                              case when x.value > 0 and y.value > 0 then 'Q1'
                                   when x.value > 0                then 'Q2'
                                   when y.value > 0                then 'Q3'
                                   else 'Q4' end) matched
                     from analytics.score s
                     join analytics.score x on x.security_code = s.security_code
                                            and x.trade_date = s.trade_date
                                            and x.capability_key = 'stock_quality'
                     join analytics.score y on y.security_code = s.security_code
                                            and y.trade_date = s.trade_date
                                            and y.capability_key = 'env_resonance'
                    where s.capability_key = 'oper_score' and s.quadrant is not null""")
    n_all, n_ok = (_int(agree and agree.get('n')) or 0), (_int(agree and agree.get('matched')) or 0)
    rule = {'mapping': {'Q1': 'X+ Y+', 'Q2': 'X+ Y-', 'Q3': 'X- Y+', 'Q4': 'X- Y-'},
            'verified_rows': n_all, 'verified_matched': n_ok,
            'verified_mismatched': n_all - n_ok,
            'how': '全库逐场把存储 quadrant 与按 sign(X)/sign(Y) 推出的结果对比，不是按命名猜的'}

    rows = []
    for r in (pick or []):
        # `first_quadrant` 不是"另一套象限口径"，是**进窗口第一天**的象限。
        # 不改名就会被当成与当日象限矛盾的数据去"对齐"。
        r['window_entry_quadrant'] = r.pop('first_quadrant', None)
        r['quadrant_label'] = labels.get(r.get('quadrant'))
        r['slope'] = _num(r.get('slope'), 4)
        r['x_quality'] = _num(r.get('x_quality'))
        r['y_env'] = _num(r.get('y_env'))
        r['oper_value'] = _num(r.get('oper_value'))
        rows.append(r)

    # 榜内名次（按 `oper_value desc, security_code` 的原序，就是接口交出来的这个序）。
    # 分页要说清"第 101~200 名"按的是哪一份序：页面上点列头只换本页内的先后，
    # 不改变哪些行落在第 2 页 —— 所以名次必须在后端定下来随行走，不能让页面按当前序号猜。
    for i, r in enumerate(rows, start=1):
        r['rank_in_pool'] = i

    # 「概念板块」这一列 + 反向聚合榜（用户 2026-10-12 点名）：判据与口径只住
    # `duanxian/pg_sources.py`，这里只做接法 —— 与倍量榜那一屏用的是同一对函数，
    # 免得两处各写一套"什么算一个概念"。
    codes = [str(r['code'])[:6] for r in rows if r.get('code')]
    cons = pg_sources.concepts_by_code(codes)
    no_concept = []
    for r in rows:
        got = cons.get(str(r.get('code'))[:6]) or {}
        r['concepts'] = got.get('concepts')
        r['concept_count'] = got.get('concept_count')
        if r['concepts'] is None:
            no_concept.append(str(r['code'])[:6])
    roll = pg_sources.concept_rollup(codes)

    out = {'available': True, 'date': d,
           'distribution': dist, 'rows': rows,
           'axes': {**QUADRANT_AXES, 'weights_from_registry': _axis_weights()},
           'quadrant_rule': rule,
           'quadrant_labels': labels, 'labels_source': labels_source,
           'filter': {'min_x': _num(min_x), 'min_y': _num(min_y),
                      'rows_returned': len(rows), 'limit': lim,
                      'note': 'distribution 是全场分布（未过滤）；filter 只影响 rows。'
                              'rows 按 oper_value 降序，行上的 rank_in_pool 就是这个序里的名次'},
           # ---- 概念板块：逐票那一列（rows[].concepts）+ 把这批票反向按概念数一遍
           'concepts': roll['rows'],
           'concept_pool': {'size': roll['pool_size'], 'covered': roll['covered'],
                            'concepts_total': len(roll['rows']),
                            'single_stock': sum(1 for c in roll['rows'] if (c['stocks'] or 0) <= 1)},
           'concept_rule': pg_sources.CONCEPT_RULE_NOTE,
           'null_coverage': _null_coverage(d, session_nulls),
           'coverage': _coverage(('score', 'score_trend')),
           'caveats': _caveats('quadrant_design', 'entry_quadrant', 'zero_axis',
                               'quadrant_null_share', 'coverage'),
           'source': 'pg', 'requests': 0}
    out['caveats'] += _cache_caveat(('quadrant_rule', 'coverage:score', 'coverage:score_trend',
                                     'quadrant_null_share', 'entry_quadrant_null_share'))
    nc = out['null_coverage']
    if nc['score_selected_session']['quadrant_null']:
        out['caveats'].append({
            'code': 'session_quadrant_null',
            'text': f'{d} 这一场有 {nc["score_selected_session"]["quadrant_null"]} 行综合分没有象限，'
                    '它们不进上面任何一格 —— 四格相加会小于该场有综合分的票数'})
    if labels_source.startswith('兜底'):
        out['caveats'].append({'code': 'labels_fallback', 'text': labels_source})
    if n_all and n_ok != n_all:
        out['caveats'].append({'code': 'quadrant_rule_mismatch',
                              'text': f'{n_all - n_ok} 行的存储象限与按 X/Y 正负推出的结果不一致；'
                                      '以库里的 quadrant 列为准，但这条差异要显示出来'})
    drift = out['axes']['weights_from_registry']['drift']
    if drift:
        out['caveats'].append({'code': 'axis_weight_drift',
                              'text': f'说明式轴权重与 config.view_effective_binding 不一致：{drift}'})
    # 概念那两块（逐票列 + 反向榜）的三种"没数"必须分开：查询坏了 ≠ 这批票没题材 ≠ 榜被截短
    if roll.get('failed'):
        out['caveats'].append({'code': 'concept_failed',
                              'text': '概念归属这一格整批没读到（查询失败或库不通），不是这批票没有概念 —— '
                                      '两件事必须分开说，界面显示 —，不填空也不当"没题材"。'})
    elif not roll['rows']:
        out['caveats'].append({'code': 'concept_empty',
                              'text': f'这 {roll["pool_size"]} 只在参考数据里一条概念归属都没有（少见，'
                                      '通常意味着整批都是北交所或参考数据没覆盖到）。'})
    elif no_concept:
        out['caveats'].append({'code': 'concept_missing',
                              'text': f'榜上 {len(rows)} 只有 {len(no_concept)} 只没有任何概念归属行'
                                      f'（`—`，不是 0 个题材）：{"、".join(no_concept[:8])}'
                                      + ('…' if len(no_concept) > 8 else '')})
    if lim and len(rows) >= lim:
        session_with_quadrant = sum((r.get('n') or 0) for r in counts)
        out['caveats'].append({'code': 'pool_truncated',
                              'text': f'榜被 limit={lim} 截到 {len(rows)} 行，而这一场带象限的综合分共 '
                                      f'{session_with_quadrant} 行 —— 所以概念反向榜的分母就是这 {len(rows)} 只，'
                                      '不是这一场全部的票。界面上那两页是在这前 200 名里翻页，'
                                      '不是往后翻了就还有数（要更深得调大 limit 重新取数，上限 1000）。'})
    return out


#: 「近 N 场名次之和」可选的窗口（用户 2026-10-15 点名这三档，界面上就是三个按钮）。
#: 不开放任意 N：能传出一个界面上没有的窗口，就等于让同一屏在不同人手里回答不同的问题
#: （与 `score_trend` 那条"不静默换窗口"是同一纪律）。
RANK_WINDOWS = (3, 7, 11)


def rank_stability(days: int = 7, limit: int = 50, date: str = '', refresh: bool = False) -> dict:
    """近 N 场**全市场名次之和**从低到高（用户 2026-10-15：「显示近3天、7天、11天的名次之和，
    按照和的总和从低到高显示前50位。总和越小，证明这几天他的名次一直保持越高」）。

    名次**不在这里算，也不是新记录**：`analytics.score.rank_in_market`
    （`capability_key='oper_score'` 那一行）每天每票一行早就落库，写入方是
    `app/compute/scoring.py:217`。10-15 实测：164 场（2026-01-27 起）、每场 5,55x 只、
    名次 1…N 连续、`rank_in_market` 无 NULL。所以这一屏只是把已入库的名次按窗口相加，
    不再另存一份"和" —— 存了就多一份会和源表各说各话的副本（要"当天屏幕快照"是另一件事，
    得先定判据版本号与回算范围，别顺手建表）。

    三条会被读错的口径，随数据一起交出：
     · **凑不满 N 场就不进榜**：只求"N 场每场都有名次"的票。缺场次还去求和，等于让
       少加一项的票去和加满的票比大小 —— 缺席反倒占了便宜（与「缺项不按 0 参与排名」同一条纪律）。
       被排除多少只当场交出（`universe.excluded_partial`）。
     · **窗口是交易日、不是日历日**：取的是 `oper_score` 有名次的最近 N 场，具体日期在
       `window.sessions` 里逐场列出（含每场母体只数）。「近 7 天」在长假里可能是 12 个日历日。
     · **名次是"当日全场第几名"**，各场母体只数略有出入（`universe.per_session_min/max`），
       所以这个和只回答"这几天谁一直靠前"，不是可以跨票换算的分数。
    """
    table = 'analytics.score'
    if refresh:      # 刚回填过名次的人要能立刻看到新数，不必等那一小时缓存
        clear_agg_cache('rank_stability_')
    d = _dash(date) or _latest(table) or ''
    if not d or not pg.session_has(table, d):
        return _fail('名次稳定榜', table, d or None)
    want = _int(days) or 7
    if want not in RANK_WINDOWS:
        return _reject('名次稳定榜', d,
                       f'窗口只收 {"、".join(f"{x} 场" for x in RANK_WINDOWS)}（界面上就是这三档），收到「{days}」')
    lim = max(1, min(_int(limit) or 50, 200))

    # 窗口与母体：两发全库聚合，按 (看哪一天, 窗口) 缓存一小时，年龄由 cached_aggregates 交代。
    # 分开两发是因为它们回答的是两个问题：这一窗共哪几天、每天多少只有名次；以及谁凑不满。
    stats = _agg(f'rank_stability_stats:{d}:{want}', """
        with w as (select trade_date from (
                     select distinct trade_date from analytics.score
                      where capability_key = 'oper_score' and rank_in_market is not null
                        and trade_date <= %s order by 1 desc limit %s) x),
        per as (select s.trade_date, count(*)::int universe,
                       count(*) filter (where s.is_stale)::int stale
                  from analytics.score s join w using (trade_date)
                 -- ⚠️ 母体只数**有名次的行**，不是这一天的所有 `oper_score` 行：
                 -- 2026-10-19 回算历史时，2026-07 那几场会同时留着迁入的旧行（指数码等，
                 -- `is_stale=true`、名次为空）。把它们算进母体，屏幕上那句
                 -- 「名次是当日全场第几名」就成了假话（名次实际排到 ~5,5xx，母体却报 8,9xx）。
                 where s.capability_key = 'oper_score' and s.rank_in_market is not null
                 group by 1),
        br as (select w.trade_date,
                      exists (select 1 from analytics.score_detail d
                               where d.trade_date = w.trade_date
                                 and d.component_key = 'sector_guangdu') has_breadth
                 from w)
        select count(*)::int sessions, min(trade_date)::text first_day, max(trade_date)::text last_day,
               min(universe)::int universe_min, max(universe)::int universe_max,
               sum(stale)::int stale_rows,
               (select count(*) filter (where not has_breadth)::int from br) breadth_missing,
               (select count(*) filter (where has_breadth)::int from br) breadth_present
          from per""", (d, want))
    counts = _agg(f'rank_stability_counts:{d}:{want}', """
        with w as (select trade_date from (
                     select distinct trade_date from analytics.score
                      where capability_key = 'oper_score' and rank_in_market is not null
                        and trade_date <= %s order by 1 desc limit %s) x),
        g as (select s.security_code, count(*) n from analytics.score s join w using (trade_date)
               where s.capability_key = 'oper_score' and s.rank_in_market is not null group by 1)
        select count(*)::int codes,
               count(*) filter (where n = (select count(*) from w))::int full_cov,
               count(*) filter (where n < (select count(*) from w))::int partial
          from g""", (d, want))
    if stats is None or counts is None:
        # 查询失败 ≠ "这些场次没有名次"：不探针就会把库不通念成数据缺失（本项目犯过两次）
        return _fail('名次稳定榜', table, d, '窗口聚合这一发没读回来（库不通或查询失败），不是这些场次没有名次')
    if not _int(stats.get('sessions')):
        return _fail('名次稳定榜', table, d, f'{d} 及之前库里没有带名次的场次')

    rows = _rows("""with w as (select trade_date from (
                     select distinct trade_date from analytics.score
                      where capability_key = 'oper_score' and rank_in_market is not null
                        and trade_date <= %s order by 1 desc limit %s) x),
        j as (select s.security_code, s.trade_date, s.rank_in_market, s.quadrant, s.value
                from analytics.score s join w using (trade_date)
               where s.capability_key = 'oper_score' and s.rank_in_market is not null),
        g as (select security_code,
                     count(*)::int sessions_used,
                     sum(rank_in_market)::bigint rank_sum,
                     min(rank_in_market)::int best_rank,
                     max(rank_in_market)::int worst_rank,
                     (array_agg(rank_in_market order by trade_date desc))[1] latest_rank,
                     (array_agg(quadrant order by trade_date desc))[1] quadrant,
                     (array_agg(value order by trade_date desc))[1] oper_value,
                     -- 逐日名次串在一次聚合里顺手拼出来。⚠️ 别改成"相关子查询再扫一次 j"：
                     -- 实测那样 3 场就要 21 秒、11 场直接撞 statement timeout（O(只数 × 窗口) 自连接）。
                     string_agg(rank_in_market::text, '→' order by trade_date) rank_path
                from j group by 1)
        select g.security_code, sec.name, sec.board, g.sessions_used, g.rank_sum, g.best_rank,
               g.worst_rank, g.latest_rank, g.quadrant, g.oper_value, g.rank_path
          from g left join reference_data.security sec using (security_code)
         where g.sessions_used = (select count(*) from w)
         order by g.rank_sum asc, g.best_rank asc, g.security_code asc
         limit %s""", (d, want, lim))
    if rows is None:
        return _fail('名次稳定榜', table, d, '名次求和这一发查询没读回来（库不通或查询失败）')

    labels, labels_source = _labels()
    for i, r in enumerate(rows, start=1):
        r['board_rank'] = i                      # 榜内第几行（和相同时按最好名次、再按代码定序）
        r['avg_rank'] = _num((_int(r.get('rank_sum')) or 0) / (_int(r.get('sessions_used')) or 1), 1)
        r['rank_spread'] = (_int(r.get('worst_rank')) or 0) - (_int(r.get('best_rank')) or 0)
        r['quadrant_label'] = labels.get(r.get('quadrant'))
        r['rank_sum'] = _int(r.get('rank_sum'))
        r['oper_value'] = _num(r.get('oper_value'))

    out = {'available': True, 'date': d, 'capability_key': 'oper_score',
           'window': {'days': want, 'sessions': _int(stats.get('sessions')),
                      'first_day': stats.get('first_day'), 'last_day': stats.get('last_day'),
                      'is_trading_days': True},
           'universe': {'scored_codes': _int(counts.get('codes')),
                        'fully_covered': _int(counts.get('full_cov')),
                        'excluded_partial': _int(counts.get('partial')),
                        'per_session_min': _int(stats.get('universe_min')),
                        'per_session_max': _int(stats.get('universe_max')),
                        'stale_rows': _int(stats.get('stale_rows')),
                        'breadth_missing': _int(stats.get('breadth_missing')),
                        'breadth_present': _int(stats.get('breadth_present')),
                        'note': '名次是"当日全场第几名"，各场母体只数略有出入；'
                                '只有 N 场每场都有名次的票进榜（缺场次会让"和"少加一项而假优）'},
           'limit': lim, 'rows': rows, 'total_ranked': _int(counts.get('full_cov')),
           'quadrant_labels': labels, 'labels_source': labels_source,
           'rule': ('名次之和 = 最近 N 个有综合分名次的交易日里，每天的全市场名次相加；'
                    '按和升序取前 ' + str(lim) + '。和越小=这几天一直靠前。'
                    '并列时先比这段时间的**最好名次**（更小者在前），再比代码；'
                    '平均名次、最好/最差名次、极差与逐日名次路径一起交出，'
                    '用来判断是"一直稳"还是"某天冲进来看坏了均值"。'),
           'definitions': {'rank_sum': '近 N 场名次相加（名次 1 = 当日全场第一），越小越好',
                           'avg_rank': 'rank_sum ÷ 场次（就是平均名次，跨窗口长度可比；场次不同别直接比和）',
                           'best_rank / worst_rank / rank_spread': '这段时间里最好 / 最差的名次与两者之差',
                           'latest_rank': '最近一场（' + str(stats.get('last_day')) + '）的当日名次',
                           'rank_path': '逐日名次（早→晚，用 → 连），一眼看出是稳还是大起大落',
                           'quadrant': '最近一场的当日象限（与「四维象限」那一屏同一列，不是新判据）'},
           'coverage': _coverage(('score',)),
           'caveats': _caveats('quadrant_design', 'coverage') + _cache_caveat(
               ('coverage:score', f'rank_stability_stats:{d}:{want}', f'rank_stability_counts:{d}:{want}')),
           'source': 'pg', 'requests': 0}
    if _int(stats.get('stale_rows')):
        out['caveats'].append({'code': 'stale_ranks',
                              'text': f'这一窗里有 {_int(stats.get("stale_rows"))} 行的综合分带 is_stale'
                                      '（用的不是该场最新输入），名次照加，但要留意那几天的数不是当场算出来的'})
    if labels_source.startswith('兜底'):
        out['caveats'].append({'code': 'labels_fallback', 'text': labels_source})
    # 与「名次稳步前进榜」同一句判据输入不齐（同一次现数，不背日期）：
    # 回算把名次历史拉长到 2025-08-28，而行业广度分项只存在于最近那几十场。
    bm, bp = _int(stats.get('breadth_missing')), _int(stats.get('breadth_present'))
    if bm:
        out['caveats'].append({
            'code': 'caliber_break',
            'text': f'这一窗 {want} 场里，有 {bm} 场在 `score_detail` 里没有行业广度分项'
                    f'（`sector_guangdu`）—— 那几场的 Y 轴（env_resonance）是少一个分项算出来的'
                    '（权重被摊到其余分项上）。'
                    + (f'同窗另有 {bp} 场带这个分项 ⇒ 名次之和把两类场次加在一起，'
                       '这个"和"跨它们不可比，只能各自看。'
                       if bp else
                       '这一窗全都没有该分项 ⇒ 这一屏内部是同一把缺分项的尺，'
                       '但与最近那批有广度的场次不可直接相比。')})
    if not rows:
        out['caveats'].append({'code': 'empty_board',
                              'text': f'凑满 {want} 场的票是 0 只 —— 这一窗的场次没读全（多半是回填中途），'
                                      '不是市场没有票。换一档窗口或带 refresh=1 重算。'})
    return out


# ================================================================ 名次稳步前进榜

#: 可切换的两档：`xy` 看 X+Y 两轴合名次，`oper` 看综合分名次。界面就是这两个按钮。
STEADY_METRICS = ('xy', 'oper')
#: 窗口固定 6 场（用户 2026-10-18 点名「近 6 个交易日」）。步数门槛是按这一档算的，
#: 改这里必须同时改 `STEADY_MIN_BETTER`/`STEADY_MAX_WORSE` 与界面那句「共 N 步」。
STEADY_SESSIONS = 6
#: 5 步里至少几步在前进。实测（2026-10-19，最近 6 场、5,55x 只母体）：
#: 两轴合名次「5 步全前进」只有 41 只、总分名次「全前进」53 只 —— 太稀，一屏读不出层次；
#: 「≥3 步前进且≤1 步后退」给到 395 只（合名次）/ 625 只（总分），加上幅度门槛后 369 / 489。
STEADY_MIN_BETTER = 3
#: 允许几步后退。0 步＝上面那个"全前进"档（41 只）；2 步及以上就不再是"稳步"。
STEADY_MAX_WORSE = 1
#: 净前进幅度下限，按「÷ 首场那一次的名次序列值」算，所以两档可比：
#: 合名次从 9,517 走到 2,069（前进 78%）与总分从 3,037 走到 435（86%）是同一把尺。
#: 不设这道门时，榜顶会被"从 5,200 一步跳到 1,000"那种单日跨档刷上来。
STEADY_MIN_GAIN_FRAC = 0.10

_XY_NOTE_KEYS = ('rank_direction', 'env_rank_levels', 'relative_not_absolute')


def rank_momentum(metric: str = 'xy', limit: int = 80, date: str = '', refresh: bool = False) -> dict:
    """近 6 场**名次**稳步前进的票（用户 2026-10-18 点名：「展示这个 xy 名次近 6 个交易日稳步增长的个股，
    是名次！可以手动切换成总分稳步增长的」）。

    与「名次稳定榜」（`rank_stability`）的分工要先说清，否则这两屏会被读成同一张榜的两种排法：
     · 稳定榜答的是「这几天谁**一直靠前**」——名次之和越小越好，一场冲进前 10 就把均值拉下来；
     · 这一屏答的是「这几天谁**一直在往前走**」——看的是名次的**变化方向**，
       一只现在排在 3,000 名的票可以在这 6 场里场场前进，而它离"靠前"还远。
    两张榜读的是同一列 `analytics.score.rank_in_market`，都不另存副本、都不建新表。

    两档 `metric`（界面两个按钮，别的值 `_reject`）：
     · `xy`：序列 = 当日 X 名次 + Y 名次（`stock_quality` + `env_resonance` 的 `rank_in_market` 相加）。
       **两个轴各自都要净前进**才算数 —— 只加不合的话，一轴大涨会掩盖另一轴恶化。
     · `oper`：序列 = 综合分名次（`oper_score`，就是稳定榜那一列），单一序列、无掩盖问题。

    「稳步」的判据（三条都随数据交出，界面把它们原句上屏）：6 场每场都有名次（共 5 步）⇒
    前进 ≥ `STEADY_MIN_BETTER` 步、后退 ≤ `STEADY_MAX_WORSE` 步、净前进幅度 ≥ 首场序列值的
    `STEADY_MIN_GAIN_FRAC`。缺场次不进榜的理由与稳定榜同一条纪律：凑不满还比方向，
    等于让少走两步的票去和走满五步的票比"谁更稳"。

    三条只有实测才知道的口径，跟着数据一起交：
     · **`env_resonance` 全场只有 32 个不同名次取值**（2026-10-19 实测：5,563 只票挤在 32 档里，
       最大并列 755 只）。所以 Y 轴的"持平"很常见、"前进"常常是一次跨档跳变，
       合名次里 Y 那一半分辨率远低于 X —— 这一条不是推测，是每次现从库里数出来的（`universe`）。
     · **名次是相对量**：前进意味着"这 6 场里它相对全场的位置变好了"，不等于这只票本身变好
       （分数原值随 `oper_value` 一起交出，读者自己对照）。
     · **榜是按前进幅度排的，不是按现在的位置排的**，所以榜顶完全可能是从 5,000 名走过来的票。
    """
    table = 'analytics.score'
    if refresh:
        clear_agg_cache('rank_momentum_')
    d = _dash(date) or _latest(table) or ''
    if not d or not pg.session_has(table, d):
        return _fail('名次稳步前进榜', table, d or None)
    m = (metric or 'xy').strip().lower()
    if m not in STEADY_METRICS:
        return _reject('名次稳步前进榜', d,
                       f'只收 {"、".join(STEADY_METRICS)}（界面上就是这两个按钮：xy=两轴合名次、'
                       f'oper=综合分名次），收到「{metric}」')
    lim = max(1, min(_int(limit) or 80, 200))
    want = STEADY_SESSIONS

    # 窗口与母体：一次全库聚合，按 (看哪一天, 档位) 缓存一小时。除了场次数与母体只数，
    # 还顺手数 Y 轴的名次档位数与并列规模 —— 那句「32 档」不能写在注释里当事实卖，要现数。
    stats = _agg(f'rank_momentum_stats:{d}:{m}', f"""
        with w as (select trade_date from (
                     select distinct trade_date from analytics.score
                      where capability_key = 'oper_score' and rank_in_market is not null
                        and trade_date <= %s order by 1 desc limit {want}) x),
        per as (select s.trade_date,
                       count(*) filter (where s.capability_key = 'oper_score')::int universe,
                       count(*) filter (where s.capability_key = 'oper_score'
                                          and s.is_stale)::int stale,
                       count(distinct s.rank_in_market)
                          filter (where s.capability_key = 'env_resonance')::int y_levels,
                       count(distinct s.rank_in_market)
                          filter (where s.capability_key = 'stock_quality')::int x_levels
                  from analytics.score s join w using (trade_date)
                 where s.capability_key in ('oper_score','stock_quality','env_resonance')
                   and s.rank_in_market is not null
                 group by 1),
        ties as (select max(cnt)::int biggest_y_tie from (
                   select s.trade_date, s.rank_in_market, count(*) cnt
                     from analytics.score s join w using (trade_date)
                    where s.capability_key = 'env_resonance' and s.rank_in_market is not null
                    group by 1,2) t),
        -- 这一窗每场有没有「行业广度」分项。2026-10-19 实测：`score_detail` 里
        -- `sector_guangdu` 只存在于 2026-07-29 起的 48 场，而名次历史已回算到 2025-08-28
        -- ⇒ 173 场的 `sector_heat`（进而 Y 轴 `env_resonance`）是**少一个分项**算出来的
        -- （加工那侧把它的权重静默摊到其余分项上，日志里是 `[EMPTY] sector_guangdu_v1`）。
        -- 只数这一窗（每场一次 index 探针），不去扫全表求"全局从哪天起才有"——
        -- 那句实测要 3.4 秒、扫 1,958 万行，比整张榜还贵，而且写死在屏幕上就会过期。
        br as (select w.trade_date,
                      exists (select 1 from analytics.score_detail d
                               where d.trade_date = w.trade_date
                                 and d.component_key = 'sector_guangdu') has_breadth
                 from w)
        select count(*)::int sessions, min(trade_date)::text first_day, max(trade_date)::text last_day,
               min(universe)::int universe_min, max(universe)::int universe_max,
               sum(stale)::int stale_rows,
               min(y_levels)::int y_levels_min, max(y_levels)::int y_levels_max,
               min(x_levels)::int x_levels_min, max(x_levels)::int x_levels_max,
               (select biggest_y_tie from ties),
               (select count(*) filter (where not has_breadth)::int from br) breadth_missing,
               (select count(*) filter (where has_breadth)::int from br) breadth_present
          from per""", (d,))
    if stats is None:
        return _fail('名次稳步前进榜', table, d,
                     '窗口与母体这一发没读回来（库不通或查询失败），不是这些场次没有名次')
    if not _int(stats.get('sessions')):
        return _fail('名次稳步前进榜', table, d, f'{d} 及之前库里没有带名次的场次')

    # 榜单本体：一次 GROUP BY 里把首末值、步数、最大单步与三条逐日路径全数出来。
    # ⚠️ 别把路径改成相关子查询再扫一遍 j —— 稳定榜那边实测 3 场 21 秒、11 场撞 statement timeout。
    rows = _rows(f"""
        with w as (select trade_date from (
                     select distinct trade_date from analytics.score
                      where capability_key = 'oper_score' and rank_in_market is not null
                        and trade_date <= %s order by 1 desc limit {want}) x),
        j as (select s.security_code, s.trade_date, s.capability_key, s.rank_in_market,
                     s.quadrant, s.value
                from analytics.score s join w using (trade_date)
               where s.capability_key in ('stock_quality','env_resonance','oper_score')
                 and s.rank_in_market is not null),
        c as (select security_code, trade_date,
                     max(rank_in_market) filter (where capability_key = 'stock_quality') rx,
                     max(rank_in_market) filter (where capability_key = 'env_resonance') ry,
                     max(rank_in_market) filter (where capability_key = 'oper_score') ro,
                     max(quadrant) filter (where capability_key = 'oper_score') quadrant,
                     max(value) filter (where capability_key = 'oper_score') oper_value
                from j group by 1,2),
        f as (select *, case when %s = 'xy' then rx + ry else ro end as ser
                from c
               where ro is not null
                 and (%s = 'oper' or (rx is not null and ry is not null))),
        st as (select *, lag(ser) over (partition by security_code order by trade_date) prev_ser
                 from f),
        g as (select security_code,
                     count(*)::int sessions_used,
                     (array_agg(ser order by trade_date))[1] first_series,
                     (array_agg(ser order by trade_date desc))[1] latest_series,
                     count(*) filter (where ser < prev_ser)::int better_steps,
                     count(*) filter (where ser > prev_ser)::int worse_steps,
                     count(*) filter (where ser = prev_ser)::int tied_steps,
                     max(prev_ser - ser)::int biggest_step_gain,
                     (array_agg(rx order by trade_date))[1] first_x,
                     (array_agg(rx order by trade_date desc))[1] latest_x,
                     (array_agg(ry order by trade_date))[1] first_y,
                     (array_agg(ry order by trade_date desc))[1] latest_y,
                     (array_agg(quadrant order by trade_date desc))[1] quadrant,
                     (array_agg(oper_value order by trade_date desc))[1] oper_value,
                     string_agg(ser::text, '→' order by trade_date) series_path,
                     string_agg(coalesce(rx::text, '·'), '→' order by trade_date) x_path,
                     string_agg(coalesce(ry::text, '·'), '→' order by trade_date) y_path
                from st group by 1),
        ok as (select *,
                      (first_series - latest_series)::numeric
                        / nullif(first_series, 0) as gain_frac,
                      count(*) over () as matched_total
                 from g
                where sessions_used = (select count(*) from w)
                  and latest_series < first_series
                  and better_steps >= {STEADY_MIN_BETTER}
                  and worse_steps <= {STEADY_MAX_WORSE}
                  and (first_series - latest_series)::numeric
                      >= {STEADY_MIN_GAIN_FRAC} * first_series
                  and (%s = 'oper' or (latest_x < first_x and latest_y < first_y)))
        select ok.security_code, sec.name, sec.board, ok.sessions_used,
               ok.first_series, ok.latest_series, ok.first_series - ok.latest_series as gain_abs,
               ok.gain_frac, ok.better_steps, ok.worse_steps, ok.tied_steps, ok.biggest_step_gain,
               ok.first_x, ok.latest_x, ok.first_y, ok.latest_y,
               ok.series_path, ok.x_path, ok.y_path,
               ok.quadrant, ok.oper_value, ok.matched_total
          from ok left join reference_data.security sec using (security_code)
         order by ok.gain_frac desc, ok.latest_series asc, ok.security_code asc
         limit %s""", (d, m, m, m, lim))
    if rows is None:
        return _fail('名次稳步前进榜', table, d, '名次方向这一发查询没读回来（库不通或查询失败）')

    labels, labels_source = _labels()
    series_label = ('X+Y 两轴合名次' if m == 'xy' else '总分名次')
    um_min = _int(stats.get('universe_min'))
    um_max = _int(stats.get('universe_max'))
    for i, r in enumerate(rows, start=1):
        r['board_rank'] = i
        r['gain_frac'] = _num((_num(r.get('gain_frac')) or 0) * 100, 1)
        r['oper_value'] = _num(r.get('oper_value'))
        r['quadrant_label'] = labels.get(r.get('quadrant'))
        # 步数合计应当等于「场次 − 1」；不相等就是这一行缺场，而缺场的行上面已经筛掉了。
        # 交出来是为了让界面能自证"5 步"这句不是页面自己写的。
        r['steps_total'] = (_int(r.get('better_steps')) or 0) + (_int(r.get('worse_steps')) or 0) \
            + (_int(r.get('tied_steps')) or 0)

    matched = _int(rows[0].get('matched_total')) if rows else None
    if matched is None:
        # 榜为空时 `count(*) over ()` 一行都没有，得单独数一次"符合判据的有几只"，
        # 否则界面只能说"0 只"，读者分不清"没有稳步前进的票"与"这一场没读全"。
        cnt = _one("""with w as (select trade_date from (
                     select distinct trade_date from analytics.score
                      where capability_key = 'oper_score' and rank_in_market is not null
                        and trade_date <= %s order by 1 desc limit """ + str(want) + """) x),
        j as (select s.security_code, s.trade_date, s.capability_key, s.rank_in_market
                from analytics.score s join w using (trade_date)
               where s.capability_key in ('stock_quality','env_resonance','oper_score')
                 and s.rank_in_market is not null),
        c as (select security_code, trade_date,
                     max(rank_in_market) filter (where capability_key = 'stock_quality') rx,
                     max(rank_in_market) filter (where capability_key = 'env_resonance') ry,
                     max(rank_in_market) filter (where capability_key = 'oper_score') ro
                from j group by 1,2),
        f as (select *, case when %s = 'xy' then rx + ry else ro end as ser
                from c where ro is not null
                  and (%s = 'oper' or (rx is not null and ry is not null))),
        st as (select *, lag(ser) over (partition by security_code order by trade_date) prev_ser
                 from f),
        g as (select security_code, count(*)::int n,
                     (array_agg(ser order by trade_date))[1] fs,
                     (array_agg(ser order by trade_date desc))[1] ls,
                     count(*) filter (where ser < prev_ser)::int b,
                     count(*) filter (where ser > prev_ser)::int wr,
                     (array_agg(rx order by trade_date desc))[1] lx,
                     (array_agg(rx order by trade_date))[1] fx,
                     (array_agg(ry order by trade_date desc))[1] ly,
                     (array_agg(ry order by trade_date))[1] fy
                from st group by 1)
        select count(*)::int matched, count(*) filter (where n = (select count(*) from w))::int full_cov
          from g where n = (select count(*) from w)
            and ls < fs and b >= """ + str(STEADY_MIN_BETTER) + " and wr <= " + str(STEADY_MAX_WORSE) + f"""
            and (fs - ls)::numeric >= {STEADY_MIN_GAIN_FRAC} * fs
            and (%s = 'oper' or (lx < fx and ly < fy))""", (d, m, m, m))
        matched = _int(cnt and cnt.get('matched'))
        covered = _int(cnt and cnt.get('full_cov'))
    else:
        covered = None

    out = {'available': True, 'date': d, 'metric': m,
           'metric_label': ('X/Y 两轴合名次' if m == 'xy' else '综合分（总分）名次'),
           'series_key': 'stock_quality+env_resonance' if m == 'xy' else 'oper_score',
           'series_label': series_label,
           'window': {'sessions': _int(stats.get('sessions')), 'fixed': want,
                      'steps': max(0, (_int(stats.get('sessions')) or want) - 1),
                      'first_day': stats.get('first_day'), 'last_day': stats.get('last_day'),
                      'is_trading_days': True},
           'thresholds': {'min_better_steps': STEADY_MIN_BETTER,
                          'max_worse_steps': STEADY_MAX_WORSE,
                          'min_gain_frac_pct': _num(STEADY_MIN_GAIN_FRAC * 100, 1),
                          'require_both_axes': m == 'xy'},
           'universe': {'per_session_min': _int(stats.get('universe_min')),
                        'per_session_max': _int(stats.get('universe_max')),
                        'y_levels_min': _int(stats.get('y_levels_min')),
                        'y_levels_max': _int(stats.get('y_levels_max')),
                        'x_levels_min': _int(stats.get('x_levels_min')),
                        'x_levels_max': _int(stats.get('x_levels_max')),
                        'biggest_y_tie': _int(stats.get('biggest_y_tie')),
                        'stale_rows': _int(stats.get('stale_rows')),
                        'breadth_missing': _int(stats.get('breadth_missing')),
                        'breadth_present': _int(stats.get('breadth_present')),
                        'covered_codes': covered,
                        'note': f'名次是「当日全场第几名」（1 = 第一），越小越靠前；'
                                f'各场母体只数略有出入（{um_min}~{um_max} 只）。'
                                f'只有 {want} 场每场都有名次的票进榜 —— 缺场次还比方向，'
                                '等于让少走几步的票去和走满的票比谁更稳'},
           'limit': lim, 'rows': rows, 'total_matched': matched,
           'quadrant_labels': labels, 'labels_source': labels_source,
           'rule': (f'名次稳步前进榜（{series_label}）：取最近 {want} 个有名次的交易日（共 '
                    f'{want - 1} 步），进榜要同时满足 —— ① 每场都有这个名次；'
                    f'② 前进的步数 ≥ {STEADY_MIN_BETTER}；③ 后退的步数 ≤ {STEADY_MAX_WORSE}；'
                    f'④ 净前进幅度 ≥ 首场名次的 {int(STEADY_MIN_GAIN_FRAC * 100)}%'
                    + ('；⑤ X 与 Y 两个轴「各自」都要净前进（防止一轴大涨掩盖另一轴恶化）'
                       if m == 'xy' else '')
                    + f'。按「净前进幅度 ÷ 首场名次」从高到低取前 {lim}；'
                      '并列时先比最近一场的名次（更小者在前），再比代码。'
                      '这一屏看的是「方向」，不是位置：榜顶可能是从 5,000 名走过来的票，'
                      '要「现在谁最强」看上一张名次稳定榜。'),
           # ⚠️ 一个键一列，键名就是行上的字段名 —— 列头 tooltip 直接取这一份，
           #    页面不再自己写第二套字段含义（两处各写一套早晚走偏）。
           'definitions': {
               'board_rank': f'榜内第几行（按前进幅度降序；本次符合判据的共 {matched} 只，'
                             f'接口交出前 {lim} 只）',
               'security_code': '代码；名次读的是这只票在打分表里那几行的 rank_in_market',
               'name': '名称（读 reference_data.security；库里没登记中文名时这一格就是代码本身）',
               'board': '板块归属（主板 / 创业板 / 科创板 / 北交所，同一个字典）',
               'gain_frac': 'gain_abs ÷ first_series（百分比）—— 排序用的就是这一列，'
                            '因为它在两档之间尺度可比',
               'first_series': f'窗口第一场（{stats.get("first_day")}）的{series_label}'
                               + ('（= X 名次 + Y 名次，最小 2、最大约两倍全场只数；它不是「全场第几名」，'
                                  '只用来把两轴的位置合成一个可比的数）' if m == 'xy' else '（oper_score 的当日名次）'),
               'latest_series': f'最近一场（{stats.get("last_day")}）的{series_label}；'
                                '名次数字变小＝往前走',
               'gain_abs': 'first_series − latest_series（名次前进的绝对步数；两档尺度不同，别横着比）',
               'better_steps': f'相邻两场之间名次前进的步数（上限 {want - 1} 步，进榜要求 ≥ '
                               f'{STEADY_MIN_BETTER}）',
               'worse_steps': f'相邻两场之间名次后退的步数（进榜要求 ≤ {STEADY_MAX_WORSE}；'
                              '这一列不为 0 也能进榜，所以「稳步」不等于「每天都前进」）',
               'tied_steps': '相邻两场名次完全相同的步数（多半是并列名次，Y 轴尤其常见）',
               'steps_total': f'better + worse + tied，应当等于 {want - 1} 步；'
                              '不相等就是这一行的步数与路径不是同一批行数出来的',
               'biggest_step_gain': '单步前进最大的那一步跨了多少名 —— 用来分辨「每场挪一点」与'
                                    '「某天一次跨档」：后者只有一步大、其余靠均值撑',
               'series_path': f'{series_label}的逐日路径（早→晚，用 → 连）；这一列是文本，'
                              '排序请点前面的数值列',
               'x_path': 'X 轴（stock_quality）逐日名次，缺场次的地方用 · 占位（进榜的票不该有 ·）',
               'y_path': 'Y 轴（env_resonance）逐日名次；这一轴全场只有几十个不同取值（见 y_axis_levels '
                         '那条声明），所以路径里连续几个数字相同是并列、不是没动',
               'first_x': f'X 轴名次在窗口第一场（{stats.get("first_day")}）的值',
               'latest_x': 'X 轴名次在最近一场的值；xy 档要求它小于 first_x',
               'first_y': f'Y 轴名次在窗口第一场的值（{stats.get("first_day")}）',
               'latest_y': 'Y 轴名次在最近一场的值；xy 档要求它小于 first_y',
               'quadrant': '最近一场的当日象限（与上面「选股象限」那一屏同一列，不是新判据）',
               'quadrant_label': '当日象限的中文名（读 config.setting 的 quadrant_labels，'
                                 '这一列只是给「档」那一格配个中文说明，不参与判据）',
               'oper_value': '最近一场的综合分原值 —— 名次是相对量，这一列才是绝对量',
               'sessions_used': f'这一行实际用了几天（进榜的都必须 = {want}，缺场次不进榜）',
               'matched_total': f'本次符合判据的总只数（榜被 limit 截断时它大于行数）'},
           'coverage': _coverage(('score',)),
           'caveats': _caveats(*_XY_NOTE_KEYS, 'coverage') + _cache_caveat(
               ('coverage:score', f'rank_momentum_stats:{d}:{m}')),
           'source': 'pg', 'requests': 0}
    if not rows:
        out['caveats'].append({'code': 'empty_board',
                              'text': f'按这套判据（前进 ≥ {STEADY_MIN_BETTER} 步、后退 ≤ '
                                      f'{STEADY_MAX_WORSE} 步、净前进 ≥ 首场 {int(STEADY_MIN_GAIN_FRAC * 100)}%'
                                      + ('、两轴都净前进' if m == 'xy' else '')
                                      + f'）凑满 {want} 场的票是 0 只。凑满 {want} 场的票本身有 '
                                        f'{covered} 只，所以这多半是这一窗的场次没读全或那天全场都在退，'
                                        '不是页面坏了 —— 带 refresh=1 重算或换一档 metric 再看。'})
    elif matched is not None and matched > len(rows):
        out['caveats'].append({'code': 'board_truncated',
                              'text': f'符合判据的共 {matched} 只，接口只交前 {lim} 只 —— '
                                      '榜被上限截断过，这一屏的行数不等于「全场稳步前进的票数」。'})
    if _int(stats.get('stale_rows')):
        out['caveats'].append({'code': 'stale_ranks',
                              'text': f'这一窗里有 {_int(stats.get("stale_rows"))} 行的综合分带 is_stale'
                                      '（用的不是该场最新输入），名次照用，但要留意那几天的数不是当场算出来的'})
    # Y 轴名次的分辨率是这一屏最容易读错的地方：它不是 5,5xx 个不同名次，而是几十个档位。
    # 这句必须是现数出来的读数（`stats`），不能只写在注释里当既定事实卖。
    yl_min, yl_max = _int(stats.get('y_levels_min')), _int(stats.get('y_levels_max'))
    if yl_min:
        xl_min = _int(stats.get('x_levels_min'))
        out['caveats'].append({
            'code': 'y_axis_levels',
            'text': f'Y 轴（env_resonance）名次在这 {want} 场里每场只有 '
                    f'{yl_min}{"" if yl_min == yl_max else f"~{yl_max}"} 个不同取值，'
                    f'而 X 轴有 {xl_min} 个 —— 也就是说 5,00x 只票在 Y 轴上是挤在几十个档位里的'
                    f'（最大一档并列 {_int(stats.get("biggest_y_tie"))} 只）。'
                    '所以 y_path 里连续几个数字相同是「并列」、不是「没动」，'
                    '而 Y 轴的「前进」往往是一次跨档跳变；两轴合名次的分辨率主要由 X 轴决定'})
    # 判据输入不齐：这一窗里有几场的 Y 轴是少一个分项算出来的（见 stats 那段的注释）。
    # 只在真缺时出声，且只报这一窗自己的读数 —— 不背「全局从哪天起才有」那种日期，
    # 背了就会过期，而那条边界要扫 1,958 万行、实测 3.4 秒，比整张榜还贵。
    bm, bp = _int(stats.get('breadth_missing')), _int(stats.get('breadth_present'))
    if bm:
        out['caveats'].append({
            'code': 'caliber_break',
            'text': f'这一窗 {want} 场里，有 {bm} 场在 `score_detail` 里没有行业广度分项'
                    f'（`sector_guangdu`）—— 那几场的 sector_heat、进而 Y 轴（env_resonance）'
                    '是**少一个分项**算出来的（加工侧把这一项的权重摊到其余分项上，'
                    '加工日志里就是那句 [EMPTY] sector_guangdu_v1）。'
                    + (f'同窗另有 {bp} 场带这个分项 ⇒ 两类场次的名次不是同一把尺，'
                       '跨它们比"谁一直在往前走"会读出假关系。'
                       if bp else
                       '这一窗全都没有该分项 ⇒ 这一整屏用的是同一把缺分项的尺，'
                       '与最近那批有广度的场次不可直接相比。')})
    if labels_source.startswith('兜底'):
        out['caveats'].append({'code': 'labels_fallback', 'text': labels_source})
    return out


# ================================================================ 2. 评分明细

#: 名次/分位的横截面闸。用户 2026-10-22 指着「market_mood -46.200 分 全市场第 1（前 50.0%）」
#: 问「怎么就排名第 1 了」—— 写侧 `app/compute/scoring.py` 的 `rank(method='min')` 与
#: `rank(pct=True)` 在**一项 capability 这一场全场同一个值**时分别恒给 1 与 50.009
#: （实测 2026-10-21：5,568 只全是 -46.2004）。两个数各自都没算错，摆在一起是一句假话：
#: 「第 1」说的是并列里的最小号，「前 50%」说的是中点。
#: 这里不改库、不重写历史批次，只把**这一场现数出来的分布**交出，并在那种档上把名次与分位折成 null。
_RANK_SPREAD_SQL = """
with tgt as (select capability_key, value from analytics.score
              where trade_date = %s and security_code = %s),
     g as (select capability_key, value, count(*) tie from analytics.score
            where trade_date = %s group by 1, 2)
select g.capability_key, sum(g.tie)::int as universe, count(*)::int as distinct_values,
       max(g.tie)::int as biggest_tie,
       (sum(g.tie) filter (where g.value = t.value))::int as tie_at_value
  from g join tgt t on t.capability_key = g.capability_key
 group by 1
"""

#: 不同取值数占不到母体的这个比例 ⇒ 名次是「档位里的名次」：照念，但必须带上并列只数
_RANK_COARSE_SHARE = 0.05


def _rank_view(cap: dict, sp: dict | None) -> dict:
    """把 `analytics.score` 那两列名次翻译成「这一场这一项到底能不能排名次」。纯函数，不碰库。

    四种档（`rank_kind`）：
      · `degenerate` 全场同一个值 ⇒ 名次与分位交回 None，界面念 `—`（不是「第 1」也不是「并列第一」）；
      · `coarse` 取值极少（分档位）⇒ 名次留着，但把并列只数与档位个数一起念；
      · `ok` 正常横截面 ⇒ 名次留着，并列只数照常念（多数是 1）；
      · `unknown` 分布没数出来（库读失败 / 这一项没有聚合行）⇒ 名次**照原样念但出声**，
        不静默、也不假装判过。
    另有一档 `no_rank`：库里这一行本来就没写名次（10-19 实测那 119 场旧批次是这种），
    它在屏幕上同样是 `—`，但说的是「没有名次」，不是「名次判不了」。
    """
    rank, pct, value = cap.get('rank_in_market'), cap.get('percent_rank'), cap.get('value')
    if _int(rank) is None:
        return {'rank_kind': 'no_rank', 'rank_in_market': None, 'percent_rank': _num(pct, 4),
                'rank_note': '这一行在库里没有名次（旧批次或这一场没算名次）—— '
                             '不是第 0 名，也不是最后一名'}
    if sp is None:
        return {'rank_kind': 'unknown', 'rank_in_market': _int(rank), 'percent_rank': _num(pct, 4),
                'rank_note': '这一场的分布没能数出来（聚合读不到），名次按库里的原值念，'
                             '但这一格没有对照过并列只数 —— 不等于验过没问题'}
    dv, uni, tie = _int(sp.get('distinct_values')), _int(sp.get('universe')), _int(sp.get('tie_at_value'))
    base = {'universe': uni, 'distinct_values': dv, 'biggest_tie': _int(sp.get('biggest_tie')),
            'tie_at_value': tie}
    if not dv or not uni:
        return {**base, 'rank_kind': 'unknown', 'rank_in_market': _int(rank),
                'percent_rank': _num(pct, 4),
                'rank_note': '这一场这一项数不出分布（没有有值的行），名次没有横截面依据'}
    if dv == 1 or (tie and tie >= uni):
        return {**base, 'rank_kind': 'degenerate', 'rank_in_market': None, 'percent_rank': None,
                'rank_note': f'这一场这一项全市场 {uni:,} 只都是同一个值（{_num(value, 4)}）⇒ '
                             f'「第几名」没有横截面意义：库里那两列在这种档上分别恒给出第 1 与 50.0，'
                             '两个数互相打脸。所以名次与分位都是 —，这一项只按分值本身读'}
    if dv * 1.0 < uni * _RANK_COARSE_SHARE:
        return {**base, 'rank_kind': 'coarse', 'rank_in_market': _int(rank), 'percent_rank': _num(pct, 4),
                'rank_note': f'这一场这一项只有 {dv:,} 个不同取值（最大一档并列 '
                             f'{_int(sp.get("biggest_tie")):,} 只）⇒ 名次是档位里的名次：'
                             f'相邻几名之间可能压根没有分差，比名次要连并列只数一起看'}
    return {**base, 'rank_kind': 'ok', 'rank_in_market': _int(rank), 'percent_rank': _num(pct, 4),
            'rank_note': ''}


#: 这一屏的字段是什么意思（键就是格子上那个中文列头，值是人话）。
#: 判据与阈值不在这里 —— 这里只解释「格子里那个数是怎么来的、能不能跨票比」。
SCORE_DEFINITIONS = {
    '这一项（capability）': '评分体系把一只票当天的表现拆成几项独立的读数，每项叫一个 capability。'
                            '中文名住在 `config.capability.name`，机器键（如 market_mood）跟着一起交，'
                            '对账与推送读机器键、屏幕读中文名',
    '分量': '一项能力由若干分量加权合成（例：个股质量 = 形态分 25% + 趋势分 25% + 位置分 20% + '
            '筹码分 20% + 量能分 10%）。中文名住在 `config.view_effective_binding.script_name`',
    '值': '这个分量自己的读数，已经归一成 -100~100 的相对量：说的是「这只票这一项在全场排到什么位置」，'
          '不是股价、不是涨跌幅、不是好坏',
    '权重': '这一场这一只票实际用的权重（写在 `analytics.score_detail`）。'
            '与注册表那份不一致时这一项会自己标出来 —— 那是「改了注册表但没重算」',
    '贡献': '值 × 权重：这个分量给这一项的分出了多少力。几项能力的分是这样加出来的，'
            '所以「凭什么是这个分」能逐格回答',
    '脚本（机器键）': '算这个分量的那段脚本的键与版本（`config.script`）',
    '全市场第几名': '同一场里这一项按分值从高到低的名次（1＝最高）。'
                    '只有当这一项各票取值不同时它才有意义：'
                    '全场同一个值的那一项（例：大盘情绪）名次与分位都是并列的产物，屏上念 —；'
                    '取值只有几个档位的（例：量能阶段、板块热度）名次挤在档位里，必须连并列只数一起看',
    '前百分之几': '分位换算过来的：分位越大越靠前，所以「前 X%」＝ 100 − 分位。'
                  '与名次同一个前提，判不了也是 —',
    '并列几只': '这一场这一项里，与这只票同一个取值的只数（公用名次口径：同分同名、下一名顺延）',
    '用的是上一场的值': '`is_stale=true`：这一行的输入已不是该场最新数据（当天没刷进来），'
                        '读数仍可用，但那一格是旧的',
    '权重和': '这一项各分量权重相加应等于 1。不等或有缺 ⇒ 这一项今天的分不可跨票比，屏上标红',
}


def _capability_names() -> tuple[dict, str]:
    """能力中文名与说明的唯一出处：`config.capability`（`name` / `description`）。

    读不到时交回**空表 + 一句出处说明**，让调用方出声，而不是在页面里退回一份手抄映射 ——
    「名称住两处」正是这一屏原先"看不懂"的来源之一。
    """
    rows = _rows("select capability_key, name, description from config.capability")
    if rows is None:
        return {}, 'config.capability 读不到（库不通或查询失败）'
    out: dict = {}
    for r in rows:
        out[str(r.get('capability_key'))] = {'name': r.get('name'), 'description': r.get('description')}
    return out, f'config.capability（{len(out)} 项）'


def scores(code: str, date: str = '') -> dict:
    """单票八个 capability 的加权明细：分量、权重、贡献逐项可见。

    分量权重取 `analytics.score_detail`（这一场这一只票**实际用**的权重），
    同时把 `config.view_effective_binding` 的注册表权重与 `config.capability`
    的合成式一起交出。两处都有数而不等，就是"改了注册表但没重算"的信号，
    必须报出来 —— 只取其一迟早会静默分叉。

    名字（能力名、分量名）与名次能不能排名次，都是**这一场现查/现数**的：
    前者来自注册表那两张配置表，后者来自 `_RANK_SPREAD_SQL`。屏上不写死一份中文映射，
    也不把并列产物当「全场第 1」念。
    """
    if not code:
        return {'available': False, 'reason': '缺少 code 参数'}
    table = 'analytics.score_detail'
    sec = _full_code(code)
    d = _dash(date) or _latest('analytics.score') or ''
    if not d:
        return _fail('趋势评分', table, None)

    caps = _rows("""select capability_key, value, rank_in_market, percent_rank, quadrant, is_stale
                      from analytics.score where trade_date = %s and security_code = %s
                     order by capability_key""", (d, sec))
    if not caps:
        return _fail('趋势评分', 'analytics.score', d,
                     f'{sec} 该场没有任何 capability 评分（代码写错、停牌或尚未计算）')
    detail = _rows("""select capability_key, component_key, component_label, component_value,
                             weight, contribution, script_key, script_version
                        from analytics.score_detail
                       where trade_date = %s and security_code = %s
                       order by capability_key, weight desc nulls last, component_key""", (d, sec))
    reg = _rows("""select capability_key, script_key, script_name, path, version, checksum,
                          raw_weight, eff_weight
                     from config.view_effective_binding order by capability_key, display_order""") or []
    # 分量名的兜底出处：注册表只登记"生效绑定"，历史场次里用过的脚本可能已经从绑定视图里退下，
    # 那时按 `config.script.name` 认（同一张注册表家族，不是第三份名单）。
    script_names = {str(r.get('script_key')): r.get('name') for r in
                    (_rows("select script_key, name from config.script") or [])}
    cap_names, names_source = _capability_names()
    spread_rows = _rows(_RANK_SPREAD_SQL, (d, sec, d))
    spread = {str(r.get('capability_key')): r for r in (spread_rows or [])} if spread_rows is not None else None
    # 注册表那一发也要带上中文名：`script_name` 就是分量在屏幕上的名字（用户 2026-10-22 点名要中文）
    name_by_script = {(str(r.get('capability_key')), str(r.get('script_key'))): r.get('script_name')
                      for r in reg}

    by_cap: dict = {}
    for r in (detail or []):
        by_cap.setdefault(r['capability_key'], []).append(r)
    registry: dict = {}
    for r in reg:
        registry.setdefault(r['capability_key'], []).append(r)

    out = []
    unnamed: list[str] = []
    for c in caps:
        k = c['capability_key']
        parts = by_cap.get(k, [])
        for p in parts:
            p['component_name'] = (name_by_script.get((k, str(p.get('script_key') or p.get('component_label'))))
                                   or script_names.get(str(p.get('script_key') or '')))
            if not p['component_name']:
                unnamed.append(str(p.get('script_key') or p.get('component_key')))
        # 权重缺就整项判不出，不能 `or 0` 把 None 当 0 加 —— 那会让"权重和=1.0 正常"
        # 在缺权重的行上假报通过。
        weights = [p.get('weight') for p in parts]
        unknown = [w for w in weights if w is None]
        wsum = None if (not parts or unknown) else round(sum(float(w) for w in weights), 6)
        reg_w = sorted(_num(b['raw_weight'], 4) for b in registry.get(k, [])
                       if b['raw_weight'] is not None)
        used = sorted(_num(w, 4) for w in weights if w is not None)
        mismatch = bool(used and reg_w and len(used) == len(reg_w) and used != reg_w)
        named = cap_names.get(str(k)) or {}
        rv = _rank_view(c, None if spread is None else spread.get(str(k)))
        out.append({**c, **rv, 'security_code': sec,
                    'capability_name': named.get('name'), 'capability_note': named.get('description'),
                    'components': parts, 'component_count': len(parts),
                    'weight_sum': wsum,
                    'weight_missing': len(unknown) if parts else None,
                    'weight_sum_ok': None if wsum is None else abs(wsum - 1.0) < 0.02,
                    'registry_weights': reg_w, 'registry_binding': registry.get(k, []),
                    'weight_mismatch': mismatch})

    caveats = _caveats('quadrant_design', 'stale', 'coverage') + _cache_caveat(
        ('coverage:score', 'coverage:score_detail'))
    if not detail:
        caveats.append(_cav('no_detail',
                            '`score_detail` 该场该票没有分量明细，只有综合分 —— '
                            '「凭什么是这个分」无法回答，界面要明说而不是只显示综合分'))
    bad = [o['capability_name'] or o['capability_key'] for o in out if o['weight_mismatch']]
    if bad:
        caveats.append(_cav('weight_mismatch',
                            f'这些能力的实际分量权重与注册表不一致（{"、".join(bad)}）：'
                            '改了 config.binding 但没重算，以 score_detail 为准'))
    # 名次这一族按档分类点名（一条一档，逐能力一行会盖过分量表；逐项的数在各自格子的悬停里）
    deg = [o for o in out if o['rank_kind'] == 'degenerate']
    if deg:
        caveats.append(_cav('rank_degenerate',
                            '这几项这一场全市场都是同一个值 ⇒ 名次与分位没有横截面意义，屏上念 —'
                            '（不是「第 1」，也不是「并列第一」）：'
                            + '、'.join(f'{o["capability_name"] or o["capability_key"]}'
                                        f'（{o["universe"]:,} 只都是 {_num(o["value"], 4)}）' for o in deg)
                            + '。这几项只按分值本身读'))
    coarse = [o for o in out if o['rank_kind'] == 'coarse']
    if coarse:
        caveats.append(_cav('rank_coarse',
                            f'这几项的取值是分档位的（不到母体的 {_RANK_COARSE_SHARE:.0%}）⇒ 名次挤在少数档里，'
                            '相邻几名之间可能压根没有分差，念名次要连并列只数一起看：'
                            + '、'.join(f'{o["capability_name"] or o["capability_key"]}'
                                        f'（{o["distinct_values"]:,} 个取值、最大一档并列 {o["biggest_tie"]:,} 只）'
                                        for o in coarse)))
    norank = [o for o in out if o['rank_kind'] == 'no_rank']
    if norank:
        caveats.append(_cav('no_rank',
                            '这几项在库里这一行本来就没有名次（旧批次或这一场没算名次），屏上念 —：'
                            + '、'.join(str(o['capability_name'] or o['capability_key']) for o in norank)))
    if spread is None:
        caveats.append(_cav('rank_degenerate',
                            '这一场各项取值的分布没数出来（聚合查询失败），'
                            '所以名次与分位是否可信没有判过 —— 屏上那几个名次别当已核对过的数'))
    if unnamed:
        caveats.append(_cav('component_name_unmatched',
                            f'这 {len(unnamed)} 个分量在注册表里没有对应中文名（{"、".join(sorted(set(unnamed)))}）'
                            '⇒ 屏幕上那一格退回机器键。名字只住 `config.view_effective_binding.script_name` '
                            '与 `config.script.name` 两处，页面不自造'))
    if names_source.startswith('config.capability 读不到') or not cap_names:
        caveats.append(_cav('note', f'能力中文名读不到（{names_source}）⇒ 这一屏退回机器键名。'
                                    '中文名的唯一出处是 `config.capability.name`'))
    return {'available': True, 'code': sec, 'date': d,
            'capabilities': out, 'capability_keys': list(CAPABILITIES),
            'n_components': sum(len(o['components']) for o in out),
            'weights_registry_table': 'config.view_effective_binding',
            'names_source': names_source,
            'component_names_source': 'config.view_effective_binding.script_name（兜底 config.script.name）',
            'rank_spread': {'date': d, 'sql_rows': None if spread is None else len(spread),
                            'coarse_share_threshold': _RANK_COARSE_SHARE},
            'definitions': SCORE_DEFINITIONS,
            'coverage': _coverage(('score', 'score_detail')),
            'caveats': caveats, 'source': 'pg', 'requests': 0}


# ================================================================ 3. 评分趋势

def score_trend(code: str, window: int = 10, sessions: int = 60, refresh: bool = False) -> dict:
    """窗口斜率 + 相关性 + 连续上涨天数 + sparkline。

    **窗口不凑数**：库里只有若干种 window_days，请求的窗口一种都没有就直接返回不可用
    并列出实有窗口。静默改用 10 天去回答 20 天的问题，是把不同长度的斜率混成同一个数。

    `refresh=True`：清掉本接口的全库聚合缓存（覆盖起点、入口象限为空的比例）重算。
    """
    if refresh:
        clear_agg_cache('entry_quadrant_null_share')
        clear_agg_cache('coverage:')
    if not code:
        return {'available': False, 'reason': '缺少 code 参数'}
    table = 'analytics.score_trend'
    sec = _full_code(code)
    want = max(2, int(window or 10))
    windows = [r['window_days'] for r in (_rows(
        "select distinct window_days from analytics.score_trend where security_code = %s "
        "order by 1", (sec,)) or [])]
    if not windows:
        return {'available': False, 'security_code': sec,
                'available_windows': [],
                'reason': f'{sec} 在 `{table}` 里没有任何场次（该票未被评分覆盖，或趋势未回填）。'
                          f'该表覆盖 {(_coverage(("score_trend",))["score_trend"] or {}).get("sessions")} 个交易日起步',
                'coverage': _coverage(('score_trend',))}
    if want not in windows:
        return {'available': False, 'security_code': sec, 'date': _latest(table),
                'available_windows': windows,
                'reason': f'库里该票没有 window_days={want} 的序列，实有 {windows}；'
                          '不替代成别的窗口返回 —— 那会把不同长度的斜率混成同一个数',
                'coverage': _coverage(('score_trend',))}

    rows = _rows("""select trade_date, point_count, slope, intercept, corr_coef,
                          x_plus_y_max, x_plus_y_max_date, up_streak_days,
                          first_quadrant, sparkline
                     from analytics.score_trend
                    where security_code = %s and window_days = %s
                    order by trade_date desc limit %s""",
                 (sec, want, max(1, min(int(sessions or 60), 500))))
    if not rows:
        return _fail('评分趋势', table, _latest(table), sec)
    for r in rows:
        r['window_entry_quadrant'] = r.pop('first_quadrant', None)
        r['slope'] = _num(r.get('slope'), 4)
        r['corr_coef'] = _num(r.get('corr_coef'), 4)
        r['x_plus_y_max'] = _num(r.get('x_plus_y_max'), 3)
        # 列里存的就是 ▁▂▃▄▅▆▇ 拼好的字符串（实测 text），不是逗号数组。
        # 早前按数字数组去 split 会把整串折成 [null] —— 图形本身就是要交给页面的东西。
        if isinstance(r.get('sparkline'), str):
            r['sparkline'] = r['sparkline'].strip() or None
    thin = [r['trade_date'] for r in rows if _int(r.get('point_count')) is not None
            and _int(r['point_count']) < 8]
    # 峰值在窗口第几天（用户 2026-10-15：「为什么会有哪天最高这个值？不应该都显示那一天吗？」
    # —— 那一列是给斜率佐证形状的，光有日期要人自己心算离今天几天，所以补上序号与词）
    peak_reason, rows = _peak_day_no(rows, want, sec)
    for r in rows:
        r['x_plus_y_max_position'] = _peak_position(r.get('x_plus_y_max_day_no'), want)
    caveats = _caveats('entry_quadrant', 'entry_quadrant_null', 'coverage')
    caveats += _cache_caveat(('coverage:score_trend', 'entry_quadrant_null_share'))
    if peak_reason:
        caveats.append({'code': 'peak_day_no_unlocated', 'text': peak_reason})
    if thin:
        caveats.append({'code': 'thin_window',
                        'text': f'这些场次窗口点数不足 8，斜率与相关性不具解释力：'
                                f'{"、".join(thin[:8])}' + ('…' if len(thin) > 8 else '')})
    nulls = sum(1 for r in rows if r.get('window_entry_quadrant') is None)
    if nulls:
        caveats.append({'code': 'window_entry_quadrant_null',
                        'text': f'本次交出 {len(rows)} 场，其中 {nulls} 场的入口象限为空（显示 `—`）；'
                                '为空只说明窗口首日那行没有象限，斜率本身照样算得出'})
    return {'available': True, 'security_code': sec, 'window_days': want,
            'min_point_count': 8, 'sessions_returned': len(rows),
            'first_session': rows[-1]['trade_date'], 'last_session': rows[0]['trade_date'],
            'definitions': {**(_comments('score_trend') or {}),
                            # 这两列不在表里（是本层现算的展示列），所以口径必须在这里交，
                            # 让页面逐字透出 —— 页面自己写一句解释就有两份口径了。
                            'x_plus_y_max_day_no': '峰值在窗口内**第几个有评分日**（1＝窗口第一天，'
                                                   f'{want}＝就是这一场的基准日）。数的是 `analytics.score` 的'
                                                   '有评分日、与斜率同一把尺子，**不数日历日**（长假里 10 个'
                                                   '日历日可能只有 5 个交易日）',
                            'x_plus_y_max_position': '把那个序号翻成一句话：就在这一场（刚创新高）／最近两天／'
                                                     '中途见顶／起点附近（高点早已过去）。同一个最高值，'
                                                     '出现在窗口开头与出现在今天，含义完全相反'},
            'peak_rule': f'窗口 {want} 个有评分日：序号＝{want} 记「就在这一场」、{want - 1} 记「最近两天」、'
                         f'≤{max(1, -(-want // 3))}（前三分之一）记「起点附近（高点早已过去）」、其余记「中途见顶」；'
                         '阈值只住 duanxian/quant.py 这一处',
            'rows': rows,
            'null_coverage': _null_coverage(rows[0]['trade_date'], None),
            'coverage': {**_coverage(('score_trend',)),
                         'available_windows_for_this_stock': windows},
            'caveats': caveats, 'source': 'pg', 'requests': 0}


def _peak_position(day_no: int | None, window_days: int) -> str | None:
    """把"窗口第几天"翻译成一个词。阈值只住这一处，页面不许自己再拼一句。

    为什么要有词：`x_plus_y_max_date` 只给日期，读的人得自己心算"离今天几天"。
    同一个最高值，出现在窗口开头（高点早过了、这些天在回落）和出现在今天
    （刚创新高、还在往上）含义相反 —— 库注释原话就是"出现在第 2 天还是今天，含义完全相反"。
    """
    if day_no is None or window_days < 2:
        return None
    if day_no >= window_days:
        return '就在这一场（刚创新高）'
    if day_no >= window_days - 1:
        return '最近两天'
    if day_no <= max(1, -(-window_days // 3)):      # 前 1/3（向上取整）
        return '起点附近（高点早已过去）'
    return '中途见顶'


def _peak_day_no(rows: list[dict], window_days: int, sec: str) -> tuple[str | None, list[str]]:
    """给每行补 `x_plus_y_max_day_no`＝峰值在**窗口内第几个有评分日**。

    ⚠️ 用 `analytics.score` 的**有评分日序列**数，不用日历日：producer 侧的 `rn`
    就是按这把尺子编的（`app/compute/score_trend.py` 的 `seq` CTE），长假一压，
    10 个日历日里可能只剩 5 个交易日，用日历日数出来的"第几天"与斜率的量纲就不是一个东西。
    返回（没能定位的原因, 已就地补好 day_no 的 rows）。
    """
    dated = [r for r in rows if r.get('trade_date') and r.get('x_plus_y_max_date')]
    if not dated:
        return None, rows
    hi = max(str(r['trade_date'])[:10] for r in dated)
    # 锚在**最晚**那个基准日往回取，多取 window_days + 5 场：最早那一行的窗口起点
    # 比它自己的基准日还要早 window_days-1 场。
    # ⚠️ 别改成"取最早基准日之后的日子"：那样比它晚的基准日与峰值日全都不在序列里，
    #    实测 12 行里有 11 行定位不了（整列 `—`，看起来像"这列没做"）。
    seq = _rows("""select distinct trade_date from analytics.score
                    where capability_key = 'oper_score' and trade_date <= %s
                    order by trade_date desc limit %s""",
                (hi, len(dated) + window_days + 5)) or []
    asc = [str(r['trade_date'])[:10] for r in reversed(seq)]
    idx = {d: i for i, d in enumerate(asc)}
    short = 0
    for r in rows:
        b, p = str(r.get('trade_date') or '')[:10], str(r.get('x_plus_y_max_date') or '')[:10]
        i, j = idx.get(b), idx.get(p)
        if i is None or j is None or i - (window_days - 1) < 0 or j < i - (window_days - 1) or j > i:
            r['x_plus_y_max_day_no'] = None      # 序列不够长/峰值不在窗口内：交空，不猜一个档
            short += 1 if (r.get('x_plus_y_max_date') and b) else 0
            continue
        r['x_plus_y_max_day_no'] = j - i + window_days
    reason = (f'有 {short} 场的峰值日没落在这条有评分日序列上（该票这一场窗口从库表边界开始，'
              f'或峰值那天没有 oper_score 行），那一格显示 `—`' if short else None)
    return reason, rows


# ================================================================ 4. 倍量形态

# ---- 2026-10-19：倍量次换成**加权分**（`analytics.volume_double.dbl_energy`）
#
# 旧口径只数「日量比 ≥ 2.0」的日子，1.8 倍与 1.0 倍同样算 0 次；实测日量比 ≥2 的日子
# 只占 3.5%（近 25 场全市场 149,938 个票·日），中间那批温和放量被整段抹平。
# 现在读的是加权分：量比每 0.1 切一档，得分 = Σ 每档（这一档在窗口里出现的次数 × 这一档的分值），
# 分值可在「系统配置」里改；没有系数、没有占比、没有归一化那一层（第一稿的 ÷Σ 当晚被否掉）。
# 档表、默认分值、指纹、重算**全部只住 `app/compute/volume_energy.py`**，
# 这一侧只读它落好的 `dbl_energy` —— 判据不许有第二份抄本。
from app.compute import volume_energy as venc   # noqa: E402

#: 「稳步提升」的阈值，单位＝**分**（一天顶档放量＝1 分，所以这一格读作"比前一窗多约 1.5 个
#: 顶档放量日"）。2026-10-19 第二稿换尺后按现算定档（旧值 0.2 是"占比"量纲下的数，随 ÷Σ 一起作废）。
#: 实测分布见本文件那条 step_gap_pool 与《10-每个数怎么算》§4.1 之二；阈值只住这一处，
#: 界面读 `thresholds.step_gap_min`。
STEP_GAP_MIN = 1.5

#: 新榜「加权分进榜后的逐日走势」母体的默认档：**当日得分前 N 名**（不是分数门槛）。
#: 真值住 `volume_energy.DEFAULT_TOP_N`（用户 2026-10-19 晚点名：取前 120，每页 60、共 2 页），
#: 这里只引用，不写第二遍。
TOP_N_DEFAULT = venc.DEFAULT_TOP_N
PAGE_SIZE_DEFAULT = venc.PAGE_SIZE

#: 名次这件事只住 `duanxian/ranking.py` 一处（公用组件）。这里只引文案与 SQL 片段。
RANKING = ranking


def _fmt(x) -> str:
    """把 0.2000 / 1.50 这类浮点念成 0.2 / 1.5 —— 措辞里的数字不该带尾随零。

    只给文案用；数值本身一律原样交回 JSON（界面自己格式化），免得这里 Round 一次
    那里 Round 一次，两边念出不同的数。
    """
    try:
        f = float(x)
    except (TypeError, ValueError):
        return str(x)
    return str(int(f)) if f == int(f) else ('%g' % f)


# 前一窗按**行偏移**取：同一只票按 trade_date 排序往前第 win_days（40）行那场。
# 为什么必须按行、不按日期：`dbl_energy` 累加的是"这只票自己的最近 40 根 K 线"（写侧
# `app/compute/volume_energy.py` 的窗口就是按它自己的行编的）。停牌票的 40 根 K 线
# 摊在超过 40 个市场日里 —— 拿日期（日历日或市场场数）往前推 40 天，两窗会重叠，
# 重叠部分被减两遍，差值就不是"两个不重叠窗的对比"了。行偏移 40 ⇒ 前一窗恰好是
# 本窗整体往前 40 行，两窗相接、零重叠。
#
# 回看场数取 90（两个满窗 80 场 + 10 场停牌余量）而不是全史：全表窗口扫描实测 5.5 秒
# （2026-10-16，1.15M 行），90 场限窗 2.5 秒，且当天取值分布与全史扫描逐只一致
# （旧口径下实测两边同为 1,695 只 ≥2、22 只空；换成加权分后这条一致性由
# `tests/test_volume_double_step_gap.py` 的真库那条重新钉）。停牌超过余量的票在回看
# 范围里数不到 40 行前，按"取不到前一窗"如实交 NULL —— 宁可少一个可比样本，
# 不拿截断范围外的旧行凑一个错差值。
STEP_GAP_LOOKBACK_SESSIONS = 90

# 分段差的 CASE 本体。**假数据测试直接对 (VALUES ...) 套这段片段跑**，
# 钉的就是生产 SQL 那一份，不是测试里的抄本。
# ⚠️ 单位现在是**加权分（浮点）**：展示与比较一律走 `_num(..., 4)`，不许再 `_int`
# （截成整数会把 0.2 的差变成 0，等于把"确实提升了"念成"没动"）。
_STEP_GAP_EXPR = """case when w.win_rn <= w.win_days then null
                       when w.bars < w.win_days or coalesce(w.prev_win_bars, -1) < w.win_days then null
                       when w.dbl_energy is null or w.prev_win_energy is null then null
                       else round(w.dbl_energy - w.prev_win_energy, 4) end"""
_STEP_GAP_REASON_EXPR = """case when w.win_rn <= w.win_days then 'no_prev_row'
                             when w.bars < w.win_days or coalesce(w.prev_win_bars, -1) < w.win_days then 'window_short'
                             when w.dbl_energy is null or w.prev_win_energy is null then 'count_missing'
                             end"""

# 每行"为什么是空"的一句话。格子的悬停读的就是这份（`dbl_step_gap_null_note`），
# 前端不另写一套原因句子。三种原因各对应上面 CASE 的一个分支，顺序不能换。
STEP_GAP_NULL_NOTES = {
    'no_prev_row': '往前数不到 40 行：这只票上市晚或长期停牌，没有可比的前一整窗 —— '
                   '这不是「加权分没涨」，是没有可比对象',
    'window_short': '本窗或前一窗不满 40 场（bars < win_days）：两把尺不等长，不做差',
    'count_missing': '本窗或前一窗的加权分在库里是空的，没有可减的数（前行为空）',
}
_STEP_GAP_SHORT_LABELS = {
    'no_prev_row': '取不到 40 行前（上市/停牌）',
    'window_short': '本窗或前窗不满 40 场',
    'count_missing': '加权分那一格本身为空',
}

# `definitions.dbl_step_gap` 的口径原文（界面悬停逐字读接口，不自己复述）。
# 这段只准出现在这一处；写这段时不带 markdown 星号（Definitions 是纯文本上屏）。
STEP_GAP_DEFINITION = (
    '加权分较前一整窗的增减 = 本窗 dbl_energy − 前一整窗的 dbl_energy。前一整窗＝同一只票'
    '按 trade_date 排序往前第 win_days（40）行那一场 —— 按行偏移不按日期偏移，'
    '因为停牌会把这只票自己的 40 根 K 线摊到超过 40 个市场日里，按日期推两窗会重叠。'
    '「稳步提升」＝这一格 ≥ 1.5 分（阈值只住 duanxian/quant.py 的 STEP_GAP_MIN 一处，'
    '接口交在 thresholds.step_gap_min）。单位是「分」：顶档放量一天记 1 分，所以这一格读作'
    '「比前一整窗多出约一个半顶档放量日」，它不是次数、也不是占比。'
    '1.5 是 2026-10-19 换尺时按现算定的：旧刻度那条 0.2 是「分值÷Σ分值」量纲下的数，'
    '那一层已被取消，所以门槛一起重取；重取的判据是新档要筛出与旧口径同一量级的母体，'
    '两档各有多少只由 step_gap_pool 现算随接口交出，本条不背数字。'
    '取 1.5 不取 0.5：0.5 分差不多只是多出了某一天 1.6 倍上下的放量（一档 0.4 分左右），'
    '一场超额就能翻牌，边际噪声全进榜。'
    '三种情况交空（—）而不是 0：① 往前数不到 40 行（上市晚或长期停牌，没有可比前窗）；'
    '② 本行或前行 bars < win_days（不满窗，两把尺不等长）；③ 本行或前行的加权分是空的'
    '（那几天它在日线里没有行，没有可减的数）。0 的意思是「本窗与前窗一样」，是读数；'
    '— 是「没有可比前窗」，两者必须长得不一样。被否掉的读法：按出现次数加权'
    '（出现次数≈上市时长，只会把老票顶上去）；对滚动的加权分序列算斜率'
    '（相邻两场重叠 39 场、高度自相关，信息量低，分段差更稳）。'
)


def _step_gap_source() -> str:
    """每票逐场的窗口化读数（win_rn / 前一窗加权分 / 前一窗 bars）。

    %s 出现**三次**：基准日两次（卡回看子查询上沿、卡明细行上沿）+ 生效配置指纹一次。
    指纹这一条不能省：改了档表但还没重算完时，库里混着两批数 —— 把它们排进同一个窗口
    算差值，等于拿旧尺的数减新尺的数。所以窗口只在**同一批**里滑动，
    对不上批的票如实交空（界面那格是 —，不是 0）。
    主查询与全场聚合共用这一份片段 —— 两边算的不是同一份 SQL 就会打架。
    """
    return f"""
                                select v0.*,
                                       row_number() over wsw as win_rn,
                                       lag(v0.dbl_energy, coalesce(v0.win_days, 40)) over wsw as prev_win_energy,
                                       lag(v0.bars,      coalesce(v0.win_days, 40)) over wsw as prev_win_bars
                                  from analytics.volume_double v0
                                 where v0.trade_date > (select min(trade_date) from (
                                          select distinct trade_date from analytics.volume_double
                                           where trade_date <= %s
                                           order by trade_date desc limit {STEP_GAP_LOOKBACK_SESSIONS}) z)
                                   and v0.trade_date <= %s
                                   and v0.energy_fp = %s
                                window wsw as (partition by v0.security_code order by v0.trade_date)
    """


def _energy_rank_expr(alias: str = 'v') -> str:
    """当日**全场**名次的 SQL：1 ＋ 同一场、同一批里"分更高"的只数。

    这就是 competition ranking（同分同名、下一名顺延），与 `duanxian/ranking.py` 的
    `rank_sql()` / `rank_in_memory()` 是同一条规则，`tests/test_ranking_shared.py` 逐位比过。
    故意不用窗口函数：`rank() over ()` 在 `where` 之后算，象限／分段差一筛，
    "第 1 名"就变成"这一小堆里的第 1"，而界面与悬停说的是**全场名次**。
    同一场同一批（`energy_fp`）是硬条件：混批比分数等于拿旧尺的数排新尺的队。
    """
    return (f"(select count(*)::int + 1 from analytics.volume_double x"
            f"  where x.trade_date = {alias}.trade_date"
            f"    and x.energy_fp  = {alias}.energy_fp"
            f"    and x.dbl_energy > {alias}.dbl_energy)")


def volume_double(date: str = '', limit: int = 150, min_energy: float | None = None,
                  quadrants: str = '', step_gap_min: float | None = None) -> dict:
    """40 日**量能加权分** × 贴 MA60 震荡。

    给的是**形态统计**不是买卖信号，所以分母与窗口一起交出：`bars`/`ma60_days`
    决定 `dbl_energy` 是在多长的窗里累加的（不足 40 日的新票窗偏短，分低不代表不活跃）。
    "贴线"和"在均线上"是两件事，分开给：`near_ma60` 用 |dev_med|≤3%% 判贴近，
    `above_ratio` 才是站上均线的天数占比。

    ⚠️ **2026-10-19 换了尺**：这一榜的主列从 `dbl_count`（硬计数：日量比 ≥2.0 才算一次）
    换成 `dbl_energy`（分档加权：量比每 0.1 切一档，得分 = Σ 每档（这一档在窗口里出现的次数 ×
    这一档的分值），分值可配；没有系数／占比／归一化那一层）。
    `dbl_count` 仍在库里、仍在行里交回（`dbl_count_legacy`），但**不再上屏、不再参与筛**——
    留它是留对照物，不是留第二把判据（同一条榜上两把尺会让"选 ≥6"不知道在选哪一把）。

    `min_energy`（原来的 `min_count`）与 `quadrants`（用户 2026-10-13 要点名多选）
    **都在数据库这侧筛**：这一榜默认只交出 `limit`（150）行、按加权分排，前端筛就只能
    在"最高的那 150 只"里筛，选 Q2 得到 3 行会被读成"全场只有 3 只 Q2"
    （实测 2026-10-12 全场 Q2 有 1,152 只）。所以条件拼在 `order by … limit` **之前**，
    另外把**全场**每档实有几只一起交出（`quadrant_counts`，不受 limit 影响）。

    `dbl_step_gap`（「稳步提升」）：本窗加权分 − 紧邻前一整窗的加权分，前一窗按**行偏移**
    `lag(dbl_energy, win_days)` 取（见本节头注释：按日期偏移停牌会让两窗重叠）。
    `step_gap_min` 给了就同样**拼在 limit 之前**筛；三种没有可比前窗的情况交 NULL 而不是 0，
    每行带 `dbl_step_gap_null_reason` 说明是哪一种。全场可比母体里 ≥STEP_GAP_MIN 有几只
    走 `step_gap_pool` 那条聚合（同象限计数一条规矩：0 只与没读到是两句话）。

    `energy` 这一块是**尺子自己的体检**：生效指纹、库里是不是同一批、这一份配置的数学上限
    （`scale.max_score`）与实测分位。对不上批时界面必须说"这一档筛的还是旧批"，
    不能让用户改完配置以为立刻生效。
    """
    table = 'analytics.volume_double'
    d = _dash(date) or _latest(table) or ''
    if not d or not pg.session_has(table, d):
        return _fail('倍量形态', table, d or None)
    lim = max(1, min(int(limit or 150), 800))
    fp = venc.fingerprint()
    me = _num(min_energy, 4) if min_energy is not None else 0.0
    me = 0.0 if me is None else me
    gap = _num(step_gap_min, 4) if step_gap_min is not None else None
    gap_cond = ' and v.dbl_step_gap >= %s' if gap is not None else ''
    # 同场的四维象限读数一起带出来（用户 2026-10-09 要这一列）：`analytics.score` 里
    # `oper_score` 那行才有 `quadrant`（其余 capability 恒 NULL 是设计），X/Y 两轴是同场的另两行。
    # **一律钉在同一个 trade_date**：拿别一天的象限拼今天就是跨日凑数。
    # 键在"一天一票一键"上实测唯一（2026-10-08：5,557 行 = 5,557 个代码），所以这三条 left join
    # 不会把榜单炸成重复行 —— 但这是实测出来的性质，不是假设，改 score 的口径时要复验。
    q_ok, q_bad = _quadrant_tokens(quadrants)
    codes = [c for c in q_ok if c in QUADRANT_CODES]
    terms: list[str] = []
    if codes:
        terms.append('sc.quadrant in (' + ', '.join(['%s'] * len(codes)) + ')')
    if NO_QUADRANT in q_ok:
        # 「没取到象限」是一档真读数（score 当天没有它的 oper_score 行），不是"筛不到就消失"
        terms.append('sc.quadrant is null')
    cond = (' and (' + ' or '.join(terms) + ')') if terms else ''
    # `dbl_step_gap` 在 FROM 的窗口化子查询里算好（片段见 `_step_gap_source`），
    # 外层 where 直接引用那个派生列 —— 派生列不能写在同层 WHERE 里，这是 SQL 的规矩，
    # 不是风格。⚠️ 筛参（min_count、象限、分段差）全部拼在 `order by … limit` **之前**：
    # 拼在后面就只是"把交出的行删几条"，与在前端筛同一种谎（10-13 象限那轮钉过）。
    # 名次用**相关子查询数"分更高的有几只"**（= competition ranking：同分同名、下一名顺延），
    # 不用 `rank() over ()` —— 窗口函数是在 `where` 之后算的，象限/分段差一筛，"第 1 名"
    # 就变成"这一小堆里的第 1"，而用户读的是**全场名次**。分母（universe）另走一条聚合。
    # 与 `duanxian/ranking.py` 的 `RULE_NOTE` 是同一条规则，文案也读那一份（不另写一句）。
    rows = _rows(f"""select v.security_code as code, coalesce(s.name, v.security_code) as name,
                          s.board, s.market, v.win_days, v.bars, v.ma60_days,
                          v.dbl_energy, v.dbl_count, v.dbl_prev_count, v.cross_count,
                          case when v.dbl_energy is null then null else
                               {_energy_rank_expr('v')} end as rank_in_day,
                          v.dev_med, v.dev_max, v.above_ratio, v.ma60_chg,
                          v.avg_amt_yuan, v.close_last, v.ma60_last,
                          v.dbl_step_gap, v.dbl_step_gap_null_reason,
                          sc.quadrant, sc.value as oper_value, sc.rank_in_market, sc.percent_rank,
                          xq.value as x_quality, yv.value as y_env, sc.is_stale
                     from (select w.*,
                                  {_STEP_GAP_EXPR} as dbl_step_gap,
                                  {_STEP_GAP_REASON_EXPR} as dbl_step_gap_null_reason
                             from ({_step_gap_source()}) w) v
                     left join reference_data.security s using (security_code)
                     left join analytics.score sc on sc.trade_date = v.trade_date
                                                 and sc.security_code = v.security_code
                                                 and sc.capability_key = 'oper_score'
                     left join analytics.score xq on xq.trade_date = v.trade_date
                                                 and xq.security_code = v.security_code
                                                 and xq.capability_key = 'stock_quality'
                     left join analytics.score yv on yv.trade_date = v.trade_date
                                                 and yv.security_code = v.security_code
                                                 and yv.capability_key = 'env_resonance'
                    where v.trade_date = %s and v.dbl_energy >= %s{cond}{gap_cond}
                    order by v.dbl_energy desc nulls last, v.cross_count desc nulls last,
                             v.dev_med asc nulls last
                    limit %s""", (d, d, fp, d, me, *codes,
                                  *((gap,) if gap is not None else ()), lim))
    # 每档实有几只：同一场、同一个 min_count，但**不带象限条件**、**不受 limit 影响**。
    # 没有这一格，界面上"选了 Q2 剩 3 行"会被读成"全场只有 3 只 Q2"（实测全场 1,152 只）。
    q_counts: dict[str, int] = {}
    try:
        for r in _rows("""select coalesce(sc.quadrant, %s) as q, count(*) as n
                     from analytics.volume_double v
                     left join analytics.score sc on sc.trade_date = v.trade_date
                                                 and sc.security_code = v.security_code
                                                 and sc.capability_key = 'oper_score'
                    where v.trade_date = %s and v.energy_fp = %s and v.dbl_energy >= %s
                    group by 1""", (NO_QUADRANT, d, fp, me)) or []:
            if not isinstance(r, dict) or r.get('q') is None:
                continue          # 形状不对（桩给的是股票行）就什么都不写，宁缺不造
            n = _int(r.get('n'))
            if n is not None:
                q_counts[str(r['q'])] = n
    except Exception:  # noqa: BLE001  计数读不到交空表，界面按"没读到"处理，不拿 0 顶
        q_counts = {}
    if not rows:
        return _empty_after_filter(
            '倍量形态', table, d,
            f'按加权分≥{_fmt(me)}'
            + (f'、象限 {"、".join(q_ok)}' if q_ok else '')
            + (f'、加权分较前窗增减≥{_fmt(gap)}' if gap is not None else '') + '筛完')
    labels, labels_source = _labels()
    for r in rows:
        dm = _num(r.get('dev_med'))
        r.update({'dbl_energy': _num(r.get('dbl_energy'), 4), 'bars': _int(r.get('bars')),
                  'ma60_days': _int(r.get('ma60_days')), 'win_days': _int(r.get('win_days')),
                  # 旧口径的硬计数：只留作对照（`dbl_count` 退役不上屏），库里还在、仍由每日链写
                  'dbl_count_legacy': _int(r.get('dbl_count')),
                  'dev_med': dm, 'dev_max': _num(r.get('dev_max')),
                  'above_ratio': _num(r.get('above_ratio'), 4),
                  'ma60_chg': _num(r.get('ma60_chg')),
                  'close_last': _num(r.get('close_last'), 3),
                  'ma60_last': _num(r.get('ma60_last'), 3),
                  'avg_amt_yuan': _num(r.get('avg_amt_yuan'), 0),
                  # 四维象限：分数是**带符号的原值**（当日分布 Q1 均 +8.2、Q4 均 −36.9），
                  # 不是 0~100，缺数交回 None 而不是 0 —— 0 会被读成"中立"。
                  'quadrant_label': (labels or {}).get(r.get('quadrant')) if r.get('quadrant') else None,
                  'oper_value': _num(r.get('oper_value'), 2),
                  'x_quality': _num(r.get('x_quality'), 2),
                  'y_env': _num(r.get('y_env'), 2),
                  'rank_in_market': _int(r.get('rank_in_market')),
                  'percent_rank': _num(r.get('percent_rank'), 2),
                  # 当日全场名次（按加权分，同分同名、下一名顺延）；没分数的交 None → 界面 —
                  'rank_in_day': _int(r.get('rank_in_day')),
                  # 分段差：NULL 就交 NULL（界面显示 —），0 是"本窗与前窗一样"的读数。
                  # 为空的行再带一个原因码与那句原因（文案只住 STEP_GAP_NULL_NOTES 一处）。
                  # ⚠️ 浮点：这里绝不能用 `_int`，0.2 的差被截成 0 就等于把"确实提升了"念成"没动"。
                  'dbl_step_gap': _num(r.get('dbl_step_gap'), 4),
                  'near_ma60': None if dm is None else abs(dm) <= 3.0})
        rc = r.get('dbl_step_gap_null_reason')
        r['dbl_step_gap_null_reason'] = rc if isinstance(rc, str) and rc in STEP_GAP_NULL_NOTES else None
        r['dbl_step_gap_null_note'] = STEP_GAP_NULL_NOTES.get(r['dbl_step_gap_null_reason'] or '') \
            if r['dbl_step_gap'] is None else None
    no_q = [str(r['code'])[:6] for r in rows if not r.get('quadrant')]
    pool = sorted({str(r['code'])[:6] for r in rows if r['code']})
    # 「今日涨幅 / 15 日涨幅」（用户 2026-10-10 要点名加在榜尾）：判据与 SQL 只住
    # `duanxian/day_returns.py`，这里只做接法。整批一次查询（实测 150 只 / 16 场 0.25s）。
    dr = day_returns.returns_by_code([r['code'] for r in rows if r.get('code')], d)
    no_pct = []
    for r in rows:
        got = dr.get(str(r.get('code'))) or {}
        r['chg_today'] = got.get('chg_today')
        r['chg_15'] = got.get('chg_span')
        r['pct_bars'] = got.get('bars')          # 窗口里真有行情的场数（停牌过会小于 16）
        r['pct_base_day'] = got.get('base_day')   # 基期是哪一场，悬停要能自证
        if r['chg_15'] is None:
            no_pct.append(str(r['code'])[:6])
    roll = pg_sources.concept_rollup(pool)
    short = [r['code'] for r in rows if r['bars'] is not None and r['bars'] < r['win_days']]
    # ---- 「稳步提升」的全场母体读数（同象限计数一条规矩：不受 limit 影响、0 只 ≠ 没读到）。
    # 带 min_energy 但不带分段差筛参 —— 它是"这一档实有几只"的分母，与界面上那个勾选框一对。
    # 缓存键里必须带**生效指纹**：改过档表但还没重算完时，同一 (场, 阈值) 的旧结果不能接着用，
    # 否则界面上一边显示"配置已改"、一边还端着上一批的只数。
    # 依赖 `_agg` 缓存一小时（那条窗口聚合每次 2.5 秒上下，同一条榜单请求里没必要扫两遍），
    # 缓存年龄由下面 `_cache_caveat` 如实交出。
    pool_key = f'step_gap_pool:{d}:{fp}:{me}'
    step_pool_raw = _agg(pool_key, f"""
            select count(*) filter (where dbl_step_gap >= %s)::int as hit,
                   count(*) filter (where dbl_step_gap is null)::int as uncomparable,
                   count(*)::int as total
              from (select w.*,
                           {_STEP_GAP_EXPR} as dbl_step_gap
                      from ({_step_gap_source()}) w) q
             where q.trade_date = %s and q.dbl_energy >= %s""",
                         (STEP_GAP_MIN, d, d, fp, d, me))
    step_pool = None if not isinstance(step_pool_raw, dict) else {
        'min': STEP_GAP_MIN,
        'hits': _int(step_pool_raw.get('hit')),
        'uncomparable': _int(step_pool_raw.get('uncomparable')),
        'total': _int(step_pool_raw.get('total'))}
    # 名次要说分母：同一个"第 40 名"在"全场 5,514 只有分"与"这一档 124 只"里是两句话。
    # 这条聚合是**当日整场**（不带 min_energy/象限/分段差筛），与上面那个 rank 同一批口径。
    uni_raw = _agg(f'energy_universe:{d}:{fp}',
                   """select count(dbl_energy)::int as scored, count(*)::int as rows_total,
                             max(dbl_energy)::numeric as top_score,
                             count(*) filter (where dbl_energy is null)::int as unscored
                        from analytics.volume_double
                       where trade_date = %s and energy_fp = %s""", (d, fp))
    universe = None if not isinstance(uni_raw, dict) else {
        'scored': _int(uni_raw.get('scored')), 'rows_total': _int(uni_raw.get('rows_total')),
        'top_score': _num(uni_raw.get('top_score'), 4), 'unscored': _int(uni_raw.get('unscored'))}
    sg_missing = [r for r in rows if r.get('dbl_step_gap') is None]
    # ---- 尺子自身的体检（不是可选装饰：改配置→重算这条链要能在页面上被看出来）
    estatus = {}
    try:
        estatus = {'fingerprint': fp, 'in_sync': True, 'profile_source': '',
                   'scale': venc.scale(), 'distribution': {}}
        stt = venc.status()
        estatus.update({'in_sync': stt.get('in_sync'), 'profile_source': stt.get('profile_source'),
                        'distribution': stt.get('distribution'),
                        'stored_batches': stt.get('batches')})
    except Exception as e:  # noqa: BLE001  体检失败要说得出，不能把整张榜变红
        estatus = {'fingerprint': fp, 'in_sync': None, 'error': f'尺子体检读不到：{e}'}
    caveats = _caveats('coverage') + _cache_caveat(
        [('coverage:volume_double', pool_key),
         ('coverage:volume_double', f'energy_universe:{d}:{fp}')])
    if estatus.get('in_sync') is False:
        caveats.append({'code': 'energy_batch_stale',
                        'text': f'这一榜筛的是「旧一批」加权分：库里最新批次不是当前生效配置'
                                f'（生效指纹 {fp}）。改档表/改量纲之后要重算历史场才会换数，'
                                '系统配置那一卡能看到重算跑到哪了；没追平之前这里的数字别当新口径读。'})
    elif estatus.get('in_sync') is None:
        caveats.append({'code': 'energy_status_unread',
                        'text': '加权分那把尺的体检读数没读到（不是"没问题"也不是"有问题"）：'
                                + str(estatus.get('error') or '')})
    no_energy = [str(r['code'])[:6] for r in rows if r.get('dbl_energy') is None]
    if no_energy:
        caveats.append({'code': 'energy_missing',
                        'text': f'榜上有 {len(no_energy)} 只没有加权分（那几天它在日线里没有行，'
                                f'或这一场还没按当前配置重算）：{"、".join(no_energy[:8])}'
                                + ('…' if len(no_energy) > 8 else '')
                                + '。这一格显示 —，**不当 0 分** —— 0 是"这 40 天一次量都没放过"，'
                                  '与"没数"是两件事。'})
    if q_bad:
        caveats.append({'code': 'quadrant_bad_token',
                        'text': f'象限筛选里这些值不是四个档之一：{"、".join(q_bad)} —— '
                                '它们**没有参与筛选**（不静默丢掉，否则榜看着像筛过了其实没筛）。'
                                f'可用的是 {"、".join(QUADRANT_CODES)} 与 {NO_QUADRANT}'
                                '（没取到象限的那批）。'})
    if not q_counts:
        caveats.append({'code': 'quadrant_counts_unread',
                        'text': '各档全场实有几只没读到（那条聚合本身失败了），'
                                '界面按"没读到"显示，不拿 0 顶 —— 0 会被读成"这一档没有票"。'})
    if short:
        caveats.append({'code': 'short_window',
                        'text': f'这些票的可用 K 线不足 40 日，加权分按短窗累加出来，'
                                f'不能与满窗的票横比：{"、".join(short[:8])}'
                                + ('…' if len(short) > 8 else '')})
    if no_q:
        caveats.append({'code': 'quadrant_missing',
                        'text': f'这一场榜上 {len(rows)} 只有 {len(no_q)} 只没取到四维象限'
                                f'（`analytics.score` 当天没有它的 oper_score 行）：'
                                f'{"、".join(no_q[:8])}' + ('…' if len(no_q) > 8 else '')
                                + '。界面显示 —，**不当 0 分**。'})
    if roll.get('failed'):
        caveats.append({'code': 'concept_failed',
                        'text': '概念归属这一格没读到（查询失败，不是这批票没有概念）——'
                                '两件事必须分开说，别把"读不到"当成"没题材"。'})
    elif not roll['rows']:
        caveats.append({'code': 'concept_empty',
                        'text': f'这 {roll["pool_size"]} 只在参考数据里一条概念归属都没有（少见，'
                                f'通常意味着那批票全是北交所或参考数据没覆盖到）。'})
    if not dr:
        caveats.append({'code': 'pct_failed',
                        'text': '涨幅这两列整批没读到（库不通或那条窗口查询坏了），'
                                '不是这批票 15 日都没动。'})
    elif no_pct:
        base_day = next((v.get('base_day') for v in dr.values() if v.get('base_day')), '基期')
        caveats.append({'code': 'pct_missing',
                        'text': f'{len(no_pct)} 只算不出 15 日涨幅：{base_day}（目标日往前第 '
                                f'{day_returns.SPAN} 场）或目标日那天它在日线里没有行'
                                f'（停牌／那几天还没上市）：{"、".join(no_pct[:8])}'
                                + ('…' if len(no_pct) > 8 else '')
                                + '。界面显示 —，「不当 0%（平盘）」—— 0% 是一条实质读数。'})
    if step_pool is None:
        caveats.append({'code': 'step_gap_pool_unread',
                        'text': '「稳步提升」这一档全场可比母体里有几只没读到（那条窗口聚合失败了），'
                                '界面按"没读到"显示，不拿 0 顶 —— 0 会被读成"一只都没有"。'})
    if sg_missing:
        by_reason: dict[str, int] = {}
        for r in sg_missing:
            key = r.get('dbl_step_gap_null_reason') or 'unknown'
            by_reason[key] = by_reason.get(key, 0) + 1
        caveats.append({'code': 'step_gap_missing',
                        'text': f'这一场榜上 {len(rows)} 只有 {len(sg_missing)} 只的「加权分较前窗增减」是空的：'
                                + '、'.join(f'{_STEP_GAP_SHORT_LABELS.get(k, k)} {n} 只'
                                           for k, n in sorted(by_reason.items()))
                                + f'（点名 {"、".join(str(r["code"])[:6] for r in sg_missing[:8])}'
                                + ('…' if len(sg_missing) > 8 else '')
                                + '）。这一格显示 —，**不当 0** —— 0 的意思是「本窗与前窗一样」，'
                                  '与「没有可比的前一整窗」是两件事。'})
    return {'available': True, 'date': d, 'window_days': 40,
            # 这一张表到底覆盖到哪一天、多少场：现算，界面不背（`_NOTE['coverage']` 只指路）
            'coverage': _coverage(('volume_double',)),
            # `dbl_step_gap` 是读侧派生列（库里没有这一列、没有列注释可引），
            # 口径原文只有 STEP_GAP_DEFINITION 这一份：合进 definitions 一起交出，界面悬停逐字读。
            'definitions': {**_comments('volume_double'), 'dbl_step_gap': STEP_GAP_DEFINITION},
            # 「稳步提升」的阈值：只住 STEP_GAP_MIN 这一处，界面与筛参都从这里读。
            # 新榜母体档（前 N 名）与分页档的出厂值都住 volume_energy 一处，这里只引用。
            'thresholds': {'step_gap_min': STEP_GAP_MIN,
                           'top_n_default': TOP_N_DEFAULT, 'page_size': PAGE_SIZE_DEFAULT},
            # 尺子自己：生效配置、档表、量纲上限、实测分位、库里批次（界面逐字读，不自造）
            'energy': estatus,
            # 名次这一件事的规则与分母也只住一处（`duanxian/ranking.py`），界面逐字读、不另写一句
            'rank': {'rule': RANKING.RULE_NOTE, 'rule_short': RANKING.RULE_NOTE_SHORT,
                     'by': 'dbl_energy', 'universe': universe},
            'filter': {'min_energy': me,
                       'rows_returned': len(rows), 'limit': lim,
                       # 分段差筛参的回显：None＝没给（不筛），数字＝后端真应用的值（拼在 limit 之前）
                       'step_gap_min': gap,
                       # 象限那格的回显：应用了哪些档 + 哪些值被认不得（界面据此出声）
                       'quadrants': q_ok, 'quadrants_rejected': q_bad},
            # 全场每档实有几只（不带象限条件、不受 limit 影响）；空表 = 没读到，不是"都没有"
            'quadrant_counts': q_counts,
            # 「加权分较前窗增减 ≥STEP_GAP_MIN」在这一场「加权分≥min_energy」母体里实有几只
            # （不带 limit；min 是阈值回显，让界面不必自己写数）。None = 没读到。
            'step_gap_pool': step_pool,
            'rows': rows,
            # ---- 象限筛选的可选档：值、标签（`config.setting.quadrant_labels` 那一份）、
            #      全场实有几只、这一档是什么意思。**选项住在后端**，页面不自己抄一份 Q1~Q4
            #      （抄一份就会与「四维象限」页的标签走偏，也会把"没取到象限"这一档漏掉）。
            'quadrant_options': [
                {'value': c, 'label': (labels or {}).get(c) or c,
                 # 聚合成功时"这一档没出现在 group by 里"就是**0 只**（实测 2026-10-12 全场只有
                 # Q2/Q4 两档有票）；只有整条聚合失败时才交 None = 没读到，两者不是一件事。
                 'count': (q_counts.get(c, 0) if q_counts else None),
                 'meaning': QUADRANT_MEANING.get(c)}
                for c in QUADRANT_CODES] + [
                {'value': NO_QUADRANT, 'label': '没取到象限',
                 'count': (q_counts.get(NO_QUADRANT, 0) if q_counts else None),
                 'meaning': '当天 `analytics.score` 里没有这只票的 oper_score 行'
                            '（实测 2026-10-12：这场 5,526 只有 12 只如此）—— 不是 0 分'}],
            # ---- 四维象限那一列的口径（与「四维象限」页同一份：分量权重、合成式、四档含义）
            'quadrant_axes': {**QUADRANT_AXES, 'meaning': QUADRANT_MEANING,
                              'labels': labels, 'labels_source': labels_source,
                              'score_note': 'oper_value 是带符号的原值（可为负），不是 0~100 的百分；'
                                            'quadrant 只由 sign(X)/sign(Y) 定档'},
            # ---- 反向聚合：这批票的概念板块出现次数（用户 2026-10-09 要的那张新表）
            'concepts': roll['rows'],
            # ---- 榜尾那两列涨幅的口径（判据只住 duanxian/day_returns.py，页面不自己复述）
            'pct_rule': day_returns.RULE_NOTE,
            'pct_span': day_returns.SPAN,
            'pct_base_day': next((v.get('base_day') for v in dr.values() if v.get('base_day')), None),
            'concept_pool': {'size': roll['pool_size'], 'covered': roll['covered'],
                             'concepts_total': len(roll['rows']),
                             'single_stock': sum(1 for c in roll['rows'] if (c['stocks'] or 0) <= 1)},
            'coverage': _coverage(('volume_double',)),
            'caveats': caveats, 'source': 'pg', 'requests': 0}


def energy_track(date: str = '', top_n: int | None = None, refresh: bool = False) -> dict:
    """「加权分进榜后的逐日涨跌幅」：当日前 N 名，每只票从它第一次进榜那天铺到今天。

    形状：母体＝**基准日这一场得分前 N 名**（默认 `TOP_N_DEFAULT`＝120，可改；
    同分并列 ⇒ 边界那一批一起进来，所以只数可能略多于 N，界面按名次念不硬凑 120）；
    每只票的起算日＝它**历史上第一次进榜**（第一次排进前 N 名）那一场；
    格子里是起算日到基准日**每一场**的当日涨跌幅（后复权，判据只住 `day_returns.py`），
    列数不定、一直铺到今天；排序默认按「整体涨跌幅」从低到高。

    为什么是"前 N 名"而不是"分数门槛"（用户 2026-10-19 晚纠正）：分值一改，
    门槛对应的只数就跟着漂（上一稿的"≥0.8"就这么废了），而"前 120 名"与档表无关 ——
    怎么改配置这一块都是 2 页 × 60 行。

    三条不会从代码里自己长出来的口径，写死在这里：

    1. **整体涨跌幅的基期＝起算日的前一场收盘**，与表格里各格连乘一致
       （起算日当天那一格也算在内）。想只看"进榜之后"的走势，读第 1 格起。
    2. **起算日可能被覆盖起点截断**：这张表从 `coverage.lo` 那一场才有数，
       一只票的首次进榜日正好落在最早那几场时，真正的首次可能更早、库里看不到 ⇒
       行上带 `first_censored=True`，界面念成「不晚于 X 日」，不许念成「就是 X 日」。
    3. **今天的第几名 ≠ 第一次进榜那一天的第几名**：行里 `rank_in_day` 是今天的全场名次，
       `first_date` 是全史第一次进榜那一场，两件事分开给（名次算法见 `duanxian/ranking.py`）。

    行数不截（用户点名"不限制展示行数，分页还是保持 60 个"）：分页在前端，
    所以这里交的是**整批**；`rows_returned` 就是这批的只数。

    取数走进程内缓存（`_agg`/`_agg_call`，一小时），**键里带着「看哪一场 ＋ 倍量配置的指纹 ＋ 名次线」**：
    每天加工链跑完、或量能加权分的配置一改，`energy_fp` 就换 ⇒ 键自己变，旧键不可能把上一批的数
    顶给这一批；同一天同一批重算是同一个答案，所以这一小时的旧值不会说假话。
    真刚回填完想立刻看，带 `refresh=1`（就是清掉 `energy_track_` 那几个键）。
    """
    table = 'analytics.volume_double'
    if refresh:
        clear_agg_cache('energy_track_')
    d = _dash(date) or _latest(table) or ''
    if not d or not pg.session_has(table, d):
        return _fail('加权分进榜后的逐日走势', table, d or None)
    n = int(top_n or TOP_N_DEFAULT)
    n = max(10, min(n, 500))
    fp = venc.fingerprint()
    # 当日**全场**名次。10-26 实测：这里原来是逐行现数「同一场同一批里分更高的只数」
    # （`_energy_rank_expr` 那个相关子查询），5,519 行 × 每行一次计数 = 8,087 毫秒，占整发请求的 62%。
    # 改成窗口 `rank()` 后同一批行 39.5 毫秒、rank_in_day 与边界那批同分并列**逐位相同**
    # （探针 .local/_et_rank_probe.py）。
    # ⚠️ 这个写法只在"where 里除了分区键什么都没有"时成立：本条的筛参是
    #    `trade_date` ＋ `energy_fp` ＋ `dbl_energy is not null`，前两个正是窗口的分组条件，
    #    第三个是"没分的不占号"那条判据本身 ⇒ 窗口看到的那一堆＝当日全场有分的那一批。
    #    倍量榜主查询（`volume_double`）还叠了象限／分段差等筛参，那边一改就把全场名次
    #    筛成"这堆里的第 1"，所以那三处继续用 `_energy_rank_expr`，`test_ranking_shared.py` 钉着。
    hits = _agg(f'energy_track_hits:{d}:{fp}:{n}', f"""
        with r as (select v.security_code as code,
                          coalesce(s.name, v.security_code) as name,
                          s.board, s.market, v.dbl_energy, v.bars, v.win_days,
                          rank() over (order by v.dbl_energy desc nulls last)::int as rank_in_day
                     from analytics.volume_double v
                     left join reference_data.security s using (security_code)
                    where v.trade_date = %s and v.energy_fp = %s
                      and v.dbl_energy is not null)
        select code, name, board, market, dbl_energy, bars, win_days, rank_in_day
          from r where r.rank_in_day <= %s
         order by r.rank_in_day, r.code""", (d, fp, n), many=True) or []
    if not hits:
        return _empty_after_filter('加权分进榜后的逐日走势', table, d,
                                   f'按当日前 {n} 名取（库里这一批没有可排名的行，'
                                   '或这一场还没按当前配置重算）')
    codes = [str(r['code']) for r in hits if r.get('code')]
    # 第一次进榜＝历史上第一次排进当日同一名次线以内。名次按**当天整场**排，
    # 所以这里必须逐场 `rank() over (partition by trade_date …)`（窗口在这条 SQL 里是对的：
    # 它不带任何筛参），不能拿今天的门槛去比历史分数。
    first = {str(r['code']): str(r['first_date']) for r in (_agg(
        f'energy_track_first:{d}:{fp}:{n}',
        """with r as (select security_code, trade_date,
                                  rank() over (partition by trade_date
                                               order by dbl_energy desc nulls last) as rk
                             from analytics.volume_double
                            where energy_fp = %s and dbl_energy is not null
                              and security_code = any(%s::text[]))
             select security_code as code, min(trade_date)::text as first_date
               from r where rk <= %s group by 1""", (fp, codes, n), many=True) or []) if r.get('code')}
    cover = _coverage(('volume_double',)).get('volume_double') or {}
    cover_lo, cover_hi, cover_n = cover.get('from'), cover.get('to'), cover.get('sessions')
    ser = _agg_call(f'energy_track_series:{d}:{fp}:{n}',
                    lambda: day_returns.series_by_code(
                        {c: first[c] for c in codes if c in first}, d)) or {}
    rows: list[dict] = []
    for r in hits:
        c = str(r.get('code') or '')
        s = ser.get(c) or {}
        days = s.get('days') or []
        f0 = first.get(c)
        rows.append({'code': c, 'name': r.get('name'), 'board': r.get('board'),
                     'market': r.get('market'),
                     'dbl_energy': _num(r.get('dbl_energy'), 4),
                     # 今天的全场名次（同分同名、顺延），与 first_date（第一次进榜）两件事分开
                     'rank_in_day': _int(r.get('rank_in_day')),
                     'bars': _int(r.get('bars')), 'win_days': _int(r.get('win_days')),
                     'first_date': f0,
                     # 截断声明：起算日就是这张表最早那几场时，"第一次"只是"不晚于"
                     'first_censored': bool(f0 and cover_lo and f0 <= str(cover_lo)),
                     'sessions_since': len(days) - 1 if days else None,
                     'chg_cum': (days[-1].get('cum') if days else None),
                     'missing_base': bool(s.get('missing_base')),
                     'base_day': s.get('base_day'),
                     'series': days})
    # 默认按整体涨跌幅**从低到高**（用户点名）。None（没基期/停牌到今）排最后，
    # 不参与比较 —— 让它冒到最前会把"算不出"念成"跌得最少"。
    rows.sort(key=lambda x: (x['chg_cum'] is None,
                             x['chg_cum'] if x['chg_cum'] is not None else 0.0))
    no_first = [str(x['code'])[:6] for x in rows if not x['first_date']]
    no_cum = [str(x['code'])[:6] for x in rows if x['chg_cum'] is None]
    caveats = _caveats('coverage') + [{'code': 'energy_track_scope',
                                       'text': f'母体是「基准日这一场得分前 {n} 名」里的 '
                                               f'{len(rows)} 只（同分并列会一起进来，所以只数可以'
                                               f'略多于 {n}）；每只票的起算日是它全史里'
                                               '第一次排进前 N 名那一场，所以同一行里"今天第几名"和'
                                               '"最早哪天进榜"是两个时点，别看成一件事。'
                                               + RANKING.RULE_NOTE}]
    if any(x['first_censored'] for x in rows):
        caveats.append({'code': 'energy_track_censored',
                        'text': f'有 {sum(1 for x in rows if x["first_censored"])} 只的首次进榜日'
                                f'落在或紧贴这张表的最早一场（{cover_lo}）—— 它真正第一次进榜'
                                '「可能更早、库里看不到」。界面念「不晚于该日」，不念「就是该日」。'})
    if no_first:
        caveats.append({'code': 'energy_track_no_first',
                        'text': f'{len(no_first)} 只找不到首次进榜日（库里这一批没有它的进榜行）：'
                                f'{"、".join(no_first[:8])}' + ('…' if len(no_first) > 8 else '')})
    if no_cum:
        caveats.append({'code': 'energy_track_no_base',
                        'text': f'{len(no_cum)} 只算不出整体涨跌幅：起算日那一天的前一场'
                                '在日线里没有行（那天才上市，或基期缺数）。这一格显示 —，'
                                '「不当 0%」（0% 是「一分钱没动」）。'})
    return {'available': True, 'date': d, 'top_n': n, 'rows_capped': len(rows) > n,
            'top_n_default': TOP_N_DEFAULT, 'page_size': PAGE_SIZE_DEFAULT,
            'rank': {'rule': RANKING.RULE_NOTE, 'rule_short': RANKING.RULE_NOTE_SHORT,
                     'by': 'dbl_energy'},
            'fingerprint': fp,
            'coverage': {'lo': str(cover_lo) if cover_lo else None,
                         'hi': str(cover_hi) if cover_hi else None,
                         'sessions': cover_n},
            'rows': rows, 'rows_returned': len(rows),
            'max_series_len': max((len(x['series']) for x in rows), default=0),
            'pct_rule': day_returns.RULE_NOTE,
            'caveats': caveats, 'source': 'pg', 'requests': 0}


def breakout_signal(date: str = '', limit: int = 150, signal_type: str = '') -> dict:
    """缩量收敛后首次突破形态信号（`analytics.volume_breakout_signal`）。

    用户 2026-10-11 的原话拆成三段式：
      ① 2 个月前有过一波放量（启动痕迹，vol ≥ 前5日均量 ×1.8）
      ② 之后持续缩量到均线附近（筹码沉淀，连续 5 天以上 |close/ma60-1| < 5%）
      ③ 第一个同时满足"突破均线 + 放量 ≥1.6×"的那天 = 潜在买点

    `signal_type` 可筛：'first_break' / 'surge_after_shrink' / 'converging'；
    不传就返回全部三类。认不得的值**不参与筛选并当场点名**（静默丢掉等于交一份
    "看着像筛过了其实没筛"的榜 —— 与埋伏榜 `types` 同一条规矩）。
    """
    from app.compute import breakout_signal as bs

    table = 'analytics.volume_breakout_signal'
    d = _dash(date) or _latest(table, spec=bs.SPEC_VERSION) or ''
    if not d or not pg.session_has(table, d):
        return _fail('突破信号', table, d or None)
    lim = max(1, min(int(limit or 150), 800))

    rejected = []
    st = str(signal_type or '').strip()
    if st and st not in bs.TYPE_LABELS:
        rejected.append(st)
        st = ''

    # 只认当前判据版本：主键含 spec_version，判据改版后旧行留着对账，
    # 不带这一条就是"同一天两个版本的行数加在一起"当今天的数。
    where = "v.trade_date = %s AND v.spec_version = %s"
    params: list = [d, bs.SPEC_VERSION]
    if st:
        where += " AND v.signal_type = %s"
        params.append(st)

    rows = _rows("""select v.security_code as code, coalesce(s.name, v.security_code) as name,
                          s.board, v.signal_type, v.last_surge_date, v.surge_vol_ratio,
                          v.shrink_days, v.converge_days, v.break_vol_ratio, v.break_dev,
                          v.break_close, v.break_ma60, v.window_days, v.bars_used
                     from analytics.volume_breakout_signal v
                     left join reference_data.security s using (security_code)
                    where %s
                    order by v.break_vol_ratio desc nulls last, v.converge_days desc nulls last
                    limit %s""" % (where, '%s'), params + [lim])
    if not rows:
        return _empty_after_filter(
            '突破信号', table, d,
            f'按类型「{bs.TYPE_LABELS.get(st, st)}」筛完' if st else '三类信号都不筛也')

    for r in rows:
        r.update({'shrink_days': _int(r.get('shrink_days')),
                  'converge_days': _int(r.get('converge_days')),
                  'window_days': _int(r.get('window_days')),
                  'bars_used': _int(r.get('bars_used')),
                  'break_vol_ratio': _num(r.get('break_vol_ratio'), 2),
                  'break_dev': _num(r.get('break_dev'), 4),
                  'break_close': _num(r.get('break_close'), 2),
                  'break_ma60': _num(r.get('break_ma60'), 2),
                  'surge_vol_ratio': _num(r.get('surge_vol_ratio'), 2),
                  # 屏幕上那一格读的就是这个标签，列排序也排它（sortKey 必须等于格子里读到的东西）
                  'signal_type_label': bs.TYPE_LABELS.get(r.get('signal_type'), r.get('signal_type'))})

    short = [str(r['code'])[:6] for r in rows if r['bars_used'] is not None and r['bars_used'] < r['window_days']]
    caveats = _caveats('coverage') + _cache_caveat(('coverage:breakout_signal',))
    if short:
        caveats.append({'code': 'short_window',
                        'text': f'这些票的可用 K 线不足 {rows[0]["window_days"]} 日，信号按短窗算出，'
                                f'不能与满窗的票横比：{"、".join(short[:8])}'
                                + ('…' if len(short) > 8 else '')})
    if rejected:
        caveats.append({'code': 'breakout_type_rejected',
                        'text': '信号类型筛选里这个值不是已登记的类型：' + '、'.join(rejected)
                                + ' —— 它没有参与筛选（本轮交的是全部三类）。可用的是 '
                                + '、'.join(f'{k}（{v}）' for k, v in bs.TYPE_LABELS.items())})

    # 每一类全场实有几只：**不带筛选条件**聚合，不受 limit 影响。
    # 原先这里是从已交出的行里数（"各类信号数量 150 行里首破 7 只"），
    # 读者会当成"全场只有 7 只首破" —— 与倍量榜象限计数同一条错。
    counts: dict[str, int] = {}
    counts_ok = True
    try:
        for r in _rows("""select signal_type as t, count(*)::int as n
                     from analytics.volume_breakout_signal
                    where trade_date = %s and spec_version = %s group by 1""",
                       (d, bs.SPEC_VERSION)) or []:
            if r.get('t') in bs.TYPE_LABELS:
                counts[str(r['t'])] = int(r['n'] or 0)
    except Exception:  # noqa: BLE001  读不到就说读不到，不拿 0 顶（0 = "这一类今天一只都没有"）
        counts_ok = False
    if not counts_ok:
        caveats.append({'code': 'breakout_counts_unread',
                        'text': '每一类全场实有几只没读到（那条聚合失败了），界面按"没读到"显示，'
                                '不拿 0 顶 —— 0 的意思是"这一类今天一只都没有"。'})

    tot = _one("select count(*)::int n from analytics.volume_breakout_signal "
               "where trade_date = %s and spec_version = %s", (d, bs.SPEC_VERSION)) or {}

    # 母体：改成 bulk 取数（`bars_by_code`）之后，这一榜扫的是**当天有行情的全部票**。
    # 这一格照样要交出去 —— 页面上「命中 N 只」是不是全市场的数，只有这里说得清。
    # 原先它只扫前 800 只（按代码序取一批 = 只有深主板 000/002），靠一条
    # `breakout_universe_partial` 的 caveat 出声；防回归的钉现在在
    # tests/test_breakout_signal_compute.py（不许再出现母体上限）。
    uni = _one("select count(distinct security_code)::int n from market_data.stock_daily "
               "where trade_date = %s", (d,)) or {}
    codes_total = _int(uni.get('n'))

    return {'available': True, 'date': d, 'window_days': bs.WINDOW_DAYS,
            'definitions': _comments('volume_breakout_signal'),
            'filter': {'signal_type': st or 'all', 'signal_type_rejected': rejected,
                       'rows_returned': len(rows or []), 'limit': lim},
            'universe': {'codes_total': codes_total, 'codes_scanned': codes_total,
                         'computed_by': 'app/compute/breakout_signal.py:bars_by_code'
                                        '（一次问库取当天有行情的全部票，无母体上限）'},
            'type_options': [{'value': k, 'label': v, 'meaning': bs.TYPE_MEANINGS.get(k, ''),
                              'count': (counts.get(k, 0) if counts_ok else None)}
                             for k, v in bs.TYPE_LABELS.items()],
            'thresholds': {'surge_mult': bs.SURGE_MULT, 'break_mult': bs.BREAK_MULT,
                           'dev_thresh': bs.DEV_THRESH,
                           'converge_min_days': bs.CONVERGE_MIN_DAYS,
                           'window_days': bs.WINDOW_DAYS},
            'day_total': _int(tot.get('n')),
            'rows': rows,
            'coverage': _coverage(('volume_breakout_signal',)),
            'caveats': caveats, 'source': 'pg', 'requests': 0}



# ================================================================ 5. 板块热度

# 五要素的中文名/权重/含义只用于显示与解释；**实际分值一律来自 analytics.ignition
# 那五列本身**。权重在列注释里也写着，两边不一致时以列为准。
ELEMENTS = (
    {'key': 'squeeze', 'column': 'squeeze', 'cn': '波动状态', 'weight': .15,
     'means': '热度 20 日标准差 与 日内振幅 20 日均值，各取 120 日分位后求均。'
              '方向是"波动已在放大"得高分（回测推翻了"越沉寂越高分"的原直觉）'},
    {'key': 'position', 'column': 'position_score', 'cn': '位置', 'weight': .20,
     'means': '当日热度在自身 120 日滚动分位的**低位**程度 =(1-分位)×100，专治追高'},
    {'key': 'ignition', 'column': 'ignition', 'cn': '点火', 'weight': .30,
     'means': '热度 3 日斜率由负转正 40 分 + 斜率仍加速 30 分 + 热度 MA5 上穿 MA20 30 分'},
    {'key': 'volume', 'column': 'volume', 'cn': '量能', 'weight': .20,
     'means': '板块成交额/自身 20 日均额（占一半）+ 板块占全市场成交额份额的 20 日倍数'
              '（占一半）。看份额是为避开大盘普涨日全体误报'},
    {'key': 'breadth', 'column': 'breadth', 'cn': '广度', 'weight': .15,
     'means': '板块内上涨家数占比水平（占一半）+ 相对 10 日均值的抬升（占一半），'
              '原始数据取 `market_data.sector_daily.up_count/down_count`'})
STAGE_CN = {'ignite': '点火', 'follow': '延续', 'fade': '退潮', 'coil': '蓄势', 'none': '无信号'}
TREND_WINDOWS = (3, 5, 21)

# 两项"解释口径用"的全库聚合。默认不重算（各 2~3 秒），有缓存才随首屏带出。
_BREADTH_SQL = """
    select coalesce(si.sector_type, '(未登记)') as sector_type,
           count(distinct i.object_key)::int as sectors,
           count(i.breadth)::int as breadth_rows,
           count(*)::int as rows_total,
           max(i.term_count)::int as max_term_count,
           count(*) filter (where i.term_count >= 5)::int as full_term_rows,
           count(*) filter (where i.stage = 'ignite')::int as ignite_rows,
           count(distinct i.object_key) filter (where i.stage = 'ignite')::int as ignite_sectors
      from analytics.ignition i
      left  join reference_data.sector_info si on si.sector_code = i.object_key
     where i.scope = 'sector'
     group by 1 order by count(*) desc"""
_LEGACY_SQL = """
    select count(*)::int as rows_total,
           count(distinct trade_date)::int as sessions,
           min(trade_date) as first_date, max(trade_date) as last_date,
           count(*) filter (where member_scored_count is null)::int as scored_count_null
      from analytics.sector_heat_daily where source = 'member_agg'"""


def _heat_coverage(full: bool) -> dict:
    """三张表的起止走索引只花 0.2 秒；`sessions` 要全表 distinct（545 万行 2.3 秒），
    所以只有 `stats=full` 才填，其余情况老实给 None，不猜也不复用旧值。"""
    lite = _rows("""select 'sector_heat_daily' as t, min(trade_date) a, max(trade_date) b
                       from analytics.sector_heat_daily
                      union all select 'ignition', min(trade_date), max(trade_date)
                       from analytics.ignition
                      union all select 'sector_trend', min(trade_date), max(trade_date)
                       from analytics.sector_trend""") or []
    out = {r['t']: {'from': r['a'], 'to': r['b'], 'sessions': None} for r in lite}
    if full:
        for t, v in _coverage(('sector_heat_daily', 'ignition', 'sector_trend')).items():
            out[t] = v
    return out


def _statistical_expr() -> str:
    """「不是板块、是市场级统计指数」的判据：`sector_type='统计'`。

    2026-10-06 由 c7 从通达信自己的名称表回填名字时改判的那 48 个（880001 总市值、
    880005 涨跌家数、880011 主板总值 … 880099 通用回购）。它们的成交额逐日等于全 A 成交额，
    留在板块母体里会压住每一张按热度/份额排的榜。判据是**登记类型**，不是代码段 ——
    下一个混进来的市场级指数只要登记对了就照样进不了榜。
    """
    return "si.sector_type = '统计'"


def _unnamed_expr(alias: str) -> str:
    """"名字就是编号"的板块判据：全库 49 个（`TDX_880004` 的 name 是 `880004`）。

    这批在热度榜上排得很靠前（09-22 实测 `880004` 热度 82.65 = 当日第 2），
    不当成垃圾标出来，页面就会把一个没名字的指数当成领涨板块。
    行标记与母体计数两处共用这一份定义，不写两遍。
    """
    return "(si.name is null or si.name ~ '^[0-9]{4,6}$' or si.name = %s)" % alias


def sector_heat(date: str = '', sector_type: str = '', stage: str = '',
                limit: int = 200, stats: str = 'day', refresh: bool = False,
                sort: str = 'heat') -> dict:
    """板块热度页的数据底（FT `/sector` 在 YuanShan 侧的对应物）。

    源三张表：`analytics.sector_heat_daily`（热度）+ `analytics.ignition`（点火五要素）
    + `analytics.sector_trend`（多窗口斜率）。三件必须随数据交出的口径写在
    `caveats` 里（`sector_breadth` / `heat_two_calibers` / `coverage`），
    其中"行业板块永远判不出点火"那条的**注释原文数字已经过期**，所以本函数查
    一遍广度覆盖（`breadth_reality`）而不是转述注释。

    `stats='day'`（默认）：只查当日，秒级返回；三项全库聚合（旧口径行数、广度覆盖、
    各表场数）命中缓存才顺带带出，没缓存就是 `null` + 怎么拿到。
    `stats='full'`：现算那三项（多 ~5 秒），算完进一小时缓存，之后默认请求也带着。
    `refresh=True`：清掉本接口的缓存重算（回填刚跑完时用）。
    """
    if refresh:
        clear_agg_cache('sector_heat:')
        clear_agg_cache('coverage:')
    table = 'analytics.sector_heat_daily'
    d = date or _latest(table) or ''
    if not d or not pg.session_has(table, d):
        return _fail('板块热度', table, d or '')

    lim = max(1, min(int(limit or 200), 1000))
    want_type, want_stage = (sector_type or '').strip(), (stage or '').strip()
    # 排序档只认这两个值。给了别的**要说参数不对**，不许借"这一场没有数据"那句 ——
    # 一个是"你给的条件我按不了"，一个是"库里没这场"，混成一句会让人去查回填。
    want_sort = (sort or 'heat').strip().lower()
    if want_sort not in ('heat', 'pct'):
        return _reject('sort', d, '只支持 heat（热度合成分）或 pct（板块自己指数当日涨幅），'
                                  f'给了 {sort!r}')
    # 筛选条件放在**排名之后、limit 之前**：`universe_count` 必须是全母体数（页面要
    # 说清"这 20 个是从多少个里排出来的"），而截断必须发生在筛选之后 —— 先 limit
    # 再在 Python 里挑，会在只勾"概念"时静默少掉大半张榜。
    rows = _rows(f"""
        with base as (
            select h.sector_code, h.heat, h.part_count, si.name, si.sector_type,
                   -- 板块自己那条 880 指数当天的涨跌幅（`(今收 − 昨收) ÷ 昨收`）。
                   -- 用户 2026-10-15 点名要"按当日涨幅排"这一档 —— 它必须来自板块指数自己，
                   -- **不拿成分股涨幅做平均**（那是另一把尺子，两种算法在题材榜上能差出一个档）。
                   sd.pct_change                  as pct,
                   sd.up_count, sd.down_count, sd.member_count,
                   -- 库里有一批"名字就是编号"的板块行（`880004` 这类），热度榜上排得很靠前。
                   -- 打标交出去由界面决定藏不藏，不在取数层悄悄删行 —— 删了母体数就对不上了，
                   -- 而且"藏掉多少行"本身得是可审计的
                   {_unnamed_expr('h.sector_code')} as unnamed,
                   {_statistical_expr()}          as statistical
              from analytics.sector_heat_daily h
              left  join reference_data.sector_info si using (sector_code)
              left  join market_data.sector_daily sd on sd.trade_date = %s and sd.sector_code = h.sector_code
             where h.trade_date = %s and h.source = 'sector'
        ), r as (
            select *,
                   rank() over (order by heat desc nulls last) as heat_rank,
                   -- 两把尺子的名次**都算**：换排序档时页面上那一列的"第几名"必须跟着这一档，
                   -- 只算 heat_rank 会让"按涨幅排的榜"还挂着热度名次，读者以为榜排错了
                   rank() over (order by pct desc nulls last)   as pct_rank,
                   -- 母体统计用**窗口聚合在 CTE 里算**：外层 where 一过滤，
                   -- 在这里 count 出来的就是筛选后的数，分母会跟着筛选条件一起变小。
                   -- 一趟查完而不是另发两条统计，是为了首屏少两个往返
                   count(*) over ()                                  as universe_count,
                   count(*) filter (where heat is null) over ()       as universe_heat_null,
                   count(*) filter (where pct is null) over ()        as universe_pct_null,
                   count(*) filter (where unnamed) over ()            as universe_unnamed,
                   count(*) filter (where statistical) over ()        as universe_statistical
              from base
        )
        select r.sector_code, r.name, r.sector_type, r.unnamed, r.statistical,
               r.heat, r.part_count, r.heat_rank, r.pct, r.pct_rank,
               r.up_count, r.down_count, r.member_count, r.universe_count,
               r.universe_heat_null, r.universe_pct_null, r.universe_unnamed, r.universe_statistical,
               i.stage, i.score, i.squeeze, i.position_score, i.ignition,
               i.volume, i.breadth, i.term_count,
               i.detail ->> 'heat_pctile'   as heat_pctile,
               i.detail ->> 'vol_pctile'    as vol_pctile,
               i.detail ->> 'slope3'        as slope3,
               i.detail ->> 'vol_ratio'     as vol_ratio,
               i.detail ->> 'share_ratio'   as share_ratio,
               i.detail ->> 'up_ratio'      as up_ratio,
               i.detail ->> 'up_ratio_ma'   as up_ratio_ma,
               i.detail ->> 'ma_cross'      as ma_cross,
               i.detail ->> 'accelerating'  as accelerating,
               i.detail ->> 'turned_up'     as turned_up,
               t3.slope  as slope_3,  t3.point_count as points_3,
               t5.slope  as slope_5,  t5.point_count as points_5,
               tw.slope  as slope_21, tw.point_count as points_21
          from r
          left  join analytics.ignition i on i.trade_date = %s and i.scope = 'sector'
                       and i.object_key = r.sector_code
          -- 一个窗口一次 join，不用 array_agg 摊平：某一窗缺行时会把整个板块带走，
          -- 那是"丢板块"不是"丢字段"，榜会静默变短
          left  join analytics.sector_trend t3 on t3.trade_date = %s
                       and t3.sector_code = r.sector_code and t3.window_days = 3
          left  join analytics.sector_trend t5 on t5.trade_date = %s
                       and t5.sector_code = r.sector_code and t5.window_days = 5
          left  join analytics.sector_trend tw on tw.trade_date = %s
                       and tw.sector_code = r.sector_code and tw.window_days = 21
         where (%s = '' or r.sector_type = %s)
           and (%s = '' or i.stage = %s)
         -- 两档排序（用户 2026-10-15 点名）：`heat` 是这一屏默认的热度榜（合成分），
         -- `pct` 是"板块自己指数今天涨了多少"那把尺子 —— 两份说明各自都在页面上写着，
         -- 不互相顶替。同一涨幅下再按热度、再按代码，保证刷新两次行序不抖。
         order by {'r.pct desc nulls last, r.heat desc nulls last' if want_sort == 'pct'
                   else 'r.heat desc nulls last'}, r.sector_code
         limit {lim}
        """, (d, d, d, d, d, d, want_type, want_type, want_stage, want_stage))
    if rows is None:
        return _fail('板块热度', table, d)

    out = []
    for row in rows:
        for k in ('heat', 'score', 'squeeze', 'position_score', 'ignition', 'volume', 'pct',
                  'breadth', 'heat_pctile', 'vol_pctile', 'slope3', 'vol_ratio',
                  'share_ratio', 'up_ratio', 'up_ratio_ma'):
            row[k] = _num(row.get(k), 2)
        for k in ('slope_3', 'slope_5', 'slope_21'):
            row[k] = _num(row.get(k), 4)
        # 三个布尔项在 jsonb 里是 true/false，`->>` 取出来是字符串，统一成三态
        for k in ('ma_cross', 'accelerating', 'turned_up'):
            v = row.get(k)
            row[k] = None if v is None else (str(v).lower() == 'true')
        row['stage_cn'] = STAGE_CN.get(row.get('stage'), row.get('stage'))
        row['breadth_available'] = row.get('breadth') is not None
        out.append(row)

    # 母体数单独查一次（0.12 秒）：窗口列 `universe_unnamed` 虽然也来自全母体，但筛选后
    # 榜可能一行都不剩，那时就没有"承载窗口值的行"可读了
    universe = _one("""select count(*)::int as rows_today,
                              count(distinct h.sector_code)::int as sectors,
                              count(*) filter (where h.heat is null)::int as heat_null,
                              count(*) filter (where sd.pct_change is null)::int as pct_null
                         from analytics.sector_heat_daily h
                         left join market_data.sector_daily sd
                                on sd.trade_date = %s and sd.sector_code = h.sector_code
                        where h.trade_date = %s and h.source = 'sector'""", (d, d)) or {}
    parent = {'rows_today': _int(universe.get('rows_today')),
              'sectors': _int(universe.get('sectors')),
              'heat_null': _int(universe.get('heat_null')),
              # 按涨幅排的时候这一项必须能读到：多少个板块当天没有自己那条 880 日线。
              # 不说这句，"pct 那一档只有 400 行有值"看着像榜算错了。
              'pct_null_today': _int(universe.get('pct_null')),
              'unnamed_today': _int(rows[0].get('universe_unnamed')) if rows else None,
              'statistical_today': _int(rows[0].get('universe_statistical')) if rows else None}
    for row in out:
        row.pop('universe_heat_null', None)
        row.pop('universe_pct_null', None)
        row.pop('universe_unnamed', None)
        row.pop('universe_statistical', None)
    # 板块名 → 东财板块页（用户 2026-10-15 点名要能点开）。对表与读法各只有一份
    # （`reference_data.em_board_link` + `duanxian/em_board_link.py`），这里只按行上的
    # `sector_type` 取那一档：行业只查东财行业榜、概念只查东财概念榜，风格与市场级统计不给链接。
    em_board_link.decorate(out, kind_of=lambda row: row.get('sector_type') or '',
                           name_of=lambda row: row.get('name') or '')
    link_status = em_board_link.status()
    # 以下三项是"解释口径用"的全库聚合。默认只从缓存取、不现算：冷首屏因此
    # 不用为它们付那 5 秒；`stats=full`（或本页任何一次请求之后的一小时内）才有值，
    # 取不到时明确给 None + 怎么拿到，而不是给个旧数装作是新的。
    want_full = (stats or 'day').strip().lower() in ('full', 'all', '1')
    if want_full:
        legacy = _agg('sector_heat:legacy', _LEGACY_SQL) or {}
        breadth = _agg('sector_heat:breadth', _BREADTH_SQL, many=True) or []
    else:
        legacy = _agg_lazy('sector_heat:legacy', _LEGACY_SQL)[0] or {}
        breadth = _agg_lazy('sector_heat:breadth', _BREADTH_SQL, many=True)[0]
    coverage = _heat_coverage(want_full)
    # 列注释走 pg_description，一次 0.5 秒，不值得进缓存，也就不会有"缓存的口径"这种怪事
    definitions = {'sector_heat_daily': _comments('sector_heat_daily'),
                   'ignition': _comments('ignition'),
                   'sector_trend': _comments('sector_trend')}

    # 当日两项统计走实查（各 0.2 秒，且回填后必须立刻变新，不适合缓存）
    stage_dist = _rows("""select i.stage, count(*)::int as n
                            from analytics.ignition i
                           where i.trade_date = %s and i.scope = 'sector'
                           group by 1 order by count(*) desc""", (d,)) or []
    trend_avail = _rows("""select window_days::int as window_days, count(*)::int as rows_n
                             from analytics.sector_trend
                            where trade_date = %s group by 1 order by 1""", (d,)) or []

    # 空榜必须自解释：否则读者以为是"没数据"，实际是被口径判除了
    combo = ' / '.join(x for x in (want_type, want_stage) if x)
    filter_note = None
    if not out and combo:
        filter_note = (
            f'`{combo}` 当日确实 0 行。' +
            ('行业板块因缺涨跌家数，在点火判据上**规则上不可能**达标 —— '
             '这是算不出，不是那天没轮动（见 `breadth_reality`）。'
             if want_stage == 'ignite' and want_type == '行业' else
             '该场没有板块命中这个组合（阶段分布见 `stage_distribution_today`）。')
        )

    return {
        'available': True, 'date': d,
        'rows': out,
        'filter': {'sector_type': want_type or None, 'stage': want_stage or None,
                   'limit': lim, 'rows_returned': len(out),
                   # 排序档由后端定并交回（页面按它写榜名，不自己猜"这一屏是热度还是涨幅"）
                   'sort': want_sort,
                   'sort_label': '按板块自己指数的当日涨幅' if want_sort == 'pct' else '按热度合成分',
                   'sort_note': ('这一档的主序是 `pct`（板块那条 880 指数今天涨跌了多少），'
                                 '同涨幅再按热度、再按代码；`pct_rank` 就是这一档的名次。'
                                 if want_sort == 'pct' else
                                 '这一档的主序是 `heat`（涨幅/广度/动力/成交四项合成的热度分），'
                                 '与 `heat_rank` 同序；涨幅那一列只是顺带显示。')},
        'universe': {'rows_today': _int(universe.get('rows_today')),
                     'sectors': _int(universe.get('sectors')),
                     'heat_null': _int(universe.get('heat_null')),
                     'pct_null_today': parent['pct_null_today'],
                     "unnamed_today": parent["unnamed_today"],
                     "statistical_today": parent["statistical_today"],
                     'caliber': '只统计 source=sector 的现行口径',
                     'note': '`universe_count` 随行交出（当日进榜板块数）。页面别用 '
                             'rows.length 当母体数 —— 那是 limit 截断后的数。'
                             '`unnamed_today` 是"名字等于代码"的板块行数（回填后只剩 880595 这类），'
                             '`statistical_today` 是**市场级统计指数**的行数（总市值/涨跌家数/主板总值…，'
                             '成交额=全市场，不是板块）。两者都还在母体里，'
                             '行上有 `unnamed` / `statistical` 标记供界面决定藏不藏'},
        'filter_note': filter_note,
        # 这一格决定板块名是链接还是纯文本，并把"对上了几个"的分母交成全场数（不是这一页）。
        'board_link': {
            'available': link_status['available'],
            'kinds': {k: v for k, v in (link_status['kinds'] or {}).items() if k in em_board_link.LINKABLE_KINDS},
            'tried_at': link_status['tried_at'],
            'reason': link_status['reason'],
            'linked_today': sum(1 for r in out if r.get('em_board_code')),
            'basis': 'reference_data.em_board_link。同类才对表（我方行业对东财行业榜、我方概念对东财概念榜）；'
                     '只认精确同名，其次名+Ⅱ/Ⅲ（东财把二级行业写成「油气开采Ⅱ」）；除此之外一律不链。'
                     '风格 / 市场级统计两档东财没有对应的板块族，永远不给链接',
            'url': 'https://quote.eastmoney.com/bk/90.<em_board_code>.html',
        },
        'stage_distribution_today': stage_dist,
        # 优先级链是顺序短路的，界面排序照这个序，不按字母也不按分值
        'stage_priority': ['ignite', 'follow', 'fade', 'coil', 'none'],
        'stage_meaning': STAGE_CN,
        'elements': ELEMENTS,
        'score_formula': 'score = 五要素加权（squeeze .15 / position .20 / ignition .30 / '
                         'volume .20 / breadth .15），空要素不计权重',
        'heat_source_caliber': {
            'used': 'sector（板块级直算，sector_heat_hist.py，可回溯到 2021）',
            'legacy_member_agg': ({
                'measured': True,
                'rows_total': _int(legacy.get('rows_total')),
                'sessions': _int(legacy.get('sessions')),
                'first_date': legacy.get('first_date'), 'last_date': legacy.get('last_date'),
                'member_scored_count_null': _int(legacy.get('scored_count_null')),
                'why_it_is_wrong': '列注释原文：摊下去用唯一主板块、聚回来用全部归属 '
                                   '—— 两头口径不一致，故已废弃'}
                if legacy else {
                    'measured': False,
                    'why_it_is_wrong': '列注释原文：摊下去用唯一主板块、聚回来用全部归属 '
                                       '—— 两头口径不一致，故已废弃',
                    'note': '旧口径还剩多少行没查（那是全表扫描，不占首屏）。'
                            '要这几个数发 `stats=full`'}),
        },
        'trend_windows_available': trend_avail,
        'trend_windows_returned': list(TREND_WINDOWS),
        'breadth_reality': breadth,
        'breadth_reality_note': (None if breadth else
                                 '本次未查全历史广度覆盖（2.6 秒的全库分组）。'
                                 '结论仍在 `caveats.sector_breadth` 里写着，但那是一段话 '
                                 '不是当下的数；要数就发 `stats=full`。'),
        'caveats': _caveats('sector_breadth', 'heat_two_calibers', 'coverage') + _cache_caveat(
            ('sector_heat:legacy', 'sector_heat:breadth',
             'coverage:sector_heat_daily', 'coverage:ignition', 'coverage:sector_trend')),
        'definitions': definitions,
        'coverage': coverage,
        'freshness': {
            'stats_mode': 'full' if want_full else 'day',
            'always_live': '榜单行、当日母体数、当日阶段分布、当日趋势窗口数',
            'cached_or_absent': {
                k: ('本次现算' if want_full and k.startswith('sector_heat:') else
                    (_agg_age(k) if _agg_age(k) is not None else None))
                for k in ('sector_heat:legacy', 'sector_heat:breadth',
                          'coverage:sector_trend')},
            'cache_ttl_seconds': int(_AGG_TTL),
            'note': '首屏只查当日；三项全库聚合命中缓存才带出（值是几点算的看这里，'
                    '数字越大越旧），没缓存就是 null。慢的从来不是榜单 —— '
                    '09-23 当日榜 0.35 秒，`sector_trend` 545 万行的覆盖度统计 2.3 秒。'},
        'source': 'pg', 'requests': 0,
    }


# --- 板块趋势形成度与左侧低吸信号（读 `analytics.sector_trend_signal`）------

_REGIME_MEANING = {
    'insufficient': '点数不足：任一窗口的实际点数低于 0.7×窗口 —— 判据给不出结论，不是 0 分',
    'no_shape': '点数够，但方向/位置/广度没有凑成任一分档（与「数据不足」是两件事）',
    'downtrend': '退潮：中线与长线斜率同为负',
    'turn_up_seed': '刚转（左侧主看这一档）：短窗斜率近 3 日内由 ≤0 翻正、位置低、广度达标',
    'forming': '形成中：三窗口同向、位置仍在区间下半部',
    'established': '已确立：三窗口同向、位置已抬到上半部',
    'overheated': '过热：位置或热度分位进过热区 —— 排在「已确立」之前判，先说不该伸手',
}
_TERMS = ('dir_agree', 'persist', 'breadth', 'volume_ok', 'position', 'rank_momentum')


def sector_trend(date: str = '', regime: str = '', sector_type: str = '',
                 limit: int = 200, scope: str = 'sector', spec: str = '',
                 sort: str = 'dip') -> dict:
    """板块趋势形成度与低吸候选。**读现成的落库结果，接口自己不判**（判据只允许存在一处）。

    判据文档 `docs/页面展示工作指南/05-板块趋势与低吸判据.md`；计算在
    `app/compute/sector_trend_signal.py`。一行 = 一个板块 × 一个交易日 × 一个窗口集 × 一个判据版本。

    ⚠️ 三条不能糊：
    1. 分项为 NULL 就是当天算不出（缺涨跌家数的板块广度项），**NULL 不参与排名、0 参与**；
    2. `dip_score` 只在 `turn_up_seed`/`forming` 下有值，`reasons.weight_covered_pct` 会告诉你
       这个分数只用了多少权重 —— 覆盖率不足 100 时它是"按在场项归一"的结果，不是满分口径；
    3. 自选组（`scope=group`）只有**每日成员快照之后**的日期才是实盘序列；
       快照之前只能是 `scope=group_rebuilt`（以今日名单回算 = 未来函数）。
    """
    import datetime
    import json

    table = 'analytics.sector_trend_signal'
    d = _dash(date)
    if not d:
        r = _one(f'select max(trade_date) m from {table} where scope = %s', (scope,))
        d = str(r['m']) if r and r['m'] else ''
    if not d:
        out = _fail('板块趋势信号', table, None)
        out['hint'] = ('这张表由 `python -m app.compute.sector_trend_signal --backfill N` 生成；'
                       '空表不是「没有趋势」，是还没算过')
        return out

    lim = max(1, min(int(limit or 200), 1000))
    # 同一场可能存着多个判据版本的行（换版本不覆盖旧行是这条链的纪律）——
    # 页面默认看最新那一版，但两版的行数一起交出，别让"版本更迭"读成"数据被改写"。
    # 生效版本读 `config.setting`，不用 `max(spec_version)` —— 那是字符串比较，
    # 出现 sts-10 时会把 sts-1 当"最新版"端出来（字典序 sts-1 < sts-10 < sts-2）。
    eff_row = _one("select value from config.setting where key = 'sector_trend_signal.params'")
    eff = eff_row.get('value') if eff_row else None
    if isinstance(eff, str):
        try:
            eff = json.loads(eff)
        except ValueError:
            eff = None
    eff_spec = str((eff or {}).get('spec_version') or '')
    spec_row = _one(f'select max(spec_version) m from {table} where trade_date = %s and scope = %s', (d, scope))
    wanted = (spec or '').strip()          # 点名要看哪一版判据的结果；不给就用生效那一版
    spec = wanted or eff_spec or (spec_row or {}).get('m')
    if not spec:
        out = _fail('板块趋势信号', table, d, f'这一场（{scope}）没有已算好的行')
        return out
    where = ['s.trade_date = %s', 's.scope = %s', 's.spec_version = %s']
    args: list = [d, scope, spec]
    if regime:
        where.append('s.regime = %s')
        args.append(regime)
    if sector_type:
        where.append('i.sector_type = %s')
        args.append(sector_type)
    # 「档位表」按低吸分降序，「低吸榜」按 position 升序（越低位越前）—— 两榜排序方向相反，
    # 混成一屏会被读成"热度榜的另一种排法"。所以排序由后端两条路给，界面不许自己 reorder。
    order = ('s.position asc nulls last, s.dip_score desc nulls last'
             if (sort or '').strip() == 'seed' else
             's.dip_score desc nulls last, s.dir_agree desc nulls last, s.heat desc nulls last')
    rows = _rows(f"""
        select s.sector_code, coalesce(i.name, s.sector_code) as name, i.sector_type,
               s.window_set, s.spec_version, s.universe, s.regime, s.dip_score,
               s.dir_agree, s.persist, s.breadth, s.volume_ok, s.position, s.rank_momentum,
               s.heat, s.heat_pctile, s.slope_short, s.slope_mid, s.slope_long,
               s.points_short, s.points_mid, s.points_long, s.missing_terms, s.reasons,
               s.calculated_at
          from {table} s
          left join reference_data.sector_info i using (sector_code)
         where {' and '.join(where)}
         order by {order}
         limit %s""", (*args, lim)) or []
    dist = _rows(f"""select regime, count(*) n from {table}
                      where trade_date = %s and scope = %s and spec_version = %s group by regime
                      order by count(*) desc""", (d, scope, spec)) or []
    specs = _rows(f"""select spec_version, count(*) n from {table}
                       where trade_date = %s and scope = %s group by spec_version
                       order by spec_version""", (d, scope)) or []

    prow = _one("select value from config.setting where key = 'sector_trend_signal.params'")
    params = prow.get('value') if prow else None
    if isinstance(params, str):
        try:
            params = json.loads(params)
        except ValueError:
            params = None

    for r in rows:
        for k in _TERMS:
            r[k] = _num(r.get(k), 2)
        r.update({'dip_score': _num(r.get('dip_score'), 2), 'heat': _num(r.get('heat'), 2),
                  'heat_pctile': _num(r.get('heat_pctile'), 4),
                  'slope_short': _num(r.get('slope_short'), 4), 'slope_mid': _num(r.get('slope_mid'), 4),
                  'slope_long': _num(r.get('slope_long'), 4),
                  'points_short': _int(r.get('points_short')), 'points_mid': _int(r.get('points_mid')),
                  'points_long': _int(r.get('points_long')), 'missing_terms': _int(r.get('missing_terms')),
                  'universe': _int(r.get('universe')), 'calculated_at': str(r.get('calculated_at') or '')})
        if isinstance(r.get('reasons'), str):
            try:
                r['reasons'] = json.loads(r['reasons'])
            except ValueError:
                pass
        r['terms'] = {k: r.get(k) for k in _TERMS}

    snap = _one('select count(distinct trade_date) n from reference_data.watchlist_member_snapshot')
    snap_days = _int((snap or {}).get('n')) or 0
    # 快照"天数"不能直接信：按 `added_at` 反推出来的历史必然**只增不减**（库里没有删除记录），
    # 于是每一天的成员集都是今日的子集 —— 长得像实盘序列，实际是 rebuilt 换了件衣服。
    # 判据就是这条单调性，可重跑：见 `.local/snapshot_provenance.py`。
    snap_sets = _rows('select trade_date::text d, array_agg(security_code) codes '
                      'from reference_data.watchlist_member_snapshot group by 1 order by 1') or []
    snap_live, snap_why = True, ''
    if len(snap_sets) >= 2:
        latest = set((snap_sets[-1].get('codes') or []))
        extras = sum(1 for r in snap_sets[:-1] if not set(r.get('codes') or []) <= latest)
        mono = all(len(set(snap_sets[i].get('codes') or [])) <= len(set(snap_sets[i + 1].get('codes') or []))
                   for i in range(len(snap_sets) - 1))
        if extras == 0 and mono:
            snap_live = False
            snap_why = (f'{len(snap_sets)} 天里没有任何一天含"今日已不在"的成员，且人数逐日单调不减 —— '
                        '这是按 `config.watchlist_stock.added_at` 反推的特征（记录不了移除），'
                        '不能当实盘序列用；真·实盘要从今日起在交易日整链存')
    wlong = max([int(w) for w in (params or {}).get('windows', [21])] or [21])

    # 母体之外的档要**列出来**，不许静默丢掉：做打板的人看的就是「昨日涨停/连板/突涨」，
    # 实测它们全部落在 sector_type='指数' —— 天然不进「谁在领涨」的分母，但界面得说清为什么看不见它们。
    # 空榜要能往前找：低吸是稀有事件，"这一场没有"必须同时给出"最近一次有是哪一场、几个"，
    # 否则用户只能以为功能坏了。
    last_seed = _one("select trade_date d, count(*) n from analytics.sector_trend_signal "
                     "where scope = %s and spec_version = %s and regime = 'turn_up_seed' and trade_date <= %s "
                     "group by 1 order by 1 desc limit 1", (scope, spec, d))
    keep_types = list((params or {}).get('sector_types') or ['概念', '行业'])
    excluded = [{'sector_type': r.get('sector_type'), 'active': _int(r.get('n')),
                 'rows_today': _int(r.get('rn'))}
                for r in (_rows(
                    "select i.sector_type, count(distinct i.sector_code) as n, "
                    "count(s.sector_code) filter (where s.trade_date = %s and s.spec_version = %s) as rn "
                    "from reference_data.sector_info i "
                    "left join analytics.sector_trend_signal s "
                    "on s.sector_code = i.sector_code and s.scope = 'sector' "
                    "where i.is_active and not (i.sector_type = any(%s)) "
                    "group by 1 order by 2 desc", (d, spec, keep_types)) or [])]

    caveats = [
        {'code': 'single_judge', 'text': '档位与分数全部来自 `analytics.sector_trend_signal`，接口不做任何再判断'
                                         '（前后端各判一次就会分叉，这是这条链唯一的纪律）'},
        {'code': 'null_not_zero', 'text': '分项 NULL 表示当天算不出这一项：NULL 不参与排名、0 参与 —— '
                                          '把「缺」当 0 会让算不出的板块排到前面去'},
        {'code': 'prior_not_law', 'text': '低吸分权重（35/25/20/10/10）是可商量的先验、不是市场规律；'
                                          '改参数生成新的 spec_version 行，不覆盖旧行'},
        {'code': 'group_series', 'text': f'自选组成员快照累计 {snap_days} 天，{wlong} 日窗口需要 {wlong} 场 —— '
                                         f'不够时组这一档只能是 insufficient：这不是坏了，是还没长出来'},
    ]
    if scope == 'group_rebuilt':
        caveats.append({'code': 'future_function',
                        'text': '`scope=group_rebuilt` 的行是**以今日名单回算**的历史：用未来知道的名单做过去的决定，'
                                '属未来函数，不可用于回测与胜率统计，只能看形状'})

    return {
        'available': True, 'date': d, 'scope': scope,
        'window_set': (rows[0].get('window_set') if rows else '') or '',
        'spec_version': spec,
        'spec_versions_in_db': [{'spec_version': r.get('spec_version'), 'rows': _int(r.get('n'))} for r in specs],
        'params': params,
        'sort': (sort or 'dip').strip() or 'dip',
        'last_seed_session': (str(last_seed['d']) if last_seed and last_seed.get('d') else None),
        'last_seed_count': _int((last_seed or {}).get('n')),
        # 每行都要能被追问"这是哪一版参数算的"：读的版本不等于生效版本时，界面必须标出来（同"缓存必须带年龄"那条规矩）
        'effective_spec_version': str((params or {}).get('spec_version') or ''),
        'spec_is_effective': spec == str((params or {}).get('spec_version') or ''),
        # 母体口径必须由后端说，不能让界面自己数：active 的概念+行业有 1,907 个，
        # 而当日有热度行的只有几百个 —— 不交出这两个数，「前 N 名」的分母就会被读成 1,907。
        'universe': {'sectors': len({r['sector_code'] for r in rows}) or None,
                     'rows_returned': len(rows), 'limit': lim,
                     'active_total': _int((_one('select count(*) n from reference_data.sector_info '
                                                'where is_active and sector_type = any(%s)',
                                                (list((params or {}).get('sector_types') or ['概念', '行业']),)) or {}).get('n')),
                     'excluded_types': excluded,
                     'note': '母体 = 当日有热度行的板块（不是 active 的概念+行业总数）。'
                             '差额是热度表没覆盖的那批 —— 「前 N 名」的分母看 sectors，别看 active_total'},
        'regime_distribution_today': [{'regime': r.get('regime'), 'n': _int(r.get('n'))} for r in dist],
        'regime_meaning': _REGIME_MEANING,
        'terms_meaning': {'dir_agree': '三窗口斜率同向程度（同向 60 分 + 相对同日 60 分位的强度最多 40 分）',
                          'persist': '中窗口内热度逐日抬升的天数占比',
                          'breadth': '上涨家数占比 ×100（低于 breadth_min 判「普涨没起来」）',
                          'volume_ok': '量比得分：温和放量满分，超过 vol_range 上限反而降分',
                          'position': '收盘价在长窗口高低区间里的位置 ×100（低吸的核心一项）',
                          'rank_momentum': '热度排名相对 rank_lookback 日前的前进程度'},
        'rows': rows,
        'groups': {'snapshot_days': snap_days, 'sessions_needed': wlong,
                   'ready': bool(snap_live and snap_days >= wlong),
                   'live_trustworthy': snap_live, 'provenance_note': snap_why or None,
                   'by_date': [{'trade_date': r.get('d'), 'members': len(r.get('codes') or [])} for r in snap_sets]},
        'definitions': {'dip_score': '低吸分，只在 turn_up_seed / forming 两档下有值',
                        'reasons': '当时那一行的原始输入（斜率、点数、量比、位置、排名、翻正天数）——'
                                   '界面上「凭什么叫它刚转」念的就是它'},
        'coverage': _coverage(('sector_trend_signal',)),
        'caveats': caveats,
        'generated_at': datetime.datetime.now(datetime.timezone.utc).astimezone().isoformat(timespec='seconds'),
        'source': 'pg', 'requests': 0,
    }


def sector_members(sector: str = '', date: str = '', limit: int = 120) -> dict:
    """题材/板块的**成分股明细**：位置、区间涨幅、市值、换手、当日是否封住。

    为什么要单独给这张表：预设的 AI 题（"题材内部高低切的补涨挖掘"）要按
    "股价位置 / 涨幅大小 / 市值高低"分层，而热度榜只给到板块一级 —— 没有个股明细，
    模型只能老实回答"材料里没有个股"（2026-09-27 实测到就是这个）。

    三条口径必须跟着数据一起交出去：
      · `pos60` = 收盘价在近 60 个自然日高低价区间里的位置（0 贴地板、100 贴天花板），
        分母是 `bars`（不足 60 日的新票位置会虚高虚低，所以 `bars` 一起给）；
      · `reference_data.sector_member` 的 `in_date/out_date` 大量为空 —— **成分没有时点**，
        这是"按当前成分回看历史"，含幸存者偏差，不能当"当时确实这么分组"用；
      · 市值取当日 `stock_snapshot`，那天没快照就是 null，不拿今天的股本倒推历史。
    """
    table = 'reference_data.sector_member'
    d = _dash(date) or _latest('market_data.stock_daily') or ''
    if not d:
        return _fail('板块成分明细', table, None)
    if not (sector or '').strip():
        return _reject('板块成分明细', d, 'sector 参数为空 —— 要给板块代码或板块名')
    lim = max(1, min(int(limit or 120), 400))
    key = sector.strip()
    rows = _rows("""
        with m as (
            select mm.security_code
              from reference_data.sector_member mm
             where (mm.sector_code = %(sector)s
                    or mm.sector_code in (select sector_code from reference_data.sector_info where name = %(sector)s))
               and (mm.in_date is null or mm.in_date <= %(d)s::date)
               and (mm.out_date is null or mm.out_date >  %(d)s::date)
        ),
        win as (
            select d2.security_code,
                   count(*)                                        as bars,
                   max(d2.high)                                    as hi,
                   min(d2.low)                                     as lo,
                   (array_agg(d2.close order by d2.trade_date))[1] as close_first
              from market_data.stock_daily d2 join m on m.security_code = d2.security_code
             where d2.trade_date between (%(d)s::date - interval '60 day')::date and %(d)s::date
             group by d2.security_code
        ),
        cur as (
            select d3.security_code, d3.close, d3.pct_change, d3.turnover_rate_percent,
                   d3.amount_yuan, d3.limit_up
              from market_data.stock_daily d3 join m on m.security_code = d3.security_code
             where d3.trade_date = %(d)s::date
        )
        select m.security_code                                            as code,
               coalesce(s.name, m.security_code)                          as name,
               s.board, s.is_st,
               c.close, c.pct_change, c.turnover_rate_percent, c.amount_yuan,
               (c.close is not null and c.limit_up is not null
                 and abs(c.close - c.limit_up) <= 0.005)                  as sealed,
               w.bars,
               round((c.close - w.lo) / nullif(w.hi - w.lo, 0) * 100, 1)  as pos60,
               round((c.close / nullif(w.close_first, 0) - 1) * 100, 2)   as gain60,
               round(sn.float_market_value_yuan / 1e8, 2)                 as float_mcap_yi,
               sn.pe_ttm, sn.pb
          from m
          join cur c on c.security_code = m.security_code
          left join win w on w.security_code = m.security_code
          left join reference_data.security s on s.security_code = m.security_code
          left join market_data.stock_snapshot sn
                 on sn.security_code = m.security_code and sn.trade_date = %(d)s::date
         order by pos60 asc nulls last, gain60 asc nulls last
         limit %(lim)s
    """, {'sector': key, 'd': d, 'lim': lim})
    info = _one("""select sector_code, name, sector_type from reference_data.sector_info
                    where sector_code = %s or name = %s limit 1""", (key, key)) or {}
    total = _one("select count(*) n from reference_data.sector_member mm where (mm.sector_code = %s or mm.sector_code in (select sector_code from reference_data.sector_info where name = %s))", (key, key)) or {}
    if not rows:
        return _fail('板块成分明细', table, d, '这个板块当天没有可算的成分（代码或名称没对上，或成分股当天全停）')
    for r in rows:
        r.update({'bars': _int(r.get('bars')), 'is_st': bool(r.get('is_st')),
                  'close': _num(r.get('close'), 2), 'pct_change': _num(r.get('pct_change'), 2),
                  'turnover_rate_percent': _num(r.get('turnover_rate_percent'), 2),
                  'pos60': _num(r.get('pos60'), 1), 'gain60': _num(r.get('gain60'), 2),
                  'float_mcap_yi': _num(r.get('float_mcap_yi'), 2),
                  'pe_ttm': _num(r.get('pe_ttm'), 2), 'pb': _num(r.get('pb'), 2),
                  'amount_yuan': _num(r.get('amount_yuan'))})
    return {'available': True, 'date': d, 'sector': info.get('sector_code') or key,
            'sector_name': info.get('name') or key, 'sector_type': info.get('sector_type'),
            'rows': rows, 'total_members': _int(total.get('n')), 'limit': lim,
            'window_days': 60,
            'caveats': _caveats('sector_member', extra=(
                '成分表没有时点（in_date/out_date 大量为空）：这是按当前成分回看历史，含幸存者偏差。'
                'pos60 的分母是该股近 60 自然日在库里的 K 线根数，新股 bars 不足时位置不可比。'
                '市值取当日快照，缺快照的行是 null 不是 0。'))}


# ---- 自选股里的「新高」 ------------------------------------------------------
# 用户 2026-10-07 要在趋势评分页看自选分组里的票谁在创新高。先把本机数据现实钉死
# （三条都是 10-07 只读实测，不是推测）：
#   · `market_data.stock_daily` 只有 **2021-08-02 → 2026-09-30**（6,396,852 行 / 5,576 只 /
#     单只最多 1,253 场，最少 1 场）；
#   · 通达信本地 `.day` 也是**同一天起**（600519/000001/300750 逐一解析确认都是 2021-08-02
#     起 1,253 根）—— 客户端那时才开始下，本机没有更早的价格；
#   · `reference_data.security.list_date` **全表 NULL**（9,551 行零个有值）—— 上市日拿不到。
# 所以这一块算的是**库内新高**，界面必须这么写；把窗口拉到全部就宣称"历史新高"
# 是拿数据的边界当事实卖。只有第一根 K 线晚于库起点的那些票 `hist_truncated=false`，
# 才谈得上"本机看到了它在库里的全部日线"（仍然不等于上市以来）。
_HFQ = "({col} * coalesce(adjustment_factor, 1))"      # 与 `app/db/repo/quote.py::_ADJ_EXPR['hfq']` 同式
NEWHIGH_WINDOWS = (120, 250, 500, 750, 1000, 1250)     # 自然日；1250 ≈ 库内全部


def _first_session(table: str = 'market_data.stock_daily') -> str | None:
    """本机日线的第一天。走 `_agg` 缓存一小时：每一页都要用同一个数判"历史被截断没有"，
    而 `min(trade_date)` 在分区表上是全表级扫描（实测 0.3 秒，别每页付一次）。"""
    row = _agg(f'first_session:{table}', f'select min(trade_date)::text as d from {table}')
    return str(row['d'])[:10] if row and row.get('d') else None


def _new_high_peaks(peaks: list, day: str, days: int | None) -> dict:
    """补上"最高日是哪天、那天未复权收/高是多少"，只查**这一页**的票。

    为什么不塞进主聚合：主聚合那 1,093 只 × 1,253 根已经 4 秒，再加
    `array_agg(... order by ...)` 那种"取最大那行的其它列"直接变 **14.6~19.9 秒** ——
    10-07 逐句量的，第一版就是被它拖到撞 `statement_timeout=60s` 而整块取不到数。
    这一句按主键 `(security_code, trade_date)` 只扫这一页的票（实测 100 只约 4 秒），
    所以**也走 `_agg` 缓存**：母体与窗口不变时，一小时内第二次打开这一屏只付一次。
    并列最高取最早那天。`peaks` 是 `(code, 复权最高收, 复权最高高)` 的三元组，
    那两个峰值必须是**未取整的原值**（`::text` 交回来的），拿取整后的数去做等值匹配，
    实测 100 只有 58 行会匹配不上 —— 那就是"峰日读不到"的由来，不是数据缺。
    """
    if not peaks:
        return {}
    holders = ', '.join(['(%s::text, %s::numeric, %s::numeric)'] * len(peaks))
    params: list = []
    for c, pc, ph in peaks:
        params += [c, str(pc), str(ph)]
    params += [day, days, day, days]      # where 里四个位置：<= date、(days is null or >= date - days)
    adj_close = _HFQ.format(col='x.close')
    adj_high = _HFQ.format(col='x.high')
    # 键里带整页代码的指纹：只写第一只票的话，换一页（第一只没变）会命中同一份缓存，
    # 于是把上一页的峰日原样端给这一页 —— 那种错一格看不出来。
    fp = hashlib.md5(','.join(str(c) for c, _, _ in peaks).encode()).hexdigest()[:12]
    rows = _agg(f'new_high_peaks:{day}:{days}:{fp}', f"""with t(code, peak_close, peak_high) as (values {holders})
        select t.code,
               min(x.trade_date) filter (where {adj_close} = t.peak_close)   as peak_close_date,
               (array_agg(x.close order by x.trade_date)
                  filter (where {adj_close} = t.peak_close))[1]              as peak_close_raw,
               min(x.trade_date) filter (where {adj_high} = t.peak_high)     as peak_high_date,
               (array_agg(x.high order by x.trade_date)
                  filter (where {adj_high} = t.peak_high))[1]                 as peak_high_raw
          from t join market_data.stock_daily x on x.security_code = t.code
         where x.trade_date <= %s::date
           and (%s::int is null or x.trade_date >= %s::date - %s::int)
         group by t.code, t.peak_close, t.peak_high""", tuple(params), many=True)
    return {str(r['code']): r for r in (rows or [])}


def new_high(group_id=None, code: str = '', date: str = '',
             window: str = 'all', limit: int = 200, refresh: bool = False) -> dict:
    """自选（可按分组/单票）里的票：**谁在库内新高、谁离新高还差几个点**。

    四条口径随数据一起交出去：
      · 比较用**后复权价**（价 × `coalesce(adjustment_factor,1)`）。拿未复权价比历史高点，
        一次十送二十就能让"永远创不了新高"（实测 2025-07-29 比亚迪 337 → 111.42，
        那天 0.32817673 这个因子就是为了抹平这道断层）；
      · `window` = `all`（该股在库里的第一天起）或 120/250/500/750/1000/1250 自然日；
      · **新高只到本机历史起点**（`history_from`，实测 2021-08-02）：`hist_truncated=true`
        的那行意思是"这票在那天之前就有历史，本机看不到更早的价格"，不许读成历史新高；
      · `adjustment_factor` 为 NULL 的那些行按因子 1 参与计算，条数逐行交在
        `bars_without_factor` 里（自选 1,093 只里约 37 只有过这种行 —— 不是零）。
    判定是"目标日复权收盘 ≥ 窗口内复权最高"，用 `>=` 所以不需要容差。目标日没有行
    （停牌/退市/没入库）的票 `traded_on_date=false`，它只说明"那天没数"，不等于"没创新高"。
    """
    table = 'market_data.stock_daily'
    d = _dash(date) or _latest(table) or ''
    if not d:
        return _fail('自选股新高', table, None)
    lim = max(1, min(int(limit or 200), 500))
    win = str(window or 'all').strip().lower()
    days = None
    if win not in ('all', '', 'full'):
        try:
            days = int(win)
        except ValueError:
            days = -1
        if days not in NEWHIGH_WINDOWS:
            return _reject('自选股新高', d,
                           'window 只接受 all 或 ' + '/'.join(str(x) for x in NEWHIGH_WINDOWS)
                           + f'（自然日），收到 {window!r}')
    want = (code or '').strip()[:9]
    gid = int(group_id) if group_id not in (None, '', 0, '0') else None

    grp = _one("""select g.group_id, g.name, g.is_focus, count(w.security_code)::int as n
                    from config.watchlist_group g
                    left join config.watchlist_stock w using (group_id)
                   where g.group_id = %(gid)s::int
                   group by 1, 2, 3""", {'gid': gid}) if gid is not None else None
    if gid is not None and not grp:
        return _reject('自选股新高', d,
                       f'没有 group_id={gid} 这个自选分组（先在系统里同步同花顺自选）')

    uni = """(select distinct w.security_code as code
                from config.watchlist_stock w
                join config.watchlist_group g using (group_id)
               where (%(gid)s::int is null or g.group_id = %(gid)s::int)
                 and (%(want)s::text is null or w.security_code = %(want)s::text))"""
    base = {'gid': gid, 'want': want or None, 'd': d, 'days': days}
    # 母体分母只数自选清单本身。**不许在这里加 `exists(...)`**：那是给 1,093 只票各发一次
    # 分区表探测（实测把整块推到 60 秒 `statement_timeout` 之外），而"有数据的只数"
    # 主聚合的结果条数就是它，一分不差。
    total = _one(f"select count(*)::int n from {uni} u", base)
    if not total or not total.get('n'):
        return _fail('自选股新高', 'config.watchlist_stock', d,
                     '自选清单是空的（`config.watchlist_stock` 里没有记录，或这个组里没有票）')

    key = f'new_high:{gid}:{want}:{days}:{d}'
    if refresh:
        # `new_high` 这个前缀同时盖住主聚合与"这一页的峰日"两份缓存（`new_high_peaks:`）：
        # 只清一份的话，重算出来的新排行会配上旧峰日 —— 两个数各自都对，拼起来是错的。
        clear_agg_cache('new_high')
    agg = _agg(key, f"""
        with uni as {uni},
        per as (
            select sd.security_code                            as code,
                   count(*)                                    as bars,
                   min(sd.trade_date)                          as first_date,
                   max(sd.trade_date)                          as last_date,
                   max({_HFQ.format(col='sd.close')})          as peak_close,
                   max({_HFQ.format(col='sd.high')})           as peak_high,
                   count(*) filter (where sd.adjustment_factor is null) as bars_without_factor
              from market_data.stock_daily sd
              join uni on uni.code = sd.security_code
             where sd.trade_date <= %(d)s::date
               and (%(days)s::int is null or sd.trade_date >= %(d)s::date - %(days)s::int)
             group by sd.security_code
        ),
        cur as (
            select sd.security_code                            as code,
                   sd.close, sd.high, sd.pct_change,
                   {_HFQ.format(col='sd.close')}               as adj_close
              from market_data.stock_daily sd
              join uni on uni.code = sd.security_code
             where sd.trade_date = %(d)s::date
        )
        select p.code, coalesce(s.name, p.code)                as name,
               s.board, s.is_st,
               p.bars, p.first_date::text                      as first_date,
               p.last_date::text                               as last_date,
               round(c.close, 2)                               as close,
               round(c.high, 2)                                as high,
               round(c.pct_change, 2)                          as pct_change,
               round(p.peak_close, 3)                          as peak_close_adj,
               round(p.peak_high, 3)                           as peak_high_adj,
               p.peak_close::text                              as peak_close_exact,
               p.peak_high::text                               as peak_high_exact,
               round((c.adj_close / nullif(p.peak_close, 0) - 1) * 100, 2) as pct_from_peak,
               round((c.adj_close / nullif(p.peak_high, 0) - 1) * 100, 2)  as pct_from_high_peak,
               coalesce(c.adj_close >= p.peak_close, false)     as new_high_close,
               coalesce(c.adj_close >= p.peak_high, false)      as new_high_intraday,
               (c.code is not null)                            as traded_on_date,
               p.bars_without_factor
          from per p
          left join cur c on c.code = p.code
          left join reference_data.security s on s.security_code = p.code
         order by new_high_close desc nulls last, pct_from_peak desc nulls last,
                  p.bars desc nulls last, p.code""", base, many=True)
    if agg is None:
        return _fail('自选股新高', table, d)

    hist_from = _first_session(table)
    for r in agg:
        r['bars'] = _int(r.get('bars'))
        r['bars_without_factor'] = _int(r.get('bars_without_factor')) or 0
        for k in ('close', 'high', 'pct_change', 'pct_from_peak', 'pct_from_high_peak'):
            r[k] = _num(r.get(k), 2)
        for k in ('peak_close_adj', 'peak_high_adj'):
            r[k] = _num(r.get(k), 3)
        r['is_st'] = bool(r.get('is_st'))
        r['new_high_close'] = bool(r.get('new_high_close'))
        r['new_high_intraday'] = bool(r.get('new_high_intraday'))
        r['traded_on_date'] = bool(r.get('traded_on_date'))
        fd = str(r.get('first_date') or '')[:10]
        r['hist_truncated'] = bool(hist_from and fd and fd <= hist_from[:10])

    page = agg[:lim]
    peaks = _new_high_peaks([(r['code'], r['peak_close_exact'], r['peak_high_exact'])
                             for r in page], d, days)
    for r in page:
        pk = peaks.get(str(r['code'])) or {}
        r['peak_close_date'] = str(pk.get('peak_close_date') or '')[:10] or None
        r['peak_close_raw'] = _num(pk.get('peak_close_raw'), 2)
        r['peak_high_date'] = str(pk.get('peak_high_date') or '')[:10] or None
        r['peak_high_raw'] = _num(pk.get('peak_high_raw'), 2)

    groups = _rows("""select g.group_id, g.name, g.is_focus, count(w.security_code)::int as n
                        from config.watchlist_group g
                        left join config.watchlist_stock w using (group_id)
                       group by 1, 2, 3
                       order by n desc, g.display_order nulls last, g.group_id""") or []
    for g in groups:
        g['n'] = _int(g.get('n'))
        g['is_focus'] = bool(g.get('is_focus'))

    return {'available': True, 'date': d,
            'scope': ('group' if gid is not None else ('single' if want else 'all_watchlist')),
            'group_id': (grp or {}).get('group_id'), 'group_name': (grp or {}).get('name'),
            'group_count': len(groups), 'groups': groups,
            'window': 'all' if days is None else days,
            'window_label': '库内全部' if days is None else f'近 {days} 自然日',
            'history_from': hist_from,
            'universe_total': _int(total.get('n')),
            'universe_with_data': len(agg),
            'universe_without_data': max(0, (_int(total.get('n')) or 0) - len(agg)),
            # ⚠️ 这是**本页里**创新高的条数，不是"全部自选里有几只创新高"：
            # 母体 1,093 只、默认取 200 只时，把两个读法混了会少报十几倍。
            'new_high_in_rows': sum(1 for r in page if r['new_high_close']),
            'rows_returned': len(page), 'rows_total': len(agg),
            'cache_age_seconds': _agg_age(key),
            'rows': page, 'limit': lim,
            'caliber': ('母体＝自选清单（`config.watchlist_stock` ∩ `config.watchlist_group`，'
                        '可限定一个分组或一只票）；比较价＝后复权（价 × coalesce(adjustment_factor,1)）；'
                        f'窗口＝{"该股在库里的第一天起" if days is None else f"近 {days} 自然日"}；'
                        f'看得到的高只到本机历史起点 {hist_from or "（读不到）"} —— '
                        '这是库内新高，不是上市以来新高（`reference_data.security.list_date` 全表为空，'
                        '通达信本地日线也是同一天起）。'),
            'caveats': _caveats('stock_daily', extra=(
                f'本机日线最早 {hist_from or "读不到"}、最晚 {d}，所以"创了新高"读作'
                '"在本机这五年多的库里最高"，界面不许写成历史新高。'
                '`hist_truncated=true` 的行 = 该股在库起点之前就有历史、更早的价格看不到；'
                'false 的行才覆盖到它在库里的第一天（仍不等于上市日 —— 本机没有上市日字段）。'
                '复权因子为 NULL 的行按因子 1 参与计算，条数在 `bars_without_factor` 里点名。'
                '目标日没有行的票 `traded_on_date=false`，只说明"那天没数"；'
                '自选里有票但整个窗口一根 K 线都没有的计入 `universe_without_data`，不会静默消失。'))
            + _cache_caveat((key, f'first_session:{table}'))}


# ============================================================ 启动前量能画像

#: 三类母体的中文读法。**判据不住这里**（在 app/compute/startup_volume.py），这里只负责说清"这一屏是什么"。
#: `board4_45` 是 `board4` 的**子集**（同一批启动日，只是再加一道"前四根涨幅过门槛"），不是第四类票。
STARTUP_COHORTS = {
    'board4': '近 {months} 个月出现过 4 板及以上（连续涨停 ≥4 天）的票',
    'board4_45': '近 {months} 个月出现过 4 板及以上，且前四根累计涨幅大于 45%（基准=首板日前一天收盘）的票',
    'board4_or_gain45': '近 {months} 个月出现过 4 板及以上，或启动日起 4 个交易日累计涨幅大于 45%'
                        '（基准=启动日前一天收盘）的票 —— 两样占一样就入选',
    'jump40': '近 {months} 个月里，创业板/科创板/北证存在 4 日累计涨幅超四成的票',
}


STARTUP_SUM_SQL = f"""select count(*) n,
       round(avg(ratio_median), 4) med_of_median, round(avg(ratio_avg), 4) mean_of_avg,
       round(avg(ratio_p60), 4) mean_p60, round(avg(ratio_p80), 4) mean_p80,
       round(avg(ratio_p90), 4) mean_p90, round(avg(dbl_days), 2) mean_dbl_days,
       round(avg(up_days), 2) mean_up_days, round(avg(down_days), 2) mean_down_days,
       round(avg(still_days), 2) mean_still_days, round(avg(calm_days), 2) mean_calm_days,
       round(avg(equiv_limit_ups), 3) mean_equiv,
       round((percentile_cont(0.5) within group (order by ratio_p80))::numeric, 4) p80_of_p80,
       count(*) filter (where dbl_days >= 3) three_plus_dbl,
       count(*) filter (where bars_used < window_days) short_rows
  from analytics.startup_volume_profile p
 where p.spec_version = %(spec)s and p.cohort = %(coh)s
   and p.launch_date > (date %(to)s - (%(mon)s || ' months')::interval)"""


def startup_volume(cohort: str = 'board4', months: int = 3, limit: int = 200) -> dict:
    """启动前 30 根有效日线的量能画像（表 `analytics.startup_volume_profile`）。

    三类母体分开交，不合并成一榜：**「4 板」这个说法只在 10% 档成立**，
    双创 20%、北证 30%，同一门槛数出来几乎是空的（实测近 3 个月 4 板及以上 85 个样本里
    主板占绝大多数）。跨板块比强度用 `equiv_limit_ups`（等效几个涨停），不用原始涨幅。
    `board4_45`（用户 2026-10-15 加的那道"前四根涨幅 >45%"）是 `board4` 的**子集**：
    同一批启动日，只是再把"段长够、强度不够"的那批剔掉 —— 近 3 个月实测剔掉 0 条
    （75/75 全过，最低 46.24%，因为 4 个 10% 满涨停本来就是 46.41%），
    拉到近 12 个月会剔掉 67 条，全是 4 个 5% 涨停的 ST 票（前四根只有 21%~39%）。
    每一行都带 `bars_used`：不足 30 根是次新/长期停牌，**不能与满窗的票并排排名**，
    所以短窗的行会在 caveats 里点名，不是悄悄少几根就算完。
    每行另带一根 `mood_score`（−100~100 的当日大盘环境读数，同一场全市场同一个值），
    合成口径只住 `duanxian/pg_sources.py::mood_score()`，屏幕只读那一个数。
    """
    table = 'analytics.startup_volume_profile'
    spec = 'sv-2'
    coh = (cohort or 'board4').strip().lower()
    if coh not in STARTUP_COHORTS:
        return _reject('启动前量能画像', None,
                       f'cohort 只接受 {" / ".join(STARTUP_COHORTS)} 之一，收到 {cohort!r}')
    mon = max(1, min(int(months or 3), 12))
    lim = max(1, min(int(limit or 200), 1200))   # 上限按最深档的实际行数给（jump40 近 12 月 627 行）
    top = _one(f'select max(launch_date)::text d from {table} where spec_version = %s and cohort = %s',
               (spec, coh)) or {}
    d_to = top.get('d')
    if not d_to:
        return _fail('启动前量能画像', table, None, f'这一版（{spec}）还没有这一类母体的行：步骤没跑还是母体为空？')
    rows = _rows(f"""select p.security_code as code, coalesce(p.name, p.security_code) as name,
                          p.board, p.launch_date::text as launch_date, p.trigger_len,
                          p.trigger_gain, p.gain_first4, p.equiv_limit_ups, p.window_days, p.bars_used,
                          p.up_days, p.down_days, p.flat_days, p.still_days, p.dbl_days,
                          p.near_dbl_days, p.calm_days, p.long_gap_days,
                          p.ratio_avg, p.ratio_median, p.ratio_p60, p.ratio_p80, p.ratio_p90,
                          p.ratio_max, p.ratio_min, p.std_days, p.ratios
                     from {table} p
                    where p.spec_version = %s and p.cohort = %s
                      and p.launch_date > (date %s - (%s || ' months')::interval)
                    order by p.launch_date desc, p.dbl_days desc nulls last, p.ratio_p80 desc nulls last
                    limit %s""", (spec, coh, d_to[:10], str(mon), lim))
    total = _one(f'select count(*) n, '
                 'count(*) filter (where p.bars_used < 10) below_min, '
                 '(date %s - (%s || \' months\')::interval)::date::text win_from '
                 f'from {table} p where p.spec_version = %s and p.cohort = %s '
                 'and p.launch_date > (date %s - (%s || \' months\')::interval)',
                 (d_to[:10], str(mon), spec, coh, d_to[:10], str(mon))) or {}
    # 这一版实际算到哪一天：不查这一条，"近 12 个月"在库里只有 3 个月批次的情况下
    # 会交出一份贴着 12 个月标签、其实只有 3 个月名单的榜 —— 屏幕上看不出区别。
    span = _one(f'select min(launch_date)::text a, max(launch_date)::text b, count(*) n '
                f'from {table} where spec_version = %s and cohort = %s', (spec, coh)) or {}
    # 整组汇总在**服务端按同一批过滤条件**算：页面自己再 average 一遍就会出现
    # "排完序截断后的均值"和"整组均值"两个数打架（本项目在别处踩过同名坑）。
    summ = _one(STARTUP_SUM_SQL, {'spec': spec, 'coh': coh, 'to': d_to[:10], 'mon': str(mon)}) or {}
    for k in ('n', 'three_plus_dbl', 'short_rows'):
        summ[k] = _int(summ.get(k))
    for k in [x for x in summ if x not in ('n', 'three_plus_dbl', 'short_rows')]:
        summ[k] = _num(summ.get(k), 4)
    for r in rows:
        for k in ('trigger_len', 'window_days', 'bars_used', 'up_days', 'down_days', 'flat_days',
                  'still_days', 'dbl_days', 'near_dbl_days', 'calm_days', 'long_gap_days'):
            r[k] = _int(r.get(k))
        for k in ('ratio_avg', 'ratio_median', 'ratio_p60', 'ratio_p80', 'ratio_p90',
                  'ratio_max', 'ratio_min', 'std_days', 'equiv_limit_ups'):
            r[k] = _num(r.get(k), 4 if k != 'equiv_limit_ups' else 3)
        r['trigger_gain'] = _num(r.get('trigger_gain'), 4)
        r['gain_first4'] = _num(r.get('gain_first4'), 4)
        # 逐日序列：jsonb 里存的是 [{d,v}]，v 可能为 null（那天没有量），原样交出不补 0
        seq = r.get('ratios')
        if isinstance(seq, str):
            try:
                import json as _json
                seq = _json.loads(seq)
            except Exception:  # noqa: BLE001
                seq = []
        r['ratios'] = [{'d': (p or {}).get('d'), 'v': _num((p or {}).get('v'), 4)} for p in (seq or [])]
        # 倍量区间统计：数这 30 根里落在每个区间的天数（用户 2026-10-11 要的）
        vals = [p['v'] for p in r['ratios'] if p['v'] is not None]
        # 五档区间的判据只住 `startup_volume.range_counts()` 一处（与「单票量价相似样本」共用）
        r.update(SV.range_counts(vals))
    short = [r['code'] for r in rows if r['bars_used'] is not None and r['window_days']
             and r['bars_used'] < r['window_days']]
    # 「情绪」那一列（用户 2026-10-15：启动日后面加大盘与环境的情绪，−100~100，依据放列名说明）。
    # 读法与合成分**只住 `pg_sources`**一处：这一屏不重算，与连板榜「首板日大盘情绪」读同一张
    # `analytics.market_sentiment_daily`。整批读不回来（库不通/查询坏）与"那几天没有行"是两件事，
    # 前者必须点名链路坏了，不许让屏幕把"没读到"念成"那几天没情绪"。
    moods = pg_sources.sentiment_on_dates([r.get('launch_date') for r in rows]) or {}
    mood_failed = bool(moods.get('failed'))
    mood_missing = []
    for r in rows:
        m = pg_sources.mood_score(moods.get(str(r.get('launch_date') or '')[:10]))
        r.update(m)
        if m.get('mood_score') is None:
            mood_missing.append(r['code'])
    mood_days = sorted({str(r.get('launch_date'))[:10] for r in rows if r.get('launch_date')})
    gaps = [r['code'] for r in rows if (r['long_gap_days'] or 0) > 0]
    caveats = _caveats('coverage', extra=(
        '这一屏是「启动前长什么样」的描述统计，不是买卖信号，也不回答"放量就会涨"：'
        '母体本身就是"已经涨起来的票"，拿它的启动前分布去推"任意票放量后会不会涨"是选择偏差。'
        '要给放量做预测，得反过来取全体票的启动前分布当对照。'))
    if coh in ('board4', 'board4_45'):
        caveats.append({'code': 'limit_band',
                        'text': '连板判定读的是 `market_data.stock_daily.limit_up`。这一列 2026-10-07 起改按'
                                '「当日证据」定档（`app/compute/limit_prices.py`：通达信当日报价反推 > 行情可证非 ST '
                                '> ST 区间表 > 规则），收盘价越过推导涨跌停价的行从 7,108 降到 47；'
                                '判据没改、读的行情变了，所以版本从 sv-1 升到 sv-2（旧 6 条 ST 样本留在 sv-1 里对账，'
                                '不与新批并排）。还欠的一块是 151,891 行／145 只只有当前 is_st 快照支撑'
                                '（`limit_basis=rule_st_snapshot`）：近 3 个月的样本窗口里已经数不到这种行，'
                                '把回溯拉到 6／12 个月时，2026-08-27 之前那批 ST 票的板数仍可能是猜的档位。'})
    if short:
        caveats.append({'code': 'short_window',
                        'text': f'这些票启动前凑不满 30 根有效日线（次新或长期停牌），'
                                f'分位数与满窗的票不可横比：{"、".join(short[:8])}'
                                + ('…' if len(short) > 8 else '')})
    if gaps:
        caveats.append({'code': 'resumed_gap',
                        'text': f'这些票的窗口里有"上一根隔了 >10 个自然日"的日子（停牌复牌），'
                                f'那几天的倍数分母很久以前的量，偏大不是异常：{"、".join(gaps[:8])}'
                                + ('…' if len(gaps) > 8 else '')})
    below = [r['code'] for r in rows if r['bars_used'] is not None and r['bars_used'] < 10]
    n_below = _int(total.get('below_min')) or 0
    if n_below or below:
        named = '、'.join(below[:8]) if below else '（这些行排在后面，本页没显示到）'
        caveats.append({'code': 'min_bars',
                        'text': f'这一批里有 {n_below or len(below)} 行凑不满 10 根倍数（判据下限 MIN_BARS），'
                                f'八个分布读数（均值/中位/60·80·90 分位/最大/最小/波动）按口径一律留空 —— '
                                f'但增/减/持平那几列是照实数的，所以"天数有值、分布为空"是设计而不是缺数：'
                                f'{named}'})
    if span.get('a') and total.get('win_from') and str(span['a']) > str(total['win_from']):
        caveats.append({'code': 'profile_window',
                        'text': f'这一版母体最早只算到 {span["a"]}，而 {mon} 个月窗口的起点是 {total["win_from"]} —— '
                                f'也就是说这一屏挂着"{mon} 个月"的标签，实际只覆盖 {span["a"]} 之后那一段，'
                                f'不是本来就没有更早的票。补法：`python -m app.compute.startup_volume '
                                f'--months {mon} --end {d_to[:10]}`。'})
    if mood_failed:
        caveats.append({'code': 'mood_off',
                        'text': '情绪那一列这次整批读不回来（`analytics.market_sentiment_daily` 不可访问或查询失败）——'
                                '整列会是空。空着说的是"没读到"，不是"那几天没有情绪"。'})
    elif mood_missing:
        caveats.append({'code': 'mood_gap',
                        'text': f'这 {len(mood_missing)} 只票的启动日在档位表里没有行、或宽度与题材两档缺一档，'
                                f'情绪那一格按口径留空（不是 0，0 在这把尺子上是「中性」）：'
                                f'{"、".join(mood_missing[:8])}' + ('…' if len(mood_missing) > 8 else '')})
    if coh == 'board4_45':
        # 用户 10-15 新加的那道门槛在当前窗口里到底筛没筛掉东西，必须让屏幕自己说，
        # 不能让人以为"两个数一样"是页面坏了。
        from app.compute import startup_volume as sv
        cut_pct = '%g%%' % (sv.GAIN_FIRST4_MIN * 100)
        base = _one(f'select count(*) n from {table} where spec_version = %s and cohort = %s '
                    'and launch_date > (date %s - (%s || \' months\')::interval)',
                    (spec, 'board4', d_to[:10], str(mon))) or {}
        n4, n45 = _int(base.get('n')), _int(total.get('n'))
        cut = (n4 - n45) if (n4 is not None and n45 is not None) else None
        caveats.append({'code': 'gain_gate',
                        'text': f'这一档就是「board4」里前四根涨幅大于 {cut_pct}（基准=首板日前一天收盘）'
                                f'的那批：本窗口 board4 {n4} 条、这一档 {n45} 条，筛掉 {cut} 条。'
                                + ('两个数相等时不是页面坏了 —— 4 个 10% 满涨停本来就是 +46.41%，'
                                   '所以近期窗口里这道门槛一条都筛不掉；把它筛得掉的样本是 4 个 5% 涨停的 ST 票'
                                   '（前四根只有 21%~39%），实测拉到 12 个月是 356 → 289、筛掉 67 条。'
                                   if cut == 0 else '')
                                + '每一行的前四根涨幅就在「前4日涨幅」那一列，可以自己核门槛。'})
    definitions = dict(_comments('startup_volume_profile') or {})
    definitions.update({
        'mood_score': pg_sources.MOOD_SCALE_NOTE,
        'mood_breadth': '启动日那天的宽度档原值（冰点/偏弱/中性/偏强/普涨），来自 '
                        'analytics.market_sentiment_daily.breadth_level。合成分里它占 50%',
        'mood_theme': '启动日那天的题材档原值（冰点/普通/活跃/亢奋），来自 theme_level。合成分里它占 50%。'
                      '库里这一族没有 0 档，所以平淡日常常算出 −17 而不是 0',
        'mood_parts': '上面两档各自归到 −100~100 后的中间值（宽度 ÷2×100、题材 ÷1.5×100），'
                      '合成分 = 两者各一半。把它摆出来是为了让人能按别的权重自己重算',
    })
    return {'available': True, 'cohort': coh, 'cohort_text': STARTUP_COHORTS[coh].format(months=mon),
            'months': mon, 'window_days': 30, 'spec_version': spec,
            'profile_earliest_launch': span.get('a'), 'profile_rows_total': _int(span.get('n')),
            'window_from': total.get('win_from'),
            'latest_launch': d_to, 'rows_returned': len(rows), 'rows_total': _int(total.get('n')),
            'limit': lim, 'summary': summ, 'definitions': definitions,
            # 那一列情绪的现场坐标：读了哪几天、缺几个、合成分权重与各档极值（都只住 pg_sources）
            'mood': {'days': len(mood_days), 'missing': len(mood_missing),
                     'failed': mood_failed, 'weights': pg_sources.MOOD_WEIGHTS,
                     'extremes': pg_sources.MOOD_EXTREMES,
                     'source': 'analytics.market_sentiment_daily（msd-1；与连板榜「首板日大盘情绪」同源）'},
            'caliber': ('母体＝' + STARTUP_COHORTS[coh].format(months=mon) +
                        '；窗口＝启动日之前该股自己的 30 根有效日线（停牌不占名额）；'
                        '倍数＝当日成交量 ÷ 上一根日线成交量（读 analytics.volume_ratio_daily，vr-1）；'
                        '倍量门槛 ≥2.0（用户给定），准倍量 1.8~2.0、缩量 ≤0.5、"量没变" 0.95~1.05；'
                        '分位数用线性插值（同 percentile_cont），不是取第 k 个样本；'
                        '跨板块强度对齐用 equiv_limit_ups = ln(1+区间涨幅)/ln(1+档位)。'),
            'rows': rows, 'caveats': caveats}


# ------------------------------------------------------------------- 埋伏票（左侧机会）榜

def ambush(date: str = '', limit: int = 150, types: str = '',
           only_strict: bool = False, min_weekly_ups: int = 0) -> dict:
    """埋伏票榜（`analytics.ambush_setup`，判据只住 `app/compute/ambush_setup.py`）。

    类型多选**必须在库里筛**（拼在 `order by … limit` 之前）：这一榜按合成分截断，
    在前端筛就只能筛到"分数最高的那 150 只"，会把"这一类只剩几行"读成"全场只有几类"。
    每类全场实有几只随 `type_options` 一起交出（那条聚合不带类型条件、不受 limit 影响）。

    `types` 里认不得的值不参与筛选并当场点名（`filter.types_rejected` + caveat）——
    静默丢掉等于交一份"看着像筛过了"的榜。
    """
    from app.compute import ambush_setup as amb

    table = 'analytics.ambush_setup'
    d = _dash(date) or _latest(table) or ''
    if not d or not pg.session_has(table, d):
        return _fail('埋伏票', table, d or None)
    lim = max(1, min(int(limit or 150), 800))

    picked, rejected = [], []
    for cell in str(types or '').replace('，', ',').split(','):
        t = cell.strip()
        if not t:
            continue
        if t in amb.TYPE_LABELS:
            if t not in picked:
                picked.append(t)
        elif t not in rejected:
            rejected.append(t)
    min_ups = max(0, min(int(min_weekly_ups or 0), 10))

    # 只放筛选条件自己的参数；目标日与 limit 在调用处首尾各加一次
    cond, params = '', []
    if picked:
        cond += ' and a.ambush_types && %s::text[]'
        params.append(picked)
    if only_strict:
        cond += ' and a.strict_pass'
    if min_ups:
        cond += ' and coalesce(a.weekly_ups6, 0) >= %s'
        params.append(min_ups)

    rows = _rows(f"""select a.security_code as code, coalesce(s.name, a.security_code) as name,
                 s.board, a.close_hfq, a.ma_spread_pct, a.range20_pct,
                 a.weekly_ups6, a.weekly_streak, a.weekly_bars,
                 a.week_sessions_cur, a.week_partial,
                 a.shrink_ratio, a.box_pos_pct, a.trend60_pct, a.obv_ratio,
                 a.days_since_big, a.hold_vs_big_pct, a.big_day_date,
                 a.ambush_types, a.strict_pass, a.ambush_score,
                 a.bars_60, a.missing, a.spec_version, a.calculated_at,
                 a.ma5, a.ma10, a.ma20, a.ma30, a.ma60,
                 a.dev_ma20_pct, a.dev_ma60_pct, a.big_low_hfq, a.big_close_hfq
            from analytics.ambush_setup a
            left join reference_data.security s using (security_code)
           where a.trade_date = %s{cond}
           order by a.strict_pass desc nulls last, a.ambush_score desc nulls last,
                    a.security_code
           limit %s""", [d] + params + [lim])
    for r in (rows or []):
        for k in ('close_hfq', 'ma_spread_pct', 'range20_pct', 'shrink_ratio', 'box_pos_pct',
                  'trend60_pct', 'obv_ratio', 'hold_vs_big_pct', 'ambush_score',
                  'dev_ma20_pct', 'dev_ma60_pct', 'big_low_hfq', 'big_close_hfq',
                  'ma5', 'ma10', 'ma20', 'ma30', 'ma60'):
            r[k] = _num(r.get(k), 3 if k != 'obv_ratio' else 4)
        for k in ('weekly_ups6', 'weekly_streak', 'weekly_bars', 'week_sessions_cur',
                  'days_since_big', 'bars_60'):
            r[k] = _int(r.get(k))
        r['strict_pass'] = bool(r.get('strict_pass'))
        r['week_partial'] = None if r.get('week_partial') is None else bool(r.get('week_partial'))
        r['ambush_type_labels'] = [amb.TYPE_LABELS.get(t, t) for t in (r.get('ambush_types') or [])]

    # 每一类全场实有几只：不带类型条件、不受 limit 影响（同倍量榜 quadrant_counts 一条规矩）
    counts: dict[str, int] = {}
    counts_ok = True
    try:
        for r in _rows("""select t.value as t, count(*)::int as n
                     from analytics.ambush_setup a, lateral unnest(a.ambush_types) as t(value)
                    where a.trade_date = %s group by 1""", (d,)) or []:
            if r.get('t') in amb.TYPE_LABELS:
                counts[str(r['t'])] = int(r['n'] or 0)
    except Exception:  # noqa: BLE001  读不到就说读不到，不拿 0 顶（0 会被读成"这一类今天没有票"）
        counts_ok = False

    # 只报表里真有的两个数。⚠️ 不报"满窗母体"：这张表只落命中的票，
    # 从它自己数不出"全场 5,526 只里有多少满窗"，硬数就是一个假数（那一句该由取数侧说）。
    tot = _one("select count(*)::int n, count(*) filter (where strict_pass)::int strict"
               "  from analytics.ambush_setup where trade_date = %s", (d,)) or {}
    caveats = _caveats('coverage') + _cache_caveat(('coverage:ambush_setup',))
    if rejected:
        caveats.append({'code': 'ambush_type_rejected',
                        'text': '埋伏类型筛选里这些值不是已登记的类型：'
                                + '、'.join(rejected)
                                + ' —— 它们没有参与筛选（否则榜看着像筛过了其实没筛）。可用的是 '
                                + '、'.join(f'{t}（{amb.TYPE_LABELS[t]}）' for t in amb.TYPE_LABELS)})
    if not counts_ok:
        caveats.append({'code': 'ambush_counts_unread',
                        'text': '每一类全场实有几只没读到（那条聚合失败了），界面按"没读到"显示，'
                                '不拿 0 顶 —— 0 的意思是"这一类今天一只都没有"。'})
    if not rows:
        caveats.append({'code': 'ambush_empty',
                        'text': '这一场在当前筛选条件下没有票（不是库里没有埋伏票：'
                                f'当天共 {int(tot.get("n") or 0)} 只命中过至少一种形态）。'})
    return {'available': True, 'date': d, 'source': 'pg',
            'spec_version': amb.SPEC_VERSION,
            'filter': {'types': picked, 'types_rejected': rejected,
                       'only_strict': bool(only_strict), 'min_weekly_ups': min_ups,
                       'rows_returned': len(rows or []), 'limit': lim},
            'type_options': [{'value': t, 'label': amb.TYPE_LABELS[t],
                              'count': (counts.get(t, 0) if counts_ok else None),
                              'meaning': amb.TYPE_MEANINGS.get(t, '')}
                             for t in amb.TYPE_LABELS],
            'thresholds': {'spread_max': amb.SPREAD_MAX, 'range20_max': amb.RANGE20_MAX,
                           'weekly_ups6_min': amb.WEEKLY_UPS6_MIN,
                           'weekly_min_bars': amb.WEEKLY_MIN_BARS,
                           'shrink_max': amb.SHRINK_MAX, 'bars_min': amb.BARS_MIN},
            'day_total': _int(tot.get('n')), 'day_strict_total': _int(tot.get('strict')),
            'caliber': ('埋伏分只为排序（0~100，不是涨跌概率）：紧（均线极差 '
                        + f'{amb.SPREAD_MAX}% 以下最满）{amb.SCORE_WEIGHTS["tight"]:.0f} 分、'
                        '周线重心抬升 ' + f'{amb.SCORE_WEIGHTS["weekly"]:.0f} 分、'
                        '缩量 ' + f'{amb.SCORE_WEIGHTS["quiet"]:.0f} 分、'
                        '箱体位置（贴内而不脱沿）' + f'{amb.SCORE_WEIGHTS["box"]:.0f} 分；'
                        '缺的那一块从分母里剔掉，不按 0 计。'
                        '价格一律后复权（close × adjustment_factor）；'
                        '周线按 ISO 周分桶，当前周不足 5 个交易日不计入连升（会显示这一周几天）。'),
            'definitions': _comments('ambush_setup'),
            'coverage': _coverage(('ambush_setup',)),
            'caveats': caveats,
            'rows': rows or []}
