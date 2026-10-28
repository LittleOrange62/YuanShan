# -*- coding: utf-8 -*-
"""`analytics.first_board_daily`（fbd-1）· 「首板分析」的写侧。

用户 2026-10-21 点名的那一屏（位置：首页「全市场换手率 TOP20」上面）：
「分析首板个股的情况，以及近期的走势，分析涨停原因，特别是前一天的走势」。
本模块只负责**算出来并落库**（读侧接口与前端由用户本人接）。

六条写在旁边的口径（DDL 的头部有同一份，改一边要改另一边）：

1. **母体只有一把尺**：`analytics.first_board_shape`（`spec_version='fb-2'`）里 `launch_date` 等于
   本场的那些行 —— 本地日线 `close >= limit_up - 0.01` 且是连板段第一天，当天收盘就能构造、不筛结果。
   本模块**不另起一把「首板」判据**，也不去问财/东财的池子里认母体（池子是源站口径，见第 3 条）。
   母体那张表的日期列叫 `launch_date`（没有 trade_date 列），本表按"每日一场"的习惯叫 `trade_date`。
2. **当日读数逐字搬母体**：`launch_pct` / `launch_turnover` / `launch_amount_yuan` / `is_one_word`
   与近 25 场口径的 `vol_grab_ratio`（＝母体 `pre5`，是**量**能抢跑比不是涨幅）、`dbl_days`、`calm_days`
   都从母体那一行直接取，一个都不重算 —— 两处各算一遍必然飘出第二个数，而屏幕上只有一条格子。
   只有 `launch_close` / `limit_up_price` 是本地日线原值（未复权，两列同尺才看得出离档位差几分）。
3. **涨停原因/封板细节来自同花顺涨停揭秘池，且这条路会过期**：题材串走
   `duanxian/fetchers.fetch_zt_reasons`（本机没有 IWENCAI_API_KEY ⇒ 落
   `duanxian/historical_sources.public_limit_reasons`），封板时间/炸板次数/封单/流通市值走**同一个端点的整池原始行**
   （那个函数只留题材串、把其余字段丢掉了，所以这里自己拉一次；拉回来照样逐行核对
   `first_limit_up_time` 落在本场，日期不符/一只都没有就整场判为取数失败，不"匹配不到就放行"）。
   ⚠️ **保留窗实测约一年**（2026-10-21 回填 40 场时现探）：2026-08-19…10-21 每场都拉得到池子、
   2025-11-03 还有 66 只、**2025-09-22 起 status_code 已经非 0** ⇒ 超出窗就是真取不到，
   漏采仍按**永久缺**处理（那一场只能 NULL + `missing` 点名，不许拿别的交易日的题材串冒充）。
   ⚠️ 先前测的「20260925 返回 0 行」不是过期，是**那天不是交易日**（2026-09-25 中秋，
   本地 `stock_daily` 那天 0 行）—— 别照"只保留最近几场"去安排采集频率。
   ⚠️ **池子不收的三类要分开说**（同一次回填 1,911 行实测）：北交所 23 只首板 0 只有题材串
   （`filter=HS,GEM2STAR` 压根不覆盖）、ST 82 只里只有 1 只有（源站基本不收 ST 涨停）、
   其余 1,806 只非 ST 里 1,792 只有（99.2%）。所以"题材串空着"绝大多数是**源站不收**
   （`source_row_absent`）而不是漏采（`source_no_records`）—— 界面上这是两句话。
   首封时间/炸板次数走这一条源，**不依赖本地分钟线**（`market_data.stock_minute` 只到 2026-10-19，
   10-20/10-21 整天没有分钟数据）。实测池内 50 只里 27 只 `open_num` 整格为空（那些行 change_tag
   一律是 FIRST_LIMIT）—— 看着像 0，但那是源站没写，**不折算成 0**：留 NULL 记 `source_field_null`。
4. **几天几板只住 `duanxian/board_span.py`**，近 N 场涨幅只住 `duanxian/day_returns.py`，
   倍量加权分只住 `analytics.volume_double`（当日行 + `energy_fp` 指纹）。三处都是现成的口子，
   本模块只调用不另算。注意 `boards`（允许中间空 1 天）与母体 `seg_len`（严格连续，首板当天恒为 1）
   不是一把尺子，两列都留；`source_boards_agree` 只把本地与源站 `high_days` 的**不一致**点出来，
   谁也不覆盖谁（源站那串文本解析不出板数就交 NULL，绝不拿它去顶本地 boards）。
5. **前一日是这一屏的主角**（用户原话"特别是前一天的走势"）：涨幅/换手/成交额取 `stock_daily` 原值，
   量比＝前一日成交量 ÷ 前一日往前 5 场的均量（**不含前一日**）。形状七档的阈值只住本模块
   `PREV_SHAPE_THRESHOLDS` 一处，定在实测分位上（2026-10-21 现查近 25 场、1,094 只首板：
   前日涨幅 p10 −5.05 / p25 −2.94 / p50 −0.51 / p75 +1.55 / p90 +3.51；
   前日量比 p25 0.77 / p50 1.00 / p75 1.40 / p90 2.04；前日换手中位 3.84%；
   触板炸开 34/1,094＝3.1%；≤−5% 113 只＝10.3%；≥+5% 未封 64 只＝5.8%；**收盘封住 0 只**）。
   最后那一条是首板定义带来的**不变量**：`prev_sealed` 为 true 的行数必须恒等于 0，
   `verify()` 钉死它 —— 不为 0 就是母体串了（或档位/容差变了），查因，不要改判据。
6. **缺数交 NULL，不交 0**：0 在这里是真实读数（炸板 0 次、前一日平盘），NULL 才是"这一格判不了"。
   每一格为什么空都记进 `missing`（键名＝列名，值是短码），屏幕才能老实显示"漏采永久缺"。
   形状判据同理：所需输入任一为 NULL 且更高优先级的档还排除不掉，就整格交 NULL，**不许硬猜**。

用法：
    python -m app.compute.first_board_daily --date 2026-10-21            # 只算这一场
    python -m app.compute.first_board_daily --recent 40                  # 最近 40 场
    python -m app.compute.first_board_daily --since 2026-08-14           # 从这一天到库里最新一场
    python -m app.compute.first_board_daily --recent 5 --dry-run         # 只算不写
    python -m app.compute.first_board_daily --verify                     # 体检（不打网络）
退出码：0 写了 / 1 一条都没写成 / 2 按设计不写（区间里没有首板事件）/ 3 写了但体检不过。
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import requests                                                 # noqa: E402

from app.db import engine                                       # noqa: E402
from app.compute import limit_up_pool                            # noqa: E402  同一次出网喂两张表
from duanxian import board_span, day_returns, fetchers          # noqa: E402
from duanxian import historical_sources as hsrc                 # noqa: E402

TABLE = 'analytics.first_board_daily'
#: `fbd-2`（2026-10-27）：加四列"这只票自己的历史位置"（涨/跌停次数、连板最高、两年区间位置、
#: 均线排列）。加列不改旧列口径，但读侧要能分清"这批场次有没有这四列" ⇒ 升版本号，43 场全部重算。
#: 旧行 `fbd-1` 留在表里对账（与异动提醒 `abn-1` 同一条处置）。
SPEC_VERSION = 'fbd-2'

#: 母体：`analytics.first_board_shape` 的判据版本。升 fb-3 时这一行要一起动（母体换尺＝本表换母体）
SHAPE_SPEC = 'fb-2'
#: 封板容差：**与母体 EVENTS_SQL 同一条**（`close >= limit_up - 0.01`）。
#: 这里换成 board_span 的 0.001 会让"前一日封住"这条自检漏掉 0.001~0.01 那档，母体串了也看不见。
LIMIT_SEAL_EPS = 0.01
PREV_BASE_BARS = 5        # 前一日量比的分母：前一日往前几场的均量（不含前一日）
PREV_BASE_MIN = 3         # 分母里真有的行少于 3 根就不给量比（次新股/长停牌凑不出"近 5 场均量"）
CHG_SPANS = (5, 10, 15)   # 近期走势三档（day_returns 的 span，含目标日）

# ---- 2026-10-27 用户点名的四列「这只票自己的历史位置」------------------------------------
# 三个窗口都按**市场场次**数（与 `prev_readings` 同一把尺）：停牌的日子不占位，
# 所以"3 个月"是 63 场而不是 90 个自然日 —— 拿自然日算会把长假整段算进窗口。
HIST_3M_SESSIONS = 63       # ≈3 个月：涨/跌停次数
HIST_6M_SESSIONS = 126      # ≈6 个月：连板最高（读 lup-1 的 consec，不自己数）
HIST_2Y_SESSIONS = 489      # ≈2 年：区间位置与四条均线共用这一发窗口
POS_MIN_BARS = 250          # 两年窗口里真有的日线不足 250 根 ⇒ 位置判不了（没有两年可说）
MA_SPANS = (5, 10, 20, 60)  # 均线排列用的四条；60 是这一档的下界，缺它就判不了

#: 均线排列五档的短码 → 四个字的名字（表格里那一格）与长句（悬停）。
#: 两串都住写侧：界面不许自己给某一档另起名字（`prev_shape` 那七档同一条纪律，两屏会分叉）。
MA_STACK_NAMES: Dict[str, str] = {
    'bull_strong': '四线多头',
    'bull_under_ma60': '反弹压季线',
    'tangled': '均线纠缠',
    'pullback_ma60': '回调托季线',
    'bear_strong': '四线空头',
}
MA_STACK_LABELS: Dict[str, str] = {
    'bull_strong': 'MA5 > MA10 > MA20 > MA60，四条线从短到长依次在上：趋势全线向上，'
                   '今天的板是接力在多头排列上',
    'bull_under_ma60': 'MA5 > MA10 > MA20（短期已经金叉向上），但季线 MA60 还压在 MA20 上方：'
                       '这是一次还没走出季线的反弹，上方那道线就是第一道抛压',
    'tangled': '四条线互有交叉（既不是依次在上也不是依次在下）：方向没理顺，'
               '均线这一项给不出结论，别看这一格当信号',
    'pullback_ma60': 'MA5 < MA10 < MA20（短期在回落），但季线 MA60 还在 MA20 下方托着：'
                     '这是回踩不是破位，季线是这道回调的天然支撑位',
    'bear_strong': 'MA5 < MA10 < MA20 < MA60，四条线依次在下：趋势全线向下，'
                   '今天的板是逆势板，均线这一项是减分项',
}

#: 源站整池：与 `duanxian/historical_sources.py:16 THS` 同一个端点（那边只留题材串，其余字段要自己拉）
POOL_PAGE_LIMIT_ROWS = 200
POOL_MAX_PAGES = 20
POOL_TIMEOUT = 12
POOL_FIELD = ('199112,10,9001,330323,330324,330325,9002,330329,133971,133970,'
              '1968584,3475914,9003,9004')
POOL_FILTER = 'HS,GEM2STAR'      # ⚠️ 不含北交所

#: 题材串出处（`reason_source` 的取值；口径原文只住这一处，屏幕念这一份）
REASON_SOURCE_IWENCAI = 'iwencai'
REASON_SOURCE_POOL = 'ths_limit_up_pool'
REASON_SOURCE_NOTES = {
    REASON_SOURCE_IWENCAI: '问财结构化查询（配了 IWENCAI_API_KEY 才走这条）：涨停原因列名带日期，'
                           '与请求场次不符就当取数失败',
    REASON_SOURCE_POOL: '同花顺涨停揭秘池：逐行核对封板时间戳落在本场；源站编辑归因、非公司确认；'
                        '沪深主板/创业板/科创板，不含北交所，实测也基本不含 ST；保留窗约一年'
                        '（2026-10-21 探：2025-11-03 还在、2025-09-22 起已空）',
}

#: 前一日形状七档的阈值（**只住这一处**；括号里是 2026-10-21 现查近 25 场 1,094 只首板的分位）
PREV_SHAPE_THRESHOLDS: Dict[str, float] = {
    'deep_pullback_pct': -5.0,     # 实测 p10 −5.05 ⇒ 前一日跌到前 10% 的深水区
    'surge_unsealed_pct': 3.5,     # 实测 p90 +3.51 ⇒ 前一日已经涨到前 10%，却没封住
    'flat_abs_pct': 2.0,           # |pct| ≤ 2（实测 p25 −2.94、p50 −0.51）⇒ "几乎没动"
    'shrink_vol_ratio': 0.77,      # 实测量比 p25 0.77 ⇒ 缩到近 5 场均量的 77% 以下才算缩量
    'mild_up_vol_ratio': 1.40,     # 实测量比 p75 1.40 ⇒ 温和放量的上界，再高就是放量抢跑
}

#: 判档顺序（优先级从高到低）。**顺序就是判据**：前一日同时满足两档时按这一串取第一个
SHAPE_PRIORITY = ('broke_at_limit', 'deep_pullback', 'surge_unsealed',
                  'quiet_shrink', 'pullback_shrink', 'mild_up', 'other')

#: 每档一句人话（屏幕上直接念，不另拼句子；引用一律用「」，不嵌 ASCII 双引号、不带 markdown 星号）
SHAPE_LABELS: Dict[str, str] = {
    'broke_at_limit': '前一日盘中冲到过涨停价、收盘没封住：主力昨天就试过盘，今天的封板是有备而来',
    'deep_pullback': '前一日跌幅在 5% 及以上（实测落在近 25 场首板的前 10%）：昨天是恐慌换手、'
                     '获利盘出清，今天的板更像超跌反弹而不是接力',
    'surge_unsealed': '前一日已经涨到 3.5% 以上（实测 p90 附近）却没封住：昨天就在拉，'
                      '今天封板是延续，追高的钱接的是昨天的获利盘',
    'quiet_shrink': '前一日涨跌幅在正负 2% 以内、量比却缩到 0.77 以下（实测 p25）：昨天几乎没动也没人卖，'
                    '筹码锁住了，这种封板最省资金',
    'pullback_shrink': '前一日收跌、量比缩到 0.77 以下：缩量回撤是洗盘而不是出货，抛压没出来',
    'mild_up': '前一日收涨、量比不到 1.40（实测 p75）：温和放量小涨，昨天没抢跑，'
               '今天的封板是资金当天才决定',
    'other': '前一日既没冲板也没落进上面几档（多为放量上涨或大幅高走）：昨天已经在放量抢跑，'
             '今天的板要看接力资金',
}

#: 每档的两个字短标签（表格里那一格用这个，长句解释走 `prev_shape_note`/悬停）。
#: 与 SHAPE_LABELS 同住写侧：界面不许自己给某一档另起一个名字，两屏会分叉。
SHAPE_NAMES: Dict[str, str] = {
    'broke_at_limit': '触板炸开',
    'deep_pullback': '大跌反包',
    'surge_unsealed': '冲高未封',
    'quiet_shrink': '缩量横盘',
    'pullback_shrink': '缩量回调',
    'mild_up': '温和放量',
    'other': '其余',
}

#: 五档从"最多头"到"最空头"的排法（界面上那一列按它排序，格子显示的是中文档名）。
#: 与判档顺序同住写侧一份：界面不许自己给五档另排一次（排错了屏幕上"点了不亮"也不会报错）。
MA_STACK_PRIORITY = ('bull_strong', 'bull_under_ma60', 'tangled',
                     'pullback_ma60', 'bear_strong')

#: 源站文本「几天几板」的形状（源站口径，只用来与本地 board_span 对账）
HIGH_DAYS_RE = re.compile(r'^\s*(\d+)\s*天\s*(\d+)\s*板\s*$')
#: 源站对"第一次封板"写的不是「1天1板」而是「首板」（实测 2026-10-21 池内 50 只里 28 只如此）。
#: 这一档认成 (1, 1) —— 它是源站自己的措辞，认下来才算得出 `source_boards_agree`；
#: 认不出的写法（空、「-」、将来冒出的新措辞）一律 None，**不猜**。
HIGH_DAYS_WORDS: Dict[str, Tuple[int, int]] = {'首板': (1, 1)}

#: 源站那一组的列（整场取数失败时每格都要在 missing 里点名，界面上才说得出"永久缺"）
SOURCE_COLS = ('reason', 'first_seal_at', 'last_seal_at', 'break_count', 'order_amount',
               'order_volume', 'limit_up_kind', 'source_high_days', 'float_cap_yuan')

#: `missing` 的取值字典（键名＝列名；这些短码只在本模块出现一次）
GAP_SOURCE_NO_RECORDS = 'source_no_records'        # 源站这一场整场没有（超出保留窗 ⇒ 永久缺）
GAP_SOURCE_ROW = 'source_row_absent'               # 源站有这一场但池里没这只票（北交所、ST 居多）
GAP_SOURCE_DATE = 'source_date_mismatch'           # 源站给的是别的日子，不敢用
GAP_SOURCE_PAGING = 'source_paging_incomplete'     # 分页对不上合计，名单不完整
GAP_SOURCE_HTTP = 'source_http_error'              # 网络/解析失败（当场可重试，与过期不同）
GAP_REASON_EMPTY = 'source_reason_empty'           # 池子有、题材串整场为空（问财那一侧空手而归）
GAP_SOURCE_UNREADABLE = 'source_unreadable'        # 上面几条之外兜不住的情况，如实点名
GAP_NO_PREV_ROW = 'no_prev_row'                    # 前一日没有日线行（停牌/刚上市）
GAP_NO_PREV_VOLUME = 'no_prev_volume'              # 前一日成交量缺
GAP_PREV_BASE_SHORT = 'prev_base_bars_lt3'         # 前一日往前真有的行不足 3 根，均量不成立
GAP_NO_DAILY_ROW = 'no_daily_row_on_target'        # 当日日线行缺（母体行理论上必然有）
GAP_NO_LIMIT_UP = 'limit_up_missing_on_prev'       # 前一日没有涨停价档位 ⇒ 触板/封板判不了
GAP_BOARD_SOURCE = 'board_source_unavailable'      # board_span 整批读回空（读不到库，不是"都没板"）
GAP_NO_ROUND = 'no_local_round'                    # board_span 数不出这一轮
GAP_CHG_SOURCE = 'chg_source_unavailable'          # day_returns 整批读回空
GAP_CHG_BASE = 'chg_base_day_missing'              # 基期那天没行（停牌/未上市）
GAP_ENERGY_ROW = 'no_volume_double_row'            # 当日 volume_double 没这只票的行
GAP_SHAPE_INPUT = 'prev_shape_inputs_missing'
# 四列历史位置那一族（10-27 新增）：每一列判不了都要有名字，界面才说得出"是哪种判不了"
GAP_HIST_ROWS = 'hist_window_no_rows'          # 两年窗口里这只票一根日线都没有（全新上市或读库失败）
GAP_HIST_LIMIT_UP = 'hist_limit_up_missing'    # 窗口里有日线但缺涨停档位价 ⇒ 涨停次数判不了
GAP_HIST_LIMIT_DOWN = 'hist_limit_down_missing'
GAP_CONSEC_SOURCE = 'lup_rows_unavailable'     # lup-1 那一场整批改是空（没读到库，不是"这半年没连板"）
GAP_POS_BARS = f'bars_2y_lt{POS_MIN_BARS}'     # 两年窗口真有的行不足 250 根 ⇒ 区间位置判不了
GAP_MA_BARS = 'ma_bars_lt60'                   # 不足 60 根 ⇒ 四条线凑不齐 ⇒ 均线排列判不了

#: "这一场整场没拉到"的那几种（与"这只票源站不收"相对）：日志与界面上必须分开说，
#: 前者要**今天就补**（http 那档还能重试，no_records 那档永久缺），后者本来就不该有。
GAP_DAY_LEVEL = frozenset({GAP_SOURCE_NO_RECORDS, GAP_SOURCE_DATE, GAP_SOURCE_PAGING,
                           GAP_SOURCE_HTTP, GAP_REASON_EMPTY, GAP_SOURCE_UNREADABLE})


class SourceUnavailable(RuntimeError):
    """源站这一场拿不回来（已过保留期 / 日期对不上 / 分页不完整 / 网络失败）。

    **只有这一类**异常会被 `compute_day` 兜成 NULL + `missing`；其它异常照旧往外抛。
    这条界线是刻意的：把 `KeyError`/类型错误也兜成"没数据"，屏幕就会把代码坏了
    说成"源站过期"，而那两件事用户该做的事完全相反（等一下 vs 改代码）。
    """

    def __init__(self, code: str, detail: str = ''):
        super().__init__(f'{code}: {detail}' if detail else code)
        self.code = code
        self.detail = detail


# --------------------------------------------------------------------------- 纯判据

def _num(v: Any) -> Optional[float]:
    """Decimal / str / NaN → float 或 None。**不返回 0**（0 是读数，NULL 才是判不了）。"""
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if f != f else f


def _txt(v: Any) -> Optional[str]:
    """文字列收口：pandas/源站的 NaN 与空串都不是"内容"。"""
    if v is None:
        return None
    try:
        if v != v:                       # float NaN
            return None
    except (TypeError, ValueError):
        pass
    s = str(v).strip()
    return s or None


def prev_shape_walk(prev_pct: Optional[float], prev_vol_ratio: Optional[float],
                    prev_touched: Optional[bool], prev_sealed: Optional[bool]
                    ) -> Tuple[Optional[str], Optional[str]]:
    """按 `SHAPE_PRIORITY` 逐档走的**唯一一份**实现：返回 `(形状, 判不了的原因)`。

    走法（这就是"不许硬猜"的具体含义）：
      · 一档的输入齐全 ⇒ 成立就落这一档，不成立就**排除**掉它继续往下；
      · 一档的输入有 NULL ⇒ 这一档**排除不掉**，它下面所有档都不能落（否则一个本该判
        `broke_at_limit` 的票会因为量比没取到而被写成 `deep_pullback`，屏幕上两句话都成立、
        读者分不清）⇒ 交 `(None, 缺的是哪一样)`；
      · `prev_sealed` 为 true ⇒ 这一行根本不是首板（母体串了），七档全都不成立 ⇒ 交 NULL。
    """
    th = PREV_SHAPE_THRESHOLDS
    if prev_touched is None or prev_sealed is None:
        return None, GAP_SHAPE_INPUT
    if prev_sealed:
        return None, 'prev_sealed_true_not_first_board'
    if prev_touched:
        return SHAPE_PRIORITY[0], None            # 触板炸开（首板定义保证前一日没封住，上面已排除）
    if prev_pct is None:
        return None, GAP_SHAPE_INPUT
    if prev_pct <= th['deep_pullback_pct']:
        return SHAPE_PRIORITY[1], None
    if prev_pct >= th['surge_unsealed_pct']:
        return SHAPE_PRIORITY[2], None
    if prev_vol_ratio is None:
        return None, GAP_SHAPE_INPUT
    if abs(prev_pct) <= th['flat_abs_pct'] and prev_vol_ratio <= th['shrink_vol_ratio']:
        return SHAPE_PRIORITY[3], None
    if prev_pct < 0 and prev_vol_ratio <= th['shrink_vol_ratio']:
        return SHAPE_PRIORITY[4], None
    if prev_pct > 0 and prev_vol_ratio < th['mild_up_vol_ratio']:
        return SHAPE_PRIORITY[5], None
    return SHAPE_PRIORITY[6], None


def prev_shape_of(prev_pct: Optional[float], prev_vol_ratio: Optional[float],
                  prev_touched: Optional[bool], prev_sealed: Optional[bool]) -> Optional[str]:
    """前一日形状七档（纯判据，不碰库、不碰网络）。判不了 ⇒ `None`，别处的 None 一律不许当 false。"""
    return prev_shape_walk(prev_pct, prev_vol_ratio, prev_touched, prev_sealed)[0]


def prev_shape_blocker(prev_pct: Optional[float], prev_vol_ratio: Optional[float],
                       prev_touched: Optional[bool], prev_sealed: Optional[bool]) -> Optional[str]:
    """判不了时缺的是哪一样（`missing.prev_shape` 的值）。能判 ⇒ None。"""
    return prev_shape_walk(prev_pct, prev_vol_ratio, prev_touched, prev_sealed)[1]


def shape_note_of(shape: Optional[str]) -> Optional[str]:
    """那一档的人话说明。形状为空时**同样为空**（屏幕不能出现"没有档位却有一句解释"）。"""
    return SHAPE_LABELS.get(shape) if shape else None


def parse_high_days(text: Optional[str]) -> Optional[Tuple[int, int]]:
    """源站「N天M板」→ `(几天, 几板)`；「首板」→ `(1, 1)`；认不出的写法 ⇒ None（**不猜**）。"""
    s = _txt(text)
    if not s:
        return None
    if s in HIGH_DAYS_WORDS:
        return HIGH_DAYS_WORDS[s]
    m = HIGH_DAYS_RE.match(s)
    return (int(m.group(1)), int(m.group(2))) if m else None


def source_boards_of(text: Optional[str]) -> Optional[int]:
    """源站那串文本里的**板数**。解析不出就 None —— 绝不拿它去覆盖本地 `boards`（第 4 条口径）。"""
    got = parse_high_days(text)
    return got[1] if got else None


def boards_agree(local_boards: Optional[int], source_text: Optional[str]) -> Optional[bool]:
    """本地数出来的板数与源站写的板数是否一致。任一边缺 ⇒ None（判不了），**不是 false**。

    不一致是常态（两把尺子：本地回溯 30 场、连续 2 场没封住才算断；源站自己一套编辑口径），
    这一列的用处就是让不一致**看得见**，谁也不许覆盖谁。
    """
    src = source_boards_of(source_text)
    if local_boards is None or src is None:
        return None
    return int(local_boards) == int(src)


def stamp_to_clock(value: Any, day: dt.date, tz) -> Tuple[dt.date, dt.time]:
    """源站那串 unix 秒（字符串）→ `(落地日, 钟表时间)`。无效/越界就抛 `SourceUnavailable`。

    为什么每行都要核日期：`date` 只是**请求参数**，不是证据 —— 源站给过别的交易日的行
    （`duanxian/historical_sources.py:45` 那条注释讲的就是这件事），拿别日的封板时间写成本日，
    屏幕上"首封 09:32"是真的、字是假的，谁也查不出来。
    """
    stamp = _num(value)
    if stamp is None:
        raise SourceUnavailable(GAP_SOURCE_DATE, f'{day} 池内有行缺有效封板时间戳')
    try:
        at = dt.datetime.fromtimestamp(stamp, tz)
    except (OverflowError, OSError, ValueError):
        raise SourceUnavailable(GAP_SOURCE_DATE, f'{day} 封板时间戳越界 {stamp}') from None
    return at.astimezone(tz).date(), at.astimezone(tz).time()


# --------------------------------------------------------------------------- 取数

BASE_SQL = """
with fb as (                              -- 母体：fb-2 当日行，一把尺都不改
  select security_code, launch_pct, launch_turnover, launch_amount_yuan,
         launch_is_1word, pre5, dbl_days, calm_days, seg_len
    from analytics.first_board_shape
   where spec_version = %(fb)s and launch_date = %(d)s
),
univ as (                                 -- 分母随行交出：当日本地判定的封板家数
  select count(*)::int as n
    from market_data.stock_daily
   where trade_date = %(d)s and limit_up is not null
     and close >= limit_up - %(eps)s::numeric
)
select f.security_code, f.launch_pct, f.launch_turnover, f.launch_amount_yuan,
       f.launch_is_1word, f.pre5, f.dbl_days, f.calm_days, f.seg_len,
       d.close as launch_close, d.limit_up as limit_up_price,
       (select n from univ) as universe
  from fb f
  left join market_data.stock_daily d
         on d.security_code = f.security_code and d.trade_date = %(d)s
 order by f.security_code
"""

PREV_SQL = """
-- 一批代码 × 往前数个市场场次（第 1 场＝前一日，其余是量比的分母）
with s as (select trade_date from (
             select distinct trade_date from market_data.stock_daily
              where trade_date < %(d)s order by trade_date desc limit %(n)s) z
),
r as (select sd.security_code, sd.trade_date, sd.pct_change, sd.turnover_rate_percent,
             sd.amount_yuan, sd.volume_shares, sd.close, sd.high, sd.limit_up
        from market_data.stock_daily sd
        join s on s.trade_date = sd.trade_date
       where sd.security_code = any(%(codes)s::text[]))
select security_code, trade_date, pct_change, turnover_rate_percent, amount_yuan,
       volume_shares, close, high, limit_up
  from r order by 1, 2
"""

ENERGY_SQL = """
select security_code, dbl_energy, energy_fp
  from analytics.volume_double
 where trade_date = %(d)s and security_code = any(%(codes)s::text[])
"""

SESSIONS_SQL = """
select trade_date from (
  select distinct trade_date from market_data.stock_daily
   where trade_date < %(d)s order by trade_date desc limit %(n)s) z
 order by trade_date
"""

#: 两年窗口（含当日）的场次清单：3 个月/6 个月的起点就从这一串里按倒数第几场取，
#: 所以三个窗口共用同一把"市场场次"尺，不会出现"3 个月那列比 2 年那列多算了一天"。
HIST_SESSIONS_SQL = """
select trade_date from (
  select distinct trade_date from market_data.stock_daily
   where trade_date <= %(d)s order by trade_date desc limit %(n)s) z
 order by trade_date
"""

#: 一批代码 × 两年窗口的日线原行（价格全部**未复权**，复权在 Python 侧乘因子 ——
#: 与 `day_returns`/倍量那一族同一条底：`close × coalesce(adjustment_factor, 1)`）。
HIST_SQL = """
select security_code, trade_date, close, high, low,
       limit_up, limit_down, adjustment_factor
  from market_data.stock_daily
 where trade_date between %(frm)s and %(to)s
   and security_code = any(%(codes)s::text[])
 order by 1, 2
"""

#: 连板最高只读 lup-1 的 `consec`（"这一段是第几板"的唯一实现，见该模块 docstring）：
#: 这里再数一遍就会有两把尺 —— 停牌那天算不算断、ST 档位的容差取几分，两处各判一次必分叉。
CONSEC_SQL = """
select security_code, max(consec)::int as max_consec
  from analytics.limit_up_pool_daily
 where spec_version = %(lup)s and sealed
   and trade_date between %(frm)s and %(to)s
   and security_code = any(%(codes)s::text[])
 group by 1
"""


def sessions_before(day: dt.date, n: int) -> List[dt.date]:
    """`day` 往前 n 个**市场场次**（升序，最后一个＝前一日）。"""
    return [r['trade_date'] for r in (engine.query(SESSIONS_SQL, {'d': day, 'n': int(n)},
                                                   role='reader') or [])]


def prev_readings(codes: Sequence[str], day: dt.date) -> Dict[str, Dict[str, Any]]:
    """前一日读数：`{全码: {prev_date, prev_pct, prev_turnover, prev_amount_yuan, prev_vol_ratio,
    prev_touched, prev_sealed, gap}}`。

    窗口是**市场场次**（不是这只票自己的 K 线根数）：前一日全市场都有场次，停牌的那只票就是
    "自己没有行"，那叫 `no_prev_row`，不叫"平盘"。分母那 5 场只数真有的行，不足
    `PREV_BASE_MIN` 根就不给量比。
    """
    if not codes:
        return {}
    win = sessions_before(day, PREV_BASE_BARS + 1)
    if not win:
        return {c: {'gap': 'no_earlier_session'} for c in codes}
    prev_date = win[-1]
    base_dates = win[:-1]
    rows = engine.query(PREV_SQL, {'d': day, 'n': PREV_BASE_BARS + 1,
                                   'codes': list(codes)},
                        role='reader') or []
    by: Dict[str, Dict[dt.date, dict]] = {}
    for r in rows:
        by.setdefault(r['security_code'], {})[r['trade_date']] = r

    out: Dict[str, Dict[str, Any]] = {}
    for code in codes:
        here = by.get(code) or {}
        row = here.get(prev_date)
        item: Dict[str, Any] = {'prev_date': prev_date, 'gap': None}
        if row is None:
            item.update({'prev_pct': None, 'prev_turnover': None, 'prev_amount_yuan': None,
                         'prev_vol_ratio': None, 'prev_touched': None, 'prev_sealed': None,
                         'gap': GAP_NO_PREV_ROW})
            out[code] = item
            continue
        limit_up = _num(row.get('limit_up'))
        high, close = _num(row.get('high')), _num(row.get('close'))
        item.update({
            'prev_pct': _num(row.get('pct_change')),
            'prev_turnover': _num(row.get('turnover_rate_percent')),
            'prev_amount_yuan': _num(row.get('amount_yuan')),
            'prev_touched': (None if limit_up is None or high is None
                             else high >= limit_up - LIMIT_SEAL_EPS),
            'prev_sealed': (None if limit_up is None or close is None
                            else close >= limit_up - LIMIT_SEAL_EPS),
        })
        if limit_up is None:
            item['gap'] = GAP_NO_LIMIT_UP
        vol = _num(row.get('volume_shares'))
        base = [_num(here[d].get('volume_shares')) for d in base_dates if d in here]
        base = [v for v in base if v is not None]
        if vol is None:
            item['prev_vol_ratio'] = None
            item['gap'] = item['gap'] or GAP_NO_PREV_VOLUME
        elif len(base) < PREV_BASE_MIN:
            item['prev_vol_ratio'] = None
            item['gap'] = item['gap'] or GAP_PREV_BASE_SHORT
        else:
            mean = sum(base) / len(base)
            item['prev_vol_ratio'] = round(vol / mean, 4) if mean > 0 else None
            if mean <= 0:
                item['gap'] = item['gap'] or GAP_PREV_BASE_SHORT
        out[code] = item
    return out


def energy_readings(codes: Sequence[str], day: dt.date) -> Dict[str, Dict[str, Any]]:
    """当日 `volume_double` 那一行的加权分与指纹（分数只在同指纹里可比，所以两列一起带）。"""
    if not codes:
        return {}
    rows = engine.query(ENERGY_SQL, {'d': day, 'codes': list(codes)}, role='reader') or []
    return {r['security_code']: {'dbl_energy': _num(r.get('dbl_energy')),
                                 'energy_fp': _txt(r.get('energy_fp'))} for r in rows}


# --------------------------------------------------------------- 四列历史位置（纯判据）

#: 落库的列名清单（`assemble` 与 `COLS` 共用一份，加一列只改这里一次）
HIST_COLS = ('lu_sealed_3m', 'lu_touched_3m', 'ld_sealed_3m', 'ld_touched_3m',
             'max_consec_6m', 'close_adj', 'hi_2y', 'lo_2y', 'pos_2y', 'bars_2y',
             'ma5', 'ma10', 'ma20', 'ma60', 'ma_stack')


def adj_val(bar: dict, key: str = 'close') -> Optional[float]:
    """未复权价 → **后复权**价：`值 × coalesce(adjustment_factor, 1)`。

    缺因子按不复权算（与均线族那条一致：实测有 115 只票没有因子，整列判不了比按原值算更坏）。
    ⚠️ 但缺 `key` 本身（那天没这根价）就是 None —— 不许拿 1.0 去乘一个不存在的价格。
    """
    v = _num(bar.get(key))
    if v is None:
        return None
    f = _num(bar.get('adjustment_factor'))
    return v * (f if f and f > 0 else 1.0)


def _win(bars: List[dict], since: Optional[dt.date]) -> List[dict]:
    """窗口＝起始场次（含）往后的行。`bars` 已按场次升序（调用侧排好）。"""
    return [b for b in bars if since is None or b['trade_date'] >= since]


def limit_counts(bars: List[dict], since: dt.date,
                 eps: float = LIMIT_SEAL_EPS) -> Tuple[Dict[str, Any], Dict[str, str]]:
    """近 3 个月（63 场）涨停/跌停几天：**封住**与**触及**两个口径都给（用户 10-27 点名）。

    · 封住＝收盘贴在板上（`close ≥ limit_up − eps`，跌停方向反过来）；
    · 触及＝盘中摸到过价（`high ≥ limit_up − eps` / `low ≤ limit_down + eps`，含后来炸开的）。
    两把尺在本机实测同一场差 19 只（触板 103 对 封住 84）—— 只留一个数就会把"冲板没封住"那批藏掉。
    ⚠️ 窗口里只要有哪一天的档位价缺失，这一族就交 NULL + 点名：缺一天＝少计一次，
    报出来的那个数就不再是"这三个月有几天封住"，而是一个说不清少了谁的下界。
    """
    gap: Dict[str, str] = {}
    win = _win(bars, since)
    cols = {'lu_sealed_3m': None, 'lu_touched_3m': None,
            'ld_sealed_3m': None, 'ld_touched_3m': None}
    if not win:
        gap = {c: GAP_HIST_ROWS for c in cols}
        return cols, gap
    up_bad = [b for b in win if _num(b.get('limit_up')) is None or _num(b.get('high')) is None]
    dn_bad = [b for b in win if _num(b.get('limit_down')) is None or _num(b.get('low')) is None]
    if not up_bad:
        cols['lu_sealed_3m'] = sum(1 for b in win
                                   if _num(b['close']) is not None
                                   and _num(b['close']) >= _num(b['limit_up']) - eps)
        cols['lu_touched_3m'] = sum(1 for b in win if _num(b['high']) >= _num(b['limit_up']) - eps)
    else:
        gap['lu_sealed_3m'] = gap['lu_touched_3m'] = GAP_HIST_LIMIT_UP
    if not dn_bad:
        cols['ld_sealed_3m'] = sum(1 for b in win
                                   if _num(b['close']) is not None
                                   and _num(b['close']) <= _num(b['limit_down']) + eps)
        cols['ld_touched_3m'] = sum(1 for b in win if _num(b['low']) <= _num(b['limit_down']) + eps)
    else:
        gap['ld_sealed_3m'] = gap['ld_touched_3m'] = GAP_HIST_LIMIT_DOWN
    return cols, gap


def position_2y(bars: List[dict], since: dt.date,
                min_bars: int = POS_MIN_BARS) -> Tuple[Dict[str, Any], Dict[str, str]]:
    """两年区间位置% =（现价 − 两年最低）÷（两年最高 − 两年最低）×100，全部后复权。

    窗口含当日 ⇒ 天然落在 0~100：0 是正趴在两年最低点，100 是正顶在两年最高点。
    ⚠️ 这一列**不是涨幅、也不是分位数**（分位要数"有多少天比它低"），屏幕上那一格与悬停都这么写。
    最高取 `high`、最低取 `low`（不是收盘）：打板要问的是"离历史顶还有多远"，盘中顶也是顶。
    真有的行不足 `min_bars` 根 ⇒ 判不了（次新股没有两年可说），`bars_2y` 那一列照实交根数。
    """
    win = _win(bars, since)
    cols: Dict[str, Any] = {'close_adj': None, 'hi_2y': None, 'lo_2y': None,
                            'pos_2y': None, 'bars_2y': len(win)}
    gap: Dict[str, str] = {}
    today = bars[-1] if bars else None
    close_adj = adj_val(today, 'close') if today else None
    cols['close_adj'] = None if close_adj is None else round(close_adj, 4)
    if not win:
        gap = {'close_adj': GAP_HIST_ROWS, 'hi_2y': GAP_HIST_ROWS, 'lo_2y': GAP_HIST_ROWS,
               'pos_2y': GAP_HIST_ROWS}
        return cols, gap
    if len(win) < min_bars:
        gap = {'pos_2y': GAP_POS_BARS}
        return cols, gap
    highs = [adj_val(b, 'high') for b in win]
    lows = [adj_val(b, 'low') for b in win]
    highs = [h for h in highs if h is not None]
    lows = [l for l in lows if l is not None]
    if close_adj is None or not highs or not lows:
        gap = {'pos_2y': GAP_HIST_ROWS, 'hi_2y': GAP_HIST_ROWS, 'lo_2y': GAP_HIST_ROWS}
        return cols, gap
    hi, lo = max(highs), min(lows)
    cols['hi_2y'] = round(hi, 4)
    cols['lo_2y'] = round(lo, 4)
    if hi <= lo:
        gap = {'pos_2y': 'hist_range_flat'}       # 两年没波动（整段停牌）⇒ 位置没有定义
        return cols, gap
    cols['pos_2y'] = round((close_adj - lo) / (hi - lo) * 100.0, 2)
    return cols, gap


def moving_average(bars: List[dict], n: int) -> Optional[float]:
    """后复权收盘的近 n **根**均值（按这只票自己的行，不按市场场次 —— 与 `config.indicator`
    那族「不足 N 根给空值」同一条）。停牌的日子不补 0，也不缩短窗口凑数。"""
    if len(bars) < n:
        return None
    vals = [adj_val(b) for b in bars[-n:]]
    if any(v is None for v in vals):
        return None
    return sum(vals) / n                      # type: ignore[arg-type]


def ma_stack(bars: List[dict], spans: Sequence[int] = MA_SPANS
             ) -> Tuple[Dict[str, Any], Dict[str, str]]:
    """均线排列五档。判档顺序就是判据（与 `SHAPE_PRIORITY` 同一条纪律）：

      四线多头 → 反弹压季线（短多但季线在上方）→ 四线空头 → 回调托季线（短空但季线在下方）→ 纠缠。
    先认"依次在上/在下"这两端，认不出再看季线在哪一侧，最后才落到"纠缠"——
    顺序反了会把"季线压顶的反弹"报成"多头排列"，那一格就从加分变减分。
    不足 60 根 ⇒ 四条线凑不齐 ⇒ 整档判不了（不拿三档冒充五档）。
    """
    cols: Dict[str, Any] = {f'ma{n}': None for n in spans}
    for n in spans:
        v = moving_average(bars, n)
        cols[f'ma{n}'] = None if v is None else round(v, 4)
    m5, m10, m20, m60 = (cols.get(f'ma{n}') for n in spans)
    if None in (m5, m10, m20, m60):
        cols['ma_stack'] = None
        return cols, {'ma_stack': GAP_MA_BARS}
    if m5 > m10 > m20 > m60:
        stack = 'bull_strong'
    elif m5 > m10 > m20:
        stack = 'bull_under_ma60'
    elif m5 < m10 < m20 < m60:
        stack = 'bear_strong'
    elif m5 < m10 < m20:
        stack = 'pullback_ma60'
    else:
        stack = 'tangled'
    cols['ma_stack'] = stack
    return cols, {}


def hist_readings(codes: Sequence[str], day: dt.date) -> Dict[str, Dict[str, Any]]:
    """四列历史位置的取数：一发两年窗口的日线 + 一发 lup 的连板，逐票喂给上面那三个纯判据。"""
    if not codes:
        return {}
    ses = engine.query(HIST_SESSIONS_SQL, {'d': day, 'n': HIST_2Y_SESSIONS}, role='reader') or []
    dates = sorted(r['trade_date'] for r in ses)
    if not dates:
        return {c: {**{k: None for k in HIST_COLS},
                    'gap': {k: 'no_sessions' for k in HIST_COLS}} for c in codes}
    since2 = dates[0]
    since6 = dates[-HIST_6M_SESSIONS] if len(dates) >= HIST_6M_SESSIONS else dates[0]
    since3 = dates[-HIST_3M_SESSIONS] if len(dates) >= HIST_3M_SESSIONS else dates[0]
    rows = engine.query(HIST_SQL, {'frm': since2, 'to': dates[-1],
                                   'codes': list(codes)}, role='reader') or []
    by: Dict[str, List[dict]] = {}
    for r in rows:
        by.setdefault(r['security_code'], []).append(r)
    crows = engine.query(CONSEC_SQL, {'lup': limit_up_pool.SPEC_VERSION, 'frm': since6,
                                      'to': dates[-1], 'codes': list(codes)}, role='reader') or []
    consec = {r['security_code']: r['max_consec'] for r in crows}
    consec_batch_empty = not crows          # 整批读回空＝lup 那一场没跑，不是"这批票半年都没连板"
    out: Dict[str, Dict[str, Any]] = {}
    for code in codes:
        bars = sorted(by.get(code) or [], key=lambda r: r['trade_date'])
        cols: Dict[str, Any] = {}
        gaps: Dict[str, str] = {}
        for fn, args in ((limit_counts, (bars, since3)), (position_2y, (bars, since2)),
                         (ma_stack, (bars,))):
            v, g = fn(*args)
            cols.update(v)
            gaps.update(g)
        cols['max_consec_6m'] = consec.get(code)
        if cols['max_consec_6m'] is None:
            gaps['max_consec_6m'] = GAP_CONSEC_SOURCE if consec_batch_empty else GAP_NO_ROUND
        cols['gap'] = gaps
        out[code] = cols
    return out


def fetch_limit_up_pool(day: dt.date) -> Dict[str, Dict[str, Any]]:
    """整池原始行（同花顺涨停揭秘 `dataapi/limit_up/limit_up_pool`）：`{6 位代码: 源站行}`。

    与 `duanxian/historical_sources.public_limit_reasons` 同一个端点、同一套纪律，差别只在于
    **这里要那些被它丢掉的字段**（首封/末封时间、炸板次数、封单额与量、板型、几天几板、流通市值）。
    代价是同一场交易日的池子会被拉两遍（题材串那一遍走 `fetch_zt_reasons`，不改 duanxian 的口径）；
    一次一两秒、一天两问，换"题材串的判据只有一份"，值。

    四条不许松：
      · 逐行核对 `first_limit_up_time` 落在本场 —— 日期不符 ⇒ 整场判为不可用（`GAP_SOURCE_DATE`）；
      · 翻页数必须对上 `page.total`，对不上 ⇒ `GAP_SOURCE_PAGING`（半张名单比没名单更坏）；
      · 一只都没有 ⇒ `GAP_SOURCE_NO_RECORDS`，就是"源站已过保留期"那个永久缺；
      · 只有网络/解析失败抛 `SourceUnavailable`；其它异常照旧往外抛，不兜成空值。
    """
    rows: Dict[str, Dict[str, Any]] = {}
    total: Optional[int] = None
    for page in range(1, POOL_MAX_PAGES + 1):
        try:
            resp = fetchers._direct_get(
                hsrc.THS,
                params={'page': page, 'limit': POOL_PAGE_LIMIT_ROWS, 'field': POOL_FIELD,
                        'filter': POOL_FILTER, 'order_field': '330324', 'order_type': '0',
                        'date': day.strftime('%Y%m%d')},
                headers={'User-Agent': 'Mozilla/5.0'}, timeout=POOL_TIMEOUT)
            resp.raise_for_status()
            env = resp.json()
        except (requests.RequestException, ValueError) as exc:
            raise SourceUnavailable(GAP_SOURCE_HTTP,
                                    f'{type(exc).__name()}: {str(exc)[:120]}') from None
        if env.get('status_code') != 0:
            raise SourceUnavailable(GAP_SOURCE_HTTP, f'{day} 源站 status_code 非 0')
        data = env.get('data') or {}
        info = data.get('info') or []
        page_total = _num((data.get('page') or {}).get('total'))
        if page_total is not None:
            if total is not None and int(page_total) != total:
                raise SourceUnavailable(GAP_SOURCE_PAGING,
                                        f'{day} 翻页期间合计变了 {total} → {int(page_total)}')
            total = int(page_total)
        for raw in info:
            code = _txt(raw.get('code'))
            if not code or not re.fullmatch(r'\d{6}', code):
                raise SourceUnavailable(GAP_SOURCE_PAGING, f'{day} 池内代码无效 {code!r}')
            if code in rows:
                raise SourceUnavailable(GAP_SOURCE_PAGING, f'{day} 池内 {code} 重复')
            landed, _clock = stamp_to_clock(raw.get('first_limit_up_time'), day, hsrc.TZ)
            if landed != day:
                raise SourceUnavailable(GAP_SOURCE_DATE,
                                        f'{day} 池内 {code} 的封板时间戳落在 {landed}')
            rows[code] = raw
        if not total or len(rows) >= total or not info:
            break
    if not rows:
        raise SourceUnavailable(GAP_SOURCE_NO_RECORDS,
                               f'{day} 源站未返回涨停记录（不是交易日，或已超出保留窗）')
    if total is None or len(rows) != total:
        raise SourceUnavailable(GAP_SOURCE_PAGING, f'{day} 池内 {len(rows)} 行 ≠ total {total}')
    return rows


def read_source(day: dt.date) -> Dict[str, Any]:
    """一场源站读数：题材串（走现成出口）+ 整池原始行（这里自己拉）。

    返回的 `pool_gap` / `reason_gap` 是给 `missing` 用的短码；**抓到了就照实记**，
    没抓到就两个 gap 各自说清是"过期"还是"这只票源站不收"。
    """
    ymd = day.strftime('%Y%m%d')
    reasons, note = fetchers.fetch_zt_reasons(ymd)
    iwencai = bool(os.environ.get('IWENCAI_API_KEY'))
    captured_at = dt.datetime.now(dt.timezone.utc)
    pool_gap: Optional[str] = None
    pool_rows: Dict[str, Dict[str, Any]] = {}
    try:
        pool_rows = fetch_limit_up_pool(day)
    except SourceUnavailable as exc:
        pool_gap = exc.code or GAP_SOURCE_UNREADABLE
        note = note or exc.detail
    if not isinstance(reasons, dict):
        reasons = {}
    reason_gap = None if reasons else (pool_gap or GAP_REASON_EMPTY)
    return {'pool': pool_rows, 'reasons': {str(k)[:6]: _txt(v) for k, v in reasons.items()},
            'captured_at': captured_at if reasons else None,
            'source': (REASON_SOURCE_IWENCAI if iwencai else REASON_SOURCE_POOL) if reasons else None,
            'pool_gap': pool_gap, 'reason_gap': reason_gap, 'note': note}


# --------------------------------------------------------------------------- 算一行

def _seal_clock(value: Any, day: dt.date, miss: Dict[str, str], col: str,
                optional: bool = False) -> Optional[dt.time]:
    """源站时间戳 → 那一场的钟表时间，核不上日期就 NULL + 点名（**不兜成 00:00**）。

    `first_limit_up_time` 在拉池子时已经整场核过一遍（`fetch_limit_up_pool`），这里只是取值；
    `last_limit_up_time` 池子里可能整格缺（没回封过）⇒ `optional=True`，缺字段是"没有这一件事"。
    """
    if value is None:
        if optional:
            return None
        miss[col] = 'source_field_null'
        return None
    try:
        landed, clock = stamp_to_clock(value, day, hsrc.TZ)
    except SourceUnavailable as exc:
        miss[col] = exc.code or GAP_SOURCE_UNREADABLE
        return None
    if landed != day:
        miss[col] = GAP_SOURCE_DATE
        return None
    return clock


def assemble(day: dt.date, base: List[dict], prev: Dict[str, dict], boards: Dict[str, dict],
             chg: Dict[int, Dict[str, dict]], energy: Dict[str, dict],
             src: Dict[str, Any], hist: Optional[Dict[str, dict]] = None) -> List[Dict[str, Any]]:
    """把七路读数拧成行。**这一层不查库、不打网络**，单测可以直接喂字典验口径。

    `hist`（四列历史位置）给了默认 None：老用例与只验前几路的调用不必一起改，
    缺的那几列会走"这一路没读数"那一档 missing，不会静默补 0。
    """
    rows: List[Dict[str, Any]] = []
    board_failed = bool(base) and not boards        # board_span 交回空 dict ＝ 没读到，不是"都没板"
    chg_failed = bool(base) and not chg.get(CHG_SPANS[0])
    for b in base:
        code = b['security_code']
        code6 = str(code)[:6]
        p = prev.get(code) or {}
        miss: Dict[str, str] = {}
        row: Dict[str, Any] = {'security_code': code, 'trade_date': day,
                               'spec_version': SPEC_VERSION}

        # 一、当日读数（逐字搬母体；母体那一格本身就是 NULL 时不许补 0）
        for col, key in (('launch_pct', 'launch_pct'), ('launch_turnover', 'launch_turnover'),
                         ('launch_amount_yuan', 'launch_amount_yuan'),
                         ('vol_grab_ratio', 'pre5')):
            row[col] = _num(b.get(key))
            if row[col] is None:
                miss[col] = 'shape_row_null'
        for col in ('dbl_days', 'calm_days'):
            got_n = _num(b.get(col))
            row[col] = None if got_n is None else int(got_n)
            if row[col] is None:
                miss[col] = 'shape_row_null'
        row['is_one_word'] = bool(b.get('launch_is_1word'))
        row['launch_close'] = _num(b.get('launch_close'))
        row['limit_up_price'] = _num(b.get('limit_up_price'))
        if row['launch_close'] is None or row['limit_up_price'] is None:
            miss['launch_close'] = miss['limit_up_price'] = GAP_NO_DAILY_ROW

        # 二、源站读数：题材串（走现成出口）+ 首封/炸板/封单/板型/几天几板/流通市值（整池原始行）
        raw = (src.get('pool') or {}).get(code6)
        pool_gap = src.get('pool_gap')
        reason = (src.get('reasons') or {}).get(code6)
        row['reason'] = reason
        row['reason_source'] = src.get('source') if reason else None
        row['reason_captured_at'] = src.get('captured_at') if reason else None
        if reason is None:
            miss['reason'] = src.get('reason_gap') or GAP_SOURCE_ROW
        # 源站那一句口径/失败原因（例如「公开涨停原因源不可用：… 未返回涨停记录」）随行走一圈，
        # 只在 run 的日志里出声 —— 它不是列，`write` 按 COLS 取，绝不会误当数据落库。
        row['source_note'] = _txt(src.get('note'))
        if raw is None:
            gap = pool_gap or GAP_SOURCE_ROW
            for col in SOURCE_COLS:
                if col == 'reason':
                    continue
                row[col] = None
                miss[col] = gap
        else:
            row['first_seal_at'] = _seal_clock(raw.get('first_limit_up_time'), day, miss,
                                               'first_seal_at')
            row['last_seal_at'] = _seal_clock(raw.get('last_limit_up_time'), day, miss,
                                              'last_seal_at', optional=True)
            break_n = _num(raw.get('open_num'))
            row['break_count'] = None if break_n is None else int(break_n)
            row['order_amount'] = _num(raw.get('order_amount'))
            row['order_volume'] = _num(raw.get('order_volume'))
            row['limit_up_kind'] = _txt(raw.get('limit_up_type'))
            row['source_high_days'] = _txt(raw.get('high_days'))
            row['float_cap_yuan'] = _num(raw.get('currency_value'))
            for col in ('first_seal_at', 'last_seal_at', 'break_count', 'order_amount',
                        'order_volume', 'limit_up_kind', 'source_high_days', 'float_cap_yuan'):
                if row[col] is None and col not in miss:
                    miss[col] = 'source_field_null'

        # 三、这一段走到哪（本地 board_span 一把尺，源站文本只用来对账）
        got = boards.get(code6) if not board_failed else None
        if board_failed:
            row['boards'] = row['board_days'] = row['board_label'] = None
            for col in ('boards', 'board_days', 'board_label'):
                miss[col] = GAP_BOARD_SOURCE
        elif not got or got.get('boards') is None:
            row['boards'] = row['board_days'] = row['board_label'] = None
            for col in ('boards', 'board_days', 'board_label'):
                miss[col] = GAP_NO_ROUND
        else:
            row['boards'] = int(got['boards'])
            row['board_days'] = None if got.get('days') is None else int(got['days'])
            row['board_label'] = _txt(got.get('label'))
            if row['board_days'] is None:
                miss['board_days'] = GAP_NO_ROUND
        row['source_boards_agree'] = boards_agree(row.get('boards'), row.get('source_high_days'))
        if row['source_boards_agree'] is None:
            if row.get('source_high_days'):
                miss['source_boards_agree'] = 'source_high_days_unparsable'
            elif row.get('boards') is None:
                miss['source_boards_agree'] = GAP_NO_ROUND if not board_failed else GAP_BOARD_SOURCE
            else:
                miss['source_boards_agree'] = pool_gap or GAP_SOURCE_ROW

        # 四、前一日（用户点名「特别是前一天的走势」）
        row['prev_date'] = p.get('prev_date')
        for col in ('prev_pct', 'prev_turnover', 'prev_amount_yuan', 'prev_vol_ratio',
                    'prev_touched', 'prev_sealed'):
            row[col] = p.get(col)
        if row['prev_date'] is None:
            miss['prev_date'] = p.get('gap') or 'no_earlier_session'
        for col in ('prev_pct', 'prev_turnover', 'prev_amount_yuan'):
            if row.get(col) is None:
                miss[col] = p.get('gap') or GAP_NO_PREV_ROW
        if row.get('prev_vol_ratio') is None:
            miss['prev_vol_ratio'] = p.get('gap') or GAP_PREV_BASE_SHORT
        for col in ('prev_touched', 'prev_sealed'):
            if row.get(col) is None:
                miss[col] = p.get('gap') or GAP_NO_PREV_ROW
        shape = prev_shape_of(row.get('prev_pct'), row.get('prev_vol_ratio'),
                              row.get('prev_touched'), row.get('prev_sealed'))
        row['prev_shape'] = shape
        row['prev_shape_note'] = shape_note_of(shape)
        blocker = prev_shape_blocker(row.get('prev_pct'), row.get('prev_vol_ratio'),
                                     row.get('prev_touched'), row.get('prev_sealed'))
        if shape is None:
            miss['prev_shape'] = blocker or GAP_SHAPE_INPUT

        # 五、近期走势（截止首板日往回看，后复权）
        for span in CHG_SPANS:
            col = f'chg{span}'
            got_c = (chg.get(span) or {}).get(code) if not chg_failed else None
            row[col] = _num(got_c.get('chg_span')) if got_c else None
            if row[col] is None:
                miss[col] = GAP_CHG_SOURCE if chg_failed else GAP_CHG_BASE
        en = energy.get(code)
        row['dbl_energy'] = _num(en.get('dbl_energy')) if en else None
        row['energy_fp'] = _txt(en.get('energy_fp')) if en else None
        if row['dbl_energy'] is None:
            miss['dbl_energy'] = GAP_ENERGY_ROW if en is None else 'energy_null_on_row'
        if row['energy_fp'] is None:
            miss['energy_fp'] = GAP_ENERGY_ROW if en is None else 'energy_null_on_row'

        # 五之二、这只票自己的历史位置（10-27 用户点名的四列：涨跌停次数 / 连板最高 / 两年位置 / 均线排列）
        h = (hist or {}).get(code)
        if h is None:
            for col in HIST_COLS:
                row[col] = None
                miss[col] = 'hist_readings_absent'      # 这一路根本没跑（与"跑了但那只票缺数"分开）
        else:
            for col in HIST_COLS:
                row[col] = h.get(col)
            for col, why in (h.get('gap') or {}).items():
                miss[col] = why
        # 中文档位（`ma_stack_label`/`ma_stack_note`）不在这一层拼 —— 派生展示列一律由读侧
        # `duanxian/first_board_daily.py` 从本模块的 MA_STACK_NAMES/LABELS 加，落库只存短码。

        # 六、诚实性
        row['universe'] = None if b.get('universe') is None else int(b['universe'])
        if row['universe'] is None:
            miss['universe'] = 'no_daily_rows_on_target'
        row['missing'] = miss
        rows.append(row)
    return rows


def compute_day(day: dt.date, src: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    """一场的首板分析行。母体为空就交空表（这一场真没有首板，不是算不出）。

    `src` 由调用方交（`run` 里一场只拉一次整池，同一次出网还要喂
    `app/compute/limit_up_pool.py` 落首页那张涨停母体表）；不传就自己拉。
    """
    base = engine.query(BASE_SQL, {'fb': SHAPE_SPEC, 'd': day, 'eps': LIMIT_SEAL_EPS},
                        role='reader') or []
    if not base:
        return []
    codes = [b['security_code'] for b in base]
    prev = prev_readings(codes, day)
    boards = board_span.boards_by_code(codes, str(day)) or {}
    chg: Dict[int, Dict[str, dict]] = {}
    for span in CHG_SPANS:
        chg[span] = day_returns.returns_by_code(codes, str(day), span=span) or {}
    energy = energy_readings(codes, day)
    hist = hist_readings(codes, day)
    return assemble(day, base, prev, boards, chg, energy, src or read_source(day), hist)


# --------------------------------------------------------------------------- 落库

#: 列清单只有一个真值：INSERT 的列与占位符都从它生成（少一个/多一个都在启动时就炸，不跑到一半）
COLS = ('security_code', 'trade_date', 'spec_version',
        'launch_pct', 'launch_close', 'launch_turnover', 'launch_amount_yuan', 'limit_up_price',
        'is_one_word', 'float_cap_yuan',
        'reason', 'first_seal_at', 'last_seal_at', 'break_count', 'order_amount', 'order_volume',
        'limit_up_kind', 'source_high_days', 'reason_source', 'reason_captured_at',
        'boards', 'board_days', 'board_label', 'source_boards_agree',
        'prev_date', 'prev_pct', 'prev_turnover', 'prev_amount_yuan', 'prev_vol_ratio',
        'prev_touched', 'prev_sealed', 'prev_shape', 'prev_shape_note',
        'chg5', 'chg10', 'chg15', 'vol_grab_ratio', 'dbl_days', 'calm_days',
        'dbl_energy', 'energy_fp', *HIST_COLS, 'missing', 'universe')

#: jsonb 要显式 cast（psycopg 送出来的是 text，直接给 jsonb 列会报「类型不匹配」）
_CASTS = {'missing'}

INSERT_SQL = (f"insert into {TABLE} ({', '.join(COLS)}) values ("
              + ', '.join(f"%({c})s::jsonb" if c in _CASTS else f"%({c})s" for c in COLS) + ')')


def clear_day(day: dt.date) -> int:
    with engine.cursor(role='etl') as cur:
        cur.execute(f"delete from {TABLE} where trade_date = %s and spec_version = %s",
                    (day, SPEC_VERSION))
        return cur.rowcount


def write(rows: List[Dict[str, Any]]) -> int:
    """**重算一场先清这一场**（照 `app/compute/intraday_spike.py:343` 那条）。

    为什么不是纯 upsert：这一屏的母体会随"源站到没到过"变多变更少，只 upsert 不清场，
    上一次半截计算的残留行会一直挂在页面上 —— 屏幕把它们读成"今天这些票判不了"，
    而实际上今天一条都判不了的是另一批。空批也必须清（`run` 里那条 `clear_day`）。
    """
    if not rows:
        return 0
    days = sorted({r['trade_date'] for r in rows})
    with engine.cursor(role='etl') as cur:
        for day in days:
            cur.execute(f"delete from {TABLE} where trade_date = %s and spec_version = %s",
                        (day, SPEC_VERSION))
        payload = []
        for r in rows:
            p = {c: r.get(c) for c in COLS}
            p['missing'] = json.dumps(r.get('missing') or {}, ensure_ascii=False)
            payload.append(p)
        cur.executemany(INSERT_SQL, payload)
    return len(rows)


# --------------------------------------------------------------------------- 体检

def latest_day() -> Optional[dt.date]:
    return engine.scalar("select max(trade_date) from market_data.stock_daily")


def trade_days(end: dt.date, recent: int) -> List[dt.date]:
    rows = engine.query("select trade_date from (select distinct trade_date from market_data.stock_daily "
                        "where trade_date <= %(e)s order by trade_date desc limit %(n)s) z order by 1",
                        {'e': end, 'n': int(recent)}, role='reader') or []
    return [r['trade_date'] for r in rows]


def days_between(start: dt.date, end: dt.date) -> List[dt.date]:
    rows = engine.query("select distinct trade_date from market_data.stock_daily "
                        "where trade_date between %(a)s and %(b)s order by 1",
                        (start, end), role='reader') or []
    return [r['trade_date'] for r in rows]


def run(days: Iterable[dt.date], *, dry_run: bool = False) -> Dict[str, Any]:
    """逐场算并写库。每场一行输出：首板只数、题材串覆盖、前一日各档计数 —— 当场就能看出
    这一场是"源站过期"还是"母体空了"，不用回头再查一遍库。"""
    total_rows = total_written = 0
    per_day: List[Dict[str, Any]] = []
    for day in days:
        # 整池一场只拉一次：同一份既喂首板分析（题材串/封单/首封），也喂首页那块涨停母体
        # （`app/compute/limit_up_pool.py`）。分开拉就是同一场问源站两遍，两遍还可能落到不同批。
        src = read_source(day)
        rows = compute_day(day, src=src)
        written = 0 if dry_run else write(rows)
        if not dry_run and not rows:
            cleared = clear_day(day)
            if cleared:
                print(f'  {day}：母体空，清掉上一轮残留 {cleared} 行', flush=True)
        lup = limit_up_pool.land_day(day, pool=src['pool'], src_gap=src['pool_gap'],
                                     dry_run=dry_run)
        shapes: Dict[str, int] = {}
        for r in rows:
            key = r.get('prev_shape') or '（判不了）'
            shapes[key] = shapes.get(key, 0) + 1
        with_reason = sum(1 for r in rows if r.get('reason'))
        universe = rows[0].get('universe') if rows else None
        # 源站那句话只在有日子级缺口时出声：'source_no_records'＝这一场永久缺（别指望明天有），
        # 'source_http_error'＝当场没拉到（今天还能重试）。两者在日志里必须长得不一样。
        gap = {r['missing'].get('reason') for r in rows if r.get('missing', {}).get('reason')}
        note = next((r.get('source_note') for r in rows if r.get('source_note')), None)
        total_rows += len(rows)
        total_written += written
        print(f'  {day}：首板 {len(rows)} 只｜本地封板 {universe} 家｜题材串 {with_reason} 只'
              f'{"（dry-run 不写库）" if dry_run else f"｜写入 {written} 行"}｜'
              f'涨停母体 {lup} 行｜前一日 '
              + ' '.join(f'{k}={v}' for k, v in sorted(shapes.items())), flush=True)
        if gap & {GAP_DAY_LEVEL}:
            print(f'    ⚠️ 源站这一场整场取不到（{GAP_DAY_LEVEL}，事后补不回来）：{note or "无详情"}',
                  flush=True)
        per_day.append({'day': day, 'rows': len(rows), 'written': written,
                        'reason': with_reason, 'universe': universe, 'shapes': shapes,
                        'source_gap': sorted(g for g in gap if g), 'source_note': note})
    return {'days': len(per_day), 'rows': total_rows, 'written': total_written,
            'per_day': per_day, 'dry_run': dry_run}


def verify(days: Optional[Sequence[dt.date]] = None) -> List[str]:
    """体检（不打网络）。红的那几条都是「错了不报错、只给一句听着合理的读数」的地方。"""
    bad: List[str] = []
    r = engine.query_one(
        f"""select count(*) n, count(distinct trade_date) days, min(trade_date)::text a,
                   max(trade_date)::text z,
                   count(*) filter (where prev_sealed) 前日封住,
                   count(*) filter (where reason is not null) 有题材串,
                   count(*) filter (where first_seal_at is not null) 有首封,
                   count(chg15) 有chg15, count(dbl_energy) 有加权分,
                   count(*) filter (where prev_shape is null) 形状判不了,
                   count(*) filter (where source_boards_agree is false) 板数不一致,
                   count(*) filter (where boards is null) 板数判不了
          from {TABLE} where spec_version = %s""", (SPEC_VERSION,), role='reader')
    if not r or not r['n']:
        return [f'{TABLE} 一行都没有 —— 回填没跑，还是写库坏了？']
    print(f'  库里：{r["n"]} 行 / {r["days"]} 场（{r["a"]} … {r["z"]}）｜题材串 {r["有题材串"]} 行、'
          f'首封 {r["有首封"]} 行、chg15 {r["有chg15"]} 行、加权分 {r["有加权分"]} 行', flush=True)
    print(f'  前一日形状判不了 {r["形状判不了"]} 行；几天几板：判不了 {r["板数判不了"]} 行、'
          f'与源站不一致 {r["板数不一致"]} 行', flush=True)

    # ① 首板定义带来的不变量：前一日收盘封住的行数必须为 0
    if int(r['前日封住']):
        bad.append(f'{r["前日封住"]} 行 prev_sealed=true（必须恒为 0）—— 前一天也封着就不是连板段的'
                   f'第一天，母体串了；先查这批行：select security_code, trade_date from {TABLE} '
                   f"where spec_version='{SPEC_VERSION}' and prev_sealed")

    # ② 与母体逐场对齐（只比本表已铺到的区间：母体有五年历史，本表按 --recent/--since 回填，
    #    不限区间会比出一屏假红）
    drift = engine.query(
        f"""with m as (select launch_date d, count(*) n from analytics.first_board_shape
                        where spec_version = %(fb)s and launch_date >=
                              (select min(trade_date) from {TABLE} where spec_version = %(fbd)s)
                       group by 1),
                t as (select trade_date d, count(*) n from {TABLE}
                       where spec_version = %(fbd)s group by 1)
           select coalesce(m.d, t.d)::text d, coalesce(m.n, 0) 母体, coalesce(t.n, 0) 本表
             from m full join t on t.d = m.d
            where coalesce(m.n, 0) <> coalesce(t.n, 0)
            order by 1 desc limit 15""",
        {'fb': SHAPE_SPEC, 'fbd': SPEC_VERSION}, role='reader') or []
    if drift:
        bad.append('与母体行数不一致的场次（本表区间内，前 15 场）：'
                   + '、'.join(f'{m["d"]}（母体 {m["母体"]} vs 本表 {m["本表"]}）' for m in drift))

    # ③ 本表的行必须全部在母体里（多出来的行不属于 fb-2 的尺子）
    orphan = engine.query_one(
        f"select count(*) n from {TABLE} t where t.spec_version = %s "
        f"and not exists (select 1 from analytics.first_board_shape f "
        f"where f.spec_version = %s and f.security_code = t.security_code "
        f"and f.launch_date = t.trade_date)", (SPEC_VERSION, SHAPE_SPEC), role='reader')
    if orphan and int(orphan['n']):
        bad.append(f'{orphan["n"]} 行不在母体里 —— 本表的母体必须只有 {SHAPE_SPEC} 当日行，'
                   f'多出来的是哪把尺子的行？')

    # ④ 近 5 场明细（分母随行交出，覆盖率要能当场口算）
    recent = engine.query(
        f"""select trade_date::text d, count(*) 首板只数,
                   count(*) filter (where reason is not null) 有题材串,
                   count(*) filter (where first_seal_at is not null) 有首封,
                   max(universe) 本地封板家数,
                   count(chg15) 有chg15, count(dbl_energy) 有加权分
              from {TABLE} where spec_version = %s
             group by 1 order by 1 desc limit 5""", (SPEC_VERSION,), role='reader') or []
    for x in recent:
        print(f'    {x["d"]}：首板 {x["首板只数"]} 只｜本地封板 {x["本地封板家数"]} 家｜'
              f'题材串 {x["有题材串"]}、首封 {x["有首封"]}、chg15 {x["有chg15"]}、'
              f'加权分 {x["有加权分"]}', flush=True)
    if days:
        print(f'  （本轮回填区间 {days[0]} … {days[-1]}，共 {len(days)} 场）', flush=True)
    return bad


# --------------------------------------------------------------------------- CLI

def _days_from_args(args: argparse.Namespace) -> List[dt.date]:
    end = (dt.date.fromisoformat(args.date) if args.date else latest_day())
    if end is None:
        return []
    if args.since:
        return days_between(dt.date.fromisoformat(args.since), end)
    if args.recent:
        return trade_days(end, max(1, int(args.recent)))
    return [end]


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description='首板分析（fbd-1）：母体＝first_board_shape 的 fb-2 当日行')
    ap.add_argument('--date', help='只算这一场（YYYY-MM-DD）')
    ap.add_argument('--since', help='从这一天到库里最新一场（YYYY-MM-DD）')
    ap.add_argument('--recent', type=int, help='最近 N 个场次（默认 1；带 --date 时是以该日结尾的 N 场）')
    ap.add_argument('--dry-run', action='store_true', help='只算不写库')
    ap.add_argument('--verify', action='store_true', help='体检（不打网络，可单独跑）')
    args = ap.parse_args(list(argv) if argv is not None else None)

    if args.verify:
        bad = verify()
        print('\n'.join(f'✗ {b}' for b in bad) if bad else '✓ 首板分析体检通过')
        return 3 if bad else 0

    days = _days_from_args(args)
    if not days:
        print('库里没有日线，或 --date 那场取不到日期 —— 没什么可算')
        return 1
    print(f'=== 首板分析（{SPEC_VERSION}，母体 {SHAPE_SPEC}）：{days[0]} … {days[-1]} 共 {len(days)} 场'
          f'{"（dry-run）" if args.dry_run else ""}', flush=True)
    res = run(days, dry_run=args.dry_run)
    if not res['rows']:
        print('  这一段里母体一条首板都没有（区间不对，还是 fb-2 没跑到这些场次？）—— 按设计不写，不算失败')
        return 2
    if args.dry_run:
        return 0
    print(f'  写入 {res["written"]} 行', flush=True)
    bad = verify(days)
    print('\n'.join(f'✗ {b}' for b in bad) if bad else '✓ 体检通过')
    return 0 if not bad else 3


if __name__ == '__main__':
    sys.exit(main())
