# -*- coding: utf-8 -*-
"""近期倍量表股票走势分析：按加权分名次每 10 名一档，看各档在名次日之后几场的涨跌趋势。

用户 2026-11-08 推翻上一版（`quant.energy_track` 那张逐票热力表）后定的形状，原话：
「做一个近期倍量表股票的走势分析。针对倍量方法计算出来的票，分析前面一段时间，加权把名次的
后几天涨跌趋势变化；1-10名、11-20，每10个名次汇总分析，加权计算每10名后几天涨跌的趋势变化。」

上一版为什么被推翻（两条实测，不是审美问题）：
① 它把 `security_code = any(...)` 放进 `rank() over (partition by trade_date …)` 的**窗口里面**，
   名次只在「今天这 122 只」内部排 ⇒ 119/122 只的「全史首次进榜日」是假的；把筛参挪到窗口之后
   重算，起算日按月散开（12/7/6/5/7/3/4/7/2/11/29/29），与旧算法只有 3 只一致。
② 就算起算日修对，那张表仍是「每只票各铺各的日历」——同一列不是同一天、没有基准对照、
   母体是**今天**还在榜上的票（幸存者偏差）。读者拿它得不到「名次靠前是否更好」这个答案。

本模块换成一件事：**事件研究**。
  · 事件＝(票, 名次日)。回看窗口内**每一场**各取当日加权分前 M 名（边界同分并列一起进来）。
  · 档位＝名次每 10 名一档（1-10 / 11-20 / …）。同分同名 ⇒ 并列那批落同一档。
  · 读数＝该档全部事件在名次日之后第 k 场的**累计涨跌幅**（后复权，尺子只住 `day_returns.py`），
    按**名次日那天的加权分**加权：`Σ(涨幅×分) / Σ(分)`；等权均值并排交，两把尺不许混着念。
  · 基准＝同窗每一场**全市场有行情的票**等权平均（含榜内那批）。超额＝档位 − 基准（个百分点）。

四条不会从代码里自己长出来的口径，写死在这里：

1. **样本是「票×名次日」，不是「几只票」**。同一只票连着几天在榜会被重复计入，且它的前后窗口
   互相重叠 ⇒ `n` 不是独立样本数，胜率与均值都按这个口径读。每档另交 `codes`（独立票数）。
   实测近 60 场前 10 名：603 个事件只有 104 只不同的票。
2. **三种「没有数」分开交**：`pending`＝还没走到第 k 场（窗口贴着今天，右边那几场尚未发生）；
   `missing`＝走到了但那天它没有行（停牌/未上市）或名次日缺基期；`ok` 才进分子分母。
   把 pending 当 0% 会把「还没发生」念成「平盘」，把 missing 当 0% 会把停牌念成平盘。
3. **加权分母只算「涨幅算得出且分读得到」的那些事件**（`n_w` / `weight_sum` 一起交）。
   分母为 0 ⇒ 加权值交 None，**不悄悄退回等权**（那会让两列看起来一样却说是加权）。
4. **窗口右端贴着基准日 ⇒ 最长那几档必然缺右端**。`horizons` 里越大的 k，`pending` 越多；
   界面按格交出 `n_ok/n_all`，不许只画一条看着完整的曲线。

取数与缓存：读侧现算（与同族 `momentum_follow` 一致），不落表、不接每日链 —— 窗口/名次上限
都是界面可调的探索参数，落库要按参数组做主键，而这一块的用途是换着参数对照看。
进程内 TTL 缓存一小时，键里带「基准日＋窗口＋名次上限＋档表指纹＋本模块口径版本」：
改任一档分值 ⇒ `energy_fp` 换号 ⇒ 旧键不可能把上一批的数顶给这一批。

实测（2026-11-08，近 120 场 × 前 120 名，基准日 2026-11-06）：
价格窗 729,812 行 4.18s ＋ 事件 14,491 行 2.44s ＋ 聚合 <1s；同一批读数用「整段压进 SQL」
的写法交叉验过一遍（32.22s，逐位一致）⇒ 本模块选快的前者，正确性由那份探针对账。
"""
from __future__ import annotations

import time

from . import pg, ttlcache
from .day_returns import _num, _pct
from .ranking import RULE_NOTE, RULE_NOTE_SHORT, rank_sql

#: 本模块的口径版本。改判据（档位宽度、horizons、加权式、基准母体）就 +1，
#: 缓存键带着它 ⇒ 旧答案不会顶着新口径的名字继续被端出来。
SPEC_VERSION = 'etrend-1'

#: 往后看几场（累计口径：第 k 场收盘 ÷ 名次日收盘 − 1）。
HORIZONS: tuple[int, ...] = (1, 2, 3, 5, 10)

#: 每档装几个名次（用户点名「每10个名次汇总分析」）。
BUCKET_SIZE = 10

#: 回看窗口默认几场、最多几场。250 是本机倍量表当前覆盖（225 场）之外留一点余量；
#: 再大价格窗要扫两年分区，一发十几秒，而这一块的用途是「近期」。
DEFAULT_WINDOW = 120
MAX_WINDOW = 250
MIN_WINDOW = 10

#: 名次上限默认值与上下夹（与倍量榜那一族同一个出厂值，真源在 volume_energy）。
DEFAULT_TOP_N = 120
MAX_TOP_N = 500
MIN_TOP_N = 10

#: 缓存 TTL（秒）。与同族读侧聚合一致：一天最多变一次的库，一小时内同一组参数是同一个答案。
TTL = 3600

#: 三种「这一格为什么没有数」。界面按它说话，不许合并成一个「—」。
STATE_OK = 'ok'
STATE_PENDING = 'pending'
STATE_MISSING = 'missing'

WEIGHT_RULE = (
    '加权涨跌幅 = Σ(这一笔的累计涨跌幅 × 名次日那天的加权分) ÷ Σ(名次日那天的加权分)，'
    '分子分母都只算「涨跌幅算得出、加权分也读得到」的那些事件（分母条数见 n_w）。'
    '所以它不是「这一档的平均涨跌幅」，而是「按那一天倍量加权分配权后的平均涨跌幅」——'
    '分高的那几笔说话更响。等权均值并排给出，两把尺各念各的，不许混着比。')

BASELINE_RULE = (
    '基准＝同一个回看窗口里，每一场全市场有日线行情的票的等权平均涨跌幅（含榜内那批票）。'
    '分母只算「名次日那天它真有收盘」的票·日：当天压根没有行的那些格子不是样本，'
    '它们的个数另交 no_base，不混进「读不到」那一类。'
    '它回答的是「这几天市场本身在涨还是在跌」：各档都负而基准也负，说明的是市场弱，'
    '不是这套名次判据失灵。超额 = 本档 − 基准（个百分点），任一边算不出就是 —，不是 0。')

SAMPLE_RULE = (
    '一笔样本 = 一只票 × 一个名次日。同一只票连着几天在榜会被重复计入，而且它的前后窗口互相重叠，'
    '所以 n 不是独立样本数，胜率与均值都要按这个口径读（每档另给「独立票数」）。'
    '母体是「回看窗口内每一场各自的前 M 名」，不是「今天还在榜上的那批」——'
    '这样已经掉出榜的票也在样本里，不是只统计活到今天还留在榜上的那批（幸存者偏差）。')

PCT_RULE = (
    '涨跌幅一律后复权：收盘价 × adjustment_factor（跨除权日连续），不读日线里那列未复权的 '
    'pct_change。基期＝名次日那一场自己的收盘，第 k 场＝名次日之后第 k 个市场场次的收盘'
    '（按市场场次算，不是这只票自己的 K 线根数）。名次日或第 k 场它没有行 ⇒ 那一笔算不出，'
    '不进分子分母，也不是 0%。每一笔先取到两位小数（与全站涨跌幅同一把尺）再进聚合，'
    '所以「上涨占比」把显示为 0.00% 的那些笔算作平盘；这一步舍入对均值的影响在万分之一'
    '个百分点量级（实测 1,200 笔里有 1 笔的涨跌归类与不舍入时不同）。')

#: 说明抽屉的分节（中文只住这一份，前端 `GuideSections` 原样渲染，不另写一句）。
GUIDE: tuple[dict, ...] = (
    {'heading': '这一块回答什么',
     'body': '把倍量加权分的名次切成每 10 名一档，看每一档在「排到那个名次的那天」之后 '
             '1/2/3/5/10 场的累计涨跌怎么走。横着读是一档的趋势变化（越往右越远），'
             '竖着读是同一时点不同名次档的差别（靠前的档是不是更好）。'},
    {'heading': '一笔样本是怎么来的',
     'body': '回看窗口里每一场各自排一次名次（当日全场、按加权分从高到低），取前 M 名；'
             '每一只入选的票配上它自己那一天，就是一笔样本。窗口有 120 场、每档 10 个名次，'
             '所以一档大约有 1,200 笔样本，而不是 10 只票。' + SAMPLE_RULE},
    {'heading': '名次与档位',
     'body': RULE_NOTE + ' 档位按名次每 10 名切一刀：第 1~10 名一档、第 11~20 名一档，以此类推。'
             '边界那一名并列几只就一起进同一档，所以一档的笔数不保证正好等于「场数 × 10」；'
             '名次上限 M 也是上限而不是恰好 M 只。'},
    {'heading': '加权是怎么加的', 'body': WEIGHT_RULE},
    {'heading': '基准与超额', 'body': BASELINE_RULE},
    {'heading': '涨跌幅这把尺', 'body': PCT_RULE},
    {'heading': '「还没走到」与「读不到」是两件事',
     'body': '窗口右端贴着基准日，所以越大的 k 越多笔样本还没走到那一天（pending）——'
             '那不是涨跌为 0，是那几天还没发生。走到了但那天它没有行（停牌/未上市），'
             '或者名次日缺基期，是另一类（missing）。两类都不进分子分母，'
             '每一格的分母 n_ok 与总数 n_all 一起交，屏上念得出来。'},
    {'heading': '每场增量那一档怎么读',
     'body': '「每场增量」= (本列累计 − 上一列累计) ÷ 两列相差的场数，单位是百分点/场。'
             '第 1 场那一列的上一列是 0，所以它就是自己。3→5 跨两场，除过 2 才是「平均每场」，'
             '不除就会把两场的合计念成一场的本事；上一列算不出时往前找最近一个算得出的列，'
             '除的仍是真实间隔（2→5 除 3）。累计曲线往下走不代表每场都在跌，'
             '要看增量那一档才知道是加速还是减速。'},
    {'heading': '这不证明什么',
     'body': '这是历史场次的相关性统计，不是策略回测：没有扣手续费与滑点、没有模拟能不能买到'
             '（名次日收盘这个价未必成交得了）、没有做样本外检验，也没有控制行业与市值。'
             '窗口换一段、名次上限换一档，读数就会变；某一档在这段窗口里更好，'
             '不等于下一段更好，更不构成买卖建议。'},
)

#: 列头描述符（中文列名与口径只住后端一份，前端不许抄）。
COLUMNS: tuple[dict, ...] = (
    {'key': 'label', 'label': '名次档', 'note': '当日全场加权分名次的区间，每 10 名一档。'},
    {'key': 'events', 'label': '样本笔数', 'note': '票 × 名次日。同一只票多天在榜会重复计入。'},
    {'key': 'codes', 'label': '独立票数', 'note': '这一档里不同的票有几只（笔数 ÷ 票数 ≈ 平均每只在榜几天）。'},
    {'key': 'weighted', 'label': '加权涨跌幅', 'note': WEIGHT_RULE},
    {'key': 'equal', 'label': '等权涨跌幅', 'note': '同一批样本不加权的算术平均，与加权并排对照。'},
    {'key': 'win_pct', 'label': '上涨占比', 'note': '等权口径：算得出的样本里累计涨幅 > 0 的占比（%）。'
                                                  '平盘（=0）不算上涨，也不算下跌，分母含它。'},
    {'key': 'excess', 'label': '超额', 'note': '本档涨跌幅 − 同窗全市场等权基准（个百分点）。'
                                              '加权列与等权列减的是同一个基准：基准本身只有等权那一份。'},
    {'key': 'step', 'label': '每场增量', 'note': '(本列累计 − 上一个算得出的列累计) ÷ 两列相差的场数，'
                                              '百分点/场。中间那一列算不出时，除的是真实间隔（2→5 除 3）。'},
    {'key': 'n_ok', 'label': '分母', 'note': '这一格真正进了分子分母的笔数；与样本笔数的差就是还没走到与读不到那两类。'},
)


# ─────────────────────────────────────────────── 纯判据（不碰库，单测直接喂）

def bucket_of(rank, size: int = BUCKET_SIZE):
    """名次 → 档位下标（0 起）。名次取不到数 ⇒ None（不占档、不当最后一名）。"""
    r = _num(rank)
    if r is None or r < 1 or size < 1:
        return None
    return int((int(r) - 1) // size)


def bucket_label(index: int, size: int = BUCKET_SIZE) -> str:
    """档位下标 → 屏上那几个字（「1-10」）。中文与区间只在这一处拼。"""
    lo = index * size + 1
    return f'{lo}-{lo + size - 1}'


def classify(base, later, target_sr: int, last_sr: int) -> str:
    """这一笔在某个 k 上属于哪种状态。基期缺行优先判 missing：没有基期就永远算不出，
    与「那几天还没发生」是两件事，混在一起会让读者以为等几天就有数。"""
    if _num(base) is None or not _num(base):
        return STATE_MISSING
    if target_sr > last_sr:
        return STATE_PENDING
    if _num(later) is None:
        return STATE_MISSING
    return STATE_OK


def cell_stats(samples: list[dict]) -> dict:
    """一格（一档 × 一个 k）的读数。`samples` 每项 `{ret, weight, state}`。

    缺数一律 None，不补 0；加权分母为 0 时加权值交 None，**不退回等权**。
    """
    n_all = len(samples)
    n_pending = sum(1 for s in samples if s.get('state') == STATE_PENDING)
    n_missing = sum(1 for s in samples if s.get('state') == STATE_MISSING)
    ok = [s for s in samples if s.get('state') == STATE_OK and _num(s.get('ret')) is not None]
    n_ok = len(ok)
    rets = [float(_num(s['ret'])) for s in ok]
    equal = round(sum(rets) / n_ok, 4) if n_ok else None
    up = sum(1 for x in rets if x > 0)
    down = sum(1 for x in rets if x < 0)
    flat = n_ok - up - down
    pairs = [(float(_num(s['ret'])), float(_num(s['weight'])))
             for s in ok if _num(s.get('weight')) is not None and float(_num(s['weight'])) > 0]
    wsum = sum(w for _, w in pairs)
    weighted = round(sum(r * w for r, w in pairs) / wsum, 4) if wsum > 0 else None
    return {'n_all': n_all, 'n_ok': n_ok, 'n_pending': n_pending, 'n_missing': n_missing,
            'n_w': len(pairs), 'weight_sum': round(wsum, 4) if pairs else None,
            'weighted': weighted, 'equal': equal,
            'win_pct': round(up * 100.0 / n_ok, 2) if n_ok else None,
            'up': up, 'down': down, 'flat': flat}


def attach_derived(cells: list[dict], baseline: list[dict] | None,
                   horizons: tuple[int, ...] = HORIZONS) -> None:
    """就地给每格补「超额」与「每场增量」（加权与等权各算一份）。

    超额两把尺都减**同一个基准**：基准是全市场等权，它自己没有「加权」那一份
    （拿 `baseline.weighted` 去减会整列交 None —— 2026-11-08 对账时实测到了）。
    增量除的是**两个有效列相差的场数**（3→5 除 2；中间那列算不出时 2→5 除 3），
    不是恒除 1，也不是「上一个 k」与「上上一个有效值」错配。
    """
    prev = {'weighted': (0, 0.0), 'equal': (0, 0.0)}   # (场序, 累计值)；起点＝名次日当天 0 场 0%
    for i, c in enumerate(cells):
        k = horizons[i] if i < len(horizons) else c.get('k')
        b = (baseline[i] if baseline and i < len(baseline) else None) or {}
        bv = b.get('equal')
        for key in ('weighted', 'equal'):
            v = c.get(key)
            c['excess_' + key] = None if v is None or bv is None else round(v - bv, 4)
            pk, pv = prev[key]
            c['step_' + key] = (None if v is None or k is None or k <= pk
                                else round((v - pv) / (k - pk), 4))
            if v is not None and k is not None:
                prev[key] = (k, v)


def agg_buckets(events: list[dict], horizons: tuple[int, ...] = HORIZONS,
                size: int = BUCKET_SIZE) -> list[dict]:
    """事件 → 档位表。`events` 每项 `{code, rank, weight, rets: {k: {ret, state}}}`。

    名次取不到数的事件**不进任何档**（不占号、不当最后一档），但会数进 `dropped` 由调用方出声。
    """
    groups: dict[int, list[dict]] = {}
    for e in events:
        b = bucket_of(e.get('rank'), size)
        if b is None:
            continue
        groups.setdefault(b, []).append(e)
    out = []
    for b in sorted(groups):
        rows = groups[b]
        cells = []
        for k in horizons:
            # 权重跟着每一笔一起进 cell_stats：只传 rets 那一格的话，加权分母恒为 0、
            # 加权列会整列交 None（2026-11-08 对账时实测到 60 格全 None，就是漏了这一项）。
            samples = [{'weight': e.get('weight'),
                        **((e.get('rets') or {}).get(k) or {'ret': None, 'state': STATE_MISSING})}
                       for e in rows]
            cells.append({'k': k, **cell_stats(samples)})
        out.append({'index': b, 'label': bucket_label(b, size),
                    'rank_lo': b * size + 1, 'rank_hi': b * size + size,
                    'events': len(rows),
                    'codes': len({str(e.get('code')) for e in rows if e.get('code')}),
                    'weight_sum_day': round(sum(float(_num(e.get('weight')) or 0) for e in rows), 4),
                    'cells': cells})
    return out


# ─────────────────────────────────────────────── 取数（只读）

_CAL_SQL = """select trade_date::text d, row_number() over (order by trade_date)::int sr
                from (select distinct trade_date from market_data.stock_daily
                       where trade_date <= %s::date
                       order by trade_date desc limit %s) z
               order by d"""

_EV_SQL = f"""with r as (select security_code code, trade_date::text d, dbl_energy::float weight,
                          {rank_sql('dbl_energy desc nulls last', 'trade_date')} rk
                     from analytics.volume_double
                    where energy_fp = %s and dbl_energy is not null
                      and trade_date between %s::date and %s::date)
        select code, d, weight, rk from r where rk <= %s"""

_PX_SQL = """select sd.security_code code, sd.trade_date::text d,
                    (sd.close * coalesce(sd.adjustment_factor, 1))::float hfq
               from market_data.stock_daily sd
              where sd.close is not null and sd.trade_date between %s::date and %s::date"""


def _sessions(end: str, need: int) -> list[dict]:
    df = pg.fetch_df(_CAL_SQL, (end, int(need)))
    return [] if df is None else df.to_dict('records')


def _events(fp: str, lo: str, hi: str, top_n: int) -> list[dict] | None:
    df = pg.fetch_df(_EV_SQL, (fp, lo, hi, int(top_n)))
    return None if df is None else df.to_dict('records')


def _prices(lo: str, hi: str):
    df = pg.fetch_df(_PX_SQL, (lo, hi))
    return None if df is None else df.to_dict('records')


def _matrix(rows: list[dict], sr_of: dict[str, int], n_sessions: int):
    """(票 × 场次) 的后复权价矩阵，缺行＝NaN。用 numpy 是为了让「全市场基准」那一遍
    不必在 Python 里逐票逐日循环（实测 644k 笔 × 3 个 k 要 3.9 秒，矩阵化后 <0.1 秒）。"""
    import numpy as np

    codes = sorted({str(r['code']) for r in rows})
    idx = {c: i for i, c in enumerate(codes)}
    m = np.full((len(codes), n_sessions + 1), np.nan, dtype='float64')
    for r in rows:
        sr = sr_of.get(str(r['d']))
        v = _num(r.get('hfq'))
        if sr is not None and v is not None:
            m[idx[str(r['code'])], sr] = v
    return codes, idx, m


def _build(events: list[dict], prices: list[dict], sr_of: dict[str, int],
           last_sr: int, window_srs: list[int], horizons: tuple[int, ...],
           size: int) -> tuple[list[dict], dict]:
    """事件矩阵化 → 档位表 ＋ 全市场基准。纯计算（入参都是已取回的行），单测可直接喂。"""
    import numpy as np

    codes, idx, m = _matrix(prices, sr_of, last_sr)
    ev_rows = []
    dropped = 0
    for e in events:
        code = str(e.get('code') or '')
        sr = sr_of.get(str(e.get('d')))
        ci = idx.get(code)
        if sr is None or ci is None:
            dropped += 1
            continue
        base = m[ci, sr]
        rets = {}
        for k in horizons:
            tgt = sr + k
            later = m[ci, tgt] if tgt <= last_sr else None
            state = classify(base, later, tgt, last_sr)
            rets[k] = {'ret': _pct(later, base) if state == STATE_OK else None, 'state': state}
        ev_rows.append({'code': code, 'rank': _num(e.get('rk')), 'weight': _num(e.get('weight')),
                        'rets': rets})
    buckets = agg_buckets(ev_rows, horizons, size)

    # 基准：同窗每一场全市场有行情的票等权。与档位同一把尺（同一个价格矩阵、同一个基期定义），
    # 而且分母也同一个口径：**名次日那天它真有收盘**才算一笔（档位那边没有基期的事件整笔丢掉，
    # 这里若把「票 × 场」全笛卡尔积当分母，n_missing 会被那些当天压根没上市的格子灌成虚高的数）。
    base_cells = []
    wsr = np.array(window_srs, dtype='int64')
    b = m[:, wsr]
    has_base = np.isfinite(b) & (b != 0)
    stock_days = int(b.size)
    n_base_all = int(has_base.sum())
    for k in horizons:
        tgt = wsr + k
        in_range = tgt <= last_sr
        later = np.full(b.shape, np.nan)
        ok_cols = np.nonzero(in_range)[0]
        if ok_cols.size:
            later[:, ok_cols] = m[:, tgt[ok_cols]]
        with np.errstate(invalid='ignore', divide='ignore'):
            ret = np.where(has_base & np.isfinite(later), (later / b - 1) * 100.0, np.nan)
        n_pending = int((has_base & ~in_range).sum())
        finite = np.isfinite(ret)
        n_ok = int(finite.sum())
        vals = ret[finite]
        up = int((vals > 0).sum())
        down = int((vals < 0).sum())
        base_cells.append({'k': k, 'n_all': n_base_all, 'n_ok': n_ok,
                           'n_pending': n_pending, 'n_missing': n_base_all - n_ok - n_pending,
                           'n_w': 0, 'weight_sum': None,
                           'weighted': None, 'equal': round(float(vals.mean()), 4) if n_ok else None,
                           'win_pct': round(up * 100.0 / n_ok, 2) if n_ok else None,
                           'up': up, 'down': down, 'flat': n_ok - up - down})
    baseline = {'label': '同窗全市场等权', 'events': n_base_all, 'codes': len(codes),
                'stock_days': stock_days, 'no_base': stock_days - n_base_all,
                'cells': base_cells}
    for b in buckets:
        attach_derived(b['cells'], base_cells, horizons)
    attach_derived(base_cells, None, horizons)
    return buckets, {'baseline': baseline, 'dropped_events': dropped}


def _compute(date: str, window: int, top_n: int, fp: str) -> dict:
    """真取数那一遍。库不通 / 那一场没数 ⇒ 交 available=False 与 reason，不交空 rows 装成「没有」。"""
    from .quant import _coverage, _latest

    table = 'analytics.volume_double'
    d = date or _latest(table) or ''
    if not d:
        return {'available': False, 'reason': f'{table} 里一场都没有：倍量加权分还没算过。'}
    cal = _sessions(d, MAX_WINDOW + max(HORIZONS) + 2)
    if not cal:
        return {'available': False, 'reason': '本机日线日历读不到（库不通或 market_data.stock_daily 空）。'}
    sr_of = {str(r['d']): int(r['sr']) for r in cal}
    dates = [str(r['d']) for r in cal]
    if d not in sr_of:
        return {'available': False,
                'reason': f'基准日 {d} 在倍量表里有，本机日线日历里却没有这一场：两边覆盖对不上。'}
    # 日历是「≤ d 的最后 N 场」升序 ⇒ 窗口就是它末尾 window 场（不够就有几场用几场，并如实交 used）
    win = dates[-window:] if len(dates) >= window else dates
    used = len(win)
    ev_lo = win[0]
    last_sr = sr_of[d]
    # 基期就是名次日自己，右边又没有比基准日更晚的场次（日历取到 d 为止）⇒ 价格窗＝事件窗，
    # 「还没走到」由 last_sr 判，不需要为它多取右边的价格。
    px_lo, px_hi = ev_lo, d
    ev = _events(fp, ev_lo, d, top_n)
    if ev is None:
        return {'available': False, 'reason': '倍量加权分那一段取数失败（库不通或查询超时）。'}
    if not ev:
        return {'available': False,
                'reason': f'{ev_lo}~{d} 这 {used} 场里没有可排名的加权分'
                          f'（energy_fp={fp}）：这一批还没按当前档表重算，或这一段压根没数。'}
    px = _prices(px_lo, px_hi)
    if px is None:
        return {'available': False, 'reason': '日线价格那一段取数失败（库不通或查询超时）。'}
    window_srs = [sr_of[x] for x in win]
    buckets, extra = _build(ev, px, sr_of, last_sr, window_srs, HORIZONS, BUCKET_SIZE)
    if not buckets:
        return {'available': False, 'reason': '取回了事件却没有一档算得出名次（名次列全空）。'}
    cover = (_coverage(('volume_double',)) or {}).get('volume_double') or {}
    return {'available': True, 'date': d, 'top_n': top_n, 'window': window,
            'window_used': used, 'window_from': ev_lo, 'window_to': d,
            'fingerprint': fp, 'buckets': buckets, **extra,
            'coverage': {'lo': str(cover.get('from')) if cover.get('from') else None,
                         'hi': str(cover.get('to')) if cover.get('to') else None,
                         'sessions': cover.get('sessions')}}


def board(date: str = '', window: int | None = None, top_n: int | None = None,
          refresh: bool = False) -> dict:
    """「近期倍量表股票走势分析」的读侧出口（端点 `/api/quant/energy-trend`）。

    参数都夹在本模块的上下界里；`refresh=True` 只清这一组参数的键重算（刚回算完倍量分时用）。
    缓存自己声明自己：`generated_at` / `cached` / `cache_age_seconds` 与同族读侧块同一套字段，
    界面据此念「这份数实算于 N 秒前」，不留一个恒为 None 的年龄占位。
    """
    import datetime as dt

    from app.compute import volume_energy as venc

    w = int(window or DEFAULT_WINDOW)
    w = max(MIN_WINDOW, min(w, MAX_WINDOW))
    n = int(top_n or venc.DEFAULT_TOP_N)
    n = max(MIN_TOP_N, min(n, MAX_TOP_N))
    fp = venc.fingerprint()
    key = f'energy_trend:{SPEC_VERSION}:{date or "latest"}:{w}:{n}:{fp}'
    if refresh:
        ttlcache.clear(key)
    t0 = time.monotonic()
    before = ttlcache.status(key)
    data = ttlcache.cached(key, lambda: _compute(date, w, n, fp),
                         valid=lambda r: bool(r.get('available')), ttl=TTL)
    st = ttlcache.status(key)
    saved = st.get('at')
    # 命中时 ttlcache 不改 status ⇒ 前后两次 at 相同就是命中（重算最快也要秒级，stamp 不会撞同一秒）
    hit = bool(before.get('ok')) and before.get('at') == saved
    age = None
    if saved:
        age = max(0, int((dt.datetime.now(dt.timezone.utc)
                          - dt.datetime.fromisoformat(saved)).total_seconds()))
    out = dict(data)
    out.update({
        'spec_version': SPEC_VERSION, 'bucket_size': BUCKET_SIZE, 'horizons': list(HORIZONS),
        'top_n_default': venc.DEFAULT_TOP_N, 'window_default': DEFAULT_WINDOW,
        'window_max': MAX_WINDOW, 'window_min': MIN_WINDOW,
        'top_n_max': MAX_TOP_N, 'top_n_min': MIN_TOP_N,
        'rank': {'rule': RULE_NOTE, 'rule_short': RULE_NOTE_SHORT, 'by': 'dbl_energy'},
        'energy': {'scale': venc.scale(), 'fingerprint': fp},
        'weight_rule': WEIGHT_RULE, 'baseline_rule': BASELINE_RULE,
        'sample_rule': SAMPLE_RULE, 'pct_rule': PCT_RULE,
        'guide': list(GUIDE), 'columns': list(COLUMNS),
        'caveats': _caveats(out, w, n),
        'generated_at': saved, 'cached': hit, 'cache_age_seconds': age,
        'cache_key': key, 'cache_ttl': TTL,
        'elapsed_ms': int((time.monotonic() - t0) * 1000),
        'source': 'pg'})
    return out


def _caveats(d: dict, w: int, n: int) -> list[dict]:
    """声明一律带中文 `label`（屏上徽标念它，机器码只留在悬停里）。

    缺数/未走完/截断这几类按 `lib/caveatKind.ts` 的白名单制**默认留在屏上**，
    所以这里的 code 刻意不进那份白名单：它们是当场计数，不是固定散文。
    """
    out: list[dict] = []
    if not d.get('available'):
        return [{'code': 'energy_trend_unavailable', 'label': '这一块没读到',
                 'text': str(d.get('reason') or '后端没交原因')}]
    buckets = d.get('buckets') or []
    used = d.get('window_used')
    if isinstance(used, int) and used < w:
        out.append({'code': 'energy_trend_short_window', 'label': '窗口不够长',
                    'text': f'要了近 {w} 场，库里这一段只有 {used} 场（{d.get("window_from")}~{d.get("window_to")}）：'
                            '笔数与独立票数都按实际这几场算，不是按你要的那个数。'})
    pending = sum(c.get('n_pending') or 0 for b in buckets for c in (b.get('cells') or []))
    if pending:
        kmax = max(HORIZONS)
        out.append({'code': 'energy_trend_pending', 'label': '右端还没走到',
                    'text': f'窗口右端贴着基准日 {d.get("date")}，所以有 {pending} 笔·格还没走到那一天'
                            f'（最远看后 {kmax} 场）：那些格子不进分子分母，也不是 0%。'
                            '越往右（k 越大）这一类越多，每一格的分母 n_ok 与总数 n_all 都交出来了。'})
    missing = sum(c.get('n_missing') or 0 for b in buckets for c in (b.get('cells') or []))
    if missing:
        out.append({'code': 'energy_trend_missing', 'label': '有笔读不到',
                    'text': f'{missing} 笔·格走到了那一天却算不出涨跌：名次日缺基期，或第 k 场它没有行'
                            '（停牌/未上市）。这一类同样不进分子分母，不当 0%。'})
    dropped = d.get('dropped_events') or 0
    if dropped:
        out.append({'code': 'energy_trend_dropped', 'label': '有事件对不上价格',
                    'text': f'{dropped} 笔事件在日线价格里找不到对应行（名次日那天它没有收盘）：'
                            '整笔不参与任何一档，不拿 0 顶。'})
    ties = [b for b in buckets if (b.get('events') or 0) != (used or 0) * BUCKET_SIZE]
    if ties:
        out.append({'code': 'energy_trend_ties', 'label': '边界有并列',
                    'text': f'有 {len(ties)} 档的笔数不等于「场数 × {BUCKET_SIZE}」：名次边界那一名并列几只'
                            f'就一起进同一档（{RULE_NOTE_SHORT}）所以笔数会多几笔；'
                            f'名次上限 {n} 同样是上限，不是恰好 {n} 只。'})
    over = [b for b in buckets if (b.get('rank_hi') or 0) > n]
    if over:
        b = over[-1]
        out.append({'code': 'energy_trend_over_top', 'label': '并列挤出了名次上限',
                    'text': f'第 {b["label"]} 档已经超出你设的名次上限 {n}：边界那一名并列的票一起进来了。'
                            f'这一档只有 {b["events"]} 笔，前面各档约 {(used or 0) * BUCKET_SIZE} 笔 —— '
                            '笔数差这么多，别拿它的均值与前面各档直接横比。'})
    out.append({'code': 'energy_trend_overlap', 'label': '样本互相重叠',
                'text': SAMPLE_RULE})
    out.append({'code': 'energy_trend_not_backtest', 'label': '不是策略回测',
                'text': '历史场次的相关性统计：没扣手续费与滑点、没模拟名次日收盘能不能买到、'
                        '没做样本外检验。某一档在这段窗口里更好，不等于下一段更好，不构成买卖建议。'})
    return out