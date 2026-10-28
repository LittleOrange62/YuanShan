# -*- coding: utf-8 -*-
"""异动提醒的读侧（接口出口）：只读 `analytics.abnormal_move_daily`，**不算判据**。

判据只住 `app/compute/abnormal_move.py` 一处。这一层做四件事：

1. 定「哪一天」（不给就用这一版算过的最新一场，并说清是不是退档了）；
2. 交出三块榜（用户 2026-10-15 点名）：`forward` 明日涨停会踩到严重异动的票、
   `hot` 热门股偏离值前 N、`recent_severe` 近期已经踩到的（含次日涨幅与停牌）；
3. 把整场汇总在**库里**按同批条件聚合（`summary`），页面不许拿被 limit 截断的几十行再算一遍；
4. 把三条本机做不到的事写成 `caveats` 交出去（无公告日 / 指数按 0% 假设 / 北交所不纳入）。

三条不许破的读法：
  · 缺的读数给 `null` 不给 0 —— `severe=null` 是"这一场判不了"，不是"没踩到"；
    `next_pct=null` 是"还没到次日"或"整场缺席"，不是"平盘"。
  · `hot` 那一块按 10 日累计偏离排序；「几天几板」复用 `duanxian/board_span.py` 那一份实现
    （全站只此一处），这里不另数一遍连板。
  · 这是客观公开监管判据的本机复算：不打分、不预测、不给买卖建议，也不代表交易所已认定。
"""
from __future__ import annotations

from app.compute.abnormal_move import SPEC_VERSION   # 版本号只住写侧一份（这里再写一遍就会跟丢）
from .quant import _dash, _fail, _int, _latest, _num, _one, _reject, _rows

TABLE = 'analytics.abnormal_move_daily'
RECENT_DAYS = (5, 10, 20)       # 「近期严重异动」往回看几场
HOT_DEFAULT = 20

CLAUSE_LABEL = {'times': '10 日内同向异常波动凑够次数',
                'dev10': '10 日累计偏离过 ±100/−50',
                'dev30': '30 日累计偏离过 ±200/−70'}
DIR_LABEL = {'up': '涨向', 'down': '跌向', 'mixed': '涨跌两向'}
UNIVERSE_LABEL = {'sealed10': '近 10 场封过板', 'dev10_ge25': '10 日累计偏离 ≥25 点',
                  'dev30_ge60': '30 日累计偏离 ≥60 点', 'ab_flag': '有异常波动',
                  'severe': '已踩严重异动', 'forward_hit': '明日涨停可触发'}

CALIBER = (
    '单日偏离值 = 个股当日涨跌幅 − **对应分类指数**当日涨跌幅（沪主板 000002.SH、深主板 399107.SZ、'
    '创业板 399102.SZ、科创板 000680.SH 科创综指），指数涨跌幅由收盘价现算。'
    '累计偏离是**区间复利**（10-27 起这把尺；`abn-1` 那批旧行是逐日相加，留着对账）：'
    '窗口累计 = 个股区间涨幅（窗口内逐日复利）− 指数区间涨幅（同样复利）。'
    '异常波动：连续 3 个交易日累计偏离 ≥ +20%（主板）／≥ +30%（双创），向下同量级。'
    '严重异常波动（任一）：① 连续 10 个交易日内 4 次（主板）／3 次（双创）同向异常波动；'
    '② 连续 10 个交易日内累计偏离 ≥ +100% 或 ≤ −50%；③ 连续 30 个交易日内 ≥ +200% 或 ≤ −70%。'
    '明日涨停幅度由当日 limit_up ÷ pre_close 反推后归档（{5%, 10%, 20%}）。'
    '前瞻那一栏里「距 100/200 还差几个点」是**偏离值的缺口**，「触发所需涨幅」是**明日要涨多少个点**，'
    '复利口径下这两个数不相等（每涨 1 个点会把已涨的那段放大一层）。'
    '母体是筛出来的提醒榜（近 10 场封过板 / 10 日累计偏离 ≥25 点 / 30 日 ≥60 点 / 有异常波动 / '
    '已踩严重异动 / 明日涨停可触发），**不是全市场快照** —— 没进母体的票整行不写。'
)

DEFINITIONS = {
    'security_code': '股票代码（带市场后缀）。',
    'name': '股票名称，来自 reference_data.security；字典里没登记中文名的票这里就是代码本身。',
    'board': '上市板块（沪主板／深主板／创业板／科创板）。北交所不纳入：北证50 在本机 1,080 场里'
             '只有 13 场有涨跌幅，偏离值算不出来。',
    'is_st': '当日是否在 ST 档期里（按 reference_data.security_st_period 的档期，不是当前快照）。',
    'close': '当日收盘价。',
    'pct_change': '当日涨跌幅 %。',
    'index_pct': '对应分类指数当日涨跌幅 %（收盘价现算，不读 index_daily.pct_change）。',
    'dev_1d': '当日偏离值（百分点）= 上两列之差。',
    'dev_3': '连续 3 个交易日的区间复利累计偏离（个股区间涨幅 − 指数区间涨幅，百分点）'
             '—— 异常波动看的就是这一列。',
    'dev_10': '连续 10 个交易日的区间复利累计偏离（不是逐日相加：连板段两把尺能差 20~30 个点，'
              '逐日相加会漏报已触发的人，10-27 换的这把）。窗口根数不够（新股、长停牌）'
              '或窗口里有哪天缺读数时为 null，配合 dev_10_complete=false 读作"判不了"。',
    'dev_30': '连续 30 个交易日的区间复利累计偏离。上市不满 30 场的票这里是 null。',
    'up_cnt_10': '近 10 场里"向上异常波动"出现几次（主板 4 次、双创 3 次即踩到严重异动的一条）。',
    'down_cnt_10': '近 10 场里"向下异常波动"出现几次。',
    'severe': '今天踩到严重异常波动（三条任一）。**null 是判不了**（窗口不够根数、上市前 5 场、'
              '当日指数涨幅取不到），不是没踩到 —— 看 severe_reason。',
    'severe_how': '踩到哪几条：times / dev10 / dev30（逗号分隔）。',
    'severe_how_label': '把 severe_how 翻成中文句子，页面直接念，别自己再拼一遍条款。',
    'severe_dir': '涨向（up）／跌向（down）／两向（mixed）—— 涨着踩到与跌着踩到是两件事。',
    'severe_dir_label': 'severe_dir 的中文。',
    'status_label': '那一整格「异动状态」的文本：方向 + 踩到哪几条（没踩到 / 判不了（原因）也走这一列）。'
                    '界面上这一列的排序键就是它 —— 排序要按格子里真读到的东西排，不能只按其中半句排'
                    '（10-16 浏览器实测：键给成 severe_how_label 时涨跌两向在榜里交错，列头照样亮着"已排序"）。',
    'severe_reason': '没踩到或判不了的短码：none / partial:… / bars_lt_window / new_listing_lt5 / '
                     'no_index_pct / no_pct。',
    'band_next': '明日涨跌幅限制档位（0.05 / 0.10 / 0.20）。null = 当日 limit_up 或 pre_close 取不到，'
                 '这一行的前瞻整块判不了 —— 不是 0%。',
    'limit_price_next': '明日涨停价 = 今收 × (1 + band_next)，四舍五入到分。',
    'trigger_pct': '明日**要涨这么多个百分点**才踩到严重异动（三条档各算各的缺口，取最小者）。'
                   '⚠️ 它与旁边那两列「距 100／距 200 还差」**不是同一个数**：累计换成区间复利后，'
                   '明日每涨 1 个点会把这一段区间放大 (100 + 区间已涨点数) ÷ 100 个点，'
                   '所以所需涨幅 = 缺口 × 100 ÷ (100 + 区间已涨点数) —— 已经涨得越多的票，'
                   '同样一个缺口要的涨幅越小。大于 band_next×100 说明明天涨停也够不着，所以它不在预警榜里。',
    'trigger_price': '触发价 = 今收 × (1 + trigger_pct/100)。',
    'need_dev_to_100': '10 日累计偏离这一档还差几个点（基准是**明日那个窗口去掉明日**的 9 场累计，'
                       '不是今天看到的 10 场 —— 明天会把最早那场挤出去）。负数 = 这一档已经过了。'
                       '这一列是**偏离值的缺口**，不是涨幅：要涨多少个点请看 trigger_pct。',
    'need_dev_to_200': '30 日累计偏离这一档还差几个点（同上，基准是 29 场）。',
    'dev_10_if_limit': '明日涨停后那个 10 日窗的累计偏离（百分点）—— 个股那半边按复利往上放大，'
                       '**明日指数按 0% 假设**。',
    'dev_30_if_limit': '明日涨停后那个 30 日窗的累计偏离（百分点）。同上。',
    'fwd_clauses': '明日涨停会**新**补足哪几条（dev10 / dev30 / times，逗号分隔），与 severe_how 同一套短码；'
                   '已经踩到的那几条不会重复出现在这里。',
    'times_if_limit': '明日涨停 ⇒ 明日那个 3 日窗凑出下一次同向异常波动，并且 10 日内次数到位。',
    'fwd_state': '这一行进前瞻榜的是哪一种：`will_trigger` = 明天涨停会新踩到一条；'
                 '`already_partial` = 今天已经踩到某一条、但离下一条还有缺口（今天涨停幅度够不着）。'
                 '两条线混在一张表里不标出来就会被读成同一件事。',
    'fwd_state_label': '那一整格「明日涨停会怎样」的中文原句：已踩到哪几条 + 明日涨停踩到下一条 / '
                       '还差多少个点。界面上这一列的排序键就是它（sortKey 必须等于格子里真读到的字段）。',
    'hit_if_limit_up': '明日涨停会**新**踩到一条还没踩到的严重异动。null = 还有档没踩到却算不出来'
                       '（新股／长停牌／档位取不到）。10-27 起已踩到某一条的票不再被整只挡在榜外：'
                       '它照样交出来，`severe`/`severe_how` 说它已踩到哪条、`trigger_pct` 说它离下一条还差多少；'
                       '三条都踩过了才是 false（「明天不会带来新的一条」，这句是真话）。',
    'board_span': '「几天几板」：与首页连板榜同一份实现（连续几天里封过几回板，只空 1 天仍算同一轮）。',
    'next_trade_date': '指数在场的下一个交易日。',
    'next_pct': '次日涨幅 %。null 有两种：**次日还没到**（跑的就是最新一场），或次日整场缺席 —— '
                '读 next_halted / next_missing 分辨，不当成平盘。',
    'next_halted': '次日**有证据的缺席**：指数在那天有这根线、而这只票没有日线行 ⇒ 停牌（严重异动后'
                   '的停牌核查就是这种形状）。',
    'next_missing': '次日整场缺席（含停牌，也可能数据缺）。与 next_halted 同向，分开留是给回算看的。',
    'universe_by': '这行进母体的哪一条（近 10 场封过板 / 10 日累计偏离 ≥25 点 / 30 日 ≥60 点 / '
                   '有异常波动 / 已踩严重异动 / 明日涨停可触发）。',
    'universe_by_label': 'universe_by 的中文，逗号分隔。',
}


def _date_arg(date: str) -> str:
    return (date or '').strip().replace('-', '')


def _label(row: dict) -> dict:
    """派生展示列在这一层加，页面不重算判据。

    ⚠️ `status_label` 是「方向 · 条款」那**一整格**的文本：界面上那一列的 sortKey 必须等于
    格子里读到的字段（10-16 浏览器实测就是栽在这 —— 列里显示的是"涨向 · …"拼串，
    sortKey 却只给了 `severe_how_label`，于是涨跌两向在榜里交错，点了列头看着排过、其实没排）。
    """
    how = (row.get('severe_how') or '').split(',') if row.get('severe_how') else []
    row['severe_how_label'] = '、'.join(
        CLAUSE_LABEL.get(h.strip(), h.strip()) for h in how if h.strip()) or None
    fwd = (row.get('fwd_clauses') or '').split(',') if row.get('fwd_clauses') else []
    row['fwd_clauses_label'] = '、'.join(
        CLAUSE_LABEL.get(h.strip(), h.strip()) for h in fwd if h.strip()) or None
    row['severe_dir_label'] = DIR_LABEL.get(row.get('severe_dir'))
    ub = (row.get('universe_by') or '').split(',') if row.get('universe_by') else []
    row['universe_by_label'] = '、'.join(
        UNIVERSE_LABEL.get(h.strip(), h.strip()) for h in ub if h.strip()) or None
    if row.get('severe') is True:
        row['status_label'] = ' · '.join(
            x for x in (row['severe_dir_label'], row['severe_how_label']) if x)
    elif row.get('severe') is False:
        row['status_label'] = '没踩到'
    else:
        row['status_label'] = f"判不了（{row.get('severe_reason') or '未交原因'}）"
    # 前瞻榜那一列的状态（10-27 起这张表里两类并存，混成一句就会说谎）
    # ⚠️ 已踩到某一条、同时明天涨停还会踩到**下一条**的票是真实存在的（两档各算各的缺口），
    #    所以 `will_trigger` 那一支不能只念新的一条 —— 用户点名的就是"要说清它已踩到哪一条"。
    st = row.get('fwd_state')
    gap = row.get('trigger_pct')
    done = ('已踩到 ' + row['severe_how_label']) if row.get('severe') is True and row['severe_how_label'] else None
    if st == 'will_trigger':
        row['fwd_state_label'] = ((done + '｜') if done else '') + '明日涨停即踩到：' + (
            row['fwd_clauses_label'] or '（条款没交）')
    elif st == 'already_partial':
        row['fwd_state_label'] = (done or '已踩到（条款没交）') + '｜' + (
            f'再涨 {gap} 个点才踩到下一条' if gap is not None else '下一条还差多少算不出来')
    else:
        row['fwd_state_label'] = None
    return row


def _caveats(no_band: int, unknown: int, no_minute_free: bool) -> list:
    """caveat 的措辞在这一层一处写好，页面只念不编。三条本机做不到的事**不许折进默认收起的说明框**。"""
    out = [
        {'code': 'no_announce_date', 'hard': True,
         'text': '交易所的口径里「异常波动指标自公告之日（或复牌之日）起重新计算」，'
                 '而本机没有公告日这份数据 ⇒ 这里的次数与累计都是**纯滚动窗口**算出来的。'
                 '同一段连续上涨会比交易所的口径更容易反复计到。'
                 '所以这一屏只能说"按公开规则算出来踩到了"，不能说"交易所已认定为严重异常波动"。'},
        {'code': 'index_assumed_zero', 'hard': True,
         'text': '「明日涨停会不会踩到」那一块，明日的指数涨跌幅**按 0% 假设**（前瞻算不了指数）。'
                 '这一句影响的是"还差几个点"那一列的精度，且方向偏保守：'
                 '明日指数若继续跌，实际触发所需涨幅比这里给的数更小。'},
        {'code': 'bj_excluded', 'hard': True,
         'text': '北交所整块不纳入：北证50 在本机 1,080 场里只有 13 场有涨跌幅，'
                 '偏离值算不出来。这一屏缺的是北交所那一档，不是"北交所没有异动"。'},
        {'code': 'not_signal',
         'text': '这是客观公开监管判据的本地复算：不打分、不预测、不给买卖建议。'
                 '"明日涨停会触发"只说明涨幅到那个位置时累计偏离过线，不说明它会不会涨到那里。'},
        {'code': 'universe',
         'text': '母体是筛出来的提醒榜（约几百只／场），不是全市场 5,000 多只的快照：'
                 '没进母体的票整行不写，所以这里读不到某只票不等于它没有偏离值。'},
    ]
    if no_band:
        out.append({'code': 'no_band',
                    'text': f'这一场有 {no_band} 只票当日 limit_up 或 pre_close 取不到 ⇒ '
                            '明日涨停幅度判不了，那一行的前瞻整块是 null（不是 0%）。'
                            '累计偏离那一块不受影响。'})
    if unknown:
        out.append({'code': 'severe_unknown',
                    'text': f'这一场有 {unknown} 只票的"踩没踩到"算不出来（多为窗口根数不够的新票／'
                            '长停牌复牌，或那天指数那根线没入库）—— 界面把它们单列成"判不了"，'
                            '不并进"没踩到"的计数里。'})
    if no_minute_free:
        out.append({'code': 'stale_day',
                    'text': '这一屏的前瞻只有**当天算的那一天**有意义：明日涨停价要用今天的收盘价算，'
                            '回看旧场次时 fwd 那一列是当时那场的前瞻，早已过期。'})
    return out


def _summary(day: str) -> dict:
    """整场汇总在库里按同批条件聚合（不拿被 limit 截断的那几十行再算一遍）。"""
    r = _one(f"""select count(*)::int                                       universe,
                       count(*) filter (where severe)::int                    severe_n,
                       count(*) filter (where severe is null)::int            unknown_n,
                       count(*) filter (where severe is false)::int           not_severe_n,
                       count(*) filter (where hit_if_limit_up)::int           forward_n,
                       count(*) filter (where hit_if_limit_up is null)::int   forward_unknown_n,
                       count(*) filter (where band_next is null)::int         no_band_n,
                       count(*) filter (where ab_up or ab_down)::int          ab_n,
                       count(*) filter (where severe_dir = 'down')::int       severe_down_n,
                       max(dev_10)                                            max_dev10,
                       max(calculated_at)::text                               calc_at
                  from {TABLE} where trade_date = %s and spec_version = %s""",
             (day, SPEC_VERSION)) or {}
    return {'universe': _int(r.get('universe')),
            'severe': _int(r.get('severe_n')),
            'severe_unknown': _int(r.get('unknown_n')),
            'not_severe': _int(r.get('not_severe_n')),
            'forward': _int(r.get('forward_n')),
            'forward_unknown': _int(r.get('forward_unknown_n')),
            'no_band': _int(r.get('no_band_n')),
            'ab_this_day': _int(r.get('ab_n')),
            'severe_down': _int(r.get('severe_down_n')),
            'max_dev10': _num(r.get('max_dev10'), 2),
            'calculated_at': r.get('calc_at'),
            'scope': '这一场母体里全部行（不是榜被截断后的那一段）'}


def _forward(day: str, limit: int) -> list:
    """前瞻榜：明日涨停会**新**踩到一条的，加上已经踩到某一条、离下一条还有缺口的。

    10-27 用户点名：`abn-1` 那版把已踩到的票整只挡在榜外（大业股份、海天瑞声就是这么消失的）。
    现在两类都在同一张表里，靠 `fwd_state` 分开说：`will_trigger` = 明天涨停就踩到下一条；
    `already_partial` = 已踩到哪条（`severe_how`）、再涨多少个点才踩到下一条（今天涨停幅度够不着）。
    三条全踩过的不进来（那时 `trigger_pct` 是 null，榜上也没有可说的缺口）。
    """
    sql = f"""
        select t.security_code, coalesce(nullif(s.name, ''), t.security_code) as name,
               t.board, t.is_st, t.close, t.pct_change, t.dev_10, t.dev_30,
               t.severe, t.severe_how,
               t.band_next, t.limit_price_next, t.trigger_pct, t.trigger_price,
               t.need_dev_to_100, t.need_dev_to_200, t.dev_10_if_limit, t.dev_30_if_limit,
               t.fwd_clauses, t.times_if_limit, t.up_cnt_10
          from {TABLE} t
          left join reference_data.security s using (security_code)
         where t.trade_date = %s and t.spec_version = %s
           and (t.hit_if_limit_up or (t.severe and t.trigger_pct is not null))
         order by t.hit_if_limit_up desc nulls last, t.trigger_pct asc nulls last,
                  t.dev_10 desc nulls last, t.security_code
         limit %s"""
    rows = _rows(sql, (day, SPEC_VERSION, limit)) or []
    for r in rows:
        for k in ('close', 'pct_change', 'dev_10', 'dev_30', 'trigger_pct', 'trigger_price',
                  'need_dev_to_100', 'need_dev_to_200', 'dev_10_if_limit', 'dev_30_if_limit',
                  'limit_price_next'):
            r[k] = _num(r.get(k), 3 if k == 'close' else 2)
        r['band_next'] = _num(r.get('band_next'), 4)
        r['band_pct'] = None if r['band_next'] is None else round(r['band_next'] * 100.0, 2)
        r['is_st'] = bool(r.get('is_st')) if r.get('is_st') is not None else None
        r['severe'] = bool(r.get('severe')) if r.get('severe') is not None else None
        r['fwd_state'] = ('will_trigger' if r.get('fwd_clauses')
                          else 'already_partial' if r['severe'] else None)
        for k in ('times_if_limit',):
            r[k] = bool(r.get(k)) if r.get(k) is not None else None
        for k in ('up_cnt_10',):
            r[k] = _int(r.get(k))
        _label(r)
    return rows


def _hot(day: str, limit: int) -> list:
    """热门股偏离值榜：按 10 日累计偏离降序取前 N。

    「涨幅过大」在本机就用累计偏离这一把尺子定档（≥25 点进母体）—— 它比"当日涨幅"更能说明
    这一段走了多远，而且已经剔掉了指数的影响。「多天多板」不在这层重算：`board_span` 那一份
    全站唯一实现，这里调它。
    """
    rows = _rows(f"""
        select t.security_code, coalesce(nullif(s.name, ''), t.security_code) as name,
               t.board, t.is_st, t.pct_change, t.dev_3, t.dev_10, t.dev_30,
               t.dev_3_complete, t.dev_10_complete, t.dev_30_complete,
               t.up_cnt_10, t.down_cnt_10, t.severe, t.severe_how, t.severe_dir,
               t.severe_reason, t.universe_by, t.bars_used, t.listed_days
          from {TABLE} t
          left join reference_data.security s using (security_code)
         where t.trade_date = %s and t.spec_version = %s
           and t.dev_10 is not null
         order by t.dev_10 desc nulls last, t.security_code
         limit %s""", (day, SPEC_VERSION, limit)) or []
    for r in rows:
        for k in ('pct_change', 'dev_3', 'dev_10', 'dev_30'):
            r[k] = _num(r.get(k), 2)
        for k in ('up_cnt_10', 'down_cnt_10', 'bars_used', 'listed_days'):
            r[k] = _int(r.get(k))
        for k in ('dev_3_complete', 'dev_10_complete', 'dev_30_complete', 'severe', 'is_st'):
            r[k] = bool(r.get(k)) if r.get(k) is not None else None
    spans = _board_span_for([str(r['security_code'])[:6] for r in rows], day)
    for r in rows:
        sp = spans.get(str(r['security_code'])[:6]) or {}
        r['board_span'] = {'days': _int(sp.get('days')), 'boards': _int(sp.get('boards')),
                           'label': sp.get('label'), 'sealed_today': sp.get('sealed_today'),
                           # 整批都空 = 分不清"这批都不在涨停轮"与"那份实现没读到库"，
                           # 读侧必须能把这一格说成两种可能，不许挑一种肯定地说
                           'batch_empty': not spans}
    return rows


def _board_span_for(codes: list, day: str) -> dict:
    """几天几板复用 `board_span` 那一份实现。读不到就交空 dict —— 那是"没读到"，不是"都没板"。"""
    from . import board_span
    try:
        return board_span.boards_by_code(codes, day) or {}
    except Exception:                                        # noqa: BLE001
        return {}


def _recent_severe(day: str, sessions: int, limit: int) -> tuple[list, int]:
    """近期已经踩到严重异动的：异动日期、踩到哪几条、次日涨幅、次日停牌。

    往前数 `sessions` 场（含当天）。当天那一场的次日还没到 ⇒ `next_pct` 是 null，
    这是"还没到"不是"停牌"，由 `next_missing is null` 与 `next_trade_date is null` 区分。
    """
    rows = _rows(f"""
        with d as (select distinct trade_date from {TABLE}
                    where spec_version = %s and trade_date <= %s::date
                    order by 1 desc limit %s)
        select t.trade_date::text, t.security_code,
               coalesce(nullif(s.name, ''), t.security_code) as name,
               t.board, t.severe, t.severe_reason, t.severe_how, t.severe_dir,
               t.dev_10, t.dev_30, t.up_cnt_10, t.down_cnt_10,
               t.pct_change, t.next_trade_date::text, t.next_pct, t.next_halted, t.next_missing
          from {TABLE} t
          join d on d.trade_date = t.trade_date
          left join reference_data.security s using (security_code)
         where t.spec_version = %s and t.severe
         order by t.trade_date desc, t.dev_10 desc nulls last, t.security_code
         limit %s""", (SPEC_VERSION, day, sessions, SPEC_VERSION, limit)) or []
    for r in rows:
        for k in ('dev_10', 'dev_30', 'pct_change', 'next_pct'):
            r[k] = _num(r.get(k), 2)
        for k in ('up_cnt_10', 'down_cnt_10'):
            r[k] = _int(r.get(k))
        for k in ('next_halted', 'next_missing', 'severe'):
            r[k] = bool(r.get(k)) if r.get(k) is not None else None
        _label(r)
    n = _one(f"""with d as (select distinct trade_date from {TABLE}
                             where spec_version = %s and trade_date <= %s::date
                             order by 1 desc limit %s)
                    select count(*)::int n,
                           count(*) filter (where t.next_halted)::int halted,
                           count(distinct t.trade_date)::int days
                      from {TABLE} t join d on d.trade_date = t.trade_date
                     where t.spec_version = %s and t.severe""",
             (SPEC_VERSION, day, sessions, SPEC_VERSION))
    if not n:                      # 聚合没成 ≠ 一场都没有：读侧要能把这两件事分开说
        return rows, None
    return rows, _int(n.get('n')) or 0


def screen(date: str = '', recent: int = 10, hot_limit: int = HOT_DEFAULT,
           limit: int = 60) -> dict:
    """一屏要的全部。不可用时只交 available/reason，**不带三块榜的键**。"""
    if recent not in RECENT_DAYS:
        return _reject('recent', None, f'近期严重异动只往回看 {RECENT_DAYS} 场，给了 {recent}')
    if not 1 <= hot_limit <= 100 or not 1 <= limit <= 200:
        return _reject('limit', None, f'hot_limit 取 1~100、forward 榜 limit 取 1~200，'
                                      f'给了 hot_limit={hot_limit} limit={limit}')
    latest = _latest(TABLE, SPEC_VERSION)
    if not latest:
        return _fail('异动提醒', TABLE, None,
                     f'先跑 `python -m app.compute.abnormal_move --since …`'
                     f'（加工段步骤名 abnormalmove，第 15 步）')
    d = _date_arg(date) or latest.replace('-', '')
    day = _dash(d)
    got = _one(f'select count(*)::int n from {TABLE} where trade_date = %s and spec_version = %s',
               (day, SPEC_VERSION))
    if not got or not _int(got.get('n')):
        return _fail('异动提醒', TABLE, day)

    summary = _summary(day)
    fwd = _forward(day, limit)
    for r in fwd:
        _label(r)
    hot = _hot(day, hot_limit)
    for r in hot:
        _label(r)
    recent_rows, recent_total = _recent_severe(day, recent, 300)
    return {
        'available': True,
        'date': day,
        'date_fell_back': bool(date) and _dash(_date_arg(date)) != day,
        'latest_date': _dash(latest),
        'spec_version': SPEC_VERSION,
        'thresholds': {'ab_main': 20.0, 'ab_gem': 30.0, 'times_main': 4, 'times_gem': 3,
                       'dev10_up': 100.0, 'dev10_down': -50.0,
                       'dev30_up': 200.0, 'dev30_down': -70.0,
                       'universe_dev10': 25.0, 'universe_dev30': 60.0},
        'summary': summary,
        'forward': {'rows': fwd, 'returned': len(fwd), 'limit': limit,
                    'truncated': len(fwd) < (summary.get('forward') or 0)},
        'hot': {'rows': hot, 'returned': len(hot), 'limit': hot_limit},
        'recent_severe': {'rows': recent_rows, 'sessions': recent,
                          'total': recent_total,
                          'total_unread': recent_total is None,
                          'truncated': recent_total is not None and len(recent_rows) < recent_total},
        'caliber': CALIBER,
        'definitions': DEFINITIONS,
        'caveats': _caveats(summary.get('no_band') or 0, summary.get('severe_unknown') or 0,
                            day != _dash(latest)),
    }
