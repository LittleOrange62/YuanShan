# -*- coding: utf-8 -*-
"""涨停基因（封住 / 触过 / 最高连板）——**一把尺只数一遍**的那件公用实现。

为什么单独一个模块（用户 2026-11-03 点名给埋伏票榜加「股性」时定下的）：
「近 N 场涨停封住几天、盘中冲过几天、最高几板」这三列，短线复盘的首板分析与突破信号页的
埋伏票榜**都要用**。原先它只住 `app/compute/first_board_daily.py`，窗口写死 63 场；
再写一份到第二屏就是两把尺——停牌那天算不算、ST 档位容差取几分、"冲过没封住"算不算，
两处各判一次必然分叉，而且分叉了没人能看出来（同一次改动在两个屏上给出两个数）。

所以这里只放**算法与口径**，窗口起点（`since`）由调用方传：
首板榜传它的 63 / 126 / 489 场，股性表传它的 250 场。两屏读的是同一段代码。

两把尺必须并存（本机实测同一场 触板 103 对 封住 84）：只留一个数就会把
"冲板没封住"那批票藏掉，而那批恰恰是打板客最想知道的"谁在试盘"。

缺数一律交回 NULL + 在 gap 里点名，**不写 0**：0 在这里是真实读数（"这 250 场一次都没封住"），
null 才是"这几天档位价没读到、判不了"。把判不了报成 0 会把缺数排成"最差"。
"""
from __future__ import annotations

import datetime as dt
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from app.db import engine

#: 收盘"贴在板上"的容差（元）。与首板分析、涨停池用的是同一个数——它就是从那边搬过来的，
#: 那边留一个同名别名 re-export，免得两处各写一份 0.01/0.005 然后各自漂移。
SEAL_EPS = 0.01

# 缺数点名用的令牌（值会原样落进 `missing`，界面悬停念的就是它，所以只能有一份）
GAP_HIST_ROWS = 'hist_window_no_rows'          # 窗口里这只票一根日线都没有（全新上市或读库失败）
GAP_HIST_LIMIT_UP = 'hist_limit_up_missing'    # 窗口里有日线但缺涨停档位价 ⇒ 涨停次数判不了
GAP_HIST_LIMIT_DOWN = 'hist_limit_down_missing'
GAP_CONSEC_SOURCE = 'lup_rows_unavailable'     # lup-1 那一批整批读回空（没读到库，不是"这段时间没连板"）
GAP_NO_ROUND = 'no_local_round'                # 池子有行但数不出这一轮

#: 中性键名：不带窗口后缀。调用方要什么列名自己加（首板榜是 `_3m`、股性表是 `_250`），
#: 免得"同一个算法因为列名不同被写成两份"。
GENE_KEYS = ('lu_sealed', 'lu_touched', 'ld_sealed', 'ld_touched')

#: 最高连板只读 lup-1 的 `consec`（"这一段是第几板"的唯一实现，见 `limit_up_pool.py` docstring）：
#: 这里再数一遍就会有两把尺 —— 停牌那天算不算断、ST 档位的容差取几分，两处各判一次必分叉。
CONSEC_SQL = """
select security_code, max(consec)::int as max_consec
  from analytics.limit_up_pool_daily
 where spec_version = %(lup)s and sealed
   and trade_date between %(frm)s and %(to)s
   and security_code = any(%(codes)s::text[])
 group by 1
"""


def win(bars: Sequence[dict], since: Optional[dt.date]) -> List[dict]:
    """窗口＝起始场次（含）往后的行。`bars` 需已按场次升序（调用侧排好）。"""
    return [b for b in bars if since is None or b['trade_date'] >= since]


def _num(v: Any) -> Optional[float]:
    try:
        f = float(v)     # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return None if f != f else f      # NaN ⇒ 当成"没有这个数"，不许参与比较


def seal_touch_counts(bars: Sequence[dict], since: dt.date,
                      eps: float = SEAL_EPS) -> Tuple[Dict[str, Any], Dict[str, str]]:
    """窗口内涨停/跌停各两把尺：**封住**（收盘贴在档位上）与**触及**（盘中冲过档位）。

    · 封住＝`close ≥ limit_up − eps`（跌停方向反过来）；
    · 触及＝`high ≥ limit_up − eps` / `low ≤ limit_down + eps`，含后来炸开的。

    ⚠️ 窗口里只要有一天的档位价缺失，这一族就交 NULL + 点名：缺一天＝少计一次，
    报出来的那个数就不再是"这段窗口有几天封住"，而是一个说不清少了谁的下界。
    涨停那一族缺档位不影响跌停那一族（它们各自点名，互不连坐）。
    """
    gap: Dict[str, str] = {}
    w = win(bars, since)
    cols: Dict[str, Any] = {k: None for k in GENE_KEYS}
    if not w:
        return cols, {c: GAP_HIST_ROWS for c in cols}
    up_bad = [b for b in w if _num(b.get('limit_up')) is None or _num(b.get('high')) is None]
    dn_bad = [b for b in w if _num(b.get('limit_down')) is None or _num(b.get('low')) is None]
    if not up_bad:
        cols['lu_sealed'] = sum(1 for b in w
                                if _num(b.get('close')) is not None
                                and _num(b['close']) >= _num(b['limit_up']) - eps)
        cols['lu_touched'] = sum(1 for b in w if _num(b['high']) >= _num(b['limit_up']) - eps)
    else:
        gap['lu_sealed'] = gap['lu_touched'] = GAP_HIST_LIMIT_UP
    if not dn_bad:
        cols['ld_sealed'] = sum(1 for b in w
                                if _num(b.get('close')) is not None
                                and _num(b['close']) <= _num(b['limit_down']) + eps)
        cols['ld_touched'] = sum(1 for b in w if _num(b['low']) <= _num(b['limit_down']) + eps)
    else:
        gap['ld_sealed'] = gap['ld_touched'] = GAP_HIST_LIMIT_DOWN
    return cols, gap


def consec_by_code(codes: Iterable[str], frm: dt.date, to: dt.date, lup_spec: str,
                   ) -> Tuple[Dict[str, int], bool]:
    """一批票在 `[frm, to]` 里的最高连板：`({代码: 最高几板}, 整批是否读回空)`。

    第二个返回值不是装饰 —— 整批读回空意味着 lup-1 那一场根本没跑（读库失败/还没回算），
    这时"没有连板"是查不到，不是没有；调用方要按 `GAP_CONSEC_SOURCE` 点名而不是 `GAP_NO_ROUND`。
    """
    cs = list(codes)
    if not cs:
        return {}, False
    rows = engine.query(CONSEC_SQL, {'lup': lup_spec, 'frm': frm, 'to': to, 'codes': cs},
                        role='reader') or []
    return {r['security_code']: r['max_consec'] for r in rows}, not rows


# ---------------------------------------------------------------- 全场聚合的那一种写法
#
# 同一把尺有**两种表达式**：上面 `seal_touch_counts` 是"给一批行、按只数"（首板榜只需要榜上那几百只），
# 下面这个是"整场一次聚合"（股性表要算全场 5,500+ 只，逐票拉行是把表拖到内存里数）。
# 两种写法并存本来就是要禁止的（两把尺必分叉），所以它们**只在这里成对出现**，
# 并且有一条不打桩的实测对账钉着：`tests/test_limit_gene.py::同一把尺的两种写法逐只必须相等`。
# 改上面那段判据（eps、缺行怎么处理）而忘了改下面这段 ⇒ 那条测试当场红。

#: 全场逐只数涨停/跌停的两把尺（口径与 `seal_touch_counts` 逐字对应，含"缺一天档位价就整族判不了"）。
GENE_AGG_SQL = """
select security_code,
       count(*) filter (where close is not null and limit_up is not null
                        and close >= limit_up - %(eps)s)                      as lu_sealed,
       count(*) filter (where high is not null and limit_up is not null
                        and high >= limit_up - %(eps)s)                        as lu_touched,
       count(*) filter (where close is not null and limit_down is not null
                        and close <= limit_down + %(eps)s)                     as ld_sealed,
       count(*) filter (where low is not null and limit_down is not null
                        and low <= limit_down + %(eps)s)                        as ld_touched,
       count(*) filter (where limit_up is null or high is null)                as lu_bad_rows,
       count(*) filter (where limit_down is null or low is null)               as ld_bad_rows,
       count(*)                                                               as bars
  from market_data.stock_daily
 where trade_date between %(frm)s and %(to)s
 group by 1
"""


def seal_touch_market(frm: dt.date, to: dt.date, eps: float = SEAL_EPS) -> Dict[str, Dict[str, Any]]:
    """全场每只票在 `[frm, to]` 里的封住/触及天数：`{代码: {四列 + bars + gap}}`。

    与 `seal_touch_counts` 同一条判据；缺档位价的那一族同样交 NULL（`lu_bad_rows > 0` 即整族判不了），
    不给一个"少算了几天"的下界。
    """
    out: Dict[str, Dict[str, Any]] = {}
    for r in (engine.query(GENE_AGG_SQL, {'frm': frm, 'to': to, 'eps': float(eps)}, role='reader') or []):
        bad_up = int(r.get('lu_bad_rows') or 0) > 0
        bad_dn = int(r.get('ld_bad_rows') or 0) > 0
        gap: Dict[str, str] = {}
        if r.get('security_code') is None:
            continue
        out[r['security_code']] = {
            'lu_sealed': None if bad_up else int(r['lu_sealed']),
            'lu_touched': None if bad_up else int(r['lu_touched']),
            'ld_sealed': None if bad_dn else int(r['ld_sealed']),
            'ld_touched': None if bad_dn else int(r['ld_touched']),
            'bars': int(r['bars'] or 0),
            'gap': gap,
        }
        if bad_up:
            out[r['security_code']]['gap'] = {'lu_sealed': GAP_HIST_LIMIT_UP,
                                              'lu_touched': GAP_HIST_LIMIT_UP}
        if bad_dn:
            out[r['security_code']]['gap'].update({'ld_sealed': GAP_HIST_LIMIT_DOWN,
                                                   'ld_touched': GAP_HIST_LIMIT_DOWN})
    return out

