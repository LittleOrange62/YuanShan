# -*- coding: utf-8 -*-
"""最强模式 · 判据（用户 2026-11-10 点名：「加个推送 11，每天 19:19 分准时跑。计算最强模式，
你可以预设一些，然后分析方式和方法参考上面昨天的算法……并且列出最近 5 天最强模式的变化和趋势」）。

这一块答的问题：**今天这批涨停，走的是哪一种打法**。用户给的那条样例是三段式证据
（最强模式一行 ＋ 证据1｜2｜3），判法照它落成一串可复核的数，全部读库里已加工的结果，
一个都不现抓源站：

| 预设模式 | 键 |  membership 判据（全部对着「近 N 场」这一把尺） |
|---|---|---|
| 高位反复二波 | `repeat_high` | 窗口内封板 ≥`sm_repeat_min` 次，且 次数 ≥`sm_repeat_ratio` × 最长连板，且 `pos250` ≥`sm_high_pos` |
| 断板反包 | `reversal` | 今日封住且连板数 =1（＝上一场没封住），而窗口内板过 ≥`sm_reversal_min` 次 —— 断了又回来 |
| 二波接力 | `second_wave` | 今日连板 ≥2，而本轮跨度 > 本轮板数（这一轮中间空过一天以上） |
| 一波流连板 | `wave_single` | 本轮跨度 = 本轮板数 ≥`sm_wave_min`（从头连到尾、一天没断） |
| 首板扩散 | `first_spread` | 今日封住、连板 1、本轮 1 天 1 板且窗口内第一次封板（新面孔） |
| 一字锁仓 | `one_word` | 板型＝一字板 且 今日连板 ≥`sm_yizi_min`（排队买一封死，换手为零） |

三条口径上的决定，都是为了让那三行证据**互相不打架**：

1. **一把尺量到底**：次数、最长连板、几天几板全部取自 `analytics.limit_up_pool_daily`（`lup-1`）
   的封住行。这一张表的封住容差是 0.005，而 `duanxian/board_span.py` 从 `stock_daily` 数的那把是
   0.001 —— 两把混在一句话里（「9 次涨停仅 4 连板」）就会出现「次数是这把数出来的、板数是那把」，
   事后无从分开。所以「几天几板」这里**复用 `board_span.span_from_sealed` 那把纯判据**
   （断两天才算轮的规则只有一份），但喂进去的序列由本模块的母体拼，不再走 `stock_daily`。
2. **模式不互斥，排名看相对强度**：一只票可以同时是「高位反复」与「断板反包」（样例那句
   「C+D 高位反复二波/异动规避反包」本来就是两档并列）。而「今天哪一档最强」不能按只数排 ——
   `first_spread`（首板扩散）永远是当日只数最多的一档（11-09 实测 22/58，11-04 是 77/123），
   按只数排就等于天天报同一个词。所以强度＝**今日占比 − 近 `sm_baseline_sessions` 场该档占比的中位数**
   （基准不含当日），并要 `sm_min_n` 只以上才配当选；两档强度差在 `sm_tie_pp` 个百分点内就报复合模式。
3. **宁缺毋假**：库里没有今天的涨停池场次 ⇒ 交 `missing='no_pool_rows'` 并让调用方**一条都不发**；
   某只票的 `pos250` 缺 ⇒ 它不进「高位反复」那一档并计入 `pos_undecided`，不当成 0 分位。

---- 与推送／回测的关系 ----

判据只住本文件一份。`ingest/jobs/push/t11_strong_mode.py`（⑪ 那一炉）是它的一个调用方：
读到这里算、排版在 `app/intraday/templates.json` 的 `strong_mode_*`、正文先落
`analytics.intraday_broadcast` 再发渠道。逐日那六档的读数同时落 `analytics.strong_mode_daily`
（`app/db/sql/65_strong_mode_daily.sql`）—— 那样「上周说的高位反复，第二天到底走得怎么样」
问得出答案，而不需要现在猜。
"""
from __future__ import annotations

import datetime as dt
import json
import statistics
from pathlib import Path
from typing import Any, Iterable, Optional

from app.db import engine

ROOT = Path(__file__).resolve().parents[1]
CFG_FILE = ROOT / 'app' / 'intraday' / 'broadcast_config.json'

#: 涨停池母体（`app/compute/limit_up_pool.SPEC_VERSION` 同一条）与产出表
POOL_SPEC = 'lup-1'
POOL_TABLE = 'analytics.limit_up_pool_daily'
IND_TABLE = 'analytics.indicator_value'
TABLE = 'analytics.strong_mode_daily'
#: 判据版本：模式定义或阈值口径改版就换它，旧行留着对照，不覆盖
SPEC_VERSION = 'strong-mode-1'

#: 六档预设模式：`(键, 屏上名)`。顺序＝界面上那一列的顺序，也是复合模式里念出来的顺序。
MODES: tuple[tuple[str, str], ...] = (
    ('repeat_high', '高位反复二波'),
    ('reversal', '断板反包'),
    ('second_wave', '二波接力'),
    ('wave_single', '一波流连板'),
    ('first_spread', '首板扩散'),
    ('one_word', '一字锁仓'),
)
MODE_LABELS = dict(MODES)

#: 阈值默认值。**真源是 `app/intraday/broadcast_config.json`**，这里只在配置文件读不到时兜底，
#: 并交一条 `params_unread` 声明（同 ⑩ 明日展望那套纪律）。
PARAM_DEFAULTS: dict[str, Any] = {
    'sm_window_sessions': 20,      # 数「涨停次数／最长连板／几天几板」的窗口（用户样例用的就是 20 日）
    'sm_baseline_sessions': 20,    # 常态基准：该模式占比的中位数回溯几场（不含当日）
    'sm_trend_sessions': 5,        # 「最近几场的变化与趋势」（用户点名 5）
    'sm_top_n': 10,                # 证据1 取窗口涨停天数前几名
    'sm_evidence_n': 5,            # 每条证据最多点几只
    'sm_repeat_min': 5,            # 高位反复：窗口内至少几次封板
    'sm_repeat_ratio': 2,          # 高位反复：次数 ÷ 最长连板 至少几倍
    'sm_high_pos': 70.0,           # 高位反复：`pos250` 分位下限
    'sm_reversal_min': 2,          # 断板反包：窗口内至少板过几次
    'sm_wave_min': 3,              # 一波流：本轮至少几板
    'sm_yizi_min': 2,              # 一字锁仓：至少几板
    'sm_min_n': 3,                 # 一个模式今天至少命中几只才配当「最强」
    'sm_tie_pp': 3.0,              # 两档强度差在这个百分点内 ⇒ 报复合模式
    'sm_case_max_n': 2,            # 一波流只数 ≤ 这个数 ⇒ 证据3 说「属个案而非模式」
}


def load_config() -> tuple[dict, Optional[str]]:
    """参数：配置文件 → 读不到时兜底并**出声**（兜底值与出厂值必须逐键相等，见测试）。"""
    try:
        raw = json.loads(CFG_FILE.read_text(encoding='utf-8'))
    except Exception as exc:  # noqa: BLE001  文件坏了/没装都不能悄悄换尺
        return dict(PARAM_DEFAULTS), f'配置文件读不出来（{type(exc).__name__}），走代码默认值'
    if not isinstance(raw, dict):
        return dict(PARAM_DEFAULTS), '配置文件不是对象，走代码默认值'
    return {k: raw.get(k, v) for k, v in PARAM_DEFAULTS.items()}, None


def params_block(cfg: dict) -> dict:
    """随正文与落库一起交出去的那格参数快照（事后才问得出「这组读数是哪套阈值给的」）。"""
    return {k: cfg[k] for k in PARAM_DEFAULTS}


def _f(v: Any) -> Optional[float]:
    return None if v is None else float(v)


def _median(values: list[float]) -> Optional[float]:
    """中位数，**再收一位小数**：两次 `round(...,1)` 的均值能落出 27.200000000000003，
    那串数字进了正文就是「占比 27.200000000000003%」这种没法看的东西（屏上与群里都直接念它）。
    """
    return None if not values else round(float(statistics.median(values)), 1)


# --------------------------------------------------------------------- 取数（一次取满整段）

def sessions_before(end_day: dt.date, n: int) -> list[dt.date]:
    """涨停池里 ≤`end_day` 的最近 `n` 个交易日，**升序**返回（场次由库里真有行的日子给，不查日历）。"""
    rows = engine.query(
        f"select distinct trade_date from {POOL_TABLE}"
        " where spec_version = %s and trade_date <= %s::date"
        " order by trade_date desc limit %s", (POOL_SPEC, end_day, n), role='reader')
    return sorted(r['trade_date'] for r in rows)


def fetch_pool(start_day: dt.date, end_day: dt.date) -> dict[str, Any]:
    """窗口内**封住**的逐票逐场读数（母体只有封住的行：炸板那天不算板，与样例同一口径）。

    返回 `{'by_day': {场次: {代码: 当日行}}, 'all_days': [升序场次], 'pos': {(日,码): 分位}}`。
    一次查询取满整段，逐日的窗口统计全在内存里数 —— 五档模式 × 五场趋势若各查各的，
    一炉就要出五遍网似的多趟库，读到的还未必是同一段快照。
    """
    rows = engine.query(
        f"select trade_date, security_code, consec, order_amount, reason, float_cap_yuan,"
        f" board_kind, close, pct_change"
        f" from {POOL_TABLE}"
        " where spec_version = %s and sealed and trade_date between %s and %s"
        " order by trade_date, security_code", (POOL_SPEC, start_day, end_day), role='reader')
    by_day: dict = {}
    for r in rows:
        r['consec'] = int(r['consec']) if r['consec'] is not None else None
        r['order_amount'] = _f(r['order_amount'])
        r['float_cap_yuan'] = _f(r['float_cap_yuan'])
        r['close'] = _f(r['close'])
        r['pct_change'] = _f(r['pct_change'])
        by_day.setdefault(r['trade_date'], {})[r['security_code']] = r
    # 高位那一档读 `pos250`，只 join 当天真封住的票（整表拉一天 5,573 行 × 40 场＝13 万行，纯浪费）
    pos_rows = engine.query(
        f"select iv.trade_date, iv.security_code, iv.value from {IND_TABLE} iv"
        f" join {POOL_TABLE} p on p.trade_date = iv.trade_date and p.security_code = iv.security_code"
        " where iv.indicator_key = 'pos250' and p.spec_version = %s and p.sealed"
        "   and iv.trade_date between %s and %s", (POOL_SPEC, start_day, end_day), role='reader')
    names = {r['security_code']: r['name'] for r in engine.query(
        "select security_code, name from reference_data.security"
        " where security_code = any(%s::text[])",
        (sorted({r['security_code'] for r in rows}),), role='reader')} if rows else {}
    return {'by_day': by_day, 'all_days': sorted(by_day),
            'pos': {(r['trade_date'], r['security_code']): _f(r['value']) for r in pos_rows},
            'names': names}


# --------------------------------------------------------------------- 逐票统计与模式归属

def classify(ctx: dict, days: list[dt.date], idx: int, cfg: dict) -> dict:
    """第 `idx` 场（`days[idx]`）的逐票窗口读数与六档归属（**按场次记忆化**）。

    趋势那一节要把最近 5 场各排一遍名次，基准又要往前数 20 场 —— 不加记忆化的话
    一炉会重复算上百次同一份窗口统计（每场 ~900 只票 × 20 场），慢在自己在代码里造的。
    """
    cache = ctx.setdefault('_cache', {})
    if cache.get('_cfg') != id(cfg):
        cache.clear()
        cache['_cfg'] = id(cfg)
    if idx not in cache:
        cache[idx] = _classify(ctx, days, idx, cfg)
    return cache[idx]


def _classify(ctx: dict, days: list[dt.date], idx: int, cfg: dict) -> dict:
    """只读 `ctx` 里已经取回来的东西，不再打库。返回
    `{'day', 'n_sealed', 'per', 'sealed', 'tally', 'undecided'}`。
    """
    from duanxian import board_span        # 只借那把「几天几板」的纯判据，序列自己喂

    day = days[idx]
    win = cfg['sm_window_sessions']
    lo = max(0, idx - win + 1)
    window = days[lo:idx + 1]
    per: dict[str, dict] = {}
    codes: set[str] = set()
    for d in window:                      # 窗口内出现过涨停的全部票（证据1 的母体就是这个）
        codes |= set(ctx['by_day'].get(d, {}))
    for code in sorted(codes):
        series = [(d, code in ctx['by_day'].get(d, {})) for d in window]
        zt = sum(1 for _d, ok in series if ok)
        # 最长连板按**窗口内**数出来的那一段，不取表里 `consec` 列的最大值：一轮如果从窗口外起手，
        # 表里的连板会比窗口内数出来的次数还大，「近 20 场 3 次涨停仅 6 连板」就成了病句
        # （证据1 那句读的是「这段时间里走过几波、最长那波几根」，两个数必须同一把尺）。
        # 今日那一格的连板数照用表里的 `consec`（它是 ⑨⑩ 也在读的现成读数，不是这里数出来的）。
        run = mx = 0
        for _d, ok in series:
            run = run + 1 if ok else 0
            mx = max(mx, run)
        span = board_span.span_from_sealed(series)
        today = ctx['by_day'].get(day, {}).get(code)
        per[code] = {
            'code': code, 'name': ctx['names'].get(code) or code,
            'zt_days': zt, 'max_consec': mx,
            'bars': len(series), 'window': len(window),
            'round_days': span[0] if span else None, 'round_boards': span[1] if span else None,
            'label': (f'{span[0]}天{span[1]}板' if span else None),
            'sealed_today': today is not None,
            'consec': (today or {}).get('consec'),
            'kind': (today or {}).get('board_kind'),
            'reason': (today or {}).get('reason'),
            'order_amount': (today or {}).get('order_amount'),
            'float_cap_yuan': (today or {}).get('float_cap_yuan'),
            'pos250': ctx['pos'].get((day, code)),
        }
        s = per[code]
        s['modes'] = [k for k, ok in _mode_predicates(s, cfg) if ok]

    sealed_today = [s for s in per.values() if s['sealed_today']]
    tally: dict[str, dict] = {}
    for s in sealed_today:
        for k in s['modes']:
            t = tally.setdefault(k, {'mode_key': k, 'mode_label': MODE_LABELS[k],
                                     'n_stocks': 0, 'max_consec': None,
                                     'order_amount_yi': 0.0, 'order_n': 0, 'stocks': []})
            t['n_stocks'] += 1
            if s['consec'] is not None:
                t['max_consec'] = s['consec'] if t['max_consec'] is None \
                    else max(t['max_consec'], s['consec'])
            if s['order_amount'] is not None:
                t['order_n'] += 1
                t['order_amount_yi'] += s['order_amount'] / 1e8
            t['stocks'].append(s)
    n_sealed = len(sealed_today)
    for t in tally.values():
        t['share_pct'] = None if not n_sealed else round(t['n_stocks'] * 100.0 / n_sealed, 1)
        #: 封单额只有源站池子里有行的那批才有值：全都缺 ⇒ 交 None（说"没读到"），
        #: 部分缺 ⇒ 留着这个和但点名缺几只（这个和是**下限**，不说清就会被读成"今天这档没钱封"）。
        t['order_amount_yi'] = None if t['order_n'] == 0 else round(t['order_amount_yi'], 3)
        t['order_lack_n'] = t['n_stocks'] - t['order_n']
        t['stocks'] = sorted(t['stocks'], key=lambda x: (-(x['consec'] or 0), -x['zt_days']))[:12]
    uncovered = [s for s in sealed_today if not s['modes']]
    return {'day': day, 'window': len(window), 'window_start': window[0],
            'n_sealed': n_sealed, 'per': per, 'sealed': sealed_today, 'tally': tally,
            'undecided': {
                'no_mode_n': len(uncovered),
                'pos_missing_n': sum(1 for s in sealed_today if s['pos250'] is None),
                'consec_missing_n': sum(1 for s in sealed_today if s['consec'] is None)}}


def _mode_predicates(s: dict, cfg: dict) -> list[tuple[str, bool]]:
    """六档归属，**全部只看今天的行**（`consec` 为空即今天没封住 ⇒ 一档都不进）。

    判不了的那一格交 `None` 而不是 0：`pos250` 缺的票不进「高位反复」，但它照样可以进
    「断板反包」（那一档不需要分位）—— 把缺数当 0 分位会让次新股永远不算高位，而这话没人说。
    """
    if not s['sealed_today'] or s['consec'] is None:
        return [(k, False) for k, _ in MODES]
    c, zt, mx = s['consec'], s['zt_days'], s['max_consec']
    rd, rb, pos, kind = s['round_days'], s['round_boards'], s['pos250'], s['kind']
    rep_ratio = mx is not None and mx > 0 and zt >= cfg['sm_repeat_ratio'] * mx
    return [
        ('repeat_high', zt >= cfg['sm_repeat_min'] and rep_ratio
         and pos is not None and pos >= cfg['sm_high_pos']),
        ('reversal', c == 1 and zt >= cfg['sm_reversal_min']),
        ('second_wave', c >= 2 and rd is not None and rb is not None and rd > rb),
        ('wave_single', rd is not None and rb is not None and rd == rb >= cfg['sm_wave_min']),
        ('first_spread', c == 1 and rd == rb == 1 and zt <= 1),
        ('one_word', kind == '一字板' and c >= cfg['sm_yizi_min']),
    ]


# --------------------------------------------------------------------- 相对强度与最强模式

def rank_modes(ctx: dict, days: list[dt.date], idx: int, today: dict, cfg: dict) -> dict:
    """每档的今日占比 vs **近 `sm_baseline_sessions` 场的占比中位数**（基准不含当日），算强度。

    与 `classify` 同一份记忆化（`today` 那一格只是把调用方已经算好的读数传进来，不参与键）。
    """
    cache = ctx.setdefault('_cache', {})
    if cache.get('_cfg') != id(cfg):
        cache.clear()
        cache['_cfg'] = id(cfg)
    key = ('rank', idx)
    if key not in cache:
        cache[key] = _rank_modes(ctx, days, idx, today, cfg)
    return cache[key]


def _rank_modes(ctx: dict, days: list[dt.date], idx: int, today: dict, cfg: dict) -> dict:
    """为什么减基准而不是直接排只数：`first_spread` 每天都是只数最多的一档（11-04 实测 77/123），
    按只数排等于天天报「首板扩散」，那一句既不是今天特有的、也没法回答「今天像不像高位反复」。
    减掉自己的常态才读得出「今天这档比平时强多少个百分点」。
    """
    base_n = cfg['sm_baseline_sessions']
    history: dict[str, list[float]] = {k: [] for k, _ in MODES}
    sealed_hist: list[int] = []
    height_hist: list[int] = []
    j = idx - 1
    taken = 0
    while j >= 0 and taken < base_n:
        past = classify(ctx, days, j, cfg)
        if past['n_sealed']:
            sealed_hist.append(past['n_sealed'])
            height_hist.append(max((s['consec'] or 0) for s in past['sealed']))
            for k, _ in MODES:
                n = past['tally'].get(k, {}).get('n_stocks', 0)
                history[k].append(round(n * 100.0 / past['n_sealed'], 1))
        j -= 1
        taken += 1
    rows = []
    for k, label in MODES:
        #: 一只都没命中的那档：`order_amount_yi` 交 None 而不是 0.0 —— 0 亿读起来像「有钱封但很少」，
        #: 而这一档今天是「一只都没有」，两回事（同上面 share 的分母纪律）。
        t = today['tally'].get(k) or {'n_stocks': 0, 'max_consec': None,
                                      'order_amount_yi': None, 'order_lack_n': 0}
        base = _median(history[k])
        #: 今日占比就地从只数算（不读 tally 里那份）：一只都没有是那档**今天真的 0%**，
        #: 而今日全场封板 0 只是「分母没有」⇒ 后者才交 None。写反了就把"没有"念成"是 0"。
        share = None if not today['n_sealed'] else round(t['n_stocks'] * 100.0 / today['n_sealed'], 1)
        lift = None if share is None or base is None else round(share - base, 1)
        rows.append({'mode_key': k, 'mode_label': label,
                     'n_stocks': t['n_stocks'], 'share_pct': share,
                     'base_share_pct': base, 'base_sessions': len(history[k]),
                     'lift_pp': lift, 'max_consec': t['max_consec'],
                     'order_amount_yi': t.get('order_amount_yi'),
                     'order_lack_n': t.get('order_lack_n', 0),
                     'stocks': t.get('stocks', [])})
    pool = [r for r in rows if (r['n_stocks'] or 0) >= cfg['sm_min_n'] and r['lift_pp'] is not None]
    pool.sort(key=lambda r: (-r['lift_pp'], -(r['n_stocks'] or 0)))
    winners = [pool[0]] if pool else []
    if len(pool) > 1 and pool[1]['lift_pp'] >= pool[0]['lift_pp'] - cfg['sm_tie_pp']:
        winners.append(pool[1])
    winners.sort(key=lambda r: [k for k, _ in MODES].index(r['mode_key']))
    label = '／'.join(w['mode_label'] for w in winners) or None
    return {'modes': rows, 'winners': winners, 'winner_label': label,
            'no_mode': not winners,
            'threshold_mode': pool[0]['mode_key'] if pool else None,
            'baseline': {'sessions': len(history[[k for k, _ in MODES][0]]),
                         'sealed_median': _median([float(x) for x in sealed_hist]),
                         'height_median': _median([float(x) for x in height_hist])}}


# --------------------------------------------------------------------- 三条证据（用户给的样例格式）

def evidence(ctx: dict, days: list[dt.date], idx: int, today: dict, cfg: dict) -> dict:
    """三段式证据，全部交**结构**（名单＋数），措辞留给 `templates.json`。

    - 证据1：窗口涨停天数前 `sm_top_n` 名里有几只「不是单波连板」（次数 > 最长连板）——
      这一条的母体是**窗口内出现过涨停的全部票**，不是今天封住的那批（用户样例里
      皇台酒业当天没封住也在名单上）。
    - 证据2：今天封住、且「本轮跨度 > 本轮板数」（几天几板里断过板）的那批 ＋
      「今日再首板」那几只（连板数 1 而窗口内曾连板 ≥3）。
    - 证据3：真·一波流（本轮一天没断）还剩几只、分别是谁、什么题材、多大流通市值。
    """
    n = cfg['sm_evidence_n']
    top = sorted(today['per'].values(), key=lambda s: (-s['zt_days'], -(s['max_consec'] or 0)))
    top = [s for s in top if s['zt_days']][:cfg['sm_top_n']]
    not_single = [s for s in top if s['max_consec'] is not None and s['zt_days'] > s['max_consec']]
    broken = [s for s in today['sealed'] if s['round_days'] and s['round_boards']
              and s['round_days'] > s['round_boards'] >= 2]
    broken.sort(key=lambda s: (-s['round_boards'], -s['round_days']))
    reseal = [s for s in today['sealed'] if s['consec'] == 1
              and (s['max_consec'] or 0) >= cfg['sm_reversal_min']]
    reseal.sort(key=lambda s: -(s['max_consec'] or 0))
    single = [s for s in today['sealed'] if 'wave_single' in s['modes']]
    single.sort(key=lambda s: -(s['consec'] or 0))

    def brief(s: dict) -> dict:
        return {'security_code': s['code'], 'name': s['name'], 'zt_days': s['zt_days'],
                'max_consec': s['max_consec'], 'consec': s['consec'],
                'days': s['round_days'], 'boards': s['round_boards'], 'label': s['label'],
                'pos250': s['pos250'], 'reason': s['reason'],
                'float_cap_yi': None if s['float_cap_yuan'] is None
                else round(s['float_cap_yuan'] / 1e8, 1),
                'kind': s['kind'], 'modes': s['modes']}

    return {'top': [brief(s) for s in top], 'not_single_n': len(not_single),
            'not_single': [brief(s) for s in not_single[:n]],
            'broken': [brief(s) for s in broken[:n]], 'broken_n': len(broken),
            'reseal': [brief(s) for s in reseal[:n]], 'reseal_n': len(reseal),
            'single': [brief(s) for s in single[:n]], 'single_n': len(single),
            'is_case': len(single) <= cfg['sm_case_max_n']}


def trend(ctx: dict, days: list[dt.date], idx: int, cfg: dict) -> dict:
    """最近 `sm_trend_sessions` 场：逐场的最强模式、封板只数、最高板，以及逐档占比序列。

    逐场都是**现算**（同一条判据、同一个母体），不读 `strong_mode_daily` ——
    那样这一栏第一天就有五场可比，不用等表攒够；表是给事后问「这组模式第二天走得怎么样」用的。
    """
    k = cfg['sm_trend_sessions']
    picks = list(range(max(0, idx - k + 1), idx + 1))
    out = []
    for i in picks:
        day_stats = classify(ctx, days, i, cfg)
        if not day_stats['n_sealed']:
            out.append({'day': days[i].isoformat(), 'n_sealed': 0, 'winner_label': None,
                        'modes': {}, 'max_consec': None})
            continue
        ranked = rank_modes(ctx, days, i, day_stats, cfg)
        out.append({'day': days[i].isoformat(), 'n_sealed': day_stats['n_sealed'],
                    'max_consec': max((s['consec'] or 0) for s in day_stats['sealed']),
                    'winner_label': ranked['winner_label'],
                    'winners': [w['mode_key'] for w in ranked['winners']],
                    'no_mode': ranked['no_mode'],
                    'modes': {r['mode_key']: {'n': r['n_stocks'], 'share': r['share_pct'],
                                              'lift': r['lift_pp']} for r in ranked['modes']}})
    return {'sessions': out, 'span': len(out), 'notes': _trend_notes(out, cfg)}


def _trend_notes(rows: list[dict], cfg: dict) -> dict:
    """趋势那一句话的料：哪一档占比在扩、哪一档在收，梯队高度与封板只数的首尾。

    「扩／收」用的是**首末两场的占比差**（百分点），中间怎么跳的由那一行序列自己说；
    不足两场 ⇒ 交空（不说"没变化"，那是把"比不了"念成了"稳"）。
    """
    have = [r for r in rows if r['n_sealed']]
    if len(have) < 2:
        return {'expanding': [], 'shrinking': [], 'height': None, 'sealed': None}
    first, last = have[0], have[-1]

    def pair(key: str) -> Optional[dict]:
        a = (first['modes'].get(key) or {}).get('share')
        b = (last['modes'].get(key) or {}).get('share')
        if a is None or b is None:
            return None
        return {'mode_key': key, 'mode_label': MODE_LABELS[key],
                'from_share': a, 'to_share': b, 'delta_pp': round(b - a, 1)}

    diffs = [d for d in (pair(k) for k, _ in MODES) if d]
    step = cfg['sm_tie_pp']
    return {'expanding': sorted([d for d in diffs if d['delta_pp'] > step],
                                key=lambda x: -x['delta_pp']),
            'shrinking': sorted([d for d in diffs if d['delta_pp'] < -step],
                                 key=lambda x: x['delta_pp']),
            'height': {'from': first['max_consec'], 'to': last['max_consec']},
            'sealed': {'from': first['n_sealed'], 'to': last['n_sealed']},
            'first_day': first['day'], 'last_day': last['day']}


# --------------------------------------------------------------------- 总入口

def merged_params(cfg: Optional[dict] = None) -> tuple[dict, Optional[str]]:
    """`{代码默认 ⊗ 配置文件}` 那一份阈值，顺带交出「配置文件读不出来」那句声明。"""
    if cfg is not None:
        return {**PARAM_DEFAULTS, **{k: cfg[k] for k in PARAM_DEFAULTS if k in cfg}}, None
    loaded, why = load_config()
    return {**PARAM_DEFAULTS, **{k: loaded[k] for k in PARAM_DEFAULTS}}, why


def collect(day: Optional[dt.date] = None, cfg: Optional[dict] = None) -> dict:
    """今天（或指定那场）的最强模式全量读数。

    库里没有当天的涨停池 ⇒ `{'missing': 'no_pool_rows', ...}`，调用方据此**不发**。
    """
    cfg, why_unread = merged_params(cfg)
    day = day or dt.date.today()
    span = (int(cfg['sm_window_sessions']) + int(cfg['sm_baseline_sessions'])
            + int(cfg['sm_trend_sessions']) - 2)
    days = sessions_before(day, span)
    if not days or days[-1] != day:
        return {'day': day, 'missing': 'no_pool_rows',
                'latest': days[-1] if days else None, 'window': len(days)}
    ctx = fetch_pool(days[0], day)
    idx = len(days) - 1
    today = classify(ctx, days, idx, cfg)
    ranked = rank_modes(ctx, days, idx, today, cfg)
    return {'day': day, 'window': today['window'], 'window_start': today['window_start'],
            'params': params_block(cfg), 'params_unread': why_unread,
            'spec_version': SPEC_VERSION,
            'n_sealed': today['n_sealed'], 'undecided': today['undecided'],
            'stocks': today['sealed'], 'per': today['per'],
            'modes': ranked['modes'], 'winners': ranked['winners'],
            'winner_label': ranked['winner_label'], 'no_mode': ranked['no_mode'],
            'baseline': ranked['baseline'], 'evidence': evidence(ctx, days, idx, today, cfg),
            'trend': trend(ctx, days, idx, cfg), 'missing': None}


# --------------------------------------------------------------------- 落库（逐场逐档一行）

_UPSERT = f"""
insert into {TABLE}
    (trade_date, spec_version, mode_key, mode_label, n_sealed, n_stocks, share_pct,
     base_share_pct, base_sessions, lift_pp, max_consec, order_amount_yi, is_winner,
     stocks, params, missing, calculated_at)
values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb, %s::jsonb, now())
on conflict (trade_date, spec_version, mode_key) do update set
    mode_label = excluded.mode_label, n_sealed = excluded.n_sealed,
    n_stocks = excluded.n_stocks, share_pct = excluded.share_pct,
    base_share_pct = excluded.base_share_pct, base_sessions = excluded.base_sessions,
    lift_pp = excluded.lift_pp, max_consec = excluded.max_consec,
    order_amount_yi = excluded.order_amount_yi, is_winner = excluded.is_winner,
    stocks = excluded.stocks, params = excluded.params, missing = excluded.missing,
    calculated_at = now()
"""


def _slim(rows: Iterable[dict]) -> list[dict]:
    """落库存的那份名单：只留对账要用的那几格，`per` 整张表太大且每场都能重算出来。"""
    out = []
    for s in rows:
        out.append({'code': s['code'], 'name': s['name'], 'consec': s['consec'],
                    'zt_days': s['zt_days'], 'max_consec': s['max_consec'],
                    'days': s['round_days'], 'boards': s['round_boards'],
                    'label': s['label'], 'pos250': s['pos250'], 'modes': s['modes']})
    return out


def write(result: dict) -> int:
    """把这一场的逐档读数落进 `analytics.strong_mode_daily`（同日同档覆盖，重跑不重复）。"""
    if result.get('missing'):
        return 0
    winners = {w['mode_key'] for w in result['winners']}
    payload = []
    for r in result['modes']:
        payload.append((result['day'], SPEC_VERSION, r['mode_key'], r['mode_label'],
                        result['n_sealed'], r['n_stocks'], r['share_pct'], r['base_share_pct'],
                        r['base_sessions'], r['lift_pp'], r['max_consec'], r['order_amount_yi'],
                        r['mode_key'] in winners,
                        json.dumps(_slim(r['stocks']), ensure_ascii=False),
                        json.dumps(result['params'], ensure_ascii=False),
                        json.dumps({**result['undecided'],
                                    'order_lack_n': r.get('order_lack_n', 0)},
                                   ensure_ascii=False)))
    with engine.cursor(role='etl') as cur:
        cur.execute(f"delete from {TABLE} where trade_date = %s and spec_version = %s",
                    (result['day'], SPEC_VERSION))
        cur.executemany(_UPSERT, payload)
    return len(payload)


def backfill(end_day: Optional[dt.date] = None, sessions: int = 30,
             cfg: Optional[dict] = None) -> dict:
    """把最近 `sessions` 场的逐档读数补齐落库（第一炉之前手工跑一次，之后每天自己跟）。"""
    cfg, _why = merged_params(cfg)
    end_day = end_day or dt.date.today()
    need = int(cfg['sm_window_sessions']) + int(cfg['sm_baseline_sessions']) - 1
    picked = sessions_before(end_day, need + sessions)[-sessions:]
    all_days = sessions_before(picked[0], need) + picked if picked else []
    ctx = fetch_pool(all_days[0], all_days[-1])
    written: list[str] = []
    for offset in range(len(picked)):
        i = len(all_days) - len(picked) + offset
        today = classify(ctx, all_days, i, cfg)
        ranked = rank_modes(ctx, all_days, i, today, cfg)
        result = {'day': all_days[i], 'n_sealed': today['n_sealed'],
                  'undecided': today['undecided'], 'params': params_block(cfg),
                  'winners': ranked['winners'], 'modes': ranked['modes'], 'missing': None}
        if write(result):
            written.append(all_days[i].isoformat())
    return {'want': len(picked), 'written_days': len(written),
            'first': written[0] if written else None, 'last': written[-1] if written else None}


def latest_day() -> Optional[dt.date]:
    """涨停池里最新那场（缺数时屏上要说「库里只到 X」，不能不说）。"""
    row = engine.query_one(f"select max(trade_date) as d from {POOL_TABLE} where spec_version = %s",
                           (POOL_SPEC,), role='reader')
    return (row or {}).get('d')
