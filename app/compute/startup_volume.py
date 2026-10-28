# -*- coding: utf-8 -*-
"""「启动前量能画像」：涨起来之前那 30 根日线，量是怎么放的。

用户 2026-10-07 的原话拆成两类母体（表：`analytics.startup_volume_profile`，
DDL `app/db/sql/26_startup_volume_profile.sql`，判据版本 `SPEC_VERSION='sv-2'`）：

    A `board4`   最近 3 个月出现过 **4 板及以上**（连续涨停 ≥4 天）的票 → 从**首板日之前**往前 30 根
    A′ `board4_45` 同一批段里再收窄：**前四根累计涨幅 > 45%**（基准=首板日前一天收盘）
                 → A′ 是 A 的子集（启动日同一批），门槛 45% 的量在 `gain_first4` 这一列上，看得见也可核
    A″ `board4_or_gain45`（用户 2026-10-17 点名要**或**）：**4 板及以上 或 启动日起 4 个交易日涨幅 >45%**
                 → 第二边用「第 0 天收盘 → 第 4 个交易日收盘」的窗口，段长 2、3 但四天里涨过四成半的票
                   也进得来（双创 3 连板 = 72.8%、北证 2 连板 = 69%，按「且」永远进不了 A′）；
                   段长够 4 时窗口与段内前四根是同一批日子 ⇒ 两个数相等，A 整批是 A″ 的子集
    B `jump40`   创业板 / 科创板 / 北证里，最近 3 个月存在 **4 日累计涨幅 > 40%** 的票
                 → 从**触发日之前**往前 30 根

用户 2026-10-15 要的就是 A′ 这一档（原话：「这里的条件再加一个……再加一条，就是 4 天涨幅大于 45% 的票，
也就是第 0 天收盘价到第 3 天收盘价，初始计算的值要以这 4 天前一天的收盘价作为基准」）。
45% 不是随手画的：**4 个 10% 满涨停 = 1.10⁴ − 1 = 46.41%**，所以这一档筛掉的是"两天板 + 两天磨"
那种段长够、强度不够的样本。

⚠️ **为什么从 sv-1 升到 sv-2**（判据一个字没改，改的是它读的行情）：`board4` 的连板判定读
`market_data.stock_daily.limit_up`，而 2026-10-07 之前那一列对"主板 + 当前 is_st"的票一律按 ±5% 贴
（越界 7,108 行 / 144 只）。`app/compute/limit_prices.py` 把档位改成按**当日**证据定档后全史重灌，
越界只剩 47 行。重灌后按同一判据再跑一遍，sv-1 里有 6 条 ST 样本（ST龙元 600491、ST复华 600624、
ST洲际 600759、ST际华 601718、*ST起步 603557、*ST康佳A 000016）不再是 4 板 —— 它们是真样本还是
错样本由那一批行自己留着对账，但**不许和新一批混在同一屏里排名**，所以换版本号而不是删行。

为什么要分两类（这条是设计的核心，别被人合并回一榜）：
「4 板」只在 10% 档成立。双创 20%、北证 30%，同一个门槛数出来几乎是空的 ——
实测近 3 个月 4 板及以上 76 只：主板 72、创业板 3、北证 1、科创板 0。
所以 B 类换成「4 日 >40%」触发，并同时存 `equiv_limit_ups` = ln(1+区间涨幅)/ln(1+档位)，
把 40% 换成"等效几个涨停"（实测 3.53 / 1.85 / 1.28），两类才能并排比强度。
     注意这是对数比，**不对称**：−10% 在一个 10% 档里是 −1.105 个跌停（跌回去要比涨上来多一点）。

三条读数口径（写在 DDL 头部，改一处必须同批另一处）：
  ① 窗口是**这只票自己的**有效交易日：往前 30 根日线，停牌的日子不占名额；
     但复牌那根的倍数分母是很久以前的量，所以另数 `long_gap_days`（上一根隔 >10 天）。
  ② 倍数直接读 `analytics.volume_ratio_daily`（今日量 ÷ 上一根量，`spec_version='vr-1'`），
     不在这里重新除一遍 —— 两处除法迟早漂移。窗口内凑不满 30 根就如实交 `bars_used<30`，
     **不补 0 也不补 1**。
  ③ `flat_days` 是四位收口后**精确**等于 1.0000；"量基本没变"要看 `still_days`（0.95~1.05）。

分位数用线性插值（与 PG 的 `percentile_cont` 同一条），不是"取第 k 个样本"：
30 个点上 P80 若取整会跳在两个观测之间乱动，页面逐日刷新时看着像数据在抖。

用法：
    python -m app.compute.startup_volume --months 3                 # 算最近 3 个月两类母体
    python -m app.compute.startup_volume --start 2026-07-01 --end 2026-09-30
    python -m app.compute.startup_volume --dry-run                  # 只报样本与两条样例，不写库
    python -m app.compute.startup_volume --verify                   # 写完后体检

退出码：0 成功 / 1 没有可算的样本 / 2 --verify 不通过。
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import statistics
import sys
from pathlib import Path

from app.db import engine

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

SPEC_VERSION = 'sv-2'
WINDOW_DAYS = 30          # 启动日之前往前几根有效日线
MIN_BARS = 10             # 少于这个根数就不出分布读数（次新/长期停牌会算出看着像样的假分位数）
LONG_GAP_DAYS = 10        # 上一根隔超过这么多自然日 = 停牌复牌那种跳空量
DBL = 2.0                 # 倍量门槛（用户给的）
NEAR_DBL = 1.8
STILL_LO, STILL_HI = 0.95, 1.05
CALM = 0.5

#: 各板块涨跌停档位。等效涨停数按**板块**档算，本来就不区分 ST；2026-10-07 档位定档
#: 改到"按当日证据"之后（`app/compute/limit_prices.py`），连板判定读的 `limit_up`
#: 已与这里一致。剩下的欠账是 151,891 行只有当前 is_st 快照支撑的历史（`limit_basis
#: = 'rule_st_snapshot'`，近 3 个月还有 4,485 行），那一段的板数仍是猜的。
BAND_BY_BOARD = {'主板': 0.10, '创业板': 0.20, '科创板': 0.20, '北交所': 0.30}

COHORTS = ('board4', 'board4_45', 'board4_or_gain45', 'jump40')
#: 第三类母体的门槛（用户 2026-10-15：「4 天涨幅大于 45%」）。4 个满涨停 = 1.10^4−1 = 46.41%。
#: 同一个数也用在第四类（`board4_or_gain45`）的「或」那半边 —— 两处都用它，别在 SQL 里另写一个 0.45。
GAIN_FIRST4_MIN = 0.45


# ============================================================ 纯判据（不碰库，单测打这一组）

def equiv_limit_ups(gain: float | None, band: float | None) -> float | None:
    """区间涨幅换成"等效几个涨停"：ln(1+涨幅) ÷ ln(1+档位)。

    跨板块比强度就靠这一个数：40% 在主板是 3.7 个涨停、在创业板是 2.0 个、在北证是 1.3 个。
    涨幅 ≤ −100% 或对数无定义时交回 NULL，不硬算。
    """
    if gain is None or band is None or band <= 0 or band >= 1:
        return None
    if gain <= -1:
        return None
    return round(math.log1p(gain) / math.log1p(band), 3)


def pctile(values: list[float], q: float) -> float | None:
    """线性插值分位数（同 PG `percentile_cont`）。空列表交回 None，不交 0。"""
    if not values:
        return None
    s = sorted(values)
    if len(s) == 1:
        return round(s[0], 4)
    pos = (len(s) - 1) * q
    lo, hi = math.floor(pos), math.ceil(pos)
    if lo == hi:
        return round(s[lo], 4)
    return round(s[lo] + (s[hi] - s[lo]) * (pos - lo), 4)


#: 五个倍量区间（用户 2026-10-11 要的那五列）。**下闭上开**：1.6~1.8 含 1.6 不含 1.8，
#: 五档同形 ⇒ 一根 1.8 只会落进 1.8~2.0 那一档，不会被两档各数一次。
RANGE_BUCKETS = ((1.6, 1.8, 'range_16_18'), (1.4, 1.6, 'range_14_16'),
                 (1.2, 1.4, 'range_12_14'), (1.0, 1.2, 'range_10_12'),
                 (0.8, 1.0, 'range_08_10'))


def range_counts(ratios: list) -> dict:
    """一窗倍数 → 五个倍量区间的天数。**这一份是唯一的实现**：
    「启动前逐日量能比对」那张表与「单票量价相似样本」那一块都调它，不再各数一遍。"""
    vals = [float(r) for r in ratios if r is not None]
    return {key: sum(1 for v in vals if lo <= v < hi) for lo, hi, key in RANGE_BUCKETS}


def classify(ratios: list[float], gaps: list[int | None]) -> dict:
    """一窗倍数 → 天数分类 + 分布。**样本不足 MIN_BARS 时分布列全交 NULL**。"""
    vals = [float(r) for r in ratios if r is not None]
    out: dict = {
        'bars_used': len(vals),
        'up_days': sum(1 for v in vals if v > 1.0),
        'down_days': sum(1 for v in vals if v < 1.0),
        'flat_days': sum(1 for v in vals if round(v, 4) == 1.0),
        'still_days': sum(1 for v in vals if STILL_LO <= v <= STILL_HI),
        'dbl_days': sum(1 for v in vals if v >= DBL),
        'near_dbl_days': sum(1 for v in vals if NEAR_DBL <= v < DBL),
        'calm_days': sum(1 for v in vals if v <= CALM),
        'long_gap_days': sum(1 for g in gaps if g is not None and g > LONG_GAP_DAYS),
    }
    if len(vals) < MIN_BARS:
        out.update({k: None for k in
                    ('ratio_avg', 'ratio_median', 'ratio_p60', 'ratio_p80', 'ratio_p90',
                     'ratio_max', 'ratio_min', 'std_days')})
        out['missing'] = 'bars_below_min'
        return out
    out.update({
        'ratio_avg': round(statistics.fmean(vals), 4),
        'ratio_median': pctile(vals, 0.5),
        'ratio_p60': pctile(vals, 0.6),
        'ratio_p80': pctile(vals, 0.8),
        'ratio_p90': pctile(vals, 0.9),
        'ratio_max': round(max(vals), 4),
        'ratio_min': round(min(vals), 4),
        'std_days': round(statistics.stdev(vals), 4) if len(vals) > 1 else None,
        'missing': None,
    })
    return out


# ============================================================ 取数

SAMPLES_SQL = """
WITH bounds AS (
  SELECT %(d_from)s::date - interval '150 days' AS scan_from, %(d_to)s::date AS d_to
),
series AS (                   -- 每根日线都先编号（不按 limit_up 过滤）：编号连续才代表"真的连着几天"
  SELECT d.security_code, d.trade_date, d.close, d.pre_close, d.limit_up,
         row_number() OVER (PARTITION BY d.security_code ORDER BY d.trade_date) AS bar_no,
         lag(d.close) OVER (PARTITION BY d.security_code ORDER BY d.trade_date) AS prev_close
    FROM market_data.stock_daily d, bounds b
   WHERE d.trade_date BETWEEN b.scan_from AND b.d_to
),
bars AS (                     -- 同一串日线，把"这一根的开算基价"定下来（第四类的第 0 天收盘就从这里取）
  SELECT security_code, trade_date, bar_no, close,
         coalesce(nullif(pre_close, 0), nullif(prev_close, 0)) AS base_c
    FROM series
),
zt AS (                       -- 涨停标记（用库里的涨停价，不用涨幅阈值）
  -- 基价优先用 pre_close（除权日它是除权参考价，与 close 同基，这才是真涨幅）；
  -- 只有它为空/为 0 的那几行才退回上一根收盘 —— 停牌复牌日库里 pre_close 是空的
  -- （实测 600825.SH 2026-09-21 复牌，7 连板却算不出区间涨幅），一根都不许整段判空。
  SELECT security_code, trade_date, close, bar_no,
         coalesce(nullif(pre_close, 0), nullif(prev_close, 0)) AS base,
         (close >= limit_up - 0.01) AS is_zt
    FROM series
   WHERE limit_up IS NOT NULL          -- 判不出涨停价的日子不进母体（下面的编号会因此把段切断，这是有意的）
),
grp AS (                      -- 连续涨停分组（gaps-and-islands）
  -- ⚠️ 岛号用 series 的 bar_no 减，不用过滤后的行号：按 limit_up 过滤后行号是连着的，
  -- 中间那几天"没有涨停价"的票会被接成一段连板（6.4M 行里有 12,125 行 limit_up 为空）。
  SELECT security_code, trade_date, close, base, is_zt,
         bar_no - row_number() OVER (PARTITION BY security_code, is_zt ORDER BY trade_date) AS g
    FROM zt
),
runs AS (                     -- 每段连板：长度 + 首末日 + 段内累计涨幅 + **前四根涨幅**
  -- 基价缺一天就整段交 NULL：静默少乘一天会给出一个偏低的涨幅，
  -- 而它看着完全正常 —— 这类"少一天"的错在本项目已经踩过不止一次。
  -- ⚠️ array_agg 会把 NULL 留在自己的位置上（实测 {10,NULL,30} 的基数仍是 3、第 2 位是 NULL），
  --    所以 [1] / [4] 取到的**仍是第 1 / 第 4 根**，不会因为某天缺基价而整体前移一位。
  SELECT security_code, g, count(*) AS len, min(trade_date) AS first_day, max(trade_date) AS last_day,
         CASE WHEN count(*) = count(base)
              THEN exp(sum(ln(close / base))) - 1 END AS seg_gain,
         (array_agg(base ORDER BY trade_date))[1]  AS first_base,
         (array_agg(close ORDER BY trade_date))[4] AS fourth_close,
         CASE WHEN count(*) >= 4 AND (array_agg(base ORDER BY trade_date))[1] > 0
                   AND (array_agg(close ORDER BY trade_date))[4] IS NOT NULL
              THEN (array_agg(close ORDER BY trade_date))[4]
                   / (array_agg(base ORDER BY trade_date))[1] - 1 END AS gain_first4
    FROM grp WHERE is_zt GROUP BY 1, 2
),
a AS (
  SELECT security_code, first_day AS launch_date, 'board4'::text AS cohort,
         len::smallint AS trigger_len, seg_gain AS trigger_gain, gain_first4
    FROM runs
   WHERE len >= 4 AND first_day BETWEEN %(d_from)s AND %(d_to)s
),
g45 AS (                      -- 第三类母体：段长 ≥4 **且前四根涨幅过门槛**（门槛见 GAIN_FIRST4_MIN）
  -- A′ 是 A 的子集（同一批启动日），不是并榜的另一类；为什么要这一档见模块文档与 DDL 37 头部。
  SELECT security_code, first_day AS launch_date, 'board4_45'::text AS cohort,
         len::smallint AS trigger_len, seg_gain AS trigger_gain, gain_first4
    FROM runs
   WHERE len >= 4 AND gain_first4 > __GAIN4_MIN__ AND first_day BETWEEN %(d_from)s AND %(d_to)s
),
win4 AS (                     -- 启动日往后数到第 4 根**有效日线**（用户口径：第 0 天收盘 → 第 4 天收盘）
  -- 用未过滤日线序列的 bar_no+3，不用段内第 4 根：段长只有 2、3 的时候，第 4 个交易日照样存在，
  -- 而那正是第四类母体「或」那半边要收进来的票（断板了但四天里涨过四成半）。
  SELECT b1.security_code, b1.trade_date AS launch,
         CASE WHEN b1.base_c > 0 THEN b2.close / b1.base_c - 1 END AS gain4_win
    FROM bars b1 JOIN bars b2
      ON b2.security_code = b1.security_code AND b2.bar_no = b1.bar_no + 3
),
u AS (                        -- 第四类母体：4 板及以上 **或** 启动日起 4 个交易日涨幅过门槛（用户 10-17 点名要「或」）
  -- trigger_gain 与 gain_first4 都交"启动日起第 4 根收盘 ÷ 第 0 天收盘 − 1"：段长够 4 时它就是段内前四根那个数
  -- （连板的 4 天本来就是连续的 4 根日线），段长不够时它是唯一能说清「4 天涨幅」的数。缺第 4 根（贴着库尾）就 NULL，
  -- 这一行只能靠 len>=4 入选。门槛与 A′ 同一个真值。
  SELECT r.security_code, r.first_day AS launch_date, 'board4_or_gain45'::text AS cohort,
         r.len::smallint AS trigger_len,
         coalesce(w.gain4_win, r.gain_first4) AS trigger_gain,
         coalesce(w.gain4_win, r.gain_first4) AS gain_first4
    FROM runs r LEFT JOIN win4 w
      ON w.security_code = r.security_code AND w.launch = r.first_day
   WHERE (r.len >= 4 OR coalesce(w.gain4_win, -1) > __GAIN4_MIN__)
     AND r.first_day BETWEEN %(d_from)s AND %(d_to)s
),
c4 AS (                       -- 四日累计涨幅（B 类触发）
  SELECT security_code, trade_date, close, close / nullif(lag(close, 4) OVER w, 0) - 1 AS gain4
    FROM market_data.stock_daily, bounds
   WHERE trade_date BETWEEN bounds.scan_from AND bounds.d_to
     WINDOW w AS (PARTITION BY security_code ORDER BY trade_date)
),
flag AS (                     -- 只留"触发段的第一天"：同一波连着几天都超四成涨幅的不重复计
  SELECT security_code, trade_date, gain4,
         bool_or(coalesce(gain4 > 0.40, false)) OVER (PARTITION BY security_code
                    ORDER BY trade_date ROWS BETWEEN 1 PRECEDING AND 1 PRECEDING) AS prev_hit
    FROM c4
),
b AS (
  SELECT security_code, trade_date AS launch_date, 'jump40'::text AS cohort,
         4::smallint AS trigger_len, gain4 AS trigger_gain, gain4 AS gain_first4
    FROM flag
   WHERE gain4 > 0.40 AND coalesce(prev_hit, false) = false
     AND trade_date BETWEEN %(d_from)s AND %(d_to)s
     AND (security_code LIKE '30%%' OR security_code LIKE '68%%'
          OR security_code LIKE '8%%' OR security_code LIKE '4%%' OR security_code LIKE '92%%')
)
SELECT s.security_code, s.launch_date, s.cohort, s.trigger_len, s.trigger_gain, s.gain_first4,
       sec.name, sec.board
  FROM ((SELECT * FROM a) UNION ALL (SELECT * FROM b) UNION ALL (SELECT * FROM g45)
        UNION ALL (SELECT * FROM u)) s
  LEFT JOIN reference_data.security sec USING (security_code)
 ORDER BY s.launch_date DESC, s.security_code
"""

# 门槛只有一个真值：SQL 里那个占位由 `GAIN_FIRST4_MIN` 填。
# 不写成 f-string 是因为这段 SQL 里有 `LIKE '30%%'` 那种双写百分号，混进 f-string 只会多出一次坑。
SAMPLES_SQL = SAMPLES_SQL.replace('__GAIN4_MIN__', str(GAIN_FIRST4_MIN))

WINDOW_SQL = """
WITH launch AS (              -- (代码, 启动日) 一对一行，来自上一步的母体；用 jsonb 传，省掉 N+1 查询
  SELECT (x->>0)::text AS code, (x->>1)::date AS launch
    FROM jsonb_array_elements(%(pairs)s::jsonb) x
),
j AS (                        -- ⚠️ rn 必须按"这只票这一次启动"分别编号：
  SELECT l.code AS security_code, l.launch AS launch_date,   -- 按全局日期倒序编号会取到别的启动日的窗口
         v.trade_date, v.vol_ratio, v.prev_gap_days,
         row_number() OVER (PARTITION BY l.code, l.launch ORDER BY v.trade_date DESC) AS rn
    FROM launch l
    JOIN analytics.volume_ratio_daily v
      ON v.security_code = l.code AND v.spec_version = 'vr-1' AND v.trade_date < l.launch
     AND v.trade_date >= %(scan_from)s
)
SELECT security_code, launch_date, trade_date, vol_ratio, prev_gap_days
  FROM j WHERE rn <= %(win)s
 ORDER BY 1, 2, 3
"""


def _pairs_param(samples: list[dict]) -> str:
    """把 (代码, 启动日) 打成 jsonb 数组传进去，省掉一条临时表/一次 N+1 查询。"""
    return json.dumps([[s['security_code'], s['launch_date'].isoformat()] for s in samples])


def collect(d_from: dt.date, d_to: dt.date) -> list[dict]:
    """两类母体的样本 + 各自启动前 30 根的倍数序列。"""
    rows = engine.query(SAMPLES_SQL, {'d_from': d_from, 'd_to': d_to}, role='reader') or []
    if not rows:
        return []
    codes = sorted({r['security_code'] for r in rows})
    win_rows = engine.query(WINDOW_SQL, {
        'codes': codes,
        'scan_from': d_from - dt.timedelta(days=150),
        'pairs': _pairs_param(rows),
        'win': WINDOW_DAYS,
    }, role='reader') or []
    series: dict[tuple[str, dt.date], list] = {}
    for w in win_rows:
        key = (w['security_code'], w['launch_date'])
        series.setdefault(key, []).append(w)
    out = []
    for r in rows:
        points = sorted(series.get((r['security_code'], r['launch_date']), []),
                        key=lambda w: w['trade_date'])
        ratios = [p['vol_ratio'] for p in points]
        gaps = [p['prev_gap_days'] for p in points]
        band = BAND_BY_BOARD.get(r.get('board'))
        stats = classify(ratios, gaps)
        out.append({
            'security_code': r['security_code'],
            'launch_date': r['launch_date'],
            'cohort': r['cohort'],
            'spec_version': SPEC_VERSION,
            'board': r.get('board'),
            'name': r.get('name'),
            'trigger_len': r.get('trigger_len'),
            'trigger_gain': (None if r.get('trigger_gain') is None
                              else round(float(r['trigger_gain']), 4)),
            # 前四根涨幅（第三类母体的门槛读数）。缺基准/第 4 根就是 NULL，不补 0。
            'gain_first4': (None if r.get('gain_first4') is None
                             else round(float(r['gain_first4']), 4)),
            'equiv_limit_ups': equiv_limit_ups(
                None if r.get('trigger_gain') is None else float(r['trigger_gain']), band),
            'window_days': WINDOW_DAYS,
            'ratios': [{'d': p['trade_date'].isoformat(), 'v': (None if p['vol_ratio'] is None
                                                                else round(float(p['vol_ratio']), 4))}
                       for p in points],
            **stats,
        })
    return out


# ============================================================ 写入

UPSERT_SQL = """
INSERT INTO analytics.startup_volume_profile
  (security_code, launch_date, cohort, spec_version, board, name, trigger_len, trigger_gain,
   gain_first4,
   equiv_limit_ups, window_days, bars_used, up_days, down_days, flat_days, dbl_days,
   near_dbl_days, calm_days, still_days, ratio_avg, ratio_median, ratio_p60, ratio_p80,
   ratio_p90, ratio_max, ratio_min, std_days, long_gap_days, ratios, calculated_at)
VALUES
  (%(security_code)s, %(launch_date)s, %(cohort)s, %(spec_version)s, %(board)s, %(name)s,
   %(trigger_len)s, %(trigger_gain)s, %(gain_first4)s,
   %(equiv_limit_ups)s, %(window_days)s, %(bars_used)s,
   %(up_days)s, %(down_days)s, %(flat_days)s, %(dbl_days)s, %(near_dbl_days)s, %(calm_days)s,
   %(still_days)s, %(ratio_avg)s, %(ratio_median)s, %(ratio_p60)s, %(ratio_p80)s, %(ratio_p90)s,
   %(ratio_max)s, %(ratio_min)s, %(std_days)s, %(long_gap_days)s, %(ratios)s::jsonb, now())
ON CONFLICT (security_code, launch_date, cohort, spec_version) DO UPDATE SET
   board = EXCLUDED.board, name = EXCLUDED.name, trigger_len = EXCLUDED.trigger_len,
   trigger_gain = EXCLUDED.trigger_gain, gain_first4 = EXCLUDED.gain_first4,
   equiv_limit_ups = EXCLUDED.equiv_limit_ups,
   window_days = EXCLUDED.window_days, bars_used = EXCLUDED.bars_used,
   up_days = EXCLUDED.up_days, down_days = EXCLUDED.down_days, flat_days = EXCLUDED.flat_days,
   dbl_days = EXCLUDED.dbl_days, near_dbl_days = EXCLUDED.near_dbl_days,
   calm_days = EXCLUDED.calm_days, still_days = EXCLUDED.still_days,
   ratio_avg = EXCLUDED.ratio_avg, ratio_median = EXCLUDED.ratio_median,
   ratio_p60 = EXCLUDED.ratio_p60, ratio_p80 = EXCLUDED.ratio_p80, ratio_p90 = EXCLUDED.ratio_p90,
   ratio_max = EXCLUDED.ratio_max, ratio_min = EXCLUDED.ratio_min, std_days = EXCLUDED.std_days,
   long_gap_days = EXCLUDED.long_gap_days, ratios = EXCLUDED.ratios, calculated_at = now()
"""


def write(rows: list[dict]) -> int:
    written = 0
    for r in rows:
        r = dict(r)
        r['ratios'] = json.dumps(r['ratios'], ensure_ascii=False)
        written += engine.execute(UPSERT_SQL, r, role='etl')
    return written


# 这里**不删**任何历史行：母体是按窗口算的，掉出窗口的样本留着也只是多几行，
# 读侧按 `launch_date >= 今天-月数` 过滤就够。派生分析表里的行要删得先问（本项目吃过教训）。


def verify() -> tuple[bool, list[str]]:
    """体检：形状对不对，不是"有没有数"。"""
    bad: list[str] = []
    agg = engine.query(
        'SELECT cohort, count(*) n, count(*) FILTER (WHERE bars_used < window_days) 短窗, '
        '       count(*) FILTER (WHERE dbl_days > bars_used) 倍量超过总根数, '
        '       count(*) FILTER (WHERE up_days + down_days + flat_days > bars_used) 分类超总根数, '
        '       count(*) FILTER (WHERE ratios IS NULL) 无序列 '
        '  FROM analytics.startup_volume_profile WHERE spec_version = %s GROUP BY 1',
        (SPEC_VERSION,), role='reader') or []
    for a in agg:
        print('  %s：样本 %s / 窗口不满 30 根 %s / 无序列 %s'
              % (a['cohort'], a['n'], a['短窗'], a['无序列']))
        if int(a['倍量超过总根数'] or 0):
            bad.append('%s 有 %s 行 dbl_days > bars_used（分类计数越界）' % (a['cohort'], a['倍量超过总根数']))
        if int(a['分类超总根数'] or 0):
            bad.append('%s 有 %s 行 上+下+平 > 总根数' % (a['cohort'], a['分类超总根数']))
    total = sum(int(a['n']) for a in agg)
    if not total:
        bad.append('一张行都没有 —— 步骤没跑，还是母体为空？')
    return (not bad), bad


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description='启动前量能画像（4 板 / 双创北证 4 日>40%）')
    ap.add_argument('--months', type=int, default=3, help='往前几个月（默认 3，与用户原话一致）')
    ap.add_argument('--start', help='窗口起日（给了就覆盖 --months）')
    ap.add_argument('--end', help='窗口止日（默认库里最新交易日）')
    ap.add_argument('--dry-run', action='store_true', help='只报样本与前两条，不写库')
    ap.add_argument('--verify', action='store_true', help='写完后体检')
    args = ap.parse_args(argv)

    d_to = (dt.date.fromisoformat(args.end) if args.end
            else engine.scalar('SELECT max(trade_date) FROM market_data.stock_daily'))
    if d_to is None:
        print('库里没有日线，谈不上母体')
        return 1
    d_from = (dt.date.fromisoformat(args.start) if args.start
              else d_to - dt.timedelta(days=args.months * 31))
    print('=== 启动前量能画像（%s）：母体窗口 %s .. %s，每票往前 %d 根有效日线%s'
          % (SPEC_VERSION, d_from, d_to, WINDOW_DAYS, '（dry-run）' if args.dry_run else ''))

    rows = collect(d_from, d_to)
    if not rows:
        print('  没有样本（窗口太短或库里没有这段日线）')
        return 1
    by = {}
    for r in rows:
        by[r['cohort']] = by.get(r['cohort'], 0) + 1
    print('  样本：%s' % '、'.join('%s %d 条' % (k, v) for k, v in sorted(by.items())))
    for r in rows[:2]:
        print('    %s %s %s 启动 %s：窗口 %s 根 / 倍量 %s 天 / 中位 %s / P80 %s / 等效 %s 个涨停'
              % (r['name'] or '', r['security_code'], r['cohort'], r['launch_date'], r['bars_used'],
                 r['dbl_days'], r['ratio_median'], r['ratio_p80'], r['equiv_limit_ups']))
    if args.dry_run:
        return 0

    written = write(rows)
    print('  写入 %d 行（掉出窗口的旧样本不删，读侧按日期过滤）' % written)
    if args.verify:
        ok, bad = verify()
        for b in bad:
            print('  ⚠ ' + b)
        print('  体检：%s' % ('通过' if ok else '不通过'))
        return 0 if ok else 2
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
