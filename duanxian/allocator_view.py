# -*- coding: utf-8 -*-
"""分配者视角的**读数层**（写侧在 `app/compute/allocator.py`，判据只在那一份）。

这一层不重新判任何东西：读 `analytics.sector_allocator` 当日那批行、附上口径文字、
再把视频里那条"热度与回报是两笔账"的规则**当场复算**一遍（只有这一段是现算，
因为它只读三根指数的日线，1,253 行、零点几秒，而且它不是每天要落库的信号，是对账）。

一条必须说穿的边界：**这里用的指数是价格指数（000015 上证红利），不是视频里的
「中证红利低波全收益」**。分红再投资不进价格指数，所以本地复算出来的年化会**低于**
视频给的 15.56%/19.26%。宁可交一个偏低的、口径写清楚的数，也不把别人的结果抄上来
冒充本机的复算结果（这是本项目对"界面数字"的底线）。
"""
from __future__ import annotations

import datetime as dt
from typing import Any

from . import pg

SPEC_VERSION = 'alloc-1'

#: 对照用的三根指数。**为什么是它们**写在说明里，页面不自己编故事：
#:  · 000015.SH 上证红利 —— 视频说的"无聊的收费站"这一类（本地没有红利低波全收益，
#:    这条是最接近的可得替代，且是价格指数，回报偏低要标出来）；
#:  · 000688.SH 科创50 / 399006.SZ 创业板指 —— "想象力"那一侧的代表。
INDICES = {
    '000015.SH': '上证红利（价格指数）',
    '000688.SH': '科创50',
    '399006.SZ': '创业板指',
}

MA_WINDOW = 120          # 视频那条规则用的均线窗口
SELL_ABOVE = 0.15        # 收盘高出均线 15% → 次日卖一半
BUY_BACK = 'below'       # 跌回均线下面 → 全部买回

CALIBERS = {
    'share': ('份额 = 本板块成交额 ÷ 当日**全部板块成交额之和**（板块之间有重叠：'
              '一只票同时属于多个概念）。所以它是**相对口径**，不能读成"占全市场成交额的百分之几"；'
              '而分位/温度是同一个板块自己和自己的历史比，不受重叠影响。'),
    'share_pctile': ('份额分位 = 当日份额在这个板块自己近 250 场里的分位（0~1）。'
                     '**这是自身分位不是名次**：份额绝对值很小的板块也可能 99 分位。'),
    'toll': ('位置分（收费站分）= 股息率 35 + 成交额稳定性 25 + 份额长期中位 20 + 板块市值占比 20，'
             '四项都先换成当日横截面分位再加权。**权重是可商量的先验，不是市场规律。**'
             '「绕不过去」这件事本库没有数据（没有财务报表、没有牌照/市占率），'
             '所以这里全是**形状代理**：长期稳定地有现金流经过 = 像收费站。形状像 ≠ 制度上是。'),
    'crowding': ('拥挤度温度 = 份额分位 45 + 涌入速度 25 + 板块成员一年增量 20 + 融资余额/流通市值 10。'
                 '**温度 100 的意思是"所有人终于达成共识"，不是"该卖了"** —— '
                 '它只说明这一波的钱已经很挤，什么时候退是另一件事，本系统不给动作。'),
    'inflow': '涌入速度 = 当日份额 ÷ 前 20 场（跳过最近 5 场，免得基线被自己这一波抬高）的中位 − 1。',
    'member_delta': ('成员增量 = 板块成分股数量与约一年前比的变化。产业政策落地时"所有人同时上项目"'
                     '在本地唯一看得见的形状就是它 —— **不是产能数据**，本库没有产能。'),
    'div_yield': ('股息率 = 近 365 天除息的每股现金分红合计 ÷ 最近一场收盘价（不复权价配每股现金，'
                  '口径成对）。分母取当前价，所以这是**当下**的读数，不能回算历史；'
                  '送转与配股不进分子。板块值是成员的中位数，同时交出覆盖度。'),
    'stability': ('稳定性 = 1 − 日成交额的变异系数（近 250 场）。越接近 1 = 每天的量越稳，'
                  '也就是"不管行情怎样，钱都路经这里"的形状。'),
    'imagine': ('想象力分 = 60 场动量 40 + 近 20 场上涨家数占比 25 + 份额热度 20 + 概念属性 15。'
                '它量的是"这个故事现在多有吸引力"，**不判断这个故事对不对**。'),
    'quadrant': ('象限 = x 想象力分、y 位置分，分界线取**当日中位数**（不写死 50：'
                 '写死的话分布一动，整个象限全体搬家）。四格的名字来自那段视频的对照 —— '
                 '收费站 / 共识区 / 故事区 / 无人区。'),
    'stage': ('阶段 = 份额水平 × 份额方向 × 温度 × 价格动量的判定树（互斥、按顺序判）。'
              '只有资金与价格的形状，**不含政策文本**（本地没有政策库），'
              '所以它回答"这波钱走到哪一段"，不回答"政策还会不会加"。都不满足就是不在五段里。'),
    'nulls': ('算不出就是 NULL，不是 0：NULL 不参与当日排名、也不给分。'
              '每行同时交出覆盖度（股息率/市值有几成成员算得出）和 `missing`（哪几项没进总分），'
              '不然"板块股息率中位"会被读成"整个板块的股息率"。'),
    'backtest': ('那条规则是视频里的原话：满仓时收盘高出 120 日均线 15% → 次日卖一半；'
                 '跌回均线下面 → 全部买回；减仓期间不再卖。'
                 '⚠️ 本库只有**价格指数**（000015 上证红利），分红再投资不在里面，'
                 '所以这里的年化会低于视频引用的「中证红利低波全收益」那两个数 —— '
                 '宁可交一个偏低但口径写清的数，也不把别人的结果抄上来冒充本机复算。'),
}

STAGE_LABEL = {'s1_seed': 'S1 起步·权力刚转身', 's2_boom': 'S2 繁荣',
               's3_inflow': 'S3 资本涌入', 's4_consensus': 'S4 共识·估值疯狂',
               's5_overhang': 'S5 过剩·股价先掉头', None: '不在五段里'}
QUADRANT_LABEL = {'toll': '收费站', 'consensus': '共识区', 'story': '故事区', 'nowhere': '无人区'}


def _num(v: Any, nd: int | None = None) -> Any:
    """库里出来的值统一成 JSON 安全的形式。

    **NaN 必须变成 None**：pandas 的空值是 NaN，而 `json` 会把 NaN 原样写成 `NaN` ——
    那不是合法 JSON，浏览器 `JSON.parse` 直接炸，页面只报"读不到"，
    看着像接口坏了我却查不出。所以这一层就是最后一道闸。
    """
    if v is None:
        return None
    if isinstance(v, str):
        return v
    import pandas

    # 这道闸必须在"是日期就原样交回"**之前**：`pd.NaT` 偏偏是 `datetime` 的实例，
    # 先走日期分支就会把 NaT 交出去，而 `json.dumps` 写它是 `NaT` —— 不合法 JSON。
    try:
        if pandas.isna(v):        # NaN 与 NaT 都在这道闸变成 None
            return None
    except (TypeError, ValueError):
        pass
    if isinstance(v, (dt.date, dt.datetime)):
        return v
    try:
        f = float(v)
    except (TypeError, ValueError):
        return str(v)
    if f != f:          # NaN
        return None
    return round(f, nd) if nd is not None else f


def latest_session() -> str | None:
    rows = pg.fetch_df("select max(trade_date)::date d from analytics.sector_allocator "
                       "where spec_version = %s", (SPEC_VERSION,))
    if rows is None or rows.empty or rows.iloc[0]['d'] is None:
        return None
    return str(rows.iloc[0]['d'])


def sessions_count() -> dict:
    """这张表一共攒了几场 —— 只有一场的时候页面必须说"历史曲线还没有"，不能画一条假的。"""
    rows = pg.fetch_df("select count(distinct trade_date) n, min(trade_date)::date lo, "
                       "max(trade_date)::date hi from analytics.sector_allocator "
                       "where spec_version = %s", (SPEC_VERSION,))
    if rows is None or rows.empty:
        return {'available': False, 'reason': '库读不到（不是"没有板块"）'}
    r = rows.to_dict('records')[0]
    return {'sessions': int(r['n'] or 0), 'from': str(r['lo']) if r['lo'] else None,
            'to': str(r['hi']) if r['hi'] else None}


def _rows(sql: str, params=()) -> list[dict]:
    df = pg.fetch_df(sql, params)
    return [] if df is None else [
        {k: _num(v) for k, v in row.items()} for row in df.to_dict('records')]


# ---------------------------------------------------------------- 那条规则的本地复算

def ma_rule(closes: list[float], *, window: int = MA_WINDOW,
            above: float = SELL_ABOVE) -> dict:
    """视频里那条减仓规则的本地复算。**纯函数，不碰库**，规则照原文：

    · 满仓时收盘高出 N 日均线 `above` → **下一个交易日**卖一半；
    · 收盘跌回均线下面 → 下一个交易日全部买回；
    · 减仓期间不再卖（只有满仓才会触发卖出，所以连续高出均线不会一路砍到零仓）。

    交回 `buy_hold` / `rule` 两条净值与年化、触发次数与日期、以及**均线起算不用的前
    `window` 场**被丢掉了多少场 —— 不说清这段，两条曲线的起点就是假的。
    """
    n = len(closes)
    if n <= window + 1:
        return {'available': False,
                'reason': '只有 %d 场，均线窗口 %d 场不够 —— 不够就是算不出，不缩短窗口硬算' % (n, window)}
    warmup = window
    start_px = closes[warmup]
    end_px = closes[-1]
    days = (end_px / start_px) ** (252.0 / (n - warmup)) - 1.0 if start_px > 0 else None
    total = end_px / start_px - 1.0 if start_px > 0 else None

    pos = 1.0                  # 满仓起步
    equity = 1.0
    prev_pos = pos
    px_prev = start_px
    trades: list[dict] = []
    for i in range(warmup + 1, n):
        window_slice = closes[i - 1 - window:i - 1]        # 用**昨天为止**的均线，不含当日 → 无未来函数
        ma = sum(window_slice) / len(window_slice)
        px = closes[i]
        if ma > 0 and i - 1 >= warmup:
            prev_close = closes[i - 1]
            prev_ma = sum(closes[i - 2 - window:i - 2]) / window if i - 2 - window >= 0 else None
            if prev_ma and prev_close > prev_ma * (1 + above) and prev_pos >= 0.999:
                pos = 0.5                                   # 昨天信号，今天执行
                trades.append({'i': i - warmup, 'action': '卖一半',
                               'price': round(px, 4)})
            elif prev_ma and prev_close < prev_ma and pos < 0.999:
                pos = 1.0
                trades.append({'i': i - warmup, 'action': '买回满仓',
                               'price': round(px, 4)})
        ret = px / px_prev - 1.0 if px_prev else 0.0
        equity *= (1.0 + pos * ret)
        prev_pos, px_prev = pos, px
    rule_total = equity - 1.0
    rule_ann = equity ** (252.0 / (n - warmup)) - 1.0
    # 高出/跌破均线的幅度极值：**"一次都没触发"到底是形状还是没形状，得用这个数回答**，
    # 不然页面上只能写"0 次"，人无法区分"规则没生效"和"这条指数压根没有那种斜率"。
    peaks = []
    for i in range(warmup + 1, n):
        base = closes[i - 1 - window:i - 1]
        if base:
            ma = sum(base) / len(base)
            if ma > 0:
                peaks.append(closes[i - 1] / ma - 1.0)
    return {'available': True, 'window': window, 'above': above,
            'peak_above': round(max(peaks), 4) if peaks else None,
            'deepest_below': round(min(peaks), 4) if peaks else None,
            'sessions_used': n - warmup, 'sessions_dropped': warmup,
            'buy_hold_total': round(total, 4), 'buy_hold_annualized': round(days, 4),
            'rule_total': round(rule_total, 4), 'rule_annualized': round(rule_ann, 4),
            'trades': len(trades), 'trade_log': trades[-8:]}


def _index_closes(code: str, as_of: str) -> list[float]:
    rows = _rows("select close from market_data.index_daily "
                 "where security_code = %s and trade_date <= %s::date order by trade_date", (code, as_of))
    return [float(r['close']) for r in rows if r['close'] not in (None, 0)]


def _pct(x) -> str:
    """给说明文字用的百分数：`None` 交回 `—`，绝不交回 `None%` 这种半截话。"""
    return '—' if x is None else '%.1f' % (float(x) * 100)


def heat_vs_return(as_of: str) -> dict:
    """「故事的热度，与回报是两笔账」那一段的对账：三根指数各自跑同一条规则 + 一直拿着。

    一次都不触发的情况必须自己说话：000015 上证红利在本地这段窗口里**从没**收盘高出
    120 日均线 15%（实测最高 1.148 倍），所以规则一次都没卖过、两条净值必然相等 ——
    这不是"算坏了"，是这条指数本来就没有那种斜率。不写出来，页面上那个 0 会被读成 bug。
    """
    out = []
    for code, label in INDICES.items():
        closes = _index_closes(code, as_of)
        r = ma_rule(closes)
        if not r.get('available'):
            out.append({'code': code, 'label': label, 'available': False, 'reason': r['reason']})
            continue
        note = None
        if not r['trades']:
            note = ('这段窗口里它收盘高出 %d 日均线最多只有 %s%%，没够到 %s%% 那条线 —— '
                    '规则一次都没触发，两条净值相等不是算坏了，是这条指数没有那种斜率'
                    % (MA_WINDOW, _pct(r['peak_above']), int(SELL_ABOVE * 100)))
        elif r['rule_annualized'] < r['buy_hold_annualized']:
            note = '这条规则在它身上是**减分**的：躲过高波动也躲过了后面的反弹'
        out.append({'code': code, 'label': label, 'sessions': len(closes),
                    'note': note, **r})
    return {'basis': '本库日线收盘价（价格指数，不含分红再投资）；规则原文见 `calibers.backtest`',
            'rows': out}


# ---------------------------------------------------------------- 页面读数

def view(date: str = '', limit: int = 300) -> dict:
    """一屏四块：收费站榜 / 拥挤度榜 / 象限散点 / 阶段计数，再加"热度与回报"对账。

    表里没有这一场就 `available=False` 并说清最近一场是哪天 —— 界面宁可空着，
    也不许拿上一场冒充今天（这是本项目对首屏数字的通用红线）。
    """
    if not pg.enabled():
        return {'available': False, 'reason': '本地库连不上（VIBE_PG / PG_USER / PG_PASSWORD）'}
    sc = sessions_count()
    if not sc.get('sessions'):
        return {'available': False,
                'reason': '`analytics.sector_allocator` 里还没有任何一场 —— 这不是"没有板块"，'
                          '是计算节点还没跑过：`python -m app.compute.allocator`（加工链里叫 `allocator` 步）'}
    d = date or sc['to']
    where = "trade_date = %s::date and spec_version = %s"
    base = _rows("select sector_code, sector_name, sector_type, universe, share, share_pctile, "
                 "share_med250, amount_stability, div_yield_med, div_coverage, mv_share, mv_coverage, "
                 "toll_score, toll_rank, share_slope20, member_count, member_delta, margin_crowd, "
                 "crowding_temp, crowding_rank, mom60, breadth20, stage, stage_reason, imagine_score, "
                 "quadrant, missing from analytics.sector_allocator where " + where +
                 " order by toll_score desc nulls last limit %s", (d, SPEC_VERSION, min(limit, 1000)))
    if not base:
        return {'available': False, 'date': d,
                'reason': '这一场（%s）没有行；最近一场是 %s —— 差的场次没算过，补 '
                          '`python -m app.compute.allocator --since <哪天>`' % (d, sc['to'])}
    hot = _rows("select sector_code, sector_name, sector_type, crowding_temp, crowding_rank, "
                "share, share_pctile, share_slope20, member_delta, margin_crowd, mom60, stage, stage_reason "
                "from analytics.sector_allocator where " + where +
                " order by crowding_temp desc nulls last limit %s", (d, SPEC_VERSION, min(30, limit)))
    quad = _rows("select sector_code, sector_name, sector_type, toll_score, imagine_score, quadrant, "
                 "crowding_temp, share_pctile from analytics.sector_allocator where " + where +
                 " and toll_score is not null and imagine_score is not null", (d, SPEC_VERSION))
    stages = _rows("select stage, count(*) n, round(avg(crowding_temp),1) avg_temp "
                   "from analytics.sector_allocator where " + where +
                   " group by 1 order by 2 desc", (d, SPEC_VERSION))
    quads = _rows("select quadrant, count(*) n, round(avg(toll_score),1) avg_toll, "
                  "round(avg(imagine_score),1) avg_imagine "
                  "from analytics.sector_allocator where " + where +
                  " group by 1 order by 2 desc", (d, SPEC_VERSION))
    for r in base + hot:
        r['stage_label'] = STAGE_LABEL.get(r.get('stage'), r.get('stage'))
    for r in base + quad:
        r['quadrant_label'] = QUADRANT_LABEL.get(r.get('quadrant'), r.get('quadrant'))
    missing_n = sum(1 for r in base if r.get('missing'))
    return {
        'available': True, 'date': d, 'spec_version': SPEC_VERSION,
        'generated_at': dt.datetime.now().astimezone().isoformat(timespec='seconds'),
        'sessions': sc, 'universe': base[0].get('universe'),
        'rows': base, 'crowding': hot, 'scatter': quad,
        'stages': stages, 'quadrants': quads,
        'heat_vs_return': heat_vs_return(d),
        'calibers': CALIBERS,
        'data_reality': {
            'div_missing': missing_n,
            'note': ('股息率这一项在母体里有多少板块算不出（上面 `div_missing`）；'
                     '两融本地只有 2026-06 起，所以更早的场次温度里没有杠杆这一项。'
                     '本表从落库那天起才有历史 —— 历史曲线要等加工链每天攒。'),
        },
    }
