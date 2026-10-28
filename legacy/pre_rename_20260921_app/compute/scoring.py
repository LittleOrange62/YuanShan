# -*- coding: utf-8 -*-
"""
评分驱动：把 config.capability × config.v_effective_binding 跑成 fct.score + fct.score_detail。

**分项必须落库。** 一个 78 分的票，78 是怎么来的、哪个分项拖了后腿，
在 /stock 页面上要能直接看到。旧系统只存总分，复盘时只能猜，
所以这里每算一个分项就写一行 fct.score_detail，带权重和贡献值。

**脚本契约**：`app/compute/builtin/*.py` 各自实现

    def run(ctx: ScoreContext, params: dict) -> pandas.Series

返回值以 ts_code 为索引，取值落在该功能点声明的区间内（多数是 -100~100）。
脚本自己不写库、不管权重、不管排名 —— 那是本模块的事。这样在 /blocks 页面上
改权重或停用某个脚本，不需要改任何脚本代码。

**依赖顺序来自 config.capability.depends_on**，不是硬编码：
base_factors → stock_quality / sector_heat / market_mood / temp_score
             → env_resonance → oper_score → review_report
"""
from __future__ import annotations

import datetime as dt
import importlib
import time
from typing import Any

import numpy as np
import pandas as pd

from app.compute import expr as ex
from app.compute import indicators as ind_mod
from app.db import engine


class ScoreContext:
    """
    一次评分运行共享的数据与工具。面板只取一次，指标按需算并缓存——
    17 个脚本里有一半都要用 ma20、vol_ratio 这类，重复算是纯浪费。
    """

    def __init__(self, trade_date: dt.date, panel: pd.DataFrame,
                 sector_daily: pd.DataFrame | None = None):
        self.trade_date = trade_date
        self.panel = panel
        self.sector_daily = (sector_daily if sector_daily is not None
                             else pd.DataFrame())
        self._cache: dict[str, pd.Series] = {}
        self._target = pd.Timestamp(trade_date).date()
        self.today = self._cross_section()

    def _cross_section(self) -> pd.DataFrame:
        mask = self.panel.index.get_level_values('trade_date') == self._target
        cut = self.panel[mask]
        return cut.reset_index(level='trade_date', drop=True)

    @property
    def codes(self) -> pd.Index:
        return self.today.index

    # -------------------------------------------------------- 板块侧

    @property
    def sector_today(self) -> pd.DataFrame:
        """目标交易日的板块截面，索引 sector_code。"""
        if self.sector_daily.empty:
            return pd.DataFrame()
        mask = self.sector_daily.index.get_level_values('trade_date') == self._target
        return self.sector_daily[mask].reset_index(level='trade_date', drop=True)

    def sector_roll(self, col: str, n: int, how: str = 'mean') -> pd.Series:
        """板块级滚动统计，返回目标日截面，索引 sector_code。"""
        if self.sector_daily.empty or col not in self.sector_daily.columns:
            return pd.Series(dtype='float64')
        s = self.sector_daily[col].astype('float64')
        g = s.groupby(level='sector_code', sort=False).rolling(n, min_periods=1)
        out = getattr(g, how)()
        if out.index.nlevels > s.index.nlevels:
            out = out.droplevel(0)
        mask = out.index.get_level_values('trade_date') == self._target
        return out[mask].reset_index(level='trade_date', drop=True)

    def broadcast(self, by_sector: pd.Series) -> pd.Series:
        """
        板块级数值摊到成员个股上。

        行业板块只覆盖约 54% 的个股，其余票拿到空值 —— 这是数据缺口，
        不用 0 顶替：0 在 -100~100 的量纲里表示"中性"，会把"不知道"
        伪装成"一般"，评分驱动那边会把这类分项按缺失处理。
        """
        sec = self.today.get('sector')
        if sec is None or by_sector.empty:
            return pd.Series(index=self.codes, dtype='float64')
        return sec.map(by_sector).astype('float64')

    def series(self, expr: str) -> pd.Series:
        """在整段面板上算一个表达式，返回带两级索引的 Series。"""
        if expr not in self._cache:
            self._cache[expr] = ex.evaluate(expr, self.panel)
        return self._cache[expr]

    def cs(self, expr: str) -> pd.Series:
        """算表达式并只取目标交易日的截面，索引是 ts_code。"""
        s = self.series(expr)
        mask = s.index.get_level_values('trade_date') == self._target
        return s[mask].reset_index(level='trade_date', drop=True)

    # -------------------------------------------------------- 归一化工具

    def pct_score(self, values: pd.Series, *, reverse: bool = False) -> pd.Series:
        """
        截面百分位 → -100~100。

        用百分位而不是 z-score：行情分布尾巴很厚，一只妖股能把整个 z 分布压平，
        当天所有票的分数都挤在 0 附近，评分就失去分辨力了。
        """
        v = pd.to_numeric(values, errors='coerce')
        r = v.rank(pct=True, ascending=not reverse)
        return (r * 200 - 100).reindex(self.codes)

    def clip_score(self, values: pd.Series, lo: float, hi: float) -> pd.Series:
        """把物理量线性映射到 -100~100，超出区间截断。用于有绝对含义的量。"""
        v = pd.to_numeric(values, errors='coerce').clip(lo, hi)
        return ((v - lo) / (hi - lo) * 200 - 100).reindex(self.codes)

    @staticmethod
    def pct_raw(values: pd.Series, *, reverse: bool = False) -> pd.Series:
        """同 pct_score，但**不**重排到个股索引。板块级打分用这个。"""
        v = pd.to_numeric(values, errors='coerce')
        r = v.rank(pct=True, ascending=not reverse)
        return r * 200 - 100

    @staticmethod
    def clip_raw(values: pd.Series, lo: float, hi: float) -> pd.Series:
        v = pd.to_numeric(values, errors='coerce').clip(lo, hi)
        return (v - lo) / (hi - lo) * 200 - 100

    def zero(self) -> pd.Series:
        return pd.Series(0.0, index=self.codes)


# ---------------------------------------------------------------- 注册表读取

def bindings(cap_key: str, *, role: str = 'reader') -> list[dict]:
    return engine.query(
        """
        SELECT b.cap_key, b.script_key, b.script_id, b.seq, b.eff_weight,
               b.raw_weight, b.params, b.path, b.version
          FROM config.v_effective_binding b
         WHERE b.cap_key = %s
         ORDER BY b.seq
        """, (cap_key,), role=role)


def capabilities(*, role: str = 'reader') -> list[dict]:
    return engine.query(
        """
        SELECT cap_key, name, output_kind, output_spec, depends_on, requires
          FROM config.capability WHERE enabled ORDER BY cap_key
        """, role=role)


def topo_order(caps: list[dict]) -> list[dict]:
    """按 depends_on 拓扑排序。有环就抛错，不要默默按字母序跑出错的结果。"""
    by_key = {c['cap_key']: c for c in caps}
    done: list[dict] = []
    seen: set[str] = set()
    visiting: set[str] = set()

    def visit(key: str) -> None:
        if key in seen or key not in by_key:
            return
        if key in visiting:
            raise RuntimeError(f'config.capability.depends_on 有环：{key}')
        visiting.add(key)
        for dep in (by_key[key]['depends_on'] or []):
            visit(dep)
        visiting.discard(key)
        seen.add(key)
        done.append(by_key[key])

    for c in sorted(caps, key=lambda x: x['cap_key']):
        visit(c['cap_key'])
    return done


def load_module(path: str):
    """
    把 config.script.path 转成模块导入。

    只接受 `builtin/xxx.py` 这种相对路径并映射到 app.compute.builtin 包内，
    **不做任意路径加载**：config.script 是页面上可编辑的表，
    允许它指向任意文件等于把任意代码执行权交出去。

    单独拆出来是因为 app/compute/explain.py 也要按 path 找模块（取 EXPLAIN），
    这条白名单是安全边界，不能抄第二份。
    """
    p = path.replace('\\', '/').strip('/')
    if not p.startswith('builtin/') or not p.endswith('.py') or '..' in p:
        raise ValueError(f'脚本路径只允许 builtin/*.py，收到 {path!r}')
    return importlib.import_module('app.compute.builtin.' + p[len('builtin/'):-3])


def load_entry(path: str, entry: str):
    """按 config.script 的 path + entry 取出可调用的脚本入口。"""
    mod = load_module(path)
    fn = getattr(mod, entry, None)
    if not callable(fn):
        raise ValueError(f'{mod.__name__} 里没有可调用的 {entry}')
    return fn



# ---------------------------------------------------------------- 落库

SCORE_UPSERT = """
INSERT INTO fct.score (ts_code, trade_date, cap_key, value, rank_in_market,
                       pct_rank, quadrant, task_id, is_stale, calculated_at)
SELECT ts_code, trade_date, cap_key, value, rank_in_market, pct_rank,
       quadrant, task_id, false, now() FROM md.stg_score
ON CONFLICT (ts_code, trade_date, cap_key) DO UPDATE SET
    value = EXCLUDED.value, rank_in_market = EXCLUDED.rank_in_market,
    pct_rank = EXCLUDED.pct_rank, quadrant = EXCLUDED.quadrant,
    task_id = EXCLUDED.task_id, is_stale = false, calculated_at = now()
"""

DETAIL_UPSERT = """
INSERT INTO fct.score_detail (ts_code, trade_date, cap_key, part_key, part_label,
                              part_value, weight, contribution, script_key, script_ver)
SELECT ts_code, trade_date, cap_key, part_key, part_label, part_value, weight,
       contribution, script_key, script_ver FROM md.stg_score_detail
ON CONFLICT (ts_code, trade_date, cap_key, part_key) DO UPDATE SET
    part_label = EXCLUDED.part_label, part_value = EXCLUDED.part_value,
    weight = EXCLUDED.weight, contribution = EXCLUDED.contribution,
    script_key = EXCLUDED.script_key, script_ver = EXCLUDED.script_ver
"""

_SCORE_COLS = ('ts_code', 'trade_date', 'cap_key', 'value', 'rank_in_market',
               'pct_rank', 'quadrant', 'task_id')
_DETAIL_COLS = ('ts_code', 'trade_date', 'cap_key', 'part_key', 'part_label',
                'part_value', 'weight', 'contribution', 'script_key', 'script_ver')


def _write(table: str, cols, ddl: str, rows: list[tuple], upsert: str) -> int:
    if not rows:
        return 0
    with engine.conn('admin') as c, c.cursor() as cur:
        cur.execute(f'DROP TABLE IF EXISTS {table}')
        cur.execute(f'CREATE UNLOGGED TABLE {table} ({ddl})')
    engine.copy_rows(table, list(cols), iter(rows), role='admin')
    n = engine.execute(upsert, role='admin')
    with engine.conn('admin') as c, c.cursor() as cur:
        cur.execute(f'DROP TABLE IF EXISTS {table}')
    return n


def write_scores(rows: list[tuple]) -> int:
    return _write('md.stg_score', _SCORE_COLS,
                  'ts_code text, trade_date date, cap_key text, '
                  'value numeric(16,4), rank_in_market int, '
                  'pct_rank numeric(8,4), quadrant text, task_id bigint',
                  rows, SCORE_UPSERT)


def write_details(rows: list[tuple]) -> int:
    return _write('md.stg_score_detail', _DETAIL_COLS,
                  'ts_code text, trade_date date, cap_key text, part_key text, '
                  'part_label text, part_value numeric(16,4), '
                  'weight numeric(8,4), contribution numeric(16,4), '
                  'script_key text, script_ver text',
                  rows, DETAIL_UPSERT)


# ---------------------------------------------------------------- 主流程

QUADRANT_LABEL = {
    'Q1': '买入',    # 双强：趋势性买入
    'Q2': '持有',    # 强票弱环境：强势股回调，持有不加
    'Q3': '试探',    # 弱票强环境：板块热但个股弱，小仓位试探
    'Q4': '回避',    # 双弱：避开
}


def quadrant_of(x: float, y: float) -> str | None:
    """
    X 轴个股质量、Y 轴环境共振交叉出的四象限。

        Q1 买入  X≥0 Y≥0   双强，趋势性买入
        Q2 持有  X≥0 Y<0   强势股回调，持有不加
        Q3 试探  X<0 Y≥0   板块热但个股弱，小仓位试探
        Q4 回避  X<0 Y<0   避开

    象限比总分有用：同样 60 分，Q1 是顺风顺水，Q2 是逆势独走，操作上完全不同。

    **编号口径以 `config.setting.quadrant_labels` 为准**（那里写的是
    `{"Q1":"买入","Q2":"持有","Q3":"试探","Q4":"回避"}`）。
    第一版把 Q2/Q3/Q4 三个全排错了 —— Q2 写成 X<0 Y≥0、Q3 写成双弱、
    Q4 写成 X≥0 Y<0。代码内部自洽，所以跑起来不报错、测试也不拦，
    但和种子数据里的标签、说明书、以及旧库那 227,985 条评分的编号全对不上：
    **页面上会把该回避的票标成"持有"。** 这类错不抛异常，只会一直给错结论。
    """
    if x is None or y is None or pd.isna(x) or pd.isna(y):
        return None
    if x >= 0:
        return 'Q1' if y >= 0 else 'Q2'
    return 'Q3' if y >= 0 else 'Q4'


def run_capability(cap: dict, ctx: ScoreContext, prior: dict[str, pd.Series],
                   *, task_id: int | None = None) -> dict:
    """算一个功能点。prior 里放着已算完的上游功能点得分，供 depends_on 使用。"""
    cap_key = cap['cap_key']
    binds = bindings(cap_key)
    if not binds:
        return {'cap_key': cap_key, 'skipped': '没有启用的绑定'}

    total = pd.Series(0.0, index=ctx.codes)
    weight_sum = pd.Series(0.0, index=ctx.codes)
    detail_rows: list[tuple] = []
    parts_ok, parts_failed = [], {}
    coverage: dict[str, int] = {}

    for b in binds:
        try:
            fn = load_entry(b['path'], 'run')
            params = dict(b['params'] or {})
            params['_prior'] = prior
            part = fn(ctx, params)
        except Exception as exc:  # noqa: BLE001
            parts_failed[b['script_key']] = f'{type(exc).__name__}: {exc}'
            continue

        part = pd.to_numeric(part, errors='coerce').reindex(ctx.codes)
        w = float(b['eff_weight'] or 0)
        contrib = part * w
        # 只在分项有值的地方累加权重，否则缺一个分项会把总分整体拉低，
        # 看起来像"这只票变差了"，其实是数据缺失
        valid = part.notna()
        coverage[b['script_key']] = int(valid.sum())
        total = total.add(contrib.where(valid, 0.0), fill_value=0.0)
        weight_sum = weight_sum.add(pd.Series(w, index=ctx.codes).where(valid, 0.0),
                                    fill_value=0.0)
        parts_ok.append(b['script_key'])

        part_key = b['script_key'].rsplit('_v', 1)[0]
        for code, v in part[valid].items():
            detail_rows.append((code, ctx.trade_date, cap_key, part_key,
                                b['script_key'], round(float(v), 4), round(w, 4),
                                round(float(v) * w, 4), b['script_key'],
                                b['version']))

    score = (total / weight_sum.replace(0, np.nan)).round(4)
    # **分项一个值都没算出来，必须报出来。** 脚本不抛异常、只是返回一片空值时，
    # 总分照样出数：权重会自动归一到剩下的分项上，页面上看不出任何异常。
    # 实测踩过一次 —— md.sector_daily 的日期停在个股日线之前 4 天，
    # 板块 4 个分项全空，env_resonance 静默退化成"只看大盘情绪"。
    empty = [k for k, n in coverage.items() if n == 0]
    return {'cap_key': cap_key, 'score': score, 'detail_rows': detail_rows,
            'parts': parts_ok, 'failed': parts_failed,
            'coverage': coverage, 'empty_parts': empty}


def compute_date(trade_date: dt.date, *, cap_keys: list[str] | None = None,
                 task_id: int | None = None, dry_run: bool = False,
                 collect: bool = False) -> dict:
    t0 = time.time()
    caps = [c for c in topo_order(capabilities())
            if c['output_kind'] == 'score' and (not cap_keys or c['cap_key'] in cap_keys)]
    if not caps:
        return {'error': '没有需要计算的评分功能点'}

    lookback = max((c['requires'] or {}).get('lookback_days', 60) for c in caps) + 12
    panel = ind_mod.load_panel(trade_date, lookback)
    if panel.empty:
        return {'error': f'{trade_date} 没有行情数据'}
    ctx = ScoreContext(trade_date, panel, sector_panel(trade_date, lookback))

    stat: dict[str, Any] = {'trade_date': str(trade_date), 'lookback': lookback,
                            'codes': len(ctx.codes), 'caps': {}}
    prior: dict[str, pd.Series] = {}
    score_rows: list[tuple] = []
    detail_rows: list[tuple] = []

    for cap in caps:
        res = run_capability(cap, ctx, prior, task_id=task_id)
        if 'score' not in res:
            stat['caps'][cap['cap_key']] = res.get('skipped', '未产出')
            continue
        s = res['score'].dropna()
        prior[cap['cap_key']] = res['score']
        rank = s.rank(method='min', ascending=False).astype('Int64')
        pct = (s.rank(pct=True) * 100).round(4)
        for code, v in s.items():
            q = None
            if cap['cap_key'] == 'oper_score':
                q = quadrant_of(prior.get('stock_quality', pd.Series()).get(code),
                                prior.get('env_resonance', pd.Series()).get(code))
            score_rows.append((code, trade_date, cap['cap_key'], round(float(v), 4),
                               int(rank[code]), float(pct[code]), q, task_id))
        detail_rows.extend(res['detail_rows'])
        stat['caps'][cap['cap_key']] = {
            'scored': int(len(s)), 'parts': res['parts'],
            'coverage': res['coverage'],
            **({'empty_parts': res['empty_parts']} if res['empty_parts'] else {}),
            **({'failed': res['failed']} if res['failed'] else {})}

    stat['score_rows'] = len(score_rows)
    stat['detail_rows'] = len(detail_rows)
    if collect:
        # 对照验证要拿到具体数值。不默认返回：一天 3 万个元组，
        # 编排层每天调一次、只关心统计量，把它们一路带回去纯属浪费
        stat['rows'] = score_rows
    if not dry_run:
        stat['scores_written'] = write_scores(score_rows)
        stat['details_written'] = write_details(detail_rows)
    stat['seconds'] = round(time.time() - t0, 1)
    return stat


def sector_panel(trade_date: dt.date, lookback: int, *,
                 role: str = 'reader') -> pd.DataFrame:
    """
    板块日线面板，索引 (sector_code, trade_date)。板块类脚本要用。

    **只留"可用板块"**（2026-08-30 起挡不可信的，2026-09-01 起再挡历史太短的）：

    - 653 个有日线的板块里有 46 个历史上出现过 `|pct_chg| > 21%`，最离谱的
      `TDX_880046` 有 +429,900%。它们分两类 —— 连板块名都没采到的无名板块，
      以及「昨日涨停」这类**每天重筛成分**、本来就没有连续指数的风格板块；
    - 历史不足 25 天的板块（同花顺板块 2026-08-31 首次入库时有 86 个，各 1 天）
      —— MA5/MA20 用 1 个点算出来的值不是动量。

    判据和阈值都在 `app/compute/sector_heat_hist.py`（`usable_clause` /
    `MAX_ABS_PCT` / `MIN_HISTORY`），三处共用一份，不各写一遍。

    为什么要在这里就挡掉：四个板块分项里有三个是**截面百分位**，垃圾板块占着
    排名位会把其余板块的分位整体挤偏，而截面分母一天一变还会让跨日不可比。
    """
    from app.compute.sector_heat_hist import usable_clause
    rows = engine.query(
        f"""
        WITH dates AS (
            SELECT cal_date FROM ref.trade_calendar
             WHERE is_open AND cal_date <= %(d)s
             ORDER BY cal_date DESC LIMIT %(n)s)
        SELECT d.sector_code, d.trade_date, d.open, d.high, d.low, d.close,
               d.pre_close, d.pct_chg, d.vol_shares, d.amount_yuan,
               d.up_count, d.down_count, d.member_count
          FROM md.sector_daily d
         WHERE d.trade_date IN (SELECT cal_date FROM dates)
           AND {usable_clause('d')}
         ORDER BY d.sector_code, d.trade_date
        """, {'d': trade_date, 'n': lookback}, role=role)

    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    for c in ('open', 'high', 'low', 'close', 'pre_close', 'pct_chg',
              'vol_shares', 'amount_yuan', 'up_count', 'down_count', 'member_count'):
        df[c] = pd.to_numeric(df[c], errors='coerce')
    return df.set_index(['sector_code', 'trade_date']).sort_index()
