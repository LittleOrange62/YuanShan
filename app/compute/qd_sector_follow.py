# -*- coding: utf-8 -*-
"""
qd 轨 · 板块前三甲的轨迹与相似板块 qd.sector_follow（写侧，判据唯一实现）。

用户 2026-10-25 点名要三件事：领涨板块之后再把后面两名列出；这三名最近五个交易日
的名次变化；再拿这三名 t-2..t-5 的名次去对照全母体，找出现在走的位置最像它们的板块。
同日（10-25 晚）裁定：这一块从"读侧现算 + 一小时缓存"改为**链上算好落库**。
理由（也是这张表存在的理由）：`qd.sector_rotation` 每天只落 day_rank<=8（L-16），
"某个板块在某一天排第几"对 8 名之外根本查不到 ⇒ 形状匹配必须回到 market_data.sector_daily
逐场重排全母体。这件事每天只需要做一次，做完就是死的；放在读侧等于每个访客重付一遍两秒。

**接在链上 `qd_sector` 那一步里**（`qd_sector.refresh()` 末尾调本模块），不新增步数 ——
步数被 `tests/test_volume_ratio.py` 与 e1 的 STAGES 钉着，加一步要动那两处；
而本模块的输入正是同一步已经在读的 sector_daily，同场同尺，分开跑只会多一次对账。

三条不许漂的纪律：

1. **母体与排名尺子只有一份定义**，住在 `qd_sector`（`MOTHER_POOL` / `MOTHER_RANK_ORDER` /
   `MAX_SECTOR_PCT`）。本模块拼同一份文本，不重抄一遍 —— 重抄的那份迟早分叉，
   分叉的表现是首页两块的第 1 名不是同一个板块。
2. **缺就是缺**：名次缺失交 `None`（界面 `—`），绝不折成 0；这几天缺行的板块不进相似榜，
   只进 `skipped_incomplete` 计数。补出来的名次会让"没数据"排成"很差"。
3. **跨日名次只在宇宙一致时才可比**，守卫与板块持续性共用 `qd_common.universe_break`；
   跨了断点的批次照落，但把 L-22 记进 `deviation`，由界面如实说出来。

跑法：随链跑（`python -m ingest.jobs.qd3_sector --recent 1`），或回算全史
`python -m ingest.jobs.qd3_sector --from 2021-08-02`。
"""
from __future__ import annotations

import datetime as dt
import json
from typing import Dict, List, Optional

from psycopg.rows import dict_row

from app.compute import qd_common as Q
from app.compute import qd_sector as W
from app.db import engine

#: 榜单长度：领涨那一名 + 用户点名要的后面两名
TOP_N = 3
#: 轨迹回看几场（用户原话「最近 5 个交易日的趋势变化」，不含基准日那场）
TRAJ_POINTS = 5
#: 形状匹配错开几场。用户举的日期是「abc 的 t-2..t-5」对「所有板块的 t..t-3」⇒ 2 场
DEFAULT_LAG = 2
#: 参与比对的轨迹点数（对齐后的四个名次）
SHAPE_POINTS = 4
#: 每位领涨板块落库的相似板块条数
K_SIMILAR = 6
#: 落库的四档。接口 `/api/qd/sector-follow` 的 lag 参数开的就是这四档
#: （`vr/app.py` 的 Query(ge=0, le=3)），少落一档就有一档"参数能填、永远读不到行"
LAGS = (0, 1, 2, 3)
#: 一片算多少个基准日。全史 1,264 场一把跑会把六万行矩阵行同时握在手里，
#: 40 场一片实测在几秒内完成，且每片打一行进度（判"卡住"至少看得见）
CHUNK_BASE_DAYS = 40

SOURCE = 'quantdash-logic+local'

# 每行都带的固定偏差：两个分母这件事不逐行重写一遍解释，读的人一定会当成同一个数
FOLLOW_DEV = [
    {'code': 'L-23',
     'note': '候选池与当日母体是两个分母：母体＝基准日那场参与排名的板块数，'
             '候选池＝这一窗里在母体出现过行的板块并集减掉领涨板块自己。'
             '某板块可以这几天有行、当天没有行，所以池子会比当日母体大一个把'},
]


def spec_version() -> str:
    """口径版本。号跟着全局 qd 版本拼出来（当前 follow-qd-1）：全局一升这一轨跟着升，
    不会出现「rotation 换了版、follow 还在读旧批次」。本模块自己的判据改版（榜长、
    错开档、轨迹点数）也要一起升号 —— 那些值同时按批次落在每一行的列里，能对照。"""
    return 'follow-' + Q.spec_version()


# ⚠️ 这条必须是**普通字符串**，不能加 r 前缀：raw 串里的 `\\_` 到 SQL 是两个反斜杠，
# `like 'TDX\\_%%'` 就变成"要求代码里真有两个反斜杠"，零行命中且不报错
# （10-25 实测：接口照样 available:false，理由是"没有任何板块行"）。写侧同一条写法。
# 母体那段（pool）与排名那段（ORDER BY）都引 `qd_sector` 的常量，不在这里重抄。
MATRIX_SQL = f"""
WITH pool AS ({W.MOTHER_POOL}),
ranked AS (
    SELECT p.trade_date::text AS trade_date, p.sector_code, p.sector_name, p.sector_type,
           round(p.pct_change::numeric, 2)::float AS pct,
           row_number() OVER (PARTITION BY p.trade_date
                              ORDER BY {W.MOTHER_RANK_ORDER}) AS rnk,
           count(*) OVER (PARTITION BY p.trade_date) AS universe_size
      FROM pool p WHERE NOT dirty
)
SELECT trade_date, sector_code, sector_name, sector_type, rnk, universe_size, pct AS pct_change
  FROM ranked ORDER BY trade_date DESC, rnk
"""

SESSIONS_SQL = """
SELECT DISTINCT trade_date FROM market_data.sector_daily
 WHERE (%(end)s::date IS NULL OR trade_date <= %(end)s::date)
 ORDER BY trade_date DESC
"""

#: 重算一场先清这一场这一版本的全部行：领涨的是哪三只由当天涨幅决定，
#: 只 upsert 不清场会把昨天那只领涨留在榜上（同 193 号任务修掉的异动表那类残留）。
DELETE_SQL = """
DELETE FROM qd.sector_follow
 WHERE trade_date = ANY (%(dates)s::date[]) AND spec_version = %(sv)s
"""

INSERT_SQL = """
INSERT INTO qd.sector_follow
       (trade_date, lag, sector_code, spec_version, source, deviation,
        sector_name, sector_type, day_rank, pct_change, universe_size,
        top_n, trajectory_points, shape_points, k_similar,
        sessions, trajectory, similar_block)
VALUES (%(trade_date)s, %(lag)s, %(sector_code)s, %(sv)s, %(source)s, %(dev)s::jsonb,
        %(sector_name)s, %(sector_type)s, %(day_rank)s, %(pct_change)s, %(universe_size)s,
        %(top_n)s, %(trajectory_points)s, %(shape_points)s, %(k_similar)s,
        %(sessions)s::jsonb, %(trajectory)s::jsonb, %(similar_block)s::jsonb)
ON CONFLICT (trade_date, lag, sector_code, spec_version) DO UPDATE SET
    source = EXCLUDED.source, deviation = EXCLUDED.deviation,
    sector_name = EXCLUDED.sector_name, sector_type = EXCLUDED.sector_type,
    day_rank = EXCLUDED.day_rank, pct_change = EXCLUDED.pct_change,
    universe_size = EXCLUDED.universe_size,
    top_n = EXCLUDED.top_n, trajectory_points = EXCLUDED.trajectory_points,
    shape_points = EXCLUDED.shape_points, k_similar = EXCLUDED.k_similar,
    sessions = EXCLUDED.sessions, trajectory = EXCLUDED.trajectory,
    similar_block = EXCLUDED.similar_block, calculated_at = now()
"""


# =============================================================================
# 一、纯判据（不碰库，单测就跑这一组）
# =============================================================================

def sessions_needed(lag: int = DEFAULT_LAG, traj: int = TRAJ_POINTS,
                    points: int = SHAPE_POINTS) -> int:
    """要往前取几场：轨迹与模板谁要得更深就取多少，再加当日这一场。"""
    return max(traj, lag + points - 1) + 1


def mean_rank_gap(template: List[int], candidate: List[int]) -> Optional[float]:
    """对齐点上的**平均名次差**（绝对差，不做方向）。任一边缺格或长度不等 ⇒ None。"""
    if not template or len(template) != len(candidate):
        return None
    if any(t is None or c is None for t, c in zip(template, candidate)):
        return None
    return round(sum(abs(int(t) - int(c)) for t, c in zip(template, candidate)) / len(template), 2)


def rank_corr(template: List[int], candidate: List[int]) -> Optional[float]:
    """两点以上名次序列的秩相关（先秩后 pearson —— 本机 venv 没有 scipy）。

    序列全平（标准差 0）⇒ None。这不是"没相关性"，是**算不出来**；折成 0 会被
    读成"方向无关"，而 0 恰好是相关判据的中点。
    """
    if not template or len(template) != len(candidate) or len(template) < 3:
        return None
    if any(t is None or c is None for t, c in zip(template, candidate)):
        return None
    # 先查原始序列全平：全平之后"取秩"会凭排序稳定性造出一个人为的顺序，
    # 那不是相关，是编出来的
    if len(set(template)) < 2 or len(set(candidate)) < 2:
        return None

    def ranks(seq):
        order = sorted(range(len(seq)), key=lambda i: seq[i])
        out = [0.0] * len(seq)
        for pos, idx in enumerate(order):
            out[idx] = pos
        return out

    def pearson(a, b):
        n = len(a)
        ma, mb = sum(a) / n, sum(b) / n
        sa = sum((x - ma) ** 2 for x in a) ** 0.5
        sb = sum((x - mb) ** 2 for x in b) ** 0.5
        if not sa or not sb:
            return None
        return sum((x - ma) * (y - mb) for x, y in zip(a, b)) / (sa * sb)

    r = pearson(ranks(list(template)), ranks(list(candidate)))
    return None if r is None else round(r, 3)


def leaders(rows: List[dict], base: str, n: int = TOP_N) -> List[dict]:
    """基准日这场的前 N 名。名次由全母体现算，与 `qd.sector_rotation.day_rank`
    是同一段 SQL 常量算出来的（tests/test_qd_follow.py 拿落库行逐名反查）。"""
    out = []
    for r in rows:
        if r["trade_date"] != base:
            continue
        if int(r["rnk"]) <= n:
            out.append({'day_rank': int(r["rnk"]), 'sector_code': r['sector_code'],
                        'sector_name': r['sector_name'], 'sector_type': r['sector_type'],
                        'pct_change': r['pct_change'], 'universe_size': r['universe_size']})
    return sorted(out, key=lambda x: x['day_rank'])


def trajectory(series: Dict[str, Dict[str, Optional[int]]], code: str,
               dates: List[str], points: int = TRAJ_POINTS) -> List[dict]:
    """该板块**前五场**（不含基准日那场）的名次；缺行交 `rank=None`，界面空着。"""
    out = []
    for day in dates[1:points + 1]:
        out.append({'trade_date': day, 'rank': series.get(code, {}).get(day),
                    'pct_change': None})
    return out


def similar(series: Dict[str, Dict[str, Optional[int]]], info: Dict[str, dict],
            dates: List[str], code: str, lag: int = DEFAULT_LAG,
            points: int = SHAPE_POINTS, k: int = K_SIMILAR,
            universe: Optional[int] = None) -> dict:
    """错开 `lag` 场的形状匹配：模板＝领涨板块 t-lag..t-lag-(points-1)，
    候选＝各板块 t..t-(points-1)。找的是"正落后它 lag 场、轨迹同形"的板块。

    只比**两边都齐**的候选；不齐的计数交出去（`skipped_incomplete`）——
    那批不是"不像"，是"没数据"，混进榜里就是一条假相似。
    """
    tpl_days = dates[lag:lag + points]
    cand_days = dates[0:points]
    template = [series.get(code, {}).get(d) for d in tpl_days]
    # 候选池＝这几天里在母体出现过行的板块数（不等于"当日母体"：有的板块只在早几天出现）
    pool = max(len(series) - 1, 0)
    if len(tpl_days) < points or any(v is None for v in template):
        return {'items': [], 'template': template, 'template_days': tpl_days,
                'candidate_days': cand_days, 'candidate_pool': pool,
                'matched': 0, 'skipped_incomplete': 0, 'incomplete_template': True}
    items, skipped = [], 0
    for other, per_day in series.items():
        if other == code:
            continue
        cand = [per_day.get(d) for d in cand_days]
        if any(v is None for v in cand):
            skipped += 1
            continue
        gap = mean_rank_gap(template, cand)
        if gap is None:
            skipped += 1
            continue
        items.append({
            'sector_code': other,
            'sector_name': (info.get(other) or {}).get('sector_name'),
            'sector_type': (info.get(other) or {}).get('sector_type'),
            'current_rank': cand[0],
            'gap_mean_rank': gap,
            'gap_pct_of_universe': (round(gap / universe * 100.0, 2) if universe else None),
            'rank_corr': rank_corr(template, cand),
            'candidate_ranks': cand,
            'template_ranks': template})
    # 主排序＝名次距离小在前；距离并列时按秩相关高在前（None 排最后，不许当 0）
    items.sort(key=lambda x: (x['gap_mean_rank'],
                              -(x['rank_corr'] if x['rank_corr'] is not None else -2.0)))
    return {'items': items[:k], 'template': template, 'template_days': tpl_days,
            'candidate_days': cand_days, 'candidate_pool': pool,
            'matched': len(items), 'skipped_incomplete': skipped,
            'incomplete_template': False}


# =============================================================================
# 二、取数与内存视图
# =============================================================================

def day_order(rows: List[dict]) -> List[str]:
    """行已按日期倒序，取去重后的场次序列（下标 0 = 最新那场）。"""
    out: List[str] = []
    for r in rows:
        if r["trade_date"] not in out:
            out.append(r["trade_date"])
    return out


def rank_series(rows: List[dict]) -> Dict[str, Dict[str, Optional[int]]]:
    """`{sector_code: {trade_date: 名次}}`。某天没有行就是缺（后面一律不补 0）。"""
    seen: Dict[str, Dict[str, Optional[int]]] = {}
    for r in rows:
        seen.setdefault(r["sector_code"], {})[r["trade_date"]] = int(r["rnk"])
    return seen


def sector_info(rows: List[dict]) -> Dict[str, dict]:
    out: Dict[str, dict] = {}
    for r in rows:
        out.setdefault(r["sector_code"], {
            'sector_code': r['sector_code'], 'sector_name': r['sector_name'],
            'sector_type': r['sector_type']})
    return out


def pct_by_day(rows: List[dict]) -> Dict[str, Dict[str, Optional[float]]]:
    seen: Dict[str, Dict[str, Optional[float]]] = {}
    for r in rows:
        seen.setdefault(r["sector_code"], {})[r["trade_date"]] = r["pct_change"]
    return seen


def universe_by_day(rows: List[dict]) -> Dict[str, Optional[int]]:
    seen: Dict[str, Optional[int]] = {}
    for r in rows:
        seen.setdefault(r["trade_date"], r["universe_size"])
    return seen


def _json(x) -> str:
    """jsonb 落库前的最后一道闸：NaN 进不了 JSON（PG 会整条语句炸），
    所以在这里让它**当场出声**而不是写成一行坏数据。"""
    return json.dumps(x, ensure_ascii=False, allow_nan=False)


def _deviation(block: dict, window: List[str], uni: Dict[str, Optional[int]]) -> str:
    """每行的偏差：固定的 L-23 + 视情况而发的 L-24（模板缺场）与 L-22（跨宇宙断点）。"""
    dev = list(FOLLOW_DEV)
    if block.get('incomplete_template'):
        dev.append({'code': 'L-24',
                    'note': '模板那几天里有名次取不到（该板块那几天没有行情行，或窗口伸到了'
                            '库里最早一场之前）⇒ 整块不比对，items 交空数组。'
                            '空榜不等于"没有相似板块"，是没法比'})
    broken = Q.universe_break([uni.get(d) for d in window])
    if broken:
        dev.append({'code': 'L-22',
                    'note': '本行的窗口跨了母体扩容（当日参与排名的板块数 %s）⇒ '
                            '轨迹与相似榜里的名次是各当天全母体的真名次，但跨日不可直接比：'
                            '多出来的那批板块会把名次整体往后推。实测断点 2026-09-23 起，'
                            '上游 c3/c17 把通达信行业从约 180 只补到 561 只参与排名'
                            % broken})
    return _json(dev)


# =============================================================================
# 三、算与写
# =============================================================================

def build_records(rows: List[dict], window: List[str], base: str) -> list[dict]:
    """一个基准日 → 若干行（前 TOP_N 名 × LAGS 档）。

    `rows` 只装 `window` 这几天（不装整个分片）：候选池、缺行计数、母体数都要按
    "这一窗里出现过行的板块"算，多塞几天的行会把池子撑大、把比对数算高。
    `window` 是**含基准日在内、由近及远**的场次序列（长度 = sessions_needed(最深档)）。
    基准日不在 window 里就一行不交 —— 不拿前一天冒充当天。
    """
    if not window or window[0] != base:
        return []
    series, info, pct = rank_series(rows), sector_info(rows), pct_by_day(rows)
    uni = universe_by_day(rows)
    leads = leaders(rows, base)
    out: List[dict] = []
    for lag in LAGS:
        dates = window[:sessions_needed(lag)]
        for lead in leads:
            block = similar(series, info, dates, lead['sector_code'], lag=lag,
                            universe=lead.get('universe_size'))
            traj = trajectory(series, lead['sector_code'], dates)
            for cell in traj:
                cell['pct_change'] = (pct.get(lead['sector_code']) or {}).get(cell['trade_date'])
            out.append({
                'trade_date': dt.date.fromisoformat(base), 'lag': lag,
                'sector_code': lead['sector_code'],
                'sector_name': lead['sector_name'], 'sector_type': lead['sector_type'],
                'day_rank': lead['day_rank'], 'pct_change': lead['pct_change'],
                'universe_size': lead.get('universe_size'),
                'top_n': TOP_N, 'trajectory_points': TRAJ_POINTS,
                'shape_points': SHAPE_POINTS, 'k_similar': K_SIMILAR,
                'sessions': _json(dates), 'trajectory': _json(traj),
                'similar_block': _json(block),
                'dev': _deviation(block, dates, uni),
                'source': SOURCE, 'sv': spec_version(),
            })
    return out


def refresh(dates: List[dt.date], progress=None) -> dict:
    """把 `dates` 这些交易日的前三甲轨迹与相似榜算好落库。

    分片跑：一片的基准日 + 各自回看的场次一次查询（全史 1,264 场分 40 场一片，
    实测比逐场一问快一个量级 —— 逐场是 1,264 次往返）。
    算不出领涨板块的日子（当天没有任何干净板块行）**整天空掉并记进 skipped**，
    宁可缺行，不可假行。

    `progress` 由调用方（作业脚本）给的打印回调：本模块自己**不 print** ——
    被测试或常驻线程调到时，往 pytest 的全局捕获文件里写字节会造出一条与判据无关的假红。
    """
    sv = spec_version()
    out = {'follow_rows': 0, 'follow_days': 0, 'follow_skipped': []}
    targets = sorted(set(dates), reverse=True)
    if not targets:
        return out
    need = sessions_needed(max(LAGS))
    with engine.conn('etl') as c:
        with c.cursor(row_factory=dict_row) as cur:
            cur.execute(SESSIONS_SQL, {'end': targets[0]})
            sessions = [r['trade_date'] for r in cur.fetchall()]
        # 这场次序列是后面所有切片的坐标系，它坏了不是"算得差一点"而是整批错位：
        # 10-25 实测漏了 DISTINCT 时，`pos` 把每个基准日都指到"该日最后一行"的位置，
        # 于是 window 全是同一天重复六遍，轨迹与模板读起来完全自洽、却是彻底假的数。
        if len(set(sessions)) != len(sessions) or sessions != sorted(sessions, reverse=True):
            raise RuntimeError(f'场次序列不是"倒序且不重复"（拿到 {len(sessions)} 条，'
                               f'去重后 {len(set(sessions))} 条）—— 整批会错位，拒绝开跑')
        pos = {d: i for i, d in enumerate(sessions)}
        for i in range(0, len(targets), CHUNK_BASE_DAYS):
            grp = targets[i:i + CHUNK_BASE_DAYS]
            lo, hi = pos[grp[0]], pos[grp[-1]] + need
            window_dates = sessions[lo:hi]
            with c.cursor(row_factory=dict_row) as cur:
                cur.execute(MATRIX_SQL, {'dates': window_dates, 'cap': W.MAX_SECTOR_PCT})
                rows = cur.fetchall()
            by_day: Dict[str, List[dict]] = {}
            for r in rows:
                by_day.setdefault(r['trade_date'], []).append(r)
            recs: List[dict] = []
            written: List[dt.date] = []
            for base in grp:
                p = pos.get(base)
                if p is None:
                    out['follow_skipped'].append(base)      # 基准日连场次序列都没有
                    continue
                window = [d.isoformat() for d in sessions[p:p + need]]
                wrows = [r for d in window for r in by_day.get(d, ())]
                got = build_records(wrows, window, base.isoformat())
                if not got:
                    out['follow_skipped'].append(base)
                    continue
                recs += got
                written.append(base)
            # 先清后写：这一场这一版本的行由本次计算完全决定（残留的旧领涨会顶在新榜上）
            c.execute(DELETE_SQL, {'dates': grp, 'sv': sv})
            if recs:
                with c.cursor() as ins:
                    ins.executemany(INSERT_SQL, recs)
            # 一片一交：全史 1,264 场要跑十几分钟，中途被 statement_timeout 掐断或作业被回收时，
            # 已完成那些片不该跟着整笔回滚（重跑是幂等的：先删后写）
            c.commit()
            out['follow_rows'] += len(recs)
            out['follow_days'] += len(written)
            if progress:
                progress('  follow 片 %s..%s：%d 场 %d 行'
                         % (grp[-1], grp[0], len(written), len(recs)))
    return out
