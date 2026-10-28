"""数据覆盖体检 —— 每张表覆盖哪段、缺哪几个交易日、还是压根没这张表。

## 为什么单独一层

页面上一列空白有两种完全不同的原因：「这几天本来就不是交易日」和「这表根本没数」。
把它们混成一个"暂无数据"，用户就没法判断该等、该补、还是该怀疑库坏了。
本模块把状态拆成四个，**且不许合并**：

| status | 含义 | 界面该怎么显示 |
|---|---|---|
| `ok` | 有数，且区间内交易日不缺 | 正常显示区间 |
| `gaps` | 有数，但区间内缺 N 个交易日 | 显示缺哪几天 + 首个缺日 |
| `no_data` | 表存在、能读，但 **0 行** | 显式说"空表"，不能显示成区间 |
| `missing_table` | 表不存在 | 说"该表未建"，不是"没数据" |
| `no_access` | 表在但当前账号无 SELECT 权限 | 说"无读权限"（第三种很容易被前两种掩盖） |

## 三条判据

1. **新鲜度只认数据日期**（`trade_date` / `dt` / `published_at`），不认文件时间、不认状态栏 ——
   本项目铁律。表里带 `updated_at`/`calculated_at` 也只当辅助值交出，不参与"缺不缺"的判断。
2. **缺口 = 与 `reference_data.trade_calendar` 里 `is_open=true` 的交易日交叉**，
   只在 `[min, max]` 区间内算 —— 区间外是"覆盖起点/终点"，不是缺口。
3. **分区父表在 `pg_stat_user_tables` 里恒为 0 行**（实测 `market_data.stock_daily`、
   `analytics.indicator_value` 都显示 0，而它们分别有 637 万 / 1428 万行）→ 行数走
   `pg_class.reltuples` 并**含分区求和**；大表给估算值且用 `rows_kind='estimated'` 标明，
   小表（≤30 万行）才给 `count(*)` 的精确值。**绝不用统计视图**，否则页面会说"日线是空表"。
4. **空表判定不用估算值**：估算说 0 时再跑一次 `exists(select 1 ...)` 确认，
    避免"刚建表还没 analyze"被误判成空表。
5. **"一天应该有 N 只"的表，`max(date)` 单独不足以证明它有数** —— 还要比这一场的**条数**
    （`ENTITY_DAY_TABLES`，实现只在 `duanxian/completeness.py` 一处）。实测教训：分钟线
    2026-09-23 只进了北交所 351 只（应 9,527 只），区间内一天不缺、行数也不算空 →
    旧判据报 `ok`，页面读出来就是"分钟线到 9.23 了"。**半截数据冒充全量比缺数更坏**：
    谁都会拿这 351 只去做全市场日内分析。
    判据在 2026-10-06 从"写死的 0.6 占比"换成**动态条数规则**（用户原话：新股只会更多，
    所以"近一个交易日的条数必须 ≥ 前第 2 个交易日的"）：每判一次把条数记进
    `<ASTOCK_DATA_HOME>\\completeness\\session_counts.json`，基线由这份登记自己长出来，
    只数不够就打 `entity_partial` + `coverage_note`，并点亮原有的 `thin_coverage` ⚠。
    2026-10-07 同一条再收紧：比较对象回到**前一天（池子里最新一场）**，"前第 2 个"那层跳过
    由"只登记判为全的场"顶替；回退额度从百分比换成**条数偏离值**（默认 30、分钟类 200，
    系统配置改得动），页面显示的是"允许少几条"，不是"允许少百分之几"。

耗时：全表 32 张表跑一遍实测 58 秒（含两条分钟线只数判据的 ~12 秒），
因此结果按进程缓存 10 分钟，`refresh=True` 才重算。
新增的只数判据（`ENTITY_DAY_TABLES`）每张登记表多 2~4 秒（换掉"上一场"那次查询后
比原先少一条 SQL）—— 只登记"一天应该有 N 只"的那几张，
别把 637 万行的 `stock_daily` 塞进来撞 `statement_timeout`。
"""

from __future__ import annotations

import datetime
import json
import logging
import os
import threading
import time
from typing import Any, Optional

from . import pg
from .completeness import COVERAGE_KINDS
from .paths import data_path
from .util import atomic_write_json


def _abnormal_spec() -> str:
    """异动提醒那张表的判据版本：从写侧现读，不在这里抄第二份。

    10-27 那次升号（abn-1 逐日相加 → abn-2 区间复利）就会被写死的版本号卡过 ——
    写侧升了、覆盖体检还在按旧批数行数，报出来的"覆盖到哪天"是旧批的日子。
    """
    from app.compute.abnormal_move import SPEC_VERSION as _s
    return _s


FRESHNESS_BASIS = (
    "数据日期（trade_date/dt/published_at）；时间戳列只作辅助展示，"
    "不参与『缺不缺』的判断"
)


def _now_iso() -> str:
    return datetime.datetime.now().astimezone().isoformat(timespec="seconds")

logger = logging.getLogger(__name__)


# 时间戳辅助列的取值优先级（只作展示，不参与缺口判断）
_TS_PREFERENCE = ("calculated_at", "updated_at", "captured_at", "created_at", "checked_at", "ingest_at")

# (key, 表, 日期列, 额外 where, 中文名, 已知硬缺口说明)
TARGETS: list[tuple[str, str, str, str, str, Optional[str]]] = [
    ("stock_daily", "market_data.stock_daily", "trade_date", "", "全市场日线", None),
    ("stock_minute_1m", "market_data.stock_minute", "dt", "and period = 1", "1 分钟线",
     "库里只有 2026-09-22、09-23 两天。⚠️ 但 vipdoc 里存着 **2026-04-27 起 104 个交易日**的 "
     "`.lc1`（每股 24,960 条）—— 更早的不是『没有』，是**没入库**：`c16_minute --since` 能补，"
     "代价实测约 190 字节/行，全市场 104 天 ≈ 2.8 亿行 / ~50 GB（当前全库 12 GB），"
     "要不要补、补几层范围是保留策略决定，不是脚本决定。"),
    ("stock_minute_5m", "market_data.stock_minute", "dt", "and period = 5", "5 分钟线",
     "与 1 分钟共用日期，分开统计是为了看清两个粒度各自的覆盖。vipdoc 存 103 个交易日。"),
    ("index_daily", "market_data.index_daily", "trade_date", "", "指数日线", None),
    ("sector_daily", "market_data.sector_daily", "trade_date", "", "板块日线", None),
    ("stock_snapshot", "market_data.stock_snapshot", "trade_date", "", "收盘快照（PE/PB/市值）",
     "窗口只有约 30 个交易日（2026-07-22 起）。更早的估值取不到，禁止用最新值回填历史。"),
    ("market_fund_flow", "market_data.market_fund_flow", "trade_date", "", "大盘资金流",
     "2026-09-30 实测 127 场 / 2026-03-27..2026-09-29，已随 `c11` 每天续上（一发给 120 场、"
     "带日期，所以**可以回补**）。早先那句「停在 09-17、行数极少」是当时没接调度入口的状态，已作废。"),
    ("fund_flow_rank", "market_data.fund_flow_rank", "trade_date", "", "个股主力净流入榜",
     "2026-09-30 实测 11,146 行 / 只有 2 场（09-28、09-29，各约 5,570 行）。"
     "写它的是 `c21_fundflow_sina`（新浪即时截面，**报文里没有日期字段**）⇒ "
     "**一天不采就永久少一天，补不回来**，别再按「能回补」排产。"
     "早先那句「空表 0 行」的真根因仍在：东财 push2 在本机约每 5~6 个请求断一次、"
     "全市场约 56 页拉不完（`c11` 那一步今天照样整批 ConnectionError），"
     "所以个股与板块资金流目前 100% 依赖新浪这一条。"),
    ("margin_detail", "market_data.margin_detail", "trade_date", "", "两融明细", None),
    ("dragon_tiger", "market_data.dragon_tiger_detail", "trade_date", "", "龙虎榜", None),
    ("dragon_tiger_inst", "market_data.dragon_tiger_institution", "trade_date", "", "龙虎榜机构席位", None),
    ("etf_share", "market_data.etf_share", "trade_date", "", "ETF 份额", None),
    ("fund_daily", "market_data.fund_daily", "trade_date", "", "基金份额净值", None),
    ("security", "reference_data.security", "", "", "证券主档",
     "无交易日期列（是快照表）。⚠️ `is_st` 的 212 行 updated_at 全停在 2026-08-27，"
     "是过期快照 → 用它推历史涨跌停档位会双向出错。"),
    ("stock_profile", "reference_data.stock_profile", "", "", "个股档案（静态估值）",
     "无日期列的当前快照，不许当时间序列用。"),
    ("sector_info", "reference_data.sector_info", "", "", "板块主档", None),
    ("sector_member", "reference_data.sector_member", "", "", "板块成分",
     "352,661 行 / 2,584 板块 / 30,126 个代码。**入库是全的**（与 `sector_daily.member_count` "
     "逐板块对账，695 个有行情的行业板块里 0 个少于通达信自称数的 60%）—— 但**能用的宽度分两块说**："
     "① 通达信『行业』是三级细分，行业中位数只有 **24 只/板块**，且 786 个里有 **219 个 ≤10 只**"
     "（`TDX_881251` 其他医疗服务 3 只、`TDX_881456` 仓储物流 6 只、`TDX_881248` 诊断服务 6 只）—— "
     "这不是缺数，是它就这么定义的，**拿它做『题材内部个股分层』没有统计量**；"
     "② `自定义` 板块 66.7% 的成员根本不是 A 股（AMZN/NVDA 等美股、399xxx 指数、`000001CNY01` 债券、"
     "以及不带后缀的裸码），每板块 A 股中位数 = 0。"
     "**任何按成分算的口径都要先 join `reference_data.security` 并过 `asset_type='stock'`**，"
     "概念/行业/风格/指数/地区过完是 96.7~100%，只有自定义会塌。"),
    ("market_breadth", "analytics.market_breadth", "trade_date", "", "全市场宽度（派生）",
     "涨停/跌停两列判据是单边 `close>=limit_up-0.01`，双向虚高（09-22 给 70/11，双边实算 65/4）"
     "→ 本表新鲜度可见，但那两个数不许直接取用。"),
    ("indicator_value", "analytics.indicator_value", "trade_date", "", "技术指标值",
     "覆盖起点 2026-06-02：2015~2025 的年度分区存在但**本就无数据**（属『从未回算历史』，"
     "不是库坏），按年抽样会抽到空集，先看清覆盖起点再判断。"),
    ("score", "analytics.score", "trade_date", "", "综合评分", None),
    ("score_detail", "analytics.score_detail", "trade_date", "", "评分明细", None),
    ("score_trend", "analytics.score_trend", "trade_date", "", "评分趋势", None),
    ("sector_trend", "analytics.sector_trend", "trade_date", "", "板块趋势", None),
    ("sector_allocator", "analytics.sector_allocator", "trade_date", "", "分配者视角（位置/温度/阶段/象限）",
     "2026-10-06 新增，**只有落库那天起才有历史**（当天首跑只有 1 场）。"
     "判据读的分红/市值快照/两融各自窗口不同：`stock_snapshot` 只 34 场、"
     "`margin_detail` 只 2026-06 起，所以温度里的杠杆项在更早的场次是空的 —— 那是缺料，不是 0。"),
    ("sector_heat", "analytics.sector_heat_daily", "trade_date", "", "板块热度", None),
    ("ignition", "analytics.ignition", "trade_date", "", "板块点火五档", None),
    ("volume_double", "analytics.volume_double", "trade_date", "", "倍量形态", None),
    ("market_sentiment_daily", "analytics.market_sentiment_daily", "trade_date", "", "市场情绪两档（首页折线）",
     "2026-10-06 新建。历史是由 analytics.market_breadth **回算**出来的（source=local，涨停数是单边判据），"
     "往后每天收盘由 e1 的 `sentiment` 步抓乐咕覆盖成 source=legu。breadth 只有 79 场（2026-06-05 起），"
     "所以这张表也不可能比它更早。"),
    ("sector_fund_flow_daily", "market_data.sector_fund_flow_daily", "trade_date", "", "行业资金流（同花顺即时榜落库）",
     "2026-10-06 新建，**过去没有任何地方存过这份榜 ⇒ 空表是预期，不是坏了**："
     "接口只有「即时」一档（3/5/10 日窗口在本机 akshare 1.18.97 已坏），报文里没有日期字段，"
     "非交易日采到的是上一场的数却无法证明，所以写入侧归属超窗就拒写。从 10-09 收盘起每天一场。"),
    ("qd_sentiment_cycle", "qd.sentiment_cycle", "trade_date", "", "情绪周期（quantdash 口径轨）", None),
    ("qd_board_ladder", "qd.board_ladder", "trade_date", "", "连板天梯（qd 轨）", None),
    ("qd_board_ladder_member", "qd.board_ladder_member", "trade_date", "", "天梯成员（qd 轨）", None),
    ("qd_sector_rotation", "qd.sector_rotation", "trade_date", "", "板块轮动（qd 轨）", None),
    ("qd_sector_follow", "qd.sector_follow", "trade_date", "", "板块前三甲轨迹与相似榜（qd 轨）",
     "2026-10-25 新建，由同一步（e1 的 `qd_sector`）在轮动矩阵之后一并算出。"
     "一天 12 行＝前三甲 × 错开 0/1/2/3 四档。为什么单独一张而不是读 `sector_rotation`："
     "rotation 每天只落前 8 名，答不出「某个板块某天排第几」，而形状匹配要全母体逐场名次。"
     "缺一天 = 首页那一块停在上一批，界面会照实说「这一场没有落库行」，不现算冒充。"),
    ("qd_stock_strength", "qd.stock_strength", "trade_date", "", "个股强弱分档（qd 轨）", None),
    ("qd_news_item", "qd.news_item", "published_at", "", "快讯（qd 轨镜像）",
     "PG 侧 0 行是**预期**：快讯现在落 YuanShan 本地 jsonl（`data_path(\"news\")`），"
     "本表退为可选镜像。判断快讯有没有数请看本地目录，别看这张表。"),
    # 10-15 新接的两张判据表（趋势股形状榜 / 节点分析）。两张都只读 `stock_daily`
    # （`board_node_daily` 另读 `index_daily` 的 000001.SH），漏一天就是对应那一屏/那一列断一格。
    ("trend_stock_daily", "analytics.trend_stock_daily", "trade_date", "and spec_version = 'trend-1'",
     "趋势股形状榜（近 5/10 日每天收盘严格站上当日 MA5 × 当天不涨停）",
     "判据版本随 `app/compute/trend_stock.py::SPEC_VERSION` 一起改；旧版本行留着对账，"
     "所以这里**必须带 spec_version 条件**，否则 max(trade_date) 会挑到旧批那一台。"),
    ("board_node_daily", "analytics.board_node_daily", "trade_date", "and spec_version = 'node-1'",
     "节点分析（首板 × 大盘 × 前一日最高板梯队清空）",
     "母体只收当天封住涨停的票，所以一天几十行是**正常**（不是缺数）；"
     "某天上证那根日线没入库时整场记 `sh_row_missing`，那一列是「判不了」不是「没命中」。"),
    ("abnormal_move_daily", "analytics.abnormal_move_daily", "trade_date",
     f"and spec_version = '{_abnormal_spec()}'",
     "异动提醒（偏离值 / 异常波动 / 严重异常波动 / 明日涨停前瞻）",
     "母体是筛出来的提醒榜（实测 2026-10-15：422 只 / 全市场 5,214 只）⇒ 一天几百行是**设计如此**，"
     "行数少不等于缺数。判据读四类基准指数（000002/399107/399102/000680）的**收盘价现算涨跌幅**，"
     "不读 `index_daily.pct_change`（那一列近 28 场缺 18 场）；指数那一天没入库 ⇒ 整场偏离值判不了，"
     "行照写但 `severe` 是 NULL + `severe_reason`，不许读成「没有一只踩到」。"),
    # 首板形状：一行＝一个连板段的第一天（10-17 新表，fb-2）。日期列用 launch_date。
    ("first_board_shape", "analytics.first_board_shape", "launch_date",
     "and spec_version = 'fb-2'", "首板形状预示（启动前量能形状 → 走到几板 / 次日进场收益）",
     "母体是**筛出来的事件表**（一天几十到三百条首板）⇒ 行数少不等于缺数。"
     "`seg_complete=false` 的那些行段长是**下界**（段还在走），读侧默认剔掉它们；"
     "北交所那批 `bench_code`/`ex*` 必为 NULL（本机没有对应分类指数），不是漏采。"
     "超额基准的起点＝进场当日对应指数的**开盘**（fb-2；fb-1 用的是当日收盘，ex1 会恒等于 r1）。"),
    # 首板分析（10-21 新表，fbd-1）：一行＝一场首板当日的那只票（母体取自 fb-2，不是自选名单）。
    ("first_board_daily", "analytics.first_board_daily", "trade_date",
     "and spec_version = 'fbd-1'", "首板分析（首板当日读数 + 前一交易日走势与形态档）",
     "母体是**筛出来的事件表**（一天几十到三百条首板）⇒ 行数少不等于缺数；"
     "涨停原因/首封时间/炸板次数来自同花顺涨停揭秘池，ST 与北交所基本不在池里，"
     "串空着通常是「源站不收」而不是「本机没采到」（`missing` 列逐行记哪一种）。"
     "`prev_sealed` 在首板定义下恒为 false（全表实测 0 行为 true），那是一条自检不是筛子；"
     "`boards` 为 NULL 是本地算不出这一场走到几板，不等于「没连板」。"),
    # 首页「涨停与连板」的母体（10-26 新表，lup-1）：一行＝(这只票, 这一场) 的触板/封板读数。
    ("limit_up_pool_daily", "analytics.limit_up_pool_daily", "trade_date",
     "and spec_version = 'lup-1'", "涨停与连板母体（触板/封住/连板数/自算首封/封单额/题材串）",
     "母体是**当日触过板的全部**（含收盘没封住的那些）⇒ 炸板率的分母在这里。"
     "首封/开板次数是本地 1 分钟线自算，分钟线只从 2026-08-14 起（10-20/10-21 两天整天没采到），"
     "更早的场次那两列为 NULL，`seal_basis` 标的是这一格实际用了哪把尺；"
     "封单额与题材串只有同花顺整池给（池不含北交所、基本不含 ST），串空着是「源站不收」不是「没采到」。"),
    # 启动前象限轨迹：一行＝(票, 启动日, cohort, capability_key)（10-17 新表，sqt-1）。日期列用 launch_date。
    ("startup_quadrant_track", "analytics.startup_quadrant_track", "launch_date",
     "and spec_version = 'sqt-1'", "启动前 N 场四维象限轨迹（分数 / 名次 / 象限位置变化）",
     "母体是**筛出来的事件表**（sv-2 那四类启动事件，一天几条到几十条）⇒ 行数少不等于缺数。"
     "`launch_date` 只有启动日那几天有行，不是每天该有数的表，按「区间内缺哪几个交易日」判会天天报缺。"
     "读数只取 `analytics.score` 里 `is_stale=false` 的那一批（旧算法迁入批列注释写明不可混比）⇒ "
     "启动日早于可用批次的那些事件窗口凑不满 N 场，`bars<window_days` 是**覆盖事实不是缺数**。"
     "`quad_first`/`quad_switches`/`q1_days..q4_days` 在 `capability_key<>'oper_score'` 的行**必为 NULL**（"
     "库里那六个键就没有象限，`market_mood` 一族不进表），不是漏采；这些行要是出现 0，就是写坏了。"),
    # 爆量/急拉联合计数：一行＝一个母体事件的窗口（10-18 新表，ssd-1）。日期列用 launch_date。
    ("startup_spike_days", "analytics.startup_spike_days", "launch_date",
     "and spec_version = 'ssd-1'", "启动前爆量/急拉 × 量增/量缩（日线代理，联合计数）",
     "母体是**筛出来的事件表**（anyfirst＝fb-2 全部首板 + sv-2 四类，只在启动日有行）⇒ 行数少不等于缺数，"
     "按「区间内缺哪几个交易日」判会天天报缺。这是**日线代理**判据（一根交易日一个数），不是分时图上的"
     "爆量/急拉；`jump_days` 为 0 是「凑满窗口真的一次没拉高」而 NULL 是「窗口不满」——两件事，"
     "都不算缺数。`outcome_*` 从 fb-2 join：非 anyfirst 档（尤其 jump40）outcome 为空是设计如此。"),
    # 财务多期是**按「票·报告期·档」存**的，不是按交易日存 ⇒ 日期列给空串：这里算不出区间与缺口，
    # 新鲜度只能看 `fetched_at`（读侧 `duanxian/stock_fundamentals.py` 那句"多久没重采"就是它）。
    ("stock_financial_period", "reference_data.stock_financial_period", "", "",
     "个股财务多期（同花顺，累计档 + 单季档）",
     "不按交易日存 ⇒ 本页算不出区间/缺口，新鲜度只看 `fetched_at`。每日链那一步（`financials`）"
     "**只刷本机名单**（自选 ⊃ 持仓）且自限 6 分钟，所以名单外的票停在最后一次全库回刷那天 —— "
     "那不是坏了，是全市场一遍实测约 100 分钟（2026-10-16：1,750 只 37 分钟），不能天天跑。"
     "源只有同花顺一份：它断供时这张表就停在最后一场。"),
]

_KEYS = {t[0] for t in TARGETS}

#: **「一天应该有 N 只」的表：只数也要进判据。**
#: 键 → (模式, 列)。`entities` 数 `count(distinct 列)`（分钟线一天每股 240 根，
#: 光看行数会被量级掩盖）；`rows` 数当日行数（每股一天的表本来就是一行一股，
#: 不需要 distinct，实测 0.03~0.16 秒）。
#: 实测教训：分钟线 2026-09-23 只进了北交所 351 只 / 83,523 行（全市场应 9,527 只 /
#: 约 227 万行），日期不缺、场次不缺 → 旧判据报 ok，页面读出来就是"分钟线到 9.23 了"。
#: **半截数据冒充全量比缺数更坏**：用户会拿 351 只票做全市场日内分析。
ENTITY_DAY_TABLES = {
    'stock_minute_1m': ('entities', 'security_code'),
    'stock_minute_5m': ('entities', 'security_code'),
    'stock_daily': ('rows', None),
    'stock_snapshot': ('rows', None),
    'index_daily': ('rows', None),
    'sector_daily': ('rows', None),
}

#: 这张表归 `completeness` 里的哪个口径 —— 名字对不上就两边判据分叉，所以只此一处映射。
def _kind_for(key: str) -> str:
    return COVERAGE_KINDS.get(key, key)


def _entity_day_check(key: str, mode: str, col: str, table: str, date_col: str,
                      where: str, hi: str) -> dict:
    """这一场的条数够不够 —— 判据不在这里写，一律交 `duanxian/completeness.py`。

    以前这里自己发两条 SQL（上一场是哪天、两天各多少只）再比一个写死的 0.6 占比。
    2026-10-06 换成动态规则后只剩一条 SQL：数这一场的条数，基线是系统每天登记下来的
    那些场。少一条查询是因为**基线不再依赖库里恰好有上一场**
    （头几天、或上一场整场没入库时，原先那个 `max(date)` 会直接算不出基线就放行）。

    2026-10-07 判据从"≥ 基线（带百分比容差）"换成"**偏离基线不超过 N 条**"（N 在系统配置里
    改得动，分钟类单独一档），所以这里交的是 `entity_deviation` 而不是比例 ——
    页面上要写得是"允许少几条"，不是"允许少百分之几"。
    """
    from . import completeness

    expr = 'count(distinct %s)' % col if mode == 'entities' else 'count(*)'
    r = _q("select %s as c from %s t where true %s"
           " and t.%s >= date '%s' and t.%s < date '%s' + 1"
           % (expr, table, where, date_col, hi, date_col, hi))
    now_n = None if not r or r[0]["c"] is None else int(r[0]["c"])
    kind = _kind_for(key)
    v = completeness.judge(kind, hi, now_n, source="coverage")
    out = {"entities_latest": now_n, "entities_baseline": v["baseline"],
           "baseline_date": v["baseline_date"], "baseline_basis": v["baseline_basis"],
           "sessions_back": v["sessions_back"], "entity_deviation": v["deviation"],
           "deviation_from": v["deviation_from"],
           "entity_ratio": (None if not (now_n and v["baseline"])
                            else round(now_n / v["baseline"], 3)),
           "completeness_why": v["why"],
           # 数没查出来是"查不到"，不是"半截"—— 不许把失败伪装成比例问题
           "entity_partial": bool(v["complete"] is False and now_n is not None)}
    if out["entity_partial"]:
        out["coverage_note"] = v["why"]
    return out


def _q(sql: str) -> Optional[list[dict]]:
    df = pg.fetch_df(sql)
    if df is None:
        return None
    return df.to_dict("records")


def _row_estimate(table: str) -> Optional[int]:
    """行数走 `pg_class.reltuples` **含分区求和**，不扫全表。

    两个坑都实测过：① `pg_stat_user_tables.n_live_tup` 对分区父表恒为 0（`stock_daily`、
    `indicator_value` 都显示 0，实际 637 万 / 1428 万）→ 用视图会得出"日线是空表"；
    ② 直接 `count(*)` 在 1400 万行上撞 `ft_reader` 的 60 秒 statement_timeout。
    所以大表交**估算值**并用 `rows_kind='estimated'` 标出来，不假装是精确数。
    """
    rows = _q("""with t as (select to_regclass('%s') as oid)
select coalesce(sum(greatest(c.reltuples, 0))::bigint, -1) as est
from pg_class c, t
where c.oid = t.oid
   or c.oid in (select inhrelid from pg_inherits where inhparent = t.oid)""" % table)
    if not rows or rows[0]["est"] is None or int(rows[0]["est"]) < 0:
        return None
    return int(rows[0]["est"])


def _probe(target: tuple, ts_cols: dict) -> dict:
    key, table, date_col, where, label, note = target
    out: dict[str, Any] = {"key": key, "table": table, "label": label,
                           "known_issue": note,
                           "ts_column": ts_cols.get(table)}

    exists = _q("select to_regclass('%s') as r" % table)
    if not exists or exists[0]["r"] is None:
        return {**out, "status": "missing_table", "rows": None}

    acc = _q("select has_table_privilege(current_user, '%s', 'SELECT') as ok" % table)
    if not acc or not acc[0]["ok"]:
        return {**out, "status": "no_access", "rows": None}

    est = _row_estimate(table)
    heavy = bool(est) and est > 300000 and not where     # 带 where 的估算分不了粒度，退回实算
    if heavy:
        out["rows"], out["rows_kind"] = est, "estimated"
        any_row = _q("select exists(select 1 from %s where true %s limit 1) as any" % (table, where))
        if any_row is None:
            return {**out, "status": "error", "rows": None,
                    "reason": "存在性查询失败，未取到任何数字（不当成 0）"}
        if not any_row[0]["any"]:
            out["rows"], out["rows_kind"] = 0, "exact"
    else:
        cnt = _q("select count(*) as rows from %s where true %s" % (table, where))
        if cnt is None:
            return {**out, "status": "error", "rows": None,
                    "reason": "行数查询失败（多为 statement_timeout 60s 撞车），未取到任何数字"}
        out["rows"], out["rows_kind"] = int(cnt[0]["rows"]), "exact"

    if not date_col:
        out["min_date"] = out["max_date"] = None
        out["status"] = "ok" if out["rows"] else "no_data"
        out["date_semantics"] = "snapshot"        # 快照表：没有"缺哪天"这件事
        if out["ts_column"]:
            tsr = _q("select max(%s)::timestamptz as t from %s where true %s"
                     % (out["ts_column"], table, where))
            out["last_touched"] = str(tsr[0]["t"]) if tsr and tsr[0]["t"] else None
        return out

    out["date_semantics"] = "session"
    if not out["rows"]:
        out["status"] = "no_data"
        out["missing_sessions"] = None
        return out

    span = _q("select min(%s)::date as lo, max(%s)::date as hi from %s where true %s"
              % (date_col, date_col, table, where))
    if span is None or span[0]["lo"] is None:
        out["status"] = "no_data"
        out["min_date"] = out["max_date"] = None
        out["missing_sessions"] = None
        return out
    out["min_date"], out["max_date"] = str(span[0]["lo"]), str(span[0]["hi"])
    if out["ts_column"]:
        tsr = _q("select max(%s)::timestamptz as t from %s where true %s"
                 % (out["ts_column"], table, where))
        out["last_touched"] = str(tsr[0]["t"]) if tsr and tsr[0]["t"] else None

    lo, hi = out["min_date"], out["max_date"]
    # ⚠️ 缺口只在日历侧逐日探存在性，且**必须写成区间比较**。原先写
    # `t.col::date = c.calendar_date`，那个 cast 让 trade_date 上的索引失效 →
    # 每个交易日一次全表扫，1400 万行的 `indicator_value` 直接撞 60 秒 statement_timeout。
    # 同理不要对大表做 `count(distinct date)` / `not in (select distinct ...)`（实测全量 132 秒）。
    # 扫到**今天**而不是只扫到 max_date：一张表停在昨天，光看"区间内不缺"会是 ok，
    # 而"停更"恰恰是最该被看见的那几天。两类缺分开交（missing / stale），不合并。
    gap = _q("""with span as (
  select c.calendar_date as d,
         c.calendar_date <= '%s'::date as in_span,
         exists(select 1 from %s t where true %s
                and t.%s >= c.calendar_date and t.%s < c.calendar_date + 1) as present
  from reference_data.trade_calendar c
  where c.is_open and c.calendar_date between '%s'::date and current_date),
miss as (select d, in_span from span where not present)
select (select count(*) from span) as expected,
       (select count(*) from span where present) as present_days,
       (select count(*) from miss where in_span) as miss_cnt,
       (select count(*) from miss where not in_span) as stale_cnt,
       (select min(d)::date from miss where in_span) as first_missing,
       (select max(d)::date from miss where not in_span) as last_stale,
       (select array_agg(d) from (select d from miss where in_span order by d limit 10) x) as samples"""
                 % (hi, table, where, date_col, date_col, lo))
    if gap and gap[0]["miss_cnt"] is not None:
        out["calendar_sessions_to_today"] = int(gap[0]["expected"])
        out["sessions_present"] = int(gap[0]["present_days"])
        out["missing_sessions"] = int(gap[0]["miss_cnt"])
        out["stale_sessions"] = int(gap[0]["stale_cnt"])      # max_date 之后停更的场次
        out["last_stale_date"] = str(gap[0]["last_stale"]) if gap[0]["last_stale"] else None
        out["first_missing"] = str(gap[0]["first_missing"]) if gap[0]["first_missing"] else None
        out["missing_samples"] = [str(x) for x in (gap[0]["samples"] or [])]
        out["status"] = "gaps" if (out["missing_sessions"] or out["stale_sessions"]) else "ok"
        # 分钟线就是这一类：区间内"一天不缺"（min=max=2026-09-22）→ 状态是 ok，
        # 但只覆盖 1 个交易日。不另起状态（那是第六种含义），用这个标记让页面必须打 ⚠，
        # 否则"ok"会被读成"这表很全"。
        out["thin_coverage"] = bool(out.get("sessions_present")
                                    and out["sessions_present"] <= 5)
        # 「一天应该有 N 只」的表再加一道：**日期不缺 ≠ 数不缺**。
        # 09-23 的分钟线只进了北交所 351 只（应 9,527 只），场次不缺、行数也不算空，
        # 只看上面那几条它会报 ok —— 那正是"半截数据冒充全量"的读法。
        ent_spec = ENTITY_DAY_TABLES.get(key)
        if ent_spec:
            mode, col = ent_spec
            extra = _entity_day_check(key, mode, col, table, date_col, where,
                                      out["max_date"])
            out.update(extra)
            if extra.get("entity_partial"):
                out["thin_coverage"] = True
    else:
        out["status"] = "error"
        out["missing_sessions"] = None
        out["reason"] = "缺口查询失败，未取到缺天数（不当成 0）"
    return out


_refreshing = threading.Lock()                      # 后台重算只许一个在跑

CACHE_FILE = "coverage_cache.json"
FRESH_SECONDS = 600                 # 10 分钟内算"新鲜"，直接给
STALE_WARN_SECONDS = 20 * 3600      # 超过一天就是真旧了，页面上必须标红


def _cache_path() -> str:
    d = data_path("coverage")
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, CACHE_FILE)


def _load_disk() -> dict:
    try:
        with open(_cache_path(), encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_disk(keys: dict, payload: Optional[dict]) -> None:
    """整份写盘。每条都裹 `{saved_at, ...}` 信封 —— 缓存必须知道自己几时算的，
    不然页面拿昨天的覆盖说今天，而界面上是绿的。"""
    old = _load_disk()
    merged = dict(old.get("keys") or {})
    merged.update({k: v for k, v in (keys or {}).items()})
    saved = _now_iso()
    envelope = dict(old.get("all") or {})
    if payload is not None:
        envelope = {"saved_at": saved, "payload": payload}
    atomic_write_json(_cache_path(), {"saved_at": saved, "keys": merged, "all": envelope})


def _age_seconds(saved_at: Optional[str]) -> Optional[float]:
    if not saved_at:
        return None
    try:
        return max(0.0, (datetime.datetime.now().astimezone()
                         - datetime.datetime.fromisoformat(saved_at)).total_seconds())
    except ValueError:
        return None


def _kick_refresh() -> None:
    """过期时后台重算：页面先拿到旧数据（标 is_stale），不让人等 53 秒。"""
    if not _refreshing.acquire(blocking=False):
        return

    def _job() -> None:
        try:
            report(refresh=True)
        except Exception as exc:      # noqa: BLE001  重算失败下次再来，不许冒泡
            logger.warning("coverage 后台重算失败：%s: %s", type(exc).__name__, str(exc)[:160])
        finally:
            _refreshing.release()

    threading.Thread(target=_job, daemon=True, name="coverage-refresh").start()


def _calendar_span() -> dict:
    rows = _q("""select count(*) filter (where is_open) as open_days,
  min(calendar_date)::date as lo, max(calendar_date)::date as hi
from reference_data.trade_calendar""")
    if not rows:
        return {"available": False, "reason": "交易日历读不到，缺口一律算不出来"}
    return {"available": True, "open_days": int(rows[0]["open_days"]),
            "from": str(rows[0]["lo"]), "to": str(rows[0]["hi"])}


def _serve_cached(entry: dict, table: str) -> "dict | None":
    """命中磁盘缓存就用它 —— **过期也先用**，同时后台重算。

    为什么宁可给旧的：页面拿不到数据就回退成"未接入"，那 53 秒的全量能力等于白做。
    但旧数据必须自带身份：`generated_at` 是**算出来那一刻**，不是"现在"，
    并交出 `is_stale` / `very_stale`，否则拿昨天的覆盖说今天，比没缓存更坏。
    """
    if not entry:
        return None
    body = entry.get("payload") if table == "all" else entry.get("item")
    if not body:
        return None
    age = _age_seconds(entry.get("saved_at"))
    meta = {"source": "disk", "saved_at": entry.get("saved_at"),
            "age_seconds": None if age is None else int(age),
            "is_stale": bool(age is None or age > FRESH_SECONDS),
            "very_stale": bool(age is None or age > STALE_WARN_SECONDS),
            "fresh_within_seconds": FRESH_SECONDS}
    if meta["is_stale"]:
        _kick_refresh()
    if table == "all":
        out = dict(body)
        out["cache"] = meta
        return out
    out = dict(body)
    return {"available": out.get("status") != "error", "reason": None,
            "generated_at": entry.get("saved_at"), "freshness_basis": FRESHNESS_BASIS,
            "cache": meta,
            "trade_calendar": entry.get("calendar"),      # 缓存里带着，命中时一次库都不问
            "counts": None, "tables": [out]}


def report(table: str = "all", refresh: bool = False) -> dict:
    """`table` = TARGETS 里的 key，或 `all`。未识别的 key 直接报出来，不静默返回空。

    取数两层：磁盘缓存（跨重启，`data_path("coverage")/coverage_cache.json`）→ 实算。
    `refresh=True` 才强制实算（每日预计算与页面"重算"按钮走这条）。
    """
    if table != "all" and table not in _KEYS:
        return {"available": False,
                "reason": "未知的表 key：%r；可选值见 keys" % table,
                "keys": sorted(_KEYS)}

    stale_served = None
    if not refresh:
        disk = _load_disk()
        hit = disk.get("all") if table == "all" else (disk.get("keys") or {}).get(table)
        served = _serve_cached(hit, table)
        if served is not None:
            # 有缓存就直接给（过期也给，`_serve_cached` 里已经甩了后台重算）。
            # 这里若同步重算，页面还是要等那 53 秒 —— 那正是本轮要修的问题本身。
            return served

    if not pg.enabled():
        if stale_served is not None:
            stale_served["reason"] = "本地库连不上，交出的是上一次缓存（见 cache.saved_at）"
            return stale_served
        return {"available": False, "reason": "本地库未配置或连不上（VIBE_PG / PG_USER / PG_PASSWORD）"}

    cal = _calendar_span()
    ts_cols = {}
    ts_rows = _q("""select table_schema||'.'||table_name as t, string_agg(column_name, ',') as cols
from information_schema.columns
where table_schema in ('market_data','reference_data','analytics','qd')
  and column_name in ('%s')
group by 1""" % "','".join(_TS_PREFERENCE)) or []
    for r in ts_rows:
        picked = next((c for c in _TS_PREFERENCE if c in str(r["cols"]).split(",")), None)
        if picked:
            ts_cols[r["t"]] = picked

    wanted = TARGETS if table == "all" else [t for t in TARGETS if t[0] == table]
    items, new_keys = [], {}
    for t in wanted:
        try:
            item = _probe(t, ts_cols)
        except Exception as exc:      # noqa: BLE001  一张表查坏不许连累整页
            logger.warning("coverage 探测失败 %s: %s", t[0], exc)
            item = {"key": t[0], "table": t[1], "label": t[4],
                    "status": "error", "rows": None,
                    "reason": "%s: %s" % (type(exc).__name__, str(exc)[:160])}
        new_keys[t[0]] = {"saved_at": _now_iso(), "item": item, "calendar": cal}
        items.append({**item, "seconds_to_compute": None})

    saved = _now_iso()
    payload = {
        "available": any(i["status"] != "error" for i in items),
        "reason": None,
        "generated_at": saved,
        "freshness_basis": FRESHNESS_BASIS,
        "trade_calendar": cal,
        "counts": {s: sum(1 for i in items if i["status"] == s)
                   for s in ("ok", "gaps", "no_data", "missing_table", "no_access", "error")},
        "tables": items,
        "cache": {"source": "live", "saved_at": saved, "age_seconds": 0,
                  "is_stale": False, "very_stale": False,
                  "fresh_within_seconds": FRESH_SECONDS},
    }
    _save_disk(new_keys, payload if table == "all" else None)   # 信封见 _save_disk
    return payload
