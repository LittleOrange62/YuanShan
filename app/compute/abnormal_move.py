# -*- coding: utf-8 -*-
"""异动提醒：偏离值累计 + 异常波动/严重异常波动（公开监管判据的本机复算）→ `analytics.abnormal_move_daily`。

用户 2026-10-15 点名要的三件事，一台机器上全部按**本地日线 + 本地指数**算，不读任何外部异动接口：

  1. **明日涨停会不会踩到严重异动**（前瞻）。三条档各算各的"还差多少涨幅"再取最小者：
     10 日累计偏离到 100、30 日累计偏离到 200、明日那一场自己构成一次异常波动且让 10 日内
     凑满 4 次（主板）/3 次（双创）。⚠️ 前两档的基准是**明日那个窗口去掉明日**的累计
     （9 场 / 29 场，不是今天的 10 场 / 30 场）——明日会把今天窗口里最早那场挤出去，不扣就会
     把所需涨幅算小、榜里出现「触发价比涨停价还高」的自相矛盾行。交出来的数是
     **触发所需涨幅、触发价、还差的偏离点数**，不是"会涨到"的承诺。
     ⚠️ 10-27 起这一栏不再把"今天已踩到某一条"的票整只挡在外面（旧版就是这么把大业股份、
     海天瑞声弄没的）：它照样进榜，榜上那一格读同一行的 `severe_how` 说它已踩到哪一条、
     读 `trigger_pct` 说它离**下一条**还差多少个点；三条全踩过的才不进（那时算不出新缺口）。
  2. **热门股的偏离值榜**（近 10 场封过板 或 10 日累计偏离 ≥25 点 或 30 日 ≥60 点）。
  3. **近期已经踩到严重异动的**：异动日期、踩到哪几条、次日涨幅、次日是否停牌（有证据的缺席）。

规则出处与本机口径（2026-10-15 逐条网上核对，见 `docs/功能点说明/首页/异动提醒.md`）：

  · **单日偏离值 = 个股当日涨跌幅 − 对应分类指数当日涨跌幅**。按板块各取其官方基准：
    沪主板 `000002.SH` Ａ股指数、深主板 `399107.SZ` 深证Ａ指、创业板 `399102.SZ` 创业板综、
    科创板 **`000680.SH` 科创综指**（⚠️ 不是 `000688.SH` 科创50 —— 名字差一个字，用错整列会歪）。
    指数涨跌幅**一律用收盘价现算**（close ÷ 前一场 close − 1）：本机 `index_daily.pct_change`
    在 2026-10-15 实测最近 28 场缺 18 场，而窗口 `sum()` 会静默跳过 NULL ⇒ 累计偏离偏低却看不出，
    这种错比报不出来更坏，所以这一列一次都不读。
  · **异常波动**：连续 3 个交易日偏离值累计 ≥ +20%（沪/深主板）或 ≥ +30%（创业板、科创板）；向下同量级。
  · **严重异常波动（任一）**：① 连续 10 个交易日内 4 次（主板）/ 3 次（双创）**同向**异常波动；
    ② 连续 10 个交易日内偏离值累计 ≥ +100% 或 ≤ −50%；③ 连续 30 个交易日内 ≥ +200% 或 ≤ −70%。
  · **累计口径 = 区间复利**（2026-10-27 用户点名换的这把尺，`abn-2`）：
    窗口累计偏离 = 个股区间涨幅（窗口内逐日复利）− 指数区间涨幅（同样复利），
    SQL 里走 `exp(sum(ln(1+涨幅/100)))−1`。为什么换：交易所那句"偏离值累计"公开算法是**逐日相加**，
    而行情软件（用户拿来对账的那张榜）用的是**区间涨幅**——连板段两把尺能差 20~30 个点，
    逐日相加会**漏报**已触发的人。实测两例（同花顺标了「10日100%」而我们旧口径判"没踩到"）：
    近岸蛋白 688137.SH @2026-09-30 逐日相加 **78.7** / 区间复利 **108.5**；
    天山生物 300313.SZ @2026-08-25 **78.3** / **101.8**。⚠️ 旧行（`abn-1`）留在表里不覆盖，
    两把尺对不上的那段历史要看得回来，才准升版本号。
    窗口内任何一天缺读数 ⇒ 整档 NULL（判不了），绝不把缺的那几天当 0% 参与复利。
  · 上市前 5 个竞价交易日无涨跌幅限制 ⇒ **不纳入指标**（本机用 `reference_data.security_session_span`
    的 `fifth_session` 判，不是拿场次跨度猜）。

三条本机**做不到**的事，写成字段交出去而不是藏起来：

  · 真实规则里"指标自交易所公告之日（或复牌之日）起重新计算"。本机**没有公告日这份数据** ⇒
    `up_cnt_10`/`down_cnt_10` 是纯滚动计数，同一段连续上涨会比交易所口径更容易反复计到 ⇒
    这一屏只能说"按公开规则算出来踩到了"，不能说"交易所已认定"。
  · "10 个交易日内"是**按交易所日历**数的，不是按这只票有几根线：连续停牌的票若按行开窗，
    窗口会跨过停牌期把范围拉长。故全部窗口用场次序号做 `RANGE`（见 `_SQL` 里的 `sn`），
    停牌的日子不占位、也不被跳过。
  · 明日那只指数**按 0% 假设**（前瞻算不了指数）。这只影响"还差几个点"这一列的精度，
    且是**偏保守**：指数明日若继续跌，实际触发所需涨幅比本机的数更小。这句话必须上屏。

缺项一律 NULL + `severe_reason`/`universe_by` 短码 —— "判不了"（窗口不够根数、新股、指数缺收盘价）
与"没踩到"在界面上必须分得开，否则一只刚复牌的老票会被读成"30 日档没达到"。

用法：
    python -m app.compute.abnormal_move                        # 最新有日线的那一天
    python -m app.compute.abnormal_move --date 2026-10-15
    python -m app.compute.abnormal_move --recent 5
    python -m app.compute.abnormal_move --since 2025-10-15 --skip-done --max-minutes 30
    python -m app.compute.abnormal_move --dry-run              # 只算不写，打三块榜的前几条
    python -m app.compute.abnormal_move --verify --date 2026-10-15   # 回查库里行的自洽性

退出码：0 成功 / 1 没有可算的交易日 / 2 一趟都没写进去 / 3 有场次失败 / 4 自限预算用完还剩几场。
"""
from __future__ import annotations

import argparse
import datetime as dt
import math
import time
from decimal import Decimal
from typing import Any, Dict, List, Optional, Sequence, Tuple

from app.db import engine

SPEC_VERSION = 'abn-2'      # 10-27 换累计口径（逐日相加 → 区间复利），旧行 abn-1 留着对账
TABLE = 'analytics.abnormal_move_daily'

# ---- 判据阈值（出处见模块 docstring；改判据就换 SPEC_VERSION）----
AB_MAIN = 20.0        # 主板：连续 3 日累计偏离 ±20%
AB_GEM = 30.0         # 创业板、科创板：±30%
TIMES_MAIN = 4        # 主板：10 个交易日内 4 次同向异常波动
TIMES_GEM = 3         # 双创：3 次
DEV10_UP, DEV10_DOWN = 100.0, -50.0
DEV30_UP, DEV30_DOWN = 200.0, -70.0

# 母体门槛（实测 2026-10-15：约 400 只/场，全场 5,214 只 —— 这是提醒榜不是全市场快照）
U_DEV10 = 25.0        # 10 日累计偏离 ≥25 点
U_DEV30 = 60.0        # 30 日累计偏离 ≥60 点

# 窗口要覆盖 30 个交易日的偏离值累计 + 10 个交易日的次数计数（次数那条本身要看 3+10−1 场）
SCAN_SESSIONS = 45

#: 那一发全场窗口查询的排序内存（10-27 复利口径实测：默认 work_mem 下 spill 到盘、一场 70 秒；
#:  256MB 时留在内存里。只对本事务生效，见 `_run_sql`）
WORK_MEM = '256MB'

GEM_BOARDS = ('创业板', '科创板')
BOARDS = ('沪主板', '深主板', '创业板', '科创板')     # 北交所不在内：见下表注释
INDEX_OF = {'沪主板': '000002.SH', '深主板': '399107.SZ',
            '创业板': '399102.SZ', '科创板': '000680.SH'}

_COLUMNS = (
    'trade_date', 'security_code', 'spec_version', 'board', 'is_st',
    'close', 'pct_change', 'index_pct', 'dev_1d', 'bars_used', 'listed_days',
    'dev_3', 'dev_10', 'dev_30', 'dev_3_complete', 'dev_10_complete', 'dev_30_complete',
    'ab_up', 'ab_down', 'up_cnt_10', 'down_cnt_10',
    'severe', 'severe_how', 'severe_dir', 'severe_reason',
    'band_next', 'limit_price_next', 'need_dev_to_100', 'need_dev_to_200',
    'dev_10_if_limit', 'dev_30_if_limit', 'trigger_pct', 'trigger_price',
    'hit_if_limit_up', 'fwd_clauses', 'dev_prev2', 'times_if_limit',
    'next_trade_date', 'next_pct', 'next_halted', 'next_missing',
    'universe_hit', 'universe_by',
)

#: 一场算完的全部原始读数：窗口用场次序号 `sn` 做 RANGE ⇒ "连续 N 个交易日内"按交易所日历数。
#: ⚠️ 这里不读 index_daily.pct_change（缺 18/28 场，见 docstring），指数涨跌幅由 close 现算。
_SQL = """
with cal as (                                        -- 交易所日历（含每只票停牌的日子），带场次序号
    select trade_date, row_number() over (order by trade_date) as sn
    from (select distinct trade_date from market_data.index_daily
          where security_code = '000001.SH' and trade_date <= %(to)s
          order by trade_date desc limit %(scan)s) z
),
ix as (                                              -- 四只基准指数：收盘价现算涨跌幅
    select i.security_code, i.trade_date,
           100.0 * (i.close / nullif(lag(i.close) over (partition by i.security_code
                        order by i.trade_date), 0) - 1) as pct
    from market_data.index_daily i
    where i.security_code in ('000002.SH','399107.SZ','399102.SZ','000680.SH')
      and i.trade_date > (select min(trade_date) - 10 * interval '1 day' from cal)
),
idxp as (
    select trade_date,
           max(pct) filter (where security_code = '000002.SH') as sh,
           max(pct) filter (where security_code = '399107.SZ') as sz,
           max(pct) filter (where security_code = '399102.SZ') as cy,
           max(pct) filter (where security_code = '000680.SH') as kc
    from ix group by 1
),b as (                                               -- 个股日线（含北交所，后面按板块分流）
    -- ⚠️ 只带窗口与出口真正要用的列：这一份要过 23 万行的排序，多一列就多一分 spill 到盘的机会
    --   （10-27 换复利口径时加了两列 ln 就从 17 秒变 70 秒，Sort Method 直接变 external merge）。
    select s.security_code, s.trade_date, s.close, s.pre_close, s.pct_change, s.limit_up
    from market_data.stock_daily s
    where s.trade_date between (select min(trade_date) from cal) and %(to)s
),
j as (
    select b.*, c.sn,
           case when b.security_code ~ '^68' then '科创板'
                when b.security_code ~ '^30' then '创业板'
                when b.security_code ~ '^60' then '沪主板'
                when b.security_code ~ '^00' then '深主板'
                else '北交所' end as board,
           case when b.security_code ~ '^68' then '科创板'
                when b.security_code ~ '^30' then '创业板' else '主板' end as thr_group,
           case when b.security_code ~ '^68' then i.kc
                when b.security_code ~ '^30' then i.cy
                when b.security_code ~ '^60' then i.sh
                when b.security_code ~ '^00' then i.sz end as index_pct,
           -- ⚠️ 必须用 exists，不能用 left join：`security_st_period` 里 145 只票有多个档期、
           --   149 对档期互相重叠（10-15 实测），join 会把那一天叉成两行 ⇒ 窗口 sum() 把当天
           --   偏离值算两遍（实测有票 n_29 数出 30 场，而 28 preceding 的窗口最多 29 场）。
           exists (select 1 from reference_data.security_st_period st
                    where st.security_code = b.security_code
                      and b.trade_date >= st.valid_from
                      and (st.valid_to is null or b.trade_date <= st.valid_to)) as is_st,
           -- 上市前 5 个竞价交易日不纳入异动指标（交易所明文）。这一列必须进到"异常波动"的判定里：
           -- 只在最后写 severe 时排除是不够的 —— 前 5 日那些 3 日窗会被计进"10 日内同向次数"，
           -- 一只上市第 6~10 场的票于是能凭空凑出 4 次（实测 001246.SZ：只有 6 根日线却数出 4 次）。
           (sp.fifth_session is null or b.trade_date > sp.fifth_session) as past_new_5
    from b
    join cal c on c.trade_date = b.trade_date
    left join idxp i on i.trade_date = b.trade_date
    left join reference_data.security_session_span sp on sp.security_code = b.security_code
),
d as (
    select j.*, j.pct_change - j.index_pct as dev_1d,
           -- 区间复利的原料：ln(1 + 涨幅/100)。个股与指数各存一条，窗口里各自求和再 exp 回来相减。
           -- ⚠️ 缺读数那一天**交 NaN 而不是 NULL**：sum() 会静默跳过 NULL（那等于把缺的那天当
           --    零涨幅复利进去，累计被悄悄算小却看不出来），而 NaN 会顺着 sum/exp 一路传到
           --    出口，`_f()` 再把 NaN 折成 None ⇒ 界面念「判不了」。下游每一层都先用
           --    `ls = ls` 挡掉 NaN 再做阈值比较（float8 里 NaN 比一切数都大，直接 >= 会假阳性）。
           case when j.pct_change is not null and j.index_pct is not null
                 and j.pct_change > -100 and j.index_pct > -100
                then ln(1.0 + j.pct_change / 100.0) else 'NaN'::double precision end as lg_stock,
           case when j.pct_change is not null and j.index_pct is not null
                 and j.pct_change > -100 and j.index_pct > -100
                then ln(1.0 + j.index_pct / 100.0) else 'NaN'::double precision end as lg_index,
           (abs(j.close - j.limit_up) <= 0.005) as sealed
    from j where j.board <> '北交所'
),
w0 as (                                              -- 各窗口的对数和与根数（3 日窗是异常波动的原子）
    select d.*,
           sum(lg_stock)  over w3d as ls3, sum(lg_index) over w3d as li3,
           count(*)       over w3d as n_3,
           sum(lg_stock)  over w2d as ls2, sum(lg_index) over w2d as li2,
           count(*)       over w2d as n_2
    from d
    window w3d as (partition by security_code order by sn
                   range between 2 preceding and current row),
           w2d as (partition by security_code order by sn
                   range between 1 preceding and current row)
),
w1 as (                                              -- 复利差：个股区间涨幅 − 指数区间涨幅（百分点）
    select w0.*,
           case when n_3 = 3 and ls3 = ls3 and li3 = li3
                then (exp(ls3) - 1) * 100.0 - (exp(li3) - 1) * 100.0 end as dev_3,
           case when n_2 = 2 and ls2 = ls2 and li2 = li2
                then (exp(ls2) - 1) * 100.0 - (exp(li2) - 1) * 100.0 end as dev_prev2
    from w0
),
w2 as (                                              -- 由 3 日窗定"今天这一场算不算一次异常波动"
    select w1.*,
           (n_3 = 3 and past_new_5
              and dev_3 >= case when thr_group = '主板' then %(ab_main)s else %(ab_gem)s end) as ab_up,
           (n_3 = 3 and past_new_5
              and dev_3 <= -(case when thr_group = '主板' then %(ab_main)s else %(ab_gem)s end)) as ab_down
    from w1
),
w3 as (                                              -- 10 / 30 日的窗口原料与同向次数
    select w2.*,
           sum(lg_stock)   over w10 as ls10, sum(lg_index) over w10 as li10,
           count(*)        over w10 as n_10,
           sum(lg_stock)   over w30 as ls30, sum(lg_index) over w30 as li30,
           count(*)        over w30 as n_30,
           sum(ab_up::int)  over w10 as up_cnt_10,
           sum(ab_down::int) over w10 as down_cnt_10,
           max(sealed::int) over w10 as sealed_10,
           -- ↓ 前瞻那三档的"明日窗口去掉明日"：明日那个 10 日窗是 sn-8…sn+1（去掉明日剩 **9** 场），
           --   明日那个 30 日窗是 sn-28…sn+1（去掉明日剩 **29** 场）。不扣掉被挤出去的那场会把
           --   累计偏离算高 —— 实测 600241.SH 因此进了预警榜，而它明日涨停根本触发不了 10 日档。
           sum(lg_stock)   over w9  as ls9,  sum(lg_index) over w9  as li9,
           count(*)        over w9  as n_9,
           sum(lg_stock)   over w29 as ls29, sum(lg_index) over w29 as li29,
           count(*)        over w29 as n_29,
           sum(ab_up::int)  over w9  as up_cnt_9,
           sum(ab_down::int) over w9 as down_cnt_9,
           row_number()    over (partition by security_code order by sn) as bars_used
    from w2
    window w10 as (partition by security_code order by sn
                   range between 9 preceding and current row),
           w30 as (partition by security_code order by sn
                   range between 29 preceding and current row),
           w9  as (partition by security_code order by sn
                   range between 8 preceding and current row),
           w29 as (partition by security_code order by sn
                   range between 28 preceding and current row)
),
w4 as (                                              -- 10 / 30 日的复利累计（根数不够或有缺读数 ⇒ NULL＝判不了）
    select w3.*,
           case when n_10 = 10 and ls10 = ls10 and li10 = li10
                then (exp(ls10) - 1) * 100.0 - (exp(li10) - 1) * 100.0 end as dev_10,
           case when n_30 = 30 and ls30 = ls30 and li30 = li30
                then (exp(ls30) - 1) * 100.0 - (exp(li30) - 1) * 100.0 end as dev_30,
           case when n_9 = 9 and ls9 = ls9 and li9 = li9
                then (exp(ls9) - 1) * 100.0 - (exp(li9) - 1) * 100.0 end as dev_10_pre,
           case when n_29 = 29 and ls29 = ls29 and li29 = li29
                then (exp(ls29) - 1) * 100.0 - (exp(li29) - 1) * 100.0 end as dev_30_pre
    from w3
),
sp as (
    select security_code, first_session, fifth_session
    from reference_data.security_session_span
)
select w4.*, sp.first_session, sp.fifth_session,
       (select max(sn) from cal) as last_sn,
       -- 上市后第几个竞价交易日：日历窗口只取近 45 场，所以老票这里给的是"至少 45"，
       -- 只用来在界面上说"这票刚上市"，判"前 5 日不纳入"走 fifth_session（精确）。
       case when sp.first_session is null then null
            else (select count(*) from cal c2
                   where c2.trade_date >= sp.first_session and c2.trade_date <= %(to)s)
       end as listed_days
from w4 left join sp using (security_code)
where w4.trade_date = %(to)s
"""


# ------------------------------------------------------------------ 纯判据（单测全打在这一层）

def band_from_row(close: Any, limit_up: Any, pre_close: Any) -> Optional[float]:
    """明日涨跌幅限制档位：由**当日** limit_up ÷ pre_close 反推，再归到最近档 {0.05,0.10,0.20}。

    为什么不用 `reference_data.limit_price_daily`：2026-10-15 实测那张表 10-07 起没有行
    （日线已到 10-15），拿它算前瞻会把整列判成 NULL。归档是为了吃掉最小变动价位带来的
    9.8%~10.4% 噪声 —— 不归档会把一只普通主板票的明日涨停价算成 10.4%。
    """
    vals = [x for x in (limit_up, pre_close) if x is not None]
    if len(vals) < 2 or not pre_close:
        return None
    try:
        implied = float(limit_up) / float(pre_close) - 1.0
    except (TypeError, ZeroDivisionError, ValueError):
        return None
    if implied < 0:
        return None
    nearest = min((0.05, 0.10, 0.20), key=lambda b: abs(implied - b))
    # 只认离得近的：30% 是北交所（本表不纳入），落在 0.25 以上说明这行的 limit_up 不可信
    return nearest if abs(implied - nearest) <= 0.012 else None


def limit_price(close: Any, band: Optional[float]) -> Optional[float]:
    """明日涨停价 = 今收 × (1 + 档位)，四舍五入到分（A 股最小变动价位 0.01 元）。"""
    if close is None or band is None:
        return None
    return round(float(close) * (1.0 + band), 2)


def ab_thr(thr_group: str) -> float:
    return AB_MAIN if thr_group == '主板' else AB_GEM


def times_thr(thr_group: str) -> int:
    return TIMES_MAIN if thr_group == '主板' else TIMES_GEM


def judge_dev(dev: Optional[float], n: Optional[int], need: int) -> Tuple[Optional[bool], Optional[str]]:
    """一条"累计偏离"判据的三态答案：True 踩到 / False 算得出来且没踩到 / None 判不了。

    窗口根数不够（新股、长停牌）与"累计没达到"是两件事 —— 这里交 None 而不是 False，
    界面上才写得出一句「这一档判不了」而不是一句假话。
    """
    if dev is None or n is None or n < need:
        return None, 'bars_lt_window' if n is not None and n < need else 'no_dev'
    return bool(dev is not None), None


def severity(row: Dict[str, Any]) -> Dict[str, Any]:
    """严重异常波动三条款（含"判不了"的理由）。返回 severe / severe_how / severe_dir / severe_reason。"""
    if (row.get('fifth_session') and row['trade_date'] <= row['fifth_session']):
        return {'severe': None, 'severe_how': None, 'severe_dir': None,
                'severe_reason': 'new_listing_lt5'}
    if row.get('index_pct') is None:
        return {'severe': None, 'severe_how': None, 'severe_dir': None,
                'severe_reason': 'no_index_pct'}
    if row.get('pct_change') is None:
        return {'severe': None, 'severe_how': None, 'severe_dir': None,
                'severe_reason': 'no_pct'}

    thr = times_thr(row['thr_group'])
    up, dn = row.get('up_cnt_10'), row.get('down_cnt_10')
    d10, n10 = row.get('dev_10'), row.get('n_10')
    d30, n30 = row.get('dev_30'), row.get('n_30')

    how: List[str] = []
    dirs: List[str] = []
    unknown: List[str] = []

    # ① 10 个交易日内 N 次同向异常波动（涨向、跌向各判一次）
    if up is None or n10 is None or n10 < min(10, thr):
        unknown.append('times')
    else:
        if up >= thr:
            how.append('times'); dirs.append('up')
        if dn and dn >= thr:
            how.append('times'); dirs.append('down')
    # ② 10 日累计偏离 +100% / −50%
    if d10 is None or n10 is None or n10 < 10:
        unknown.append('dev10')
    else:
        if d10 >= DEV10_UP:
            how.append('dev10'); dirs.append('up')
        elif d10 <= DEV10_DOWN:
            how.append('dev10'); dirs.append('down')
    # ③ 30 日累计偏离 +200% / −70%
    if d30 is None or n30 is None or n30 < 30:
        unknown.append('dev30')
    else:
        if d30 >= DEV30_UP:
            how.append('dev30'); dirs.append('up')
        elif d30 <= DEV30_DOWN:
            how.append('dev30'); dirs.append('down')

    uniq = sorted(set(how))
    if uniq:
        # 方向唯一时给 up/down；涨跌两向都踩到（罕见）交 mixed，不相加成"中性"
        ud = sorted(set(dirs))
        return {'severe': True, 'severe_how': ', '.join(uniq),
                'severe_dir': ud[0] if len(ud) == 1 else 'mixed', 'severe_reason': None}
    judged = [k for k in ('times', 'dev10', 'dev30') if k not in unknown]
    if not judged:
        return {'severe': None, 'severe_how': None, 'severe_dir': None,
                'severe_reason': 'bars_lt_window'}
    return {'severe': False, 'severe_how': None, 'severe_dir': None,
            'severe_reason': 'none' if not unknown else 'partial:' + ','.join(unknown)}


def window_gains(row: Dict[str, Any]) -> Dict[str, Any]:
    """把 SQL 里的对数和折成"这只票在这一段区间涨了几个点"（复利口径，个股那半边）。

    为什么非要单独把它算出来：前瞻那一栏要从"还差多少**累计偏离**"换算成"明日要涨多少**个点**"，
    `abn-1`（逐日相加）时两把尺是同一把、可以直接画等号；换成区间复利后不再是同一个数 ——
    区间已经涨了 S 个点，明日再涨 x 个点，区间变成 `(100+S)×(1+x/100) − 100`，
    所以补 nd 个点的缺口只需 `nd × 100 ÷ (100+S)` 个点的涨幅。S 只能从对数和回来：
    `dev_10_pre` 是个股减指数，拆不回个股那半边。
    ⚠️ 缺读数那几天已经在 `_f()` 里折成 None ⇒ 这里也交 None，那一档随后判不了，
    **绝不按"这一区间没涨"（S=0）顶过去**（那等于把放大系数悄悄抹成 1）。
    """
    for dst, src in (('stock_2', 'ls2'), ('stock_9', 'ls9'), ('stock_29', 'ls29')):
        lg = _f(row.get(src))
        row[dst] = None if lg is None else (math.exp(lg) - 1.0) * 100.0
    return row


def _scale(dev_gap: float, stock_gain: Optional[float]) -> Optional[float]:
    """累计偏离缺口 → 明日所需涨幅（复利放大系数 `100/(100+S)`）。S 拿不到就是算不出来。"""
    if stock_gain is None or 100.0 + stock_gain <= 0:
        return None
    return dev_gap * 100.0 / (100.0 + stock_gain)


def forward(row: Dict[str, Any]) -> Dict[str, Any]:
    """明日涨停会不会**新**踩到一条还没踩到的严重异动（用户点名的那一栏）。

    三条档**各算各的所需涨幅**，取最小者当触发点：

      · 10 日档：明日窗口是 `sn-8 … sn+1`，所以基准用 `dev_10_pre`（去掉明日的那 9 场累计），
        缺口 = 100 − dev_10_pre。⚠️ 不能拿 `100 − dev_10` 当通用答案 —— 那是把今天
        窗口里最早那场（明日会被挤出去）也留在账上，所需涨幅算小、榜单里就会出现
        "触发价高于涨停价"这种自相矛盾的行（10-15 实测 600241.SH）。
      · 30 日档：同理用 `dev_30_pre`（29 场累计），阈值 200。
      · 次数档：明日那场要**自己构成一次异常波动** ⇒ 缺口 = 异常波动阈值 − dev_prev2；
        同时明日这场要凑满第 N 次 ⇒ 用 `up_cnt_9 + 1`（明日窗口内已有的次数 + 明日），
        不是 `up_cnt_10 + 1`（今天窗口那场数会多算一场已被挤出去的）。

    ⚠️ 上面三档算出来的是**还差多少个点的累计偏离**（`need_dev_to_100`/`need_dev_to_200`），
    而「明日要涨多少个点」是另一个数（`trigger_pct`）：换成区间复利后（`abn-2`），区间已经涨了
    S 个点，明日再涨 x 个点会把区间放大成 `(100+S)×(1+x/100) − 100`，所以
    `trigger_pct = 缺口 × 100 ÷ (100+S)`。S 越大的票，同样一个缺口要的涨幅越小 ——
    实测一只 10 日区间已涨 99 个点的主板票：缺口 1 个点只需再涨 0.5 个点，
    按旧的"缺口=涨幅"会把它报成 1 个点。两列都在屏上，各说各的事，不许混成一个数。

    **10-27 用户点名改的一条**：已经踩到某一条的票不再被整只挡在榜外（旧版就是这么把
    大业股份、海天瑞声弄没的）——它照样进榜，但只算**还没踩到的那几条**还差多少。
    "它已经踩到哪一条"界面读**同一行的 `severe_how`**（本来就落库了，不再另开一列，免得两处各说一遍）。
    三条都踩过了 ⇒ `hit_if_limit_up=false`（明日涨停不会带来"新"的一条，这句是真话）；
    还有没踩到的但判不动（窗口根数不够、指数缺读数）⇒ 交 `None`，界面念"判不了"，
    **绝不替它说"不会触发"**。
    """
    band = row.get('band_next')
    out: Dict[str, Any] = {'need_dev_to_100': None, 'need_dev_to_200': None,
                            'dev_10_if_limit': None, 'dev_30_if_limit': None,
                            'trigger_pct': None, 'trigger_price': None,
                            'hit_if_limit_up': None, 'fwd_clauses': None,
                            'times_if_limit': None}
    if band is None:
        return out
    hit_already = {x.strip() for x in str(row.get('severe_how') or '').split(',') if x.strip()}

    bpct = round(band * 100.0, 4)
    need_for: List[Tuple[str, float]] = []      # 还能新踩到、且判得动的档 → 各自「明日要涨多少个点」
    open_clauses = [k for k in ('dev10', 'dev30', 'times') if k not in hit_already]
    judged: List[str] = []                      # 三档里判得动的有哪几条（不等于进了 need_for）

    d10p, n9, s9 = row.get('dev_10_pre'), row.get('n_9'), row.get('stock_9')
    if d10p is not None and n9 == 9:
        nd = round(DEV10_UP - d10p, 3)
        out['need_dev_to_100'] = nd
        x = _scale(nd, s9)
        if x is not None:
            judged.append('dev10')
            # 明日涨停后那个窗的累计：指数按 0 涨幅假设，个股那半边按复利放大
            out['dev_10_if_limit'] = round(d10p + bpct * (100.0 + s9) / 100.0, 3)
            if 'dev10' not in hit_already:
                need_for.append(('dev10', x))

    d30p, n29, s29 = row.get('dev_30_pre'), row.get('n_29'), row.get('stock_29')
    if d30p is not None and n29 == 29:
        nd = round(DEV30_UP - d30p, 3)
        out['need_dev_to_200'] = nd
        x = _scale(nd, s29)
        if x is not None:
            judged.append('dev30')
            out['dev_30_if_limit'] = round(d30p + bpct * (100.0 + s29) / 100.0, 3)
            if 'dev30' not in hit_already:
                need_for.append(('dev30', x))

    # 次数档：明日那个 3 日窗 = 昨日起两场 + 明日（个股那半边同样是复利，放大系数取 stock_2）
    prev2, n2, cnt9 = row.get('dev_prev2'), row.get('n_2'), row.get('up_cnt_9')
    s2 = row.get('stock_2')
    if prev2 is not None and n2 == 2 and cnt9 is not None and n9 == 9:
        nd = round(ab_thr(row['thr_group']) - prev2, 3)
        x = _scale(nd, s2)
        if x is not None:
            judged.append('times')
            enough = cnt9 + 1 >= times_thr(row['thr_group'])
            out['times_if_limit'] = bool(enough and x <= bpct)
            if enough and 'times' not in hit_already:
                need_for.append(('times', x))

    # 负数 = 「不用涨就到档了」（明日窗口不含明日时已经过线），夹成 0 交出去
    cands = [(name, max(0.0, x)) for name, x in need_for]
    hit = sorted(name for name, x in cands if x <= bpct + 1e-9)
    out['fwd_clauses'] = ', '.join(hit) or None
    if cands:
        # 差多少就交多少：够不着（>涨停幅度）时也算出来，屏上"还差 26 个点"读的就是这一列。
        # 是否进预警榜由 hit_if_limit_up 决定，不靠把这一列抹成 NULL。
        need_pct = min(nd for _, nd in cands)
        out['trigger_pct'] = round(need_pct, 3)
        if row.get('close') is not None:
            out['trigger_price'] = round(float(row['close']) * (1.0 + need_pct / 100.0), 2)
    # 「判得动」与「进了候选」不是一件事：次数档算得出来但明日凑不满第 N 次时不进 need_for，
    # 若拿 need_for 当判据，这一条会被数成判不动 ⇒ 整只票说成「判不了」，把一句到嘴的"不会触发"丢了。
    open_judged = [k for k in open_clauses if k in judged]
    if hit:
        out['hit_if_limit_up'] = True
    elif len(open_judged) == len(open_clauses):
        out['hit_if_limit_up'] = False          # 没踩到的几条全判得动、都没到档：这句才是真话
    else:
        out['hit_if_limit_up'] = None           # 有判不动的档 ⇒ 不许说"不会触发"
    return out


def universe_by(row: Dict[str, Any]) -> Optional[str]:
    """进母体的哪一条；None = 整行不写（本屏是提醒榜，不是全市场日线快照）。"""
    hits: List[str] = []
    if row.get('severe'):
        hits.append('severe')
    if row.get('ab_up') or row.get('ab_down'):
        hits.append('ab_flag')
    if (row.get('up_cnt_10') or 0) >= 1 or (row.get('down_cnt_10') or 0) >= 1:
        hits.append('ab_flag')
    d10, d30 = row.get('dev_10'), row.get('dev_30')
    if d10 is not None and row.get('n_10') == 10 and d10 >= U_DEV10:
        hits.append('dev10_ge25')
    if d30 is not None and row.get('n_30') == 30 and d30 >= U_DEV30:
        hits.append('dev30_ge60')
    if row.get('sealed_10'):
        hits.append('sealed10')
    if row.get('hit_if_limit_up'):
        hits.append('forward_hit')
    return ', '.join(sorted(set(hits))) or None


# ------------------------------------------------------------------ 取数与写库

def _f(v: Any) -> Optional[float]:
    if v is None:
        return None
    if isinstance(v, Decimal):
        return float(v)
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return None if x != x else x          # NaN → NULL（缺数不按 0 参与判定）


def sessions_until(end: dt.date, n: int) -> List[dt.date]:
    rows = engine.query(
        'select distinct trade_date d from market_data.index_daily '
        'where security_code = %s and trade_date <= %s order by d desc limit %s',
        ('000001.SH', end, n), role='reader')
    return sorted(r['d'] for r in rows)


def latest_day() -> Optional[dt.date]:
    r = engine.query_one('select max(trade_date) d from market_data.stock_daily', role='reader')
    return r['d'] if r and r['d'] else None


def trade_days(end: dt.date, recent: int) -> List[dt.date]:
    return sessions_until(end, max(1, recent))


def days_between(start: dt.date, end: dt.date) -> List[dt.date]:
    rows = engine.query('select distinct trade_date d from market_data.stock_daily '
                        'where trade_date between %s and %s order by d', (start, end), role='reader')
    return [r['d'] for r in rows]


def _run_sql(target: dt.date) -> List[dict]:
    """跑那一发全场窗口查询。

    ⚠️ `set local work_mem`：这一发要对 23 万行做 7 层窗口排序，默认 work_mem 下排序
    spill 到盘（10-27 换复利口径后实测一场 70 秒，顶到 reader 的 60 秒 statement_timeout）；
    给到 256MB 后排序留在内存里。用 `set local` 而不是 `set` —— 连接是池里借的，
    不带 LOCAL 会把这条设置漏给下一个用这条 reader 连接的查询。
    """
    from psycopg.rows import dict_row

    with engine.conn('reader') as c, c.cursor(row_factory=dict_row) as cur:
        # SET 不收绑定参数（`set local work_mem = $1` 是语法错），所以这里是拼进去的字面量 ——
        # WORK_MEM 是本模块常量、不接受任何外部输入，改它只能改代码。
        cur.execute(f"set local work_mem = '{WORK_MEM}'")
        cur.execute(_SQL, {'to': target, 'scan': SCAN_SESSIONS,
                           'ab_main': AB_MAIN, 'ab_gem': AB_GEM})
        return cur.fetchall()


def compute_day(target: dt.date) -> Tuple[List[Dict[str, Any]], Optional[str]]:
    """算一场。返回 `(行, 降级说明)`：说明非空表示这一场有段落判不了，要出声。"""
    cal = sessions_until(target, SCAN_SESSIONS)
    if not cal:
        return [], '读不到交易日历（index_daily 的 000001.SH 没有行）—— 这一场整批判不了'
    if cal[-1] != target:
        return [], f'{target} 不是交易日（日历最后一场是 {cal[-1]}）'
    rows = _run_sql(target)
    if not rows:
        return [], f'{target} 没有日线行（vipdoc 未落盘或 c1 未入库）—— 不是"今天没有异动"'

    # 一票一场只准一行：窗口 sum() 是按行加的，join 把某天叉成两行就会把当天偏离值算两遍。
    # 10-15 实测 security_st_period 有 149 对重叠档期，左连接确实会叉 —— 与其写下翻倍的数
    # 再让人去猜，不如当场失败（这条闸坏在写库之前，台账会记这一步红）。
    codes = [r['security_code'] for r in rows]
    if len(set(codes)) != len(codes):
        dup = sorted({c for c in codes if codes.count(c) > 1})[:8]
        raise ValueError(f'{target} 取数出现一票多行（{len(rows) - len(set(codes))} 行重复，'
                         f'例：{dup}）—— join 把一个交易日叉成几行，累计偏离会翻倍，这一场拒绝写库')

    out: List[Dict[str, Any]] = []
    missing_band = 0
    for r in rows:
        row = _normalize(dict(r))
        window_gains(row)        # ls9/ls29/ls2 → stock_9/stock_29/stock_2（前瞻的复利放大系数要它）
        row['trade_date'] = target
        band = band_from_row(row.get('close'), row.get('limit_up'), row.get('pre_close'))
        if band is None:
            missing_band += 1
        row['band_next'] = band
        row['limit_price_next'] = limit_price(row.get('close'), band)
        row.update(severity(row))
        row.update(forward(row))
        ub = universe_by(row)
        if not ub:
            continue
        row['universe_by'] = ub
        row['universe_hit'] = True
        n10 = row.get('n_10')
        row['dev_3_complete'] = (row.get('n_3') == 3)
        row['dev_10_complete'] = (n10 == 10) if n10 is not None else None
        row['dev_30_complete'] = (row.get('n_30') == 30) if row.get('n_30') is not None else None
        out.append(row)

    note = None
    if missing_band:
        note = (f'{missing_band} 只票当日 limit_up 或 pre_close 取不到 ⇒ 明日涨停幅度判不了（前瞻那一栏 NULL，'
                f'不是 0%）；累计偏离那一块不受影响')
    return out, _attach_next(out, target, note)


def _attach_next(rows: List[Dict[str, Any]], target: dt.date, note: Optional[str]) -> Optional[str]:
    """已踩到严重异动的票，补"次日涨幅/次日停牌"。只查那几只，且**只在次日已经发生的时候**补。"""
    hot = [r for r in rows if r.get('severe')]
    if not hot:
        return note
    nxt = engine.query_one('select min(trade_date) d from market_data.index_daily '
                           'where security_code = %s and trade_date > %s',
                           ('000001.SH', target), role='reader')
    nd = nxt['d'] if nxt else None
    if nd is None:
        return note          # 次日还没到（跑的就是最新一场）：留 NULL，等下一场再补
    cal_has = True
    codes = [r['security_code'] for r in hot]
    bars = engine.query('select security_code, pct_change from market_data.stock_daily '
                        'where trade_date = %s and security_code = any(%s)',
                        (nd, codes), role='reader')
    got = {b['security_code']: _f(b['pct_change']) for b in bars}
    for r in hot:
        r['next_trade_date'] = nd
        r['next_missing'] = r['security_code'] not in got
        r['next_pct'] = got.get(r['security_code'])
        # 停牌要有证据：指数在那天有这根线（cal_has），而这只票没有日线行
        r['next_halted'] = bool(cal_has and r['next_missing'])
    return note


UPSERT = (f'insert into {TABLE} ({", ".join(_COLUMNS)}) values ({", ".join(["%s"] * len(_COLUMNS))}) '
          'on conflict (security_code, trade_date, spec_version) do update set '
          + ', '.join(f'{c} = excluded.{c}' for c in _COLUMNS if c not in
                      ('security_code', 'trade_date', 'spec_version')))


#: 只有这些列要过 `_f`（Decimal→float、NaN→NULL）；其余是文本/布尔/日期，原样交。
#: ⚠️ 判据层一进来就要 float：psycopg 把 numeric 交回 Decimal，而 `100.0 − Decimal` 直接抛
#:    `unsupported operand type(s) for +: 'decimal.Decimal' and 'float'`（10-15 首跑实踩）。
_NUM_COLS = frozenset({
    'close', 'pre_close', 'limit_up', 'pct_change', 'index_pct', 'dev_1d', 'dev_3', 'dev_10',
    'dev_30', 'dev_prev2', 'dev_10_pre', 'dev_30_pre', 'band_next', 'limit_price_next',
    'need_dev_to_100', 'need_dev_to_200', 'dev_10_if_limit', 'dev_30_if_limit',
    'trigger_pct', 'trigger_price', 'next_pct',
})
_INT_COLS = frozenset({'n_3', 'n_2', 'n_10', 'n_30', 'n_9', 'n_29', 'listed_days',
                       'bars_used', 'sealed_10', 'sn', 'last_sn',
                       'up_cnt_10', 'down_cnt_10', 'up_cnt_9', 'down_cnt_9'})


def _normalize(row: Dict[str, Any]) -> Dict[str, Any]:
    for c in _NUM_COLS:
        if c in row:
            row[c] = _f(row[c])
    for c in _INT_COLS:
        if row.get(c) is not None:
            row[c] = int(row[c])
    return row


def _cell(col: str, r: Dict[str, Any]) -> Any:
    if col == 'spec_version':
        return SPEC_VERSION
    v = r.get(col)
    if col in _NUM_COLS:
        return None if v is None else round(_f(v), 4) if _f(v) is not None else None
    return v


def write(day: dt.date, rows: List[Dict[str, Any]]) -> int:
    # 先删这一场这一版本的旧行再写：母体会随数据到没到变多变更少，只 upsert 会把上一次
    # 半截计算的那批留在表里（10-15 异动筛查那张表就是这么多出 54 条假读数的）。
    payload = [tuple(_cell(c, r) for c in _COLUMNS) for r in rows]
    with engine.cursor(role='etl') as cur:
        cur.execute(f'delete from {TABLE} where trade_date = %s and spec_version = %s',
                    (day, SPEC_VERSION))
        if payload:
            cur.executemany(UPSERT, payload)
    return len(payload)


def done_days() -> set:
    rows = engine.query(f'select distinct trade_date d from {TABLE} where spec_version = %s',
                        (SPEC_VERSION,), role='reader')
    return {r['d'] for r in rows}


def run(days: Sequence[dt.date], *, write_rows: bool = True,
        budget_min: Optional[float] = None, quiet: bool = False) -> Dict[str, Any]:
    """一场一场算。预算用完就停并交还剩几场（别让父进程 kill_tree 收我们）。"""
    started = time.time()
    budget = budget_min * 60.0 if budget_min else None
    ok = fail = 0
    written = 0
    notes: List[str] = []
    for d in days:
        if budget and time.time() - started > budget:
            return {'ok': ok, 'fail': fail, 'written': written, 'notes': notes,
                    'stopped_after': ok + fail, 'left': len(days) - ok - fail, 'budget_out': True}
        try:
            rows, note = compute_day(d)
        except Exception as exc:                              # noqa: BLE001
            fail += 1
            notes.append(f'{d} 算失败：{str(exc)[:160]}')
            continue
        if note:
            notes.append(f'{d} {note}')
        if write_rows:
            try:
                written += write(d, rows)
            except Exception as exc:                          # noqa: BLE001
                fail += 1
                notes.append(f'{d} 写库失败：{str(exc)[:160]}')
                continue
        ok += 1
        if not quiet:
            sev = sum(1 for r in rows if r.get('severe'))
            fwd = sum(1 for r in rows if r.get('hit_if_limit_up'))
            unknown = sum(1 for r in rows if r.get('severe') is None)
            print(f'  {d} 母体 {len(rows)} 只 · 已踩严重异动 {sev} 只 · '
                  f'明日涨停可触发 {fwd} 只 · 判不了 {unknown} 只'
                  + (f' ⚠️ {note}' if note else ''), flush=True)
    return {'ok': ok, 'fail': fail, 'written': written, 'notes': notes,
            'left': 0, 'budget_out': False,
            'seconds': round(time.time() - started, 1)}


def verify(day: dt.date) -> List[str]:
    """回查库里这一场的行是否自洽（不查 rc，只查数）。空列表 = 没毛病。"""
    bad: List[str] = []
    rows = engine.query(
        f'select severe, severe_how, severe_reason, hit_if_limit_up, fwd_clauses, band_next, '
        f'trigger_pct, trigger_price, dev_10, dev_10_complete, next_pct, next_halted, '
        f'count(*) n from {TABLE} where trade_date = %s and spec_version = %s '
        'group by 1,2,3,4,5,6,7,8,9,10,11,12', (day, SPEC_VERSION), role='reader')
    if not rows:
        return [f'{TABLE} 里 {day} 一场都没有 —— 每日加工那一步没跑，还是这场算失败被记进台账了？']
    for r in rows:
        n = r['n']
        if r['severe'] is True and not r['severe_how']:
            bad.append(f'{n} 行：severe=true 却没写踩到哪几条')
        if r['severe'] is None and r['severe_reason'] is None:
            bad.append(f'{n} 行：severe 判不了却给了理由列留空')
        if r['severe'] is False and r['severe_how']:
            bad.append(f'{n} 行：没踩到却写了条款')
        if r['hit_if_limit_up'] is True and not r['fwd_clauses']:
            bad.append(f'{n} 行：说明日涨停可触发却没交代是哪一条')
        if r['band_next'] is None and r['hit_if_limit_up'] is not None:
            bad.append(f'{n} 行：明日涨停幅度判不了，却给了"会不会触发"的结论')
        if r['trigger_pct'] is not None and r['trigger_price'] is None:
            bad.append(f'{n} 行：有触发涨幅却没触发价')
        # 库里 numeric 交回来是 Decimal：与 float 混算会 TypeError（判据层靠 `_normalize` 过这一关，
        # 这一条读的是**裸行**，所以出口自己转）。
        band = _f(r['band_next'])
        trig = _f(r['trigger_pct'])
        if r['hit_if_limit_up'] is True and trig is not None and band is not None \
                and trig > band * 100.0 + 0.001:
            bad.append(f'{n} 行：说明日涨停能触发，可所需涨幅 {trig} 个百分点 '
                       f'比涨停幅度 {round(band * 100.0, 2)} 个百分点还大')
        if r['hit_if_limit_up'] is False and trig is not None \
                and band is not None and trig <= band * 100.0:
            bad.append(f'{n} 行：说不会触发，可所需涨幅已在涨停幅度内')
        if r['next_halted'] is True and r['next_pct'] is not None:
            bad.append(f'{n} 行：既判了次日停牌又给了次日涨幅')
    return bad


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description='异动提醒（偏离值 / 异常波动 / 严重异常波动）')
    ap.add_argument('--date', help='只算这一天（YYYY-MM-DD）')
    ap.add_argument('--recent', type=int, default=1, help='往前数 N 个交易日（默认 1）')
    ap.add_argument('--since', help='从这一天起把所有交易日都算一遍')
    ap.add_argument('--skip-done', action='store_true', help='跳过表里已有该 spec_version 的场')
    ap.add_argument('--max-minutes', type=float, help='自限预算，到点就停并交还剩几场')
    ap.add_argument('--dry-run', action='store_true', help='只算不写库')
    ap.add_argument('--verify', action='store_true', help='回查库里这一场的行是否自洽')
    args = ap.parse_args(list(argv) if argv is not None else None)

    if args.verify:
        day = dt.date.fromisoformat(args.date) if args.date else latest_day()
        if not day:
            print('没有可校验的交易日'); return 1
        bad = verify(day)
        print('\n'.join(f'✗ {b}' for b in bad) if bad else f'✓ {day} 这一场 {SPEC_VERSION} 的行自洽')
        return 1 if bad else 0

    if args.date:
        days = [dt.date.fromisoformat(args.date)]
    elif args.since:
        end = latest_day()
        if not end:
            print('库里没有日线'); return 1
        days = days_between(dt.date.fromisoformat(args.since), end)
        if args.skip_done:
            done = done_days()
            days = [d for d in days if d not in done]
    else:
        end = latest_day()
        if not end:
            print('库里没有日线'); return 1
        days = trade_days(end, args.recent)

    print(f'异动提醒 spec_version={SPEC_VERSION}，{len(days)} 场：'
          f'{days[0]} ~ {days[-1]}' + ('（--dry-run 不写库）' if args.dry_run else ''), flush=True)
    res = run(days, write_rows=not args.dry_run, budget_min=args.max_minutes)
    for n in res['notes']:
        print(f'  ⚠️ {n}')
    if res['budget_out']:
        print(f'  ⏱ 预算 {args.max_minutes} 分钟用完：还剩 {res["left"]} 场没算（接着跑加 --since）')
    if args.dry_run:
        print('  --dry-run：未写库'); return 0
    print(f'  写入 {res["written"]} 行，{res["ok"]} 场成功' + (f'，{res["fail"]} 场失败' if res['fail'] else ''))
    if res['fail'] and not res['ok']:
        return 2
    if res['fail']:
        return 3
    if res['budget_out']:
        return 4
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
