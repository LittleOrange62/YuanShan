# -*- coding: utf-8 -*-
"""倍量次的**加权口径**（`analytics.volume_double.dbl_energy`）。

为什么要这一列（用户 2026-10-19 两轮裁定）：老的「倍量次」是硬计数 —— 日量比
（当日量 ÷ 前 5 日均量，基准不含当日，见 `vol_double.py` 的 `vma5`）只要没到 **2.0 倍**
就一分不算。本机实测（近 25 场全市场 149,938 个票·日）：≥2.0 的日子只占 **3.5%**，
1.5–2.0 那段（6.4%）里含着一大批真在温和放量的票，全被当成"没放量"。

**算式（第二稿，2026-10-19 晚用户纠正）**：

```
得分 = Σ_每档 ( 这一档在窗口里出现的次数 × 这一档的分值 )
```

没有"系数""占比""归一化"这一层 —— 第一稿写的「分值÷Σ分值」是错的，用户当天否掉：
「其实不存在系数这个参数，系数这个说法是错的」。分值就是**一天落在这一档计几分**，
可以打小数；窗口（40 场）的得分上限＝`40 × 顶档分值`。默认顶档（2.5–3.5）＝1.0，
所以得分与旧的"倍量次数"同量级，可以直接对着读、也可以照"次"的直觉填门槛。

四处刻意的写法：

1. **档位与分值只住本文件的 `BANDS` / `DEFAULT_SCORES` 一处**。界面、SQL、文档都从
   `band_rows()` 取，不许有第二份 0.1 的抄本（同页两块用两把尺的教训见《10-每个数怎么算》§4.1）。
2. **改配置＝换指纹＝必须重算**。`fingerprint()` 把"每个市场实际生效的分值表"整个哈希，
   落库时随 `energy_fp` 一起写；读侧与对账循环现比指纹（见 `status()`），
   对不上就说"筛的还是旧批"，不假装生效。
3. **得分要排名，名次算法只住 `duanxian/ranking.py`**（并列同名、下一名按位置顺延：
   三只并列第 1 ⇒ 下一个是第 4）。本模块只交分值与档表，不自带第二套名次算法。
4. **1.0 以下一分不给**（缩量不是放量信号）；尾档回落不等于清零
   （低位放天量确实是要动的信号，清掉是另一种武断）。

尾档为什么回落：公开资料对量比的分档口径一致 —— 0.8–1.5 正常、1.5–2.5 温和放量、
2.5–5 明显放量、5–10 剧烈放量、>10 极端放量，且**极端放量在高位是见顶/出货信号**
（多家同源摘录）。所以默认分值在 2.5–3.5 给到最高 1.0，3.5–5.0 给 0.9，≥5.0 给 0.7。
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import threading

from app.db import engine
from app.db.repo import registry as reg_repo

# ---------------------------------------------------------------- 档位（唯一的定义处）

#: (下界, 上界)；上界 None ＝开放尾档。比值一律作用在 **前 5 日均量** 上，
#: 所以 2.0 这一档就是老口径的「倍量」，1.0 以下一分不给（缩量不是放量信号）。
BANDS: tuple[tuple[float, float | None], ...] = tuple(
    [(round(1.0 + 0.1 * i, 1), round(1.1 + 0.1 * i, 1)) for i in range(15)]
    + [(2.5, 3.5), (3.5, 5.0), (5.0, None)]
)

#: 默认分值 ＝ **那一天计几分**（不是权重、不是系数、不要除以什么）。
#: 顶档 1.0 ⇒ 满窗上限 40，与旧的"倍量次数"同量级；1.9-2.0 给 0.80、2.0-2.1 给 0.88，
#: 意思是"刚好翻倍那种日子"值将近一分，而 1.0-1.1（几乎没放量）只值 0.05。
DEFAULT_SCORES: dict[str, float] = {
    '1.0-1.1': 0.05, '1.1-1.2': 0.08, '1.2-1.3': 0.12, '1.3-1.4': 0.18, '1.4-1.5': 0.25,
    '1.5-1.6': 0.35, '1.6-1.7': 0.45, '1.7-1.8': 0.55, '1.8-1.9': 0.68, '1.9-2.0': 0.80,
    '2.0-2.1': 0.88, '2.1-2.2': 0.93, '2.2-2.3': 0.96, '2.3-2.4': 0.98, '2.4-2.5': 0.99,
    '2.5-3.5': 1.00,          # 明显放量：这一档信息量最高，记满一分
    '3.5-5.0': 0.90,          # 剧烈放量：略降（低位是吸筹、高位是兑现，混在一起不敢再加）
    '5.0+': 0.70,             # 极端放量：再降一档（公开口径一致认为高位极端放量偏反转信号）
}

#: 量纲/系数/占比这一层**已经取消**（2026-10-19 晚用户纠正：「不存在系数这个参数」）。
#: 分值就是"一天落在这一档计几分"，直接累加，不除以任何东西。

#: 作用域。'global'＝所有票一张表；'market'＝`reference_data.security.market` 各配一张，
#: 没单配的市场落回全局那张。**不用 board 当键**：`security.board` 实测有 3,972 只是空的
#: （2026-10-19：SH 2,525＋SZ 1,445），按板块配会让这 3,972 只静默落回全局、界面却像配过了。
SCOPES = ('global', 'market')
MARKETS = ('SH', 'SZ', 'BJ')
OTHER_MARKET = 'OTHER'      # 不在 MARKETS 里的（新市场号、基金）走这份兜底

SETTING_KEY = 'volume_double.energy_profile'
LOOKBACK = 40               # 与 vol_double.LOOKBACK 同义，改那边要一起改（读侧窗口也按它）
VMA_WIN = 5                 # 基准窗口：前 5 日均量（不含当日）—— 与 vol_double 的 vma5 一致

#: 新榜「加权分进榜后的逐日走势」的母体＝**当日得分前 N 名**（用户 2026-10-19 晚点名：
#: 「取前 120 个吧，正好每页显示 60 个，一共 2 页」）。
#: 门槛制（旧第一稿的"加权分≥0.8"）作废：分值一改，门槛对应的只数就跟着漂，
#: 而"前 120 名"与档表无关，怎么改配置这一块都是 2 页。
DEFAULT_TOP_N = 120
#: 前端分页档（同一处，界面读接口不自己写 60）。
PAGE_SIZE = 60


def _label(lo: float, hi: float | None) -> str:
    return f'{lo:.1f}+' if hi is None else f'{lo:.1f}-{hi:.1f}'


BAND_LABELS: tuple[str, ...] = tuple(_label(lo, hi) for lo, hi in BANDS)


def validate(payload: dict) -> dict:
    """把用户交来的配置洗成规范形状；不合法就抛 ValueError（界面把那句原样显示）。

    宁可拒收，也不要「存了半份」：半份配置会让重算按错的档跑一遍，而界面看不出区别。
    """
    if not isinstance(payload, dict):
        raise ValueError('配置形状不对：应是一份对象')
    if 'normalize' in payload:
        raise ValueError('已经取消「量纲／占比」这一层（2026-10-19 晚纠正）：'
                         '分值就是一天计几分，直接累加，不除以 Σ 也不除以顶档。'
                         '请刷新页面按新形状保存。')
    scope = str(payload.get('scope') or 'global')
    if scope not in SCOPES:
        raise ValueError('作用域只认 %s，交回的是 %r' % (list(SCOPES), scope))

    def _scores(raw, where):
        if not isinstance(raw, dict):
            raise ValueError(f'{where} 的分值表缺失或形状不对')
        out: dict[str, float] = {}
        for lab in BAND_LABELS:
            v = raw.get(lab)
            if v is None:
                raise ValueError(f'{where} 少了一档：{lab} —— 少档等于偷偷把这档算成 0 分')
            try:
                f = float(v)
            except (TypeError, ValueError):
                raise ValueError(f'{where} 的 {lab} 不是数字：{v!r}')
            if f < 0 or f != f or f in (float('inf'), float('-inf')):
                raise ValueError(f'{where} 的 {lab} 得是 0 以上的有限数（现在是 {f}）')
            out[lab] = f
        extra = [k for k in raw if k not in BAND_LABELS]
        if extra:
            raise ValueError(f'{where} 有不认识的档名：{"、".join(sorted(extra))} —— '
                             '档名由后端的 BANDS 给，界面不许自己造')
        if max(out.values()) <= 0:
            raise ValueError(f'{where} 所有档分值都是 0：每场得分恒为 0，排名无从谈起')
        return out

    cleaned: dict = {'scope': scope, 'global': _scores(payload.get('global'), '全局')}
    if scope == 'market':
        raw_by = payload.get('by_market') or {}
        if not isinstance(raw_by, dict):
            raise ValueError('按交易所配置时 by_market 得是对象')
        unknown = [k for k in raw_by if k not in MARKETS]
        if unknown:
            raise ValueError('不认识的市场号：' + '、'.join(sorted(map(str, unknown)))
                             + f'（能配的只有 {"、".join(MARKETS)}，其余市场自动用全局那份）')
        cleaned['by_market'] = {k: _scores(v, f'{k}') for k, v in raw_by.items()}
    else:
        cleaned['by_market'] = {}
    return cleaned


def profile() -> dict:
    """代码默认 ← `config.setting[SETTING_KEY]`。存坏了退回默认并如实说明来源。

    这里不静默兜一份"看着合理"的数：`profile_source` 会写「默认（已存的配置不合法：…）」，
    界面与 `status()` 都读这一句，用户能看出自己那份到底生没生效。
    """
    default = {'scope': 'global', 'global': dict(DEFAULT_SCORES), 'by_market': {}}
    try:
        raw = reg_repo.setting(SETTING_KEY, None)
    except Exception as e:  # noqa: BLE001
        return dict(default, profile_source=f'默认（配置读失败：{e}）', broken=True)
    if not isinstance(raw, dict) or not raw:
        return dict(default, profile_source='默认（还没存过配置）', broken=False)
    try:
        cleaned = validate(raw)
    except ValueError as e:
        return dict(default, profile_source=f'默认（已存的配置不合法：{e}）', broken=True)
    return dict(cleaned, profile_source='config.setting 里已存的配置', broken=False)


def effective_scores(market: str | None) -> dict[str, float]:
    """某只票实际生效的那张分值表：单配的市场用单配，其余（含空/新市场号）用全局。"""
    p = profile()
    if p['scope'] == 'market':
        by = p.get('by_market') or {}
        if market in by:
            return dict(by[market])
    return dict(p['global'])


def points(market: str | None) -> dict[str, float]:
    """某天落在这一档 ⇒ 计几分。**不做任何归一**（第一稿的 ÷Σ 已被用户否掉）。"""
    return {k: float(v) for k, v in effective_scores(market).items()}


def band_rows() -> list[tuple[str, float, float | None, float]]:
    """把配置摊成 SQL 能直接 JOIN 的行：(market, lo, hi, 分值)。

    每个具体市场一行（SH/SZ/BJ/OTHER 各一份，已按作用域解析过），
    所以 SQL 里不必再写「没单配就落全局」那种优先级逻辑 —— 逻辑留在 Python 一处。
    """
    out: list[tuple[str, float, float | None, float]] = []
    for mk in MARKETS + (OTHER_MARKET,):
        pt = points(mk)
        for (lo, hi), lab in zip(BANDS, BAND_LABELS):
            out.append((mk, lo, hi, pt.get(lab, 0.0)))
    return out


def fingerprint() -> str:
    """生效配置的指纹。摊平的档表一起进哈希，所以换作用域、改任一档分值都会变号。"""
    p = profile()
    payload = json.dumps({'scope': p['scope'],
                          'rows': [[m, lo, hi, w] for m, lo, hi, w in band_rows()]},
                         sort_keys=True)
    return 'ev-' + hashlib.sha1(payload.encode('utf-8')).hexdigest()[:10]


def scale() -> dict:
    """这一份配置下得分的**数学上限**与读法 —— 界面交这个，用户就知道"一分＝一天顶档放量"。"""
    tops = [max(points(m).values() or [0.0]) for m in MARKETS + (OTHER_MARKET,)]
    cap = max(tops) if tops else 0.0
    return {'window_days': LOOKBACK,
            'max_per_day': round(cap, 4),
            'max_score': round(LOOKBACK * cap, 2),
            'unit': '分（＝窗口内每一天按所在档的分值累加；顶档一天记 '
                    + (f'{cap:g}' if cap else '0') + ' 分）',
            'formula': '得分 = Σ 每档 ( 这一档在窗口里出现的次数 × 这一档的分值 )',
            'note': '没有系数、没有占比、没有归一化：分值就是一天计几分，直接乘次数累加。'
                    '所以改任一档的分值，只有落在该档的那些天受影响，不会像「除以总和」那样'
                    '把全表每一档都牵动。满窗（40 场全在顶档）的上限见 max_score。'}


# ---------------------------------------------------------------- 算与落库（一处实现）

#: 回看余量（市场交易日）。窗要 40 行、量比基准要 5 行 ⇒ 至少 45 行；停牌会把一只票的
#: 40 根 K 线摊到超过 40 个市场日里，所以照 `vol_double.py` 那条 2026-08-31 的教训留余量。
#: 余量不够时该场这票的 `bars_in_win` 会小于 40，读侧按「不满窗」如实交回，不拿截断凑数。
LOOKBACK_SPAN_DAYS = 100


def _band_values() -> str:
    """摊平的档表 → SQL 的 VALUES 片段。**每列显式 cast**：尾档的 hi 是 NULL，
    不写 `NULL::numeric` 会被 PG 推成 text，那句 `volume_shares < hi * vma5` 当场报错。"""
    rows = []
    for mk, lo, hi, w in band_rows():
        hi_sql = 'NULL::numeric' if hi is None else f'{float(hi)}::numeric'
        rows.append(f"('{mk}', {float(lo)}::numeric, {hi_sql}, {float(w)}::numeric)")
    return ', '.join(rows)


def energy_sql(frm: dt.date, to: dt.date) -> str:
    """一条集合式 SQL：把 [frm, to] 每一场每一票的加权分算出来。

    为什么是一条而不是一天一跑：日量比要前 5 行、加权分要前 40 行，都是同一只票自己的
    行序 —— 窗口函数一次扫完全历史就能逐场给出，逐日跑等于把同一份窗口重算 210 遍
    （老 `vol_double.CORE_SQL` 一天 8.4 秒就是这么来的）。改一次配置 ≈ 一条语句，
    这是「配置一改就全量重算」能做成自动的前提。

    %s 之外只有 %(frm)s/%(to)s/%(lo)s 三个具名参数；档表是拼进来的文本（值来自
    `band_rows()`，不是用户直接提交的 SQL），所以这里没有注入面。
    """
    return f"""
        WITH mk AS (
            SELECT security_code, market FROM reference_data.security
        ),
        band(market, lo, hi, w) AS (VALUES {_band_values()}),
        bars AS (
            SELECT s.security_code, s.trade_date, s.volume_shares,
                   avg(s.volume_shares) OVER (PARTITION BY s.security_code
                                              ORDER BY s.trade_date
                                              ROWS BETWEEN {VMA_WIN} PRECEDING
                                                       AND 1 PRECEDING) AS vma5
              FROM market_data.stock_daily s
             WHERE s.trade_date >= %(lo)s AND s.trade_date <= %(to)s
               AND s.volume_shares > 0 AND s.close IS NOT NULL
        ),
        scored AS (
            SELECT b.security_code, b.trade_date, coalesce(bd.w, 0.0) AS w
              FROM bars b
              LEFT JOIN mk ON mk.security_code = b.security_code
              LEFT JOIN band bd
                     ON bd.market = coalesce(mk.market, '{OTHER_MARKET}')
                    AND b.vma5 > 0
                    AND b.volume_shares >= bd.lo * b.vma5
                    AND (bd.hi IS NULL OR b.volume_shares < bd.hi * b.vma5)
        ),
        win AS (
            SELECT security_code, trade_date,
                   sum(w)     OVER w40 AS energy,
                   count(*)   OVER w40 AS bars_in_win
              FROM scored
             WINDOW w40 AS (PARTITION BY security_code ORDER BY trade_date
                            ROWS BETWEEN {LOOKBACK - 1} PRECEDING AND CURRENT ROW)
        )
        SELECT security_code, trade_date, energy, bars_in_win
          FROM win
         WHERE trade_date >= %(frm)s AND trade_date <= %(to)s
    """


def span_start(frm: dt.date) -> dt.date:
    """取数起点：目标首场再往前 `LOOKBACK_SPAN_DAYS` 个**市场交易日**（不是日历日）。"""
    lo = engine.scalar(
        """SELECT min(trade_date) FROM (
               SELECT DISTINCT trade_date FROM market_data.stock_daily
                WHERE trade_date <= %s ORDER BY trade_date DESC LIMIT %s) z""",
        (frm, LOOKBACK_SPAN_DAYS))
    return lo or frm


def rebuild(frm: dt.date | None = None, to: dt.date | None = None,
            *, fp: str | None = None) -> dict:
    """按**当前生效**的配置重算并覆盖 `dbl_energy`/`energy_fp`，返回如实的追平报告。

    覆盖不可逆（旧值不留档）——这是用户 2026-10-19 选的第 3 项「回算覆盖」，
    代价要如实说：换了配置，历史那批加权分就回不去上一份配置的值了。
    旧口径的硬计数 `dbl_count` 仍在库里、仍由每日链写，所以对照物不是没了，
    只是不再上屏（`duanxian/quant.py` 那一侧改读 dbl_energy）。
    """
    fp = fp or fingerprint()
    if to is None:
        to = engine.scalar('SELECT max(trade_date) FROM analytics.volume_double')
        if to is None:
            return {'ok': False, 'reason': 'analytics.volume_double 是空的，没东西可算'}
    if frm is None:
        frm = engine.scalar('SELECT min(trade_date) FROM analytics.volume_double')
    if hasattr(frm, 'date'):
        frm, to = frm.date(), to.date()
    started = dt.datetime.now()
    n_target = engine.scalar(
        'SELECT count(*) FROM analytics.volume_double WHERE trade_date BETWEEN %s AND %s',
        (frm, to)) or 0
    # 驱动表必须是 `volume_double` 自己（LEFT JOIN 算出来的分），不能反过来只更新"算到了的"：
    # 2026-10-19 实测这一场 5,527 只有 13 只在 `stock_daily` 的这个区间里没有行（退市/长期停牌），
    # 只更新算到的那批会让这 13 行**永远停在旧指纹**，对账循环于是每轮都判"没追平"、永远补不完。
    # 现在这 13 行拿 NULL + 新指纹：分没算出来是事实，指纹追平也是事实，两件事分开说。
    written = engine.execute(
        """WITH x AS (""" + energy_sql(frm, to) + """)
        UPDATE analytics.volume_double v
              SET dbl_energy = round(s.energy::numeric, 4),
                  energy_fp  = %(fp)s
             FROM (SELECT v2.trade_date, v2.security_code, xx.energy AS energy
                      FROM analytics.volume_double v2
                      LEFT JOIN x xx
                             ON xx.security_code = v2.security_code
                            AND xx.trade_date    = v2.trade_date
                     WHERE v2.trade_date BETWEEN %(frm)s AND %(to)s) s
            WHERE v.trade_date    = s.trade_date
              AND v.security_code = s.security_code""",
        {'fp': fp, 'frm': frm, 'to': to, 'lo': span_start(frm)}, role='app')
    done = engine.scalar(
        """SELECT count(*) FROM analytics.volume_double
          WHERE trade_date BETWEEN %s AND %s AND energy_fp = %s""", (frm, to, fp)) or 0
    return {'ok': int(done or 0) >= int(n_target or 0),
            'fingerprint': fp, 'from': str(frm), 'to': str(to),
            'rows_target': int(n_target or 0), 'rows_synced': int(done or 0),
            'rows_unsynced': int(n_target or 0) - int(done or 0),
            'seconds': round((dt.datetime.now() - started).total_seconds(), 1)}


def latest_date() -> dt.date | None:
    d = engine.scalar('SELECT max(trade_date) FROM analytics.volume_double')
    return d.date() if hasattr(d, 'date') else d


def status() -> dict:
    """生效配置 vs 库里已算批次 —— 界面、对账循环、CLI 都读这一份，别各写一套判据。"""
    fp = fingerprint()
    p = profile()
    batches = engine.query(
        """SELECT coalesce(energy_fp, '（还没回填过）') AS fp,
                  count(*)::int AS rows, count(dbl_energy)::int AS with_value,
                  count(DISTINCT trade_date)::int AS sessions,
                  min(trade_date) AS lo, max(trade_date) AS hi,
                  max(calculated_at) AS last_written
             FROM analytics.volume_double GROUP BY 1 ORDER BY 2 DESC""") or []
    d = latest_date()
    dist: dict = {'session': str(d) if d else None}
    if d:
        row = engine.query_one(
            """SELECT count(*)::int AS n, count(dbl_energy)::int AS has_value,
                      percentile_cont(0.5)  WITHIN GROUP (ORDER BY dbl_energy)::numeric AS p50,
                      percentile_cont(0.9)  WITHIN GROUP (ORDER BY dbl_energy)::numeric AS p90,
                      percentile_cont(0.99) WITHIN GROUP (ORDER BY dbl_energy)::numeric AS p99,
                      max(dbl_energy)::numeric AS mx, min(dbl_energy)::numeric AS mn,
                      count(*) FILTER (WHERE dbl_energy >= %s)::int AS ge_0
                 FROM analytics.volume_double WHERE trade_date = %s""",
            (0.0, d)) or {}
        dist = {'session': str(d), 'rows': row.get('n'), 'with_value': row.get('has_value'),
                'p50': float(row['p50']) if row.get('p50') is not None else None,
                'p90': float(row['p90']) if row.get('p90') is not None else None,
                'p99': float(row['p99']) if row.get('p99') is not None else None,
                'max': float(row['mx']) if row.get('mx') is not None else None,
                'min': float(row['mn']) if row.get('mn') is not None else None,
                'at_or_above_zero': row.get('ge_0')}
    return {'fingerprint': fp, 'in_sync': bool(batches) and
            any(str(b.get('fp')) == fp for b in batches),
            'profile_source': p.get('profile_source'), 'broken': p.get('broken'),
            'batches': [{k: (str(v) if hasattr(v, 'isoformat') else v)
                         for k, v in b.items()} for b in batches],
            'scale': scale(), 'distribution': dist,
            'bands': [{'label': lab, 'lo': lo, 'hi': hi,
                       'score_global': p['global'].get(lab),
                       'points_global': points(None).get(lab)}
                      for (lo, hi), lab in zip(BANDS, BAND_LABELS)]}


def save(payload: dict) -> dict:
    """校验 → 存 → 交回新旧指纹。调用方（端点）负责起后台重算并把结果说给界面。

    这里**只存不算**：保存动作在请求线程里跑一条几十秒的语句会让页面看着像卡死，
    而"存了但没人算"更糟 —— 所以返回 `needs_rebuild`，由调用方决定立刻起还是交给对账循环。
    """
    before = fingerprint()
    cleaned = validate(payload)
    reg_repo.set_setting(SETTING_KEY, cleaned,
                         label='倍量加权分档（每档分值＝一天落在这一档计几分）',
                         category='quant')
    after = fingerprint()
    return {'ok': True, 'before': before, 'after': after,
            'changed': before != after, 'needs_rebuild': before != after,
            'profile': cleaned, 'scale': scale()}


# ---------------------------------------------------------------- 自动重算（保存即触发 + 对账补跑）

_REBUILD_LOCK = threading.Lock()

#: 这一场重算的现场。界面读它，所以形状要稳：`running` / `started_at` / `finished_at` /
#: `reason` / `last`（最近一次 `rebuild()` 的返回）/ `error`。
#: ⚠️ **后台线程里不许 print**：print 撞上 pytest 的输出捕获会让一整条测试假红（本项目踩过两次）。
JOB: dict = {'running': False, 'started_at': None, 'finished_at': None,
             'reason': '', 'last': None, 'error': None}


def job_state() -> dict:
    return json.loads(json.dumps(JOB, default=str))


def _run_rebuild(reason: str) -> None:
    global _REBUILD_LOCK
    try:
        JOB['last'] = rebuild()
        JOB['error'] = None if JOB['last'].get('ok') else \
            f"还有 {JOB['last'].get('rows_unsynced')} 行没打上指纹"
    except Exception as e:  # noqa: BLE001  重算失败要说得出、要能被下一轮对账再捡起来
        JOB['error'] = f'{type(e).__name__}: {e}'
        JOB['last'] = {'ok': False, 'reason': JOB['error']}
    finally:
        JOB['running'] = False
        JOB['finished_at'] = dt.datetime.now().isoformat(timespec='seconds')
        _REBUILD_LOCK.release()


def start_rebuild_async(reason: str = '配置变更') -> dict:
    """起一次后台全量重算。同一时刻只允许一场；起了就立刻返回，不等它跑完。

    三条闸：① `PYTEST_CURRENT_TEST` 里**硬不起线程**（测试不许碰真库、也不许留下野线程）；
    ② 已经有在跑的 ⇒ 返回 `started:False` 并说清"不是没接到，是有人在跑"；
    ③ 锁在**跑成功还是失败都释放**（`_run_rebuild` 的 finally），否则一次异常会让这条链
       永远"看起来在跑"，界面就再也点不动了 —— 这一条是本模块自己的闸，坏了要按"出声"处理。
    """
    if os.environ.get('PYTEST_CURRENT_TEST'):
        return {'started': False, 'reason': '测试进程里不起真重算（要跑用 CLI：'
                                            'python -m app.compute.volume_energy --rebuild）'}
    if not _REBUILD_LOCK.acquire(blocking=False):
        return {'started': False, 'reason': '已经有一场重算在跑，这一轮不重复起',
                'job': job_state()}
    JOB.update({'running': True, 'started_at': dt.datetime.now().isoformat(timespec='seconds'),
                'reason': reason, 'error': None})
    threading.Thread(target=_run_rebuild, args=(reason,), daemon=True,
                     name='volume-energy-rebuild').start()
    return {'started': True, 'reason': reason, 'job': job_state()}


def main(argv: list[str] | None = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(prog='volume_energy',
                                 description='倍量加权分：算、覆盖、对账（口径只住本模块）')
    ap.add_argument('--rebuild', action='store_true', help='按当前配置重算并覆盖')
    ap.add_argument('--from', dest='frm', default=None, help='起始场次 YYYY-MM-DD')
    ap.add_argument('--to', default=None, help='结束场次 YYYY-MM-DD')
    ap.add_argument('--status', action='store_true', help='打印生效指纹与库里批次')
    args = ap.parse_args(argv)
    if args.status or not args.rebuild:
        st = status()
        print(json.dumps({'fingerprint': st['fingerprint'], 'in_sync': st['in_sync'],
                          'profile_source': st['profile_source'], 'scale': st['scale'],
                          'distribution': st['distribution'], 'batches': st['batches']},
                         ensure_ascii=False, indent=2, default=str))
        return 0 if st['in_sync'] else 1
    _p = dt.date.fromisoformat
    out = rebuild(_p(args.frm) if args.frm else None, _p(args.to) if args.to else None)
    print(json.dumps(out, ensure_ascii=False))
    return 0 if out['ok'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
