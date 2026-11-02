# -*- coding: utf-8 -*-
"""读侧：国家队代理指标（ETF 份额/规模 ÷ 沪深300市值）的窗口、分档与异动。

用户 2026-10-26 点名「头部沪深300基金市值占沪深300流通市值比例的变化，可以用来判断国家队的持仓」，
并裁定「四大 和 全部 都计算一次，但凡有一个报警或有风险或低估都提示」⇒ 这一层把两把尺
（`big4` / `all`）各摆一份，任一越界都进 `alert.items`，并点名是哪一把、哪一条序列、越的是哪个阈值。

为什么判据住在这里而不是库里（与写侧 `app/compute/national_team.py` 的分工）：
· 写侧只落**事实**：份额、规模、净申赎、分母、两个占比、成员清单、缺数标记；
· 异动与历史分档是**阈值型判据**，阈值一改，落库的历史行就挂着旧含义（同 `first_board_shape`
  那次换尺的教训），所以每次请求按当次样本现算，并把阈值、样本数、样本区间随接口交出。

三条最容易读错、写错还不出声的事：
1. **占比线的分母（2026-10-31 换的这把尺，spec `nt-2`）**＝当日日线自算：
   300 成分每票「成交量 ÷ 换手率」反推流通股本 × 当日未复权收盘，再求和（写侧
   `app/compute/national_team.py::denominator_daily`，实测与旧尺同日差 −0.19%~−0.26%）。
   换尺之前用的是 `market_data.stock_snapshot` 求和，而那张表最早只有 2026-07-22 ⇒
   679 场里只有 47 场有占比，图上就是"前面全空、末端一小截"。现在主尺
   （占沪深300**流通市值**）从 2024-01-02 起逐场都有底 ⇒ 均线、档位、转折点都第一次有了样本底。
   ⚠️ 备用尺（占沪深300**总市值**）**没有换**：总股本不在日线里（换手率只覆盖流通股本），
   所以那一条仍然只有快照有数的那几十场，读侧对它的档位/趋势一律走「判不了」，界面上如实报覆盖。
2. 份额源是 T+1 才落（实测 `market_data.etf_share` 的最近一场比 `stock_snapshot` 晚一场）⇒
   最新那一天的占比可能算不出来，这是「还没到」，不是「没有」。
3. 这把尺不是国家队持仓本身：被动沪深300 ETF 的份额持有人是**全部**申购者，
   国家队只是其中一部分。所以它是**代理指标**——方向可参考，绝对值不等于国家队。
"""
from __future__ import annotations

import datetime as dt
import math
from typing import Any

from duanxian import pg
# 均线与 zigzag 的唯一实现（2026-10-31）：与「风险提示」那一屏共用一份，
# 两个屏对同一段走势不许报出不同的转折点。
from duanxian.trend import moving_average, zigzag_turns

TABLE = 'analytics.national_team_etf_daily'
SPEC = 'nt-2'          # 10-31 分母换尺（快照求和 → 当日日线自算，占比线全史有底），nt-1 留着对账

#: 时间档只住这一处：界面按 `range_options` 渲染按钮，不自己抄一份（抄的那份会漂）
RANGES: tuple[tuple[str, str, int | None], ...] = (
    ('3m', '近 3 个月', 3),
    ('6m', '近 6 个月', 6),
    ('1y', '近 1 年', 12),
    ('2y', '近 2 年', 24),
    ('all', '全部（库里有多长就多长）', None),
)

SCALES: tuple[dict, ...] = (
    {'key': 'big4', 'label': '四大（华泰柏瑞 510300 / 易方达 510310 / 华夏 510330 / 嘉实 159919）',
     'note': '媒体说「国家队借道沪深300ETF」通常点的就是这四只；口径最窄，异动最刺眼。'},
    {'key': 'all', 'label': '全部被动沪深300 ETF（人工审过的清单，25 只）',
     'note': '含四大之外的所有被动跟踪沪深300的场内 ETF。'
             '名字里带「300」但不是同一把尺的那些（沪港深300 / 创业板300 / 300增强 / 300价值 / '
             '300成长 / 300红利 / 300现金流 / ESG300 / 深300）一律不收——'
             '收进来就不是「沪深300 的被动仓位」了。'},
)

#: 序列描述符：键名、屏上名字、单位、点值字段、判据形状。界面上的列头/图例只从这里读，
#: 组件里不许再抄一份中文（同 `STATS_COLUMNS_SPEC` 那条纪律）。
SERIES: tuple[dict, ...] = (
    {'key': 'ratio_float', 'label': '占沪深300流通市值', 'compact': '占流通市值', 'unit': '%', 'field': 'ratio_float_pct',
     'kind': 'stock', 'judge': 'jump', 'floor_pp': 0.02, 'stock': True,
     'trend': {'smooth': 10, 'th': 0.15},
     'read': '分母是当日日线自算的 300 成分流通市值合计（与旧尺快照求和同日对账实测差 −0.19%~−0.26%；'
             '东财那一发只当对照，实测差 −0.08%）'},
    {'key': 'ratio_total', 'label': '占沪深300总市值', 'compact': '占总市值', 'unit': '%', 'field': 'ratio_total_pct',
     'kind': 'stock', 'judge': 'jump', 'floor_pp': 0.02, 'stock': True,
     'trend': {'smooth': 10, 'th': 0.15},
     'read': '同一批分子换总分母当第二把尺，只用于对照，两把不许相减。'
             '这一把的分母仍然只从 `stock_snapshot` 来（总股本不在日线里）⇒ 只有快照有数的那几十场'},
    {'key': 'share', 'label': '合计份额', 'compact': '合计份额', 'unit': '亿份', 'field': 'share_yi',
     'kind': 'stock', 'judge': 'none', 'stock': True, 'bandable': False,
     'trend': {'smooth': 10, 'th': 200.0},
     'band_skip': '份额是趋势量：新 ETF 成立、逐日申购赎回的累积、价格漂移都会推着它一路走，'
                  '对它取「自己历史的分位」读不出仓位高低 ⇒ 这一条不建档，只看当前绝对值与右边那条净申赎。',
     'read': '一级市场口径：份额增加才是有钱从场外进来买一篮子股票，成交额只看换手看不到这个。'
             '这一条不判单日异动（份额的当日变动就是右边那条「净申赎」，两处各报一遍只会把同一天数两次），'
             '也不给历史档位（见左边的说明）'},
    {'key': 'size', 'label': '合计规模', 'compact': '合计规模', 'unit': '亿元', 'field': 'size_yi',
     'kind': 'stock', 'judge': 'none', 'stock': True, 'bandable': False,
     'trend': {'smooth': 10, 'th': 400.0},
     'band_skip': '规模＝份额 × 当日收盘，行情本身就在推着它走 ⇒ 不建历史档：'
                  '这里的「偏高」只可能是沪深300涨了，不等于国家队买了。',
     'read': '份额 × 当日收盘（价格单位换算与自检只住写侧，见 `PRICE_TICK_TO_YUAN`）；'
             '它同时跟着行情涨跌与份额变动，所以「规模单日大动」多半是行情不是申赎——'
             '这一条既不判异动，也不给档位'},
    {'key': 'flow', 'label': '当日净申赎', 'compact': '净申赎', 'unit': '亿份', 'field': 'net_yi',
     'kind': 'flow', 'judge': 'level', 'stock': False,
     'trend': {'smooth': 10, 'th': 100.0},
     'read': '走 `market_data.view_etf_flow` 的分区 LAG：新 ETF 成立首日那笔是首发规模不是申购，'
             '自己「今日减昨日」会把首发算成申购（实测失真 40%）'},
)
SERIES_BY_KEY = {s['key']: s for s in SERIES}

#: 历史分档的分位切点与档名（五档：超低 / 偏低 / 中性 / 偏高 / 超高）
BAND_CUTS: tuple[tuple[float, str, str], ...] = (
    (0.10, 'ultra_low', '超低'),
    (0.25, 'low', '偏低'),
    (0.75, 'high', '偏高'),
    (0.90, 'ultra_high', '超高'),
)
BAND_LABELS = {'ultra_low': '超低', 'low': '偏低', 'mid': '中性', 'high': '偏高', 'ultra_high': '超高'}

#: 样本底：凑不满就不判。判不了要明写「判不了」，不许说成「没有越界」
#: `MIN_SAMPLES_BAND` 20 → 120（2026-10-29 用户点名「这一屏做得很差劲」后复核出来的）：
#: 当时占比线本机只有 **46 场**（旧尺的分母那张 `stock_snapshot` 从 2026-07-22 才铺），
#: 20 的门槛照样给它切出 p10/p25/p75/p90 并让屏上念「这把尺自己的历史分位」——
#: 读者把「三个月的分位」当成「历史位置」，那一格就成了假读数。抬到 120（≈ 半年场）后
#: 这一条明确走「判不了」，并把"只有 N 场、跨多久"顶到句首。
#: 2026-10-31 分母换成日线自算（nt-2）后，主尺占比线有 **679 场**（2024-01-02~），
#: 这一道闸从"挡住 46 场被念成历史位置"变成"挡住备用尺那 47 场"—— 门槛本身不动。
MIN_SAMPLES_BAND = 120     # 分档切点：至少 120 个有效点
MIN_SAMPLES_JUMP = 20      # 逐期变动的 p95：至少 20 个变动（变动数是跨缺数配出来的，门槛低一档，
                           #   但阈值句必须把"这一条只有 N 个变动、哪年到哪年"写在脸上）
MIN_SAMPLES_FLOW = 60      # 净申赎的 p95：流量噪声大，样本底给高一点
#: 趋势线（均线 + zigzag 转折点）的样本底：2026-10-31 实测备用尺「占总市值」只有 47 场有数，
#: 对 47 个点取 MA10 后能确认的转折点是 0 个 —— 那种"没有拐点"是样本底不够，不是这段真的走得平，
#: 所以 60 场以下直接交「判不了」，界面上那句要把"只有几场"念出来。
MIN_SAMPLES_TREND = 60
#: 均线窗口（场），五条序列在 `SERIES[].trend` 里各自带 `{smooth, th}`（2026-10-31 都配了 10）。
#: 这个常量只供 `RULE` 那句措辞用，画面上读的永远是接口里的 `trend.smooth`。
TREND_SMOOTH_DEFAULT = 10

#: 异动阈值分位（写在这里一处，界面从 `thresholds` 读）
ANOMY_Q = 0.95

#: 提示条一次最多交几条（超出会在 `alert.truncated` 里报数，屏上那句会明写「另有 N 条没列出」）
ALERT_CAP = 40

DEFINITIONS = {
    'scope': '这一把尺量的是「被动沪深300 ETF 的份额/规模 ÷ 沪深300市值」，'
             '持有人是全部申购者，国家队只是其中一部分——所以它是代理指标：'
             '方向可以参考，绝对值不等于国家队持仓，也不构成买卖依据。',
    'numerator': '分子＝清单内各只 ETF 当日的合计份额/规模（两所官网每日份额文件，'
                 '经 akshare 转发；`market_data.etf_share` + `market_data.fund_daily`）。',
    'denominator': '分母＝`reference_data.sector_member` 的 `ZS_沪深300` 成分逐票当日流通市值求和，'
                   '每票流通市值＝当日未复权收盘 ×（成交量 ÷ 换手率）反推出来的流通股本'
                   '（2026-10-31 换的这把尺，写侧 `app/compute/national_team.py::denominator_daily`；'
                   '与旧尺 `stock_snapshot.float_market_value_yuan` 求和同日对账实测差 −0.186%~−0.256%）。'
                   '换尺前只用快照求和，而快照表最早 2026-07-22 ⇒ 679 场只有 47 场有占比。'
                   '备用尺（占**总**市值）仍然只从快照来：总股本不在日线里，换手率只覆盖流通股本。'
                   '东财 push2 那一发（`market_data.index_market_value`）只当外部对照，'
                   '因为这台机器对 push2 有频率封，不能让它当主尺。',
    'why_null': '占比为 null 只有两种：那一天成分日线一整条都没取到（`missing` 里是 '
                '`float_mv_yuan`）或有成分票反推不出流通股本（`float_mv_yuan.partial`，'
                '少算几只写在 `denom_covered`/`denom_members`）；再加份额源 T+1 还没落那一种。'
                '备用尺「占总市值」为 null 还多一层：那天没有收盘快照行（快照表最早 2026-07-22）。'
                '这些在界面上都读作 — 并写明是哪一种，不画点、不插值、不补 0。',
    'band_direction': '档位是相对量（这一把尺自己的历史分位），不是概率，也不是「贵/便宜」的裁定。'
                      '「超高」读作存量占比在自己历史里的高位（后续可增空间小、可减空间大），'
                      '「超低」读作低位——两个方向都只说明这一把尺走到哪儿，不预测指数。',
}

RULE = ('三档判法各司一件事，同一天的同一个动作只报一遍：'
        '① 两条占比线判「跳变」＝与上一个有效读数相比的变动 ≥ 这一条已有历史 |变动| 的 p95，'
        '并过 0.02pp 的绝对下限（占比走平时不把噪声报成异动）；'
        '② 净申赎判「极值」＝当日绝对值 ≥ 这把尺已有历史 |净申赎| 的 p95；'
        '③ 份额与规模既不判单日异动、也不给历史档位（趋势量取自己的分位没有读法，'
        '规模的单日大动多半是行情不是申赎）。'
        '历史档位只对两条占比线开放，且要 ≥' + str(MIN_SAMPLES_BAND) + ' 场才切'
        '（p10/p25/p75/p90 ⇒ 五档：超低/偏低/中性/偏高/超高）——够不够场数由每一条自己报'
        '（`series_view[].bands.samples` 与那句"只有几场、跨多久"），不够就明写判不了，'
        '不把三个月的分位念成历史位置。'
        '趋势线（粗的那条均线 + 顶/底标记）：均线取近 ' + str(TREND_SMOOTH_DEFAULT) + ' 场均值（不满 '
        + str(TREND_SMOOTH_DEFAULT) + ' 场不画），转折点只认均线上被反向走够门槛的那一个方向'
        '（门槛按每条自己的单位定，见 `trend.th`），末端还没被确认的那个极值一律不标。'
        '提示条按 (日期, 动作族) 合并成一个事件：两条占比线、两把尺各摆一个读数，但只算一件事。'
        '两把尺分别判、任一越界都提示，并点名是哪一把、哪一条线、越的是哪个阈值（不相减、不取平均）。')

UNITS = {'yi': '亿份（份额）／亿元（规模）——按序列各自的 unit 读', 'wy': '万亿元', 'pct': '%（百分数，不是小数）'}

#: 异动族：同一个动作的多条线合并成**一个事件**（2026-10-29 用户点名这一屏很差劲后收的）。
#: 两条占比线（流通市值／总市值）量的就是同一批 ETF 在同一天涨了占比，净申赎那两把尺
#: 也是同一批钱，只是分母与清单一窄一宽 —— 各报一遍会让读者把一天数成六天。
FAMILY_OF = {'ratio_float': 'ratio', 'ratio_total': 'ratio', 'flow': 'flow'}
FAMILY_LABEL = {'ratio': '占比单日跳变', 'flow': '净申赎极值', 'band': '当期走到这一条历史分位的两端'}
FAMILY_SERIES_LABEL = {'ratio': '两条占比线（占流通市值／占总市值）',
                       'flow': '当日净申赎（四大与全部各一条）',
                       'band': '当期档位'}

#: 提示那一排的**五格**里最后那一格（结果含义）只住这一处（用户 2026-11-01 点名：
#: 「这里还是不够清晰，要显示：指标名、时间、数值、与阈值的比较结果、结果含义（如异动提醒）」）。
#: 键＝后端判出来的 `kind`。类型名（异动提醒／当期档提醒）也在这一处，页面不许自己判"这算不算异动"。
KIND_ALERT = {
    'jump': ('异动提醒',
             '这一天占比相对上一场单日跳变，越过了这一条自身历史的 |变动| p95 ⇒ 判的是"这天有动作"，'
             '不是当天涨跌，也不是买卖指令'),
    'flow_spike': ('异动提醒',
                   '这一天净申赎的绝对值越过了这一把尺自身历史的 |净申赎| p95 ⇒ 一级市场当天真金白银在场外进／出'),
    'band': ('当期档提醒',
             '当期读数走到这一条自身历史的最高／最低档 ⇒ 说的是现在坐在哪个位置，不对应某一天'),
}


def _part_cells(p: dict) -> dict:
    """把一个读数拆成用户点名的那五格（时间＝事件那一行的 `date`，不在这里重复）。

    每一格都带单位、带方向、带"比的是哪一把尺的哪个分位"；页面只按格子摆，
    不拼句子、不换算、不自己截短名（短名 `compact` 与判据一样只住 `SERIES` 这一处）。
    """
    compact = SERIES_BY_KEY[p['series']].get('compact') or p['series_label']
    unit = SERIES_BY_KEY[p['series']].get('unit') or ''
    metric = f'{p["scale_short"]}·{compact}'
    kind = str(p.get('kind') or ('band' if p['family'] == 'band' else
                                 ('flow_spike' if p['family'] == 'flow' else 'jump')))
    type_name, meaning = KIND_ALERT.get(kind, ('提示', KIND_ALERT['jump'][1]))
    if p['family'] == 'band':
        cut = p.get('threshold')
        high = str(p.get('band_key')) == 'ultra_high'
        value_text = f'{_num(p.get("value"), 4)}{unit}（当期读数）'
        compare_text = ((f'{">" if high else "<"} 这一条自身的 {"p90" if high else "p10"} 切点'
                         f' {_num(cut, 4)}{unit}'
                         + f'（比切点{"高" if high else "低"} '
                           f'{abs(float(p["value"]) - float(cut)):.3f}pp）')
                        if cut is not None else
                        '这一条没建起历史档 ⇒ 说不出与哪个切点比（不是"刚好在档外"）')
    elif p['family'] == 'flow':
        v, thr = float(p['value']), float(p['threshold'] or 0.0)
        side = '申购侧' if v > 0 else '赎回侧'
        value_text = f'{v:+.2f} 亿份（当日净申赎，{side}）'
        compare_text = (f'|{v:.2f}| ≥ 这一把尺 |净申赎| 的 p{int(ANOMY_Q * 100)}＝{thr:.2f} 亿份'
                        f'（超 {abs(v) / thr:.1f} 倍）' if thr else
                        f'≥ 这一把尺 p{int(ANOMY_Q * 100)}（阈值算不出来 ⇒ 只能报读数）')
        meaning = f'{meaning}；今天这一个是{"进" if v > 0 else "出"}'
    else:
        d, v, thr = float(p['delta']), _num(p.get('value'), 4), float(p['threshold'] or 0.0)
        gap = f'，与上一场隔着 {p["gap"]} 场（中间那几场没有效读数）' if (p.get('gap') or 1) > 1 else ''
        value_text = f'{d:+.3f}pp（当日 {v}{unit}）{gap}'
        compare_text = (f'|{d:.3f}pp| ≥ 这一条 |变动| 的 p{int(ANOMY_Q * 100)}＝{thr:.3f}pp'
                        f'（超 {abs(d) / thr:.1f} 倍）' if thr else
                        f'≥ 这一条 p{int(ANOMY_Q * 100)}（阈值算不出来 ⇒ 只能报读数）')
    return {'metric': metric, 'value_text': value_text, 'compare_text': compare_text,
            'alert_type': type_name, 'meaning_text': meaning, 'kind': kind}


def _alert_part(p: dict, phrase: str | None = None, short: str | None = None) -> dict:
    """提示那一排的一行＝一个读数：原始数字 ＋ 整句 ＋ 用户 11-01 点名的五格（时间在这一行的 `date`）。

    五格的措辞全部来自 `_part_cells`（同一份判据），页面只做"哪一格摆哪一列"。
    """
    base = {k: p.get(k) for k in ('scale_key', 'scale_short', 'series', 'series_label',
                                  'value', 'delta', 'gap', 'threshold')}
    return {**base,
            'phrase': _part_phrase(p) if phrase is None else phrase,
            'phrase_short': _part_phrase_short(p) if short is None else short,
            **_part_cells(p)}


def _part_phrase(p: dict) -> str:
    """事件里那一个读数的措辞：点名是哪把尺、哪条线、动了多少、阈值是多少。"""
    if p['family'] == 'flow':
        return (f'{p["scale_short"]} {float(p["value"]):+.2f} 亿份'
                f'（≥ 这把尺 |净申赎| p{int(ANOMY_Q * 100)}＝{p["threshold"]:.2f}，'
                f'{"申购" if float(p["value"]) > 0 else "赎回"}侧）')
    gap = f'，跨 {p["gap"]} 场' if (p.get('gap') or 1) > 1 else ''
    return (f'{p["scale_short"]}·{p["series_label"]} {float(p["delta"]):+.3f}pp{gap}'
            f'（≥ 这一条 |变动| p{int(ANOMY_Q * 100)}＝{p["threshold"]:.3f}pp）')


def _part_phrase_short(p: dict) -> str:
    """同一句话的短版：摆在提示那一排的格子里（一行要放得下两把尺 × 两条线）。

    短名 `compact` 与整句一样**只住这份描述符**：页面自己截断「占沪深300流通市值」成"流通"
    就是第二份真相源，改列名时屏幕会悄悄念错。数字、单位、分位一个都没少，只是把
    "≥ 这一条 |变动| p95＝" 那种句子长度压成 "≥p95"。
    """
    compact = SERIES_BY_KEY[p['series']].get('compact') or p['series_label']
    if p['family'] == 'flow':
        return (f'{p["scale_short"]}·{compact} {float(p["value"]):+.2f} 亿份'
                f'（≥p{int(ANOMY_Q * 100)} {p["threshold"]:.2f}，'
                f'{"申购" if float(p["value"]) > 0 else "赎回"}侧）')
    gap = f'，跨 {p["gap"]} 场' if (p.get('gap') or 1) > 1 else ''
    return (f'{p["scale_short"]}·{compact} {float(p["delta"]):+.3f}pp{gap}'
            f'（≥p{int(ANOMY_Q * 100)} {p["threshold"]:.3f}pp）')


# ------------------------------------------------------------ 出口清洗（与其他读侧同一道闸）

def _num(v: Any, nd: int | None = None) -> Any:
    """NaN/NaT 必须变成 None：`pd.NaT` 是 datetime 的实例，日期判断排在前头会漏。"""
    if v is None:
        return None
    if isinstance(v, str):
        return v
    import pandas
    try:
        if pandas.isna(v):
            return None
    except (TypeError, ValueError):
        pass
    if isinstance(v, (dt.date, dt.datetime)):
        return v
    try:
        f = float(v)
    except (TypeError, ValueError):
        return str(v)
    if f != f or f in (float('inf'), float('-inf')):
        return None
    return round(f, nd) if nd is not None else f


def _rows(sql: str, params=()) -> list[dict]:
    df = pg.fetch_df(sql, params)
    return [] if df is None else [{k: _num(v) for k, v in row.items()} for row in df.to_dict('records')]


def _valid(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(float(v))


def _pctl(vals: list, q: float) -> float | None:
    """线性插值分位（与 `percentile_cont` 同一把尺）。空样本 ⇒ None（判不了，不补 0）。"""
    v = sorted(float(x) for x in vals if _valid(x))
    if not v:
        return None
    if len(v) == 1:
        return round(v[0], 4)
    pos = q * (len(v) - 1)
    lo = math.floor(pos)
    hi = min(lo + 1, len(v) - 1)
    return round(v[lo] + (v[hi] - v[lo]) * (pos - lo), 4)


def _iso_days(dates: list) -> list[str]:
    """只留下真能按 ISO 解析的日期（升序去重）。

    读侧的日期来自 `trade_date::text`，正常都是 ISO；但单测会塞占位串，
    跨度这句话不能因为一个解析不了的样本就整句炸掉（炸掉＝这一条判据从屏上消失，
    而"消失"会被读成"没有异动"，那是本项目登记过的那类假读数）。
    """
    out = []
    for x in dates or []:
        s = str(x)[:10]
        try:
            dt.date.fromisoformat(s)
        except (TypeError, ValueError):
            continue
        out.append(s)
    return sorted(set(out))


def _span_note(dates: list, n: int | None = None) -> str:
    """把「这些样本跨多久、一共几个」写成一句能念的话。

    2026-10-29 用户点名这一屏很差劲之后加的：判据的分位数**必须自带跨度**，否则
    「p95」三个字会被读成历史高位，而它实际可能只是最近三个月里第二大那个数。
    """
    d = _iso_days(dates)
    cnt = n if n is not None else len(d)
    if not d:
        return f'{cnt} 个样本，日期跨度认不出来'
    if len(d) == 1:
        return f'只有 1 场（{d[0]}）'
    days = (dt.date.fromisoformat(d[-1]) - dt.date.fromisoformat(d[0])).days
    span = f'{days} 天 ≈ {days / 30.44:.1f} 个月' if days >= 62 else f'{days} 天'
    # 变动数比读数日少一个（首点没有"与上一场的差"），两个数都念出来才对得上账
    cnt_part = f'{cnt} 个样本' if cnt == len(d) else f'{cnt} 个样本（{len(d)} 个读数日）'
    return f'{cnt_part}，日期 {d[0]} ~ {d[-1]}（跨 {span}）'


def _month_span(dates: list) -> str:
    """只回跨度那一段（用在「只有 N 场」那种句子开头）。"""
    d = _iso_days(dates)
    if len(d) < 2:
        return ''
    days = (dt.date.fromisoformat(d[-1]) - dt.date.fromisoformat(d[0])).days
    return f'，跨 {days} 天' + (f' ≈ {days / 30.44:.1f} 个月' if days >= 62 else '')


# ------------------------------------------------------------ 纯判据（不碰库，单测直接打这些）

def trend_of(full: list[dict], win: list[dict], spec: dict) -> dict:
    """这一条序列的**趋势线**：近 `smooth` 场均线（画在图上那条粗线）+ 已确认的顶/底（转折点）。

    2026-10-31 用户点名「图表做的连贯一点，然后明确一些转折点」，判据形状沿用「风险提示」那一屏
    定过的三条（实现共用 `duanxian/trend.py`，两处不会各算出一套拐点）：
     · **样本底永远是整段可用历史**，窗口只决定画哪一段 —— 与这一屏的分档/异动同一口径，
       否则切一次时间档，同一个顶就换了地方；
     · **均线不满 `smooth` 场不画**（交 null，不拿现有几期硬凑成一条看着连贯的线）；
     · **末端那个还没被反向走够 `th` 的极值不交** —— 交出去就是把最后一根抖动说成拐点。
    门槛 `th` 的单位就是这条序列自己的单位（占比是 pp、份额/规模/净申赎是亿份/亿元），
    五个值全部是 2026-10-31 在 nt-2 全史 679 场上实测出来的（写侧文档 §3 记了每个门槛出几个拐点），
    不是抄来的外部经验值。
    """
    cfg = spec.get('trend') or {}
    k, th = cfg.get('smooth'), cfg.get('th')
    f = spec['field']
    vals = [p.get(f) for p in full]
    samples = sum(1 for v in vals if _valid(v))
    base = {'smooth': k, 'th': th, 'unit': spec['unit'], 'samples': samples,
            'ma': [], 'rows': []}
    if not k or not _valid(th):
        return {**base, 'available': False,
                'reason': '这一条序列在读侧没配趋势线参数（`SERIES[].trend`）⇒ 页面不自己定窗口和门槛'}
    if samples < MIN_SAMPLES_TREND:
        return {**base, 'available': False,
                'reason': f'这一条只有 {samples} 场有读数，不满 {MIN_SAMPLES_TREND} 场 ⇒ 均线与转折点都不画 —— '
                          '样本这么短的拐点是噪声，"这一段没有拐点"是判不了，不是走得平'}
    ma_full = moving_average(vals, int(k))
    by_date = {str(p['trade_date']): (None if not _valid(m) else float(m))
               for p, m in zip(full, ma_full)}
    dates = [str(p['trade_date']) for p in full]
    turns = [{'i': t['i'], 'kind': t['kind'], 'value': t['value'], 'date': dates[t['i']]}
             for t in zigzag_turns(ma_full, float(th))]
    in_win = [t for t in turns if win and t['date'] <= str(win[-1]['trade_date'])
              and t['date'] >= str(win[0]['trade_date'])]
    rows = []
    prev = None
    for t in in_win:
        gap = (None if prev is None else round(abs(t['value'] - prev['value']), 3))
        rows.append({'date': t['date'], 'kind': t['kind'], 'value': t['value'],
                     'prev_date': prev['date'] if prev else None,
                     'prev_value': prev['value'] if prev else None, 'step': gap,
                     'text': ('均线顶 ' if t['kind'] == 'peak' else '均线底 ')
                             + f'{t["date"]}（近 {k} 场均值 {t["value"]:g}{spec["unit"]}，'
                             + (f'与前一个转折点相差 {gap:g}{spec["unit"]}' if prev else
                                '这一段里第一个被确认的极值') + '）'}
                    )
        prev = t
    return {**base, 'available': True,
            'ma': [by_date.get(str(p['trade_date'])) for p in win],
            'rows': rows,
            'th_note': f'均线近 {k} 场；只认均线上被反向走够 {th:g}{spec["unit"]} 的反转'
                       + (f'（整段 {samples} 场里共确认 {len(turns)} 个，本窗口内 {len(rows)} 个）'),
            'empty_note': (None if rows else
                           f'这一段窗口里没有已确认的转折点（均线近 {k} 场、反向要够 {th:g}{spec["unit"]}）'
                           '—— 可能是这段确实走得平，也可能是末端那个极值还没被反向走够（未确认的一律不标）。')}


def band_of(value: Any, cuts: dict | None) -> tuple[str | None, str | None]:
    """当期值落进哪一档。cuts 缺（样本不足）或值缺 ⇒ (None, None)：界面 `—`，不许落到「中性」。"""
    if not cuts or not _valid(value):
        return None, None
    v = float(value)
    if v <= cuts['p10']:
        k = 'ultra_low'
    elif v <= cuts['p25']:
        k = 'low'
    elif v <= cuts['p75']:
        k = 'mid'
    elif v <= cuts['p90']:
        k = 'high'
    else:
        k = 'ultra_high'
    return k, BAND_LABELS[k]


def deltas(values: list) -> list:
    """逐期与「上一个有效读数」的差；空位与首点交 None。

    有效读数之间可能隔着缺数（占比那条线就是这样），所以这里**不要求相邻场次**——
    跨了几场由 `delta_gaps` 一起交出来，异动那句要能说出「这是隔了 N 场的变动」。
    """
    out: list[Any] = []
    last: Any = None
    for v in values:
        if not _valid(v):
            out.append(None)
            continue
        out.append(None if last is None else round(float(v) - last, 6))
        last = v
    return out


def delta_gaps(points: list[dict], field: str) -> list:
    """每个变动跨了几场（含首尾那两场之间的空档）。首点与缺数点交 None。"""
    idx = [i for i, p in enumerate(points) if _valid(p.get(field))]
    out: list[Any] = [None] * len(points)
    for a, b in zip(idx, idx[1:]):
        out[b] = b - a
    return out


def anomalies_of(points: list[dict], spec: dict, flow_ref: float | None) -> tuple[list[dict], dict]:
    """一条序列的异动名单 + 这一条的判断依据（阈值、样本数、**阈值样本跨多久**，随接口交出）。

    三档判法各司一件事，同一天的同一个动作不许报两遍：
    · `jump`（两条占比线）＝与上一个有效读数相比的变动越过这一条已有历史的 |变动| p95，
      并过一条绝对下限（占比走平时别把噪声报成异动）；
    · `level`（净申赎）＝当日绝对值越过这把尺已有历史的 |净申赎| p95；
    · `none`（份额、规模）＝既不判单日异动、也不给档位（份额当日变动就是净申赎那一条，
      规模则被行情推着走 —— 见 `SERIES` 里的 `band_skip`）。

    2026-10-29 起每一句阈值后面都挂一段「阈值样本：N 个、哪年到哪年、跨几个月」。
    起因是实测：占比线只有 46 场（2026-07-22 起），它的 p95 实际是三个月里第三大的数，
    而原句写「全史 p95」——读者据此把它当成历史高位，那就是假读数。
    """
    field = spec['field']
    judge = spec.get('judge') or ('level' if spec['kind'] == 'flow' else 'jump')
    hits: list[dict] = []
    meta = {'series': spec['key'], 'judge': judge, 'threshold': None,
            'samples': 0, 'span_note': None, 'rule': None}
    vals = [p.get(field) for p in points]
    dated = [p['trade_date'] for p in points if _valid(p.get(field))]

    if judge == 'none':
        return hits, meta

    if judge == 'level':
        abss = [abs(float(v)) for v in vals if _valid(v)]
        meta['samples'] = len(abss)
        meta['span_note'] = _span_note(dated, len(abss))
        if flow_ref is None or len(abss) < MIN_SAMPLES_FLOW:
            meta['rule'] = (f'有效读数只有 {len(abss)} 场（{dated[0] if dated else "—"} ~ '
                            f'{dated[-1] if dated else "—"}），判异动要 ≥{MIN_SAMPLES_FLOW} ⇒ 这一条不判，'
                            '界面按「判不了」读，不说成「这段没有大额申赎」')
            return hits, meta
        meta['threshold'] = flow_ref
        for p in points:
            v = p.get(field)
            if _valid(v) and abs(float(v)) >= flow_ref:
                hits.append({'date': p['trade_date'], 'value': round(float(v), 4),
                             'threshold': flow_ref, 'kind': 'flow_spike',
                             'gap': None, 'span_note': meta['span_note'],
                             'text': f'{p["trade_date"]} 净申赎 {float(v):+.2f} 亿份，'
                                     f'绝对值 ≥ 这一把尺 |净申赎| 的 p{int(ANOMY_Q * 100)}'
                                     f'（{flow_ref:.2f}）——{"申购" if v > 0 else "赎回"}侧'
                                     f'｜阈值样本：{meta["span_note"]}'})
        return hits, meta

    diffs = deltas(vals)
    gaps = delta_gaps(points, field)
    usable = [abs(d) for d in diffs if _valid(d)]
    meta['samples'] = len(usable)
    meta['span_note'] = _span_note(dated, len(usable))
    floor = float(spec.get('floor_pp') or 0.0)
    if not usable or len(usable) < MIN_SAMPLES_JUMP:
        meta['rule'] = (f'有效变动只有 {len(usable)} 个（判异动要 ≥{MIN_SAMPLES_JUMP}）⇒ 这一条不判，'
                        '界面按「判不了」读')
        return hits, meta
    thr = _pctl(usable, ANOMY_Q)
    if thr is None:
        meta['rule'] = '变动样本全空 ⇒ 这一条不判'
        return hits, meta
    meta['threshold'] = thr
    # 跨度不够时这句尾巴要自己说破：46 场的 p95 不是"历史位置"（2026-10-29 复核出来的那条误读）
    thin = (f' —— 这一条只有 {meta["samples"]} 个变动，这个 p95 说的不是历史位置'
            if len(usable) < MIN_SAMPLES_BAND else '')
    for p, d, g in zip(points, diffs, gaps):
        if not _valid(d) or abs(float(d)) < thr or abs(float(d)) < floor:
            continue
        prev = p.get('_prev')
        span = f'（跨 {g} 场，中间那几场没有有效读数）' if (g or 1) > 1 else ''
        hits.append({'date': p['trade_date'], 'value': _num(p.get(field), 4),
                     'delta': round(float(d), 4),
                     'delta_pct': (round(100.0 * float(d) / float(prev), 3)
                                   if (_valid(prev) and prev) else None),
                     'gap': g, 'threshold': thr, 'kind': 'jump', 'span_note': meta['span_note'],
                     'text': f'{p["trade_date"]} {spec["label"]}{float(d):+.3f}pp{span}，'
                             f'绝对值 ≥ 这一条 |变动| 的 p{int(ANOMY_Q * 100)}（{thr:.3f}pp）'
                             f'｜阈值样本：{meta["span_note"]}{thin}'})
    return hits, meta


def bands_of(points: list[dict], spec: dict) -> dict:
    """一条序列的历史分档切点（同一把尺、整段可用历史、只看有值的那些点）。样本不足 ⇒ available:false。

    2026-10-29 起两道闸叠在一起，因为这一屏被点名「很差劲」的根因就在档位那一格：
    · 份额／规模是趋势量 ⇒ `bandable` 为假，整条**不建档**（屏上读那句为什么）；
    · 占比线本机只有 46 场 ⇒ 120 场的门槛把它挡在门外，`reason` 把"只有 N 场、跨多久"顶在最前。
    """
    field = spec['field']
    dated = [(str(p['trade_date'])[:10], p.get(field)) for p in points if _valid(p.get(field))]
    vals = [v for _d, v in dated]
    if not spec.get('bandable', True):
        return {'available': False, 'not_applicable': True, 'samples': len(vals),
                'reason': spec.get('band_skip') or '这一条是趋势量，对它取自己历史的分位没有读法'}
    if len(vals) < MIN_SAMPLES_BAND:
        return {'available': False, 'samples': len(vals),
                'from': dated[0][0] if dated else None, 'to': dated[-1][0] if dated else None,
                'reason': f'只有 {len(vals)} 个有效读数（{dated[0][0] if dated else "—"} ~ '
                          f'{dated[-1][0] if dated else "—"}{_month_span([d for d, _v in dated])}），'
                          f'分档要 ≥{MIN_SAMPLES_BAND} 场 ⇒ 没有临界线。'
                          '这不是「历史都一个样」，更不是「现在就是高位」—— 是跨度不够分不出档，'
                          '所以这一格不许念成「这把尺自己的历史分位」。'}
    cuts = {'p10': _pctl(vals, 0.10), 'p25': _pctl(vals, 0.25),
            'p50': _pctl(vals, 0.50), 'p75': _pctl(vals, 0.75), 'p90': _pctl(vals, 0.90)}
    return {'available': True, 'samples': len(vals), 'cuts': cuts,
            'from': dated[0][0], 'to': dated[-1][0],
            'span_note': _span_note([d for d, _v in dated], len(vals)),
            'labels': dict(BAND_LABELS)}


# ------------------------------------------------------------ 取数与归一

_SQL = f"""
select trade_date::text as trade_date, scale_key, etf_count::int                     as etf_count,
       share_units / 1e8                                   as share_yi,
       size_yuan / 1e8                                     as size_yi,
       net_creation_units / 1e8                            as net_yi,
       float_mv_yuan / 1e12                                as float_mv_wy,
       ratio_float_pct, ratio_total_pct, missing,
       denom_source, denom_covered::int, denom_members::int
  from {TABLE}
 where spec_version = %s
 order by scale_key, trade_date
"""


def _split_missing(raw: Any) -> list[str]:
    if not isinstance(raw, str) or not raw:
        return []
    return [x for x in raw.split(',') if x]


def _points(rows: list[dict], field: str | None = None) -> list[dict]:
    """一把尺的点列；给了 `field` 就顺手补 `_prev`（该序列的上一个有值读数）。

    `_prev` 只在算变动时用，交出去前会摘掉——屏幕上不读它，留着只会被当成一列真数。
    """
    pts = []
    for r in rows:
        p = {k: v for k, v in r.items() if k != 'scale_key'}
        p['missing'] = _split_missing(p.get('missing'))
        # 只数个数的一列要交整数：出口那道闸会把所有数升成 float，屏幕上"4.0 只"是要不得的
        if _valid(p.get('etf_count')):
            p['etf_count'] = int(float(p['etf_count']))
        pts.append(p)
    if field is None:
        return pts
    last: Any = None
    for p in pts:
        p['_prev'] = last
        if _valid(p.get(field)):
            last = p[field]
    return pts


def _months_of(range_key: str) -> tuple[str, int | None, str, str | None]:
    for k, label, months in RANGES:
        if k == range_key:
            return k, months, label, None
    return '1y', 12, '近 1 年', (f'时间档只认 {"、".join(k for k, _l, _m in RANGES)}，'
                                 f'收到的是「{range_key}」——已退回 近 1 年')


def panel(range: Any = '1y', date: Any = '') -> dict:
    """复盘页顶部那一屏的全部读数。

    · `range` 只认 RANGES 那五档，非法值退回 1y 并写进 caveats；
    · 库不通 / 表里一行都没有 ⇒ `{available:false, reason}`，**不带 `scales` 键**；
    · 判据用的样本底永远是**整段可用历史**（不是当前窗口）——「历史临界」按历史算，
      窗口只决定画哪儿、异动列点名哪几天。
    """
    caveats: list[dict] = []
    range_key, months, range_label, why = _months_of(str(range or ''))
    if why:
        caveats.append({'code': 'param_range', 'text': why})

    if not pg.enabled():
        return {'available': False,
                'reason': '本地库连不上（VIBE_PG / PG_USER / PG_PASSWORD 没配或没起来）—— '
                          '这是「库不通」，不是「市场没有 ETF 份额数据」。'}

    try:
        rows = _rows(_SQL, (SPEC,))
    except Exception as exc:  # noqa: BLE001
        return {'available': False,
                'reason': f'本地库读不到（查询失败：{type(exc).__name__}）—— '
                          '这是「库不通」，不是「没有份额行」。'}
    if not rows:
        return {'available': False,
                'reason': f'`{TABLE}` 里还没有一行（spec `{SPEC}`）—— 加工链的 `national_team` 步还没跑过。'
                          '先跑：`python -m app.compute.national_team --start 20240101 --end <YYYYMMDD>`。'}

    anchor_txt, why = _norm_date(date)
    if why:
        caveats.append({'code': 'param_date', 'text': why})
    all_dates = sorted({r['trade_date'] for r in rows})
    anchor = anchor_txt or all_dates[-1]
    if months is None:
        win_from = all_dates[0]
    else:
        win_from = (_month_before(anchor, months)).isoformat()
    win_dates = [d for d in all_dates if win_from <= d <= anchor]
    if not win_dates:
        return {'available': False, 'anchor': anchor, 'range': range,
                'reason': f'窗口 {win_from} ~ {anchor} 内一行都没有；库里最近一场是 {all_dates[-1]} —— '
                          '差的场次没算过，补 `python -m app.compute.national_team '
                          '--start <YYYYMMDD> --end <YYYYMMDD>`。'}

    out_scales: list[dict] = []
    raw: list[dict] = []      # 每一条 (尺 × 序列 × 日) 的原始命中；提示条按事件合并后才交出去
    for sc in SCALES:
        srows = [r for r in rows if r['scale_key'] == sc['key']]
        full = _points(srows)
        win = [p for p in full if win_from <= p['trade_date'] <= anchor]
        # 判据样本底 = 整段可用历史（不是窗口）；点列只交窗口内的，异动按窗口点名
        per: dict[str, dict] = {}
        for spec in SERIES:
            fpts = _points(srows, spec['field'])
            flow_ref = None
            if spec['kind'] == 'flow':
                abss = [abs(float(p[spec['field']])) for p in fpts if _valid(p.get(spec['field']))]
                flow_ref = _pctl(abss, ANOMY_Q) if len(abss) >= MIN_SAMPLES_FLOW else None
            hits, meta = anomalies_of(fpts, spec, flow_ref)
            # 分档只对"存量类"序列算，且 2026-10-29 起份额/规模也整条不建档（趋势量取分位没有读法）：
            # 净申赎是有正负的量级，对它取分位等于把"赎得狠"和"买得狠"排进同一条尺（p10 全是负、
            # p90 全是正），那种"档"没有读法；它用的是 ±p95 那条例外线。
            b = (bands_of(fpts, spec) if spec['stock'] else
                 {'available': False, 'not_applicable': True,
                  'samples': sum(1 for p in fpts if _valid(p.get(spec['field']))),
                  'reason': '净申赎有正负，历史分位档对它不成立 —— 这条只看 ±p95 的异动线'})
            in_win = [h for h in hits if win_from <= h['date'] <= anchor]
            latest_val = next((p[spec['field']] for p in reversed(fpts)
                               if _valid(p.get(spec['field']))), None)
            bk, bl = band_of(latest_val, b.get('cuts'))
            # 当期走到这把尺历史的最高/最低档，是给「异动那天」补的读法，也是唯一会单独出声的档
            band_note = (f'同时当期落在「{bl}」档（这一条 {b.get("span_note") or f"{b.get('samples')} 场"} 的分位）'
                         if spec['stock'] and bk in ('ultra_low', 'ultra_high') else '')
            short = sc['label'].split('（')[0]
            for h in in_win:
                raw.append({'scale_key': sc['key'], 'scale_short': short,
                            'series': spec['key'], 'series_label': spec['label'],
                            'family': FAMILY_OF.get(spec['key'], spec['key']),
                            'kind': h['kind'], 'band_note': band_note,
                            'date': h['date'], 'value': h['value'], 'delta': h.get('delta'),
                            'gap': h.get('gap'), 'threshold': h['threshold'],
                            'span_note': h.get('span_note'), 'raw_text': h['text']})
            per[spec['key']] = {'series': spec, 'bands': b,
                                'current': _num(latest_val, 4), 'band_key': bk, 'band_label': bl,
                                'anomaly_meta': meta,
                                'anomalies': in_win,
                                'anomaly_count_all': len(hits), 'anomaly_count_window': len(in_win),
                                # 趋势线（均线 + 已确认顶/底）：判据与措辞都在 `trend_of` 一处
                                'trend': trend_of(fpts, win, spec)}
        share_dates = sorted(p['trade_date'] for p in full if _valid(p.get('share_yi')))
        ratio_dates = sorted(p['trade_date'] for p in full if _valid(p.get('ratio_float_pct')))
        ratio_total_dates = sorted(p['trade_date'] for p in full if _valid(p.get('ratio_total_pct')))
        out_scales.append({
            'key': sc['key'], 'label': sc['label'], 'short': sc['label'].split('（')[0],
            'note': sc['note'],
            'etf_count_latest': win[-1].get('etf_count') if win else None,
            'share_sessions': len(share_dates),
            'share_from': share_dates[0] if share_dates else None,
            'share_to': share_dates[-1] if share_dates else None,
            'ratio_sessions': len(ratio_dates),
            'ratio_from': ratio_dates[0] if ratio_dates else None,
            'ratio_total_sessions': len(ratio_total_dates),
            'ratio_total_from': ratio_total_dates[0] if ratio_total_dates else None,
            'points': win,
            'series_view': per,
        })

    # ── 提示条按**事件**合并，不按 (尺 × 序列 × 日) 各报一遍 ──────────────────────
    # 2026-10-29 实测：10-23 那一天同一件事在清单里占 6 行（两把尺 × 两条占比线，再加净申赎），
    # 而 `count_all` 把"条目数"当"异动数"念 ⇒ 读者按行数估事件数必然高估好几倍。
    # 合并键＝(日期, 动作族)：占比跳变把两条线并成一句、净申赎极值把两把尺并成一句。
    events: dict[tuple, dict] = {}
    for r in raw:
        key = (r['date'], r['family'])
        e = events.get(key)
        if e is None:
            e = events[key] = {'date': r['date'], 'family': r['family'],
                               'family_label': FAMILY_LABEL[r['family']],
                               'series': r['family'],
                               'series_label': FAMILY_SERIES_LABEL[r['family']],
                               'kind': r['kind'], 'parts': [], 'scale_keys': [], 'scale_shorts': [],
                               'span_notes': [], 'band_notes': []}
        e['parts'].append(r)
        if r['scale_key'] not in e['scale_keys']:
            e['scale_keys'].append(r['scale_key'])
            e['scale_shorts'].append(r['scale_short'])
        for note, bucket in ((r.get('span_note'), e['span_notes']),
                             (r.get('band_note'), e['band_notes'])):
            if note and note not in bucket:
                bucket.append(note)

    items: list[dict] = []
    for e in events.values():
        parts = sorted(e['parts'], key=lambda p: (p['scale_key'], p['series']))
        dstr, fam_label = e['date'], e['family_label']
        head = (f'{dstr} {fam_label}——{len(parts)} 个读数说的是同一件事，'
                f'只列一遍（两把尺不相减、两条占比线不换分母相减）：')
        body = '，'.join(_part_phrase(p) for p in parts)
        tail = ('｜阈值样本：' + '；'.join(e['span_notes'])) if e['span_notes'] else ''
        rep = max(parts, key=lambda p: abs(p.get('delta') if e['family'] == 'ratio' else p['value']))
        items.append({'date': e['date'], 'family': e['family'], 'family_label': e['family_label'],
                      'scale_key': '+'.join(e['scale_keys']),
                      'scale_keys': list(e['scale_keys']),
                      'scale_label': '＋'.join(e['scale_shorts']),
                      'series': e['family'], 'series_label': e['series_label'],
                      'kind': e['kind'], 'value': rep['value'], 'delta': rep.get('delta'),
                      'threshold': rep['threshold'], 'parts_count': len(parts),
                      'parts': [_alert_part(p) for p in parts],
                      # 提醒类型（异动提醒／当期档提醒）取代表读数那一格，与格子同源
                      'alert_type': _part_cells(rep)['alert_type'],
                      # 2026-10-31 用户点名「整体你好好检查，我感觉效果不佳」：整句留给抽屉与朗读，
                      # 屏上按 head/无那串复读的阈值样本摆一张表 —— `tail` 是同一条序列每个事件都
                      # 一模一样的一句话，逐条复读八遍就是那 800 像素的黄字。
                      'head': head, 'body': body, 'tail': tail,
                      'text': head + body + tail + ('；' + '；'.join(e['band_notes'])
                                                    if e['band_notes'] else '')})

    # 当期档（不走异动那条路的：只落在超高/超低档、但窗口里没异动）也要出声。
    # 每个 (尺 × 序列) 只会出现一次，所以这里不需要再和上面那份去重。
    for s in out_scales:
        for key, v in s['series_view'].items():
            if v['band_key'] in ('ultra_low', 'ultra_high'):
                phrase = (f'{s["short"]}·{SERIES_BY_KEY[key]["label"]} {v["current"]} '
                          f'落在「{v["band_label"]}」档（这一条的分位，'
                          f'样本 {v["bands"].get("samples")} 场，{v["bands"].get("span_note") or "跨度未交"}）')
                short = (f'{s["short"]}·{SERIES_BY_KEY[key].get("compact") or SERIES_BY_KEY[key]["label"]} '
                         f'{v["current"]} 落在「{v["band_label"]}」档')
                cut = (v['bands'].get('cuts') or {}).get(
                    'p10' if v['band_key'] == 'ultra_low' else 'p90')
                # 当期档那一行也走同一套五格（`threshold`／`band_key`／`band_label` 一起带进 _part_*，
                # 否则这一族在屏上就只能念整句，与异动那两族长成两个样子）
                bpart = {'scale_key': s['key'], 'scale_short': s['short'], 'series': key,
                         'series_label': SERIES_BY_KEY[key]['label'], 'family': 'band',
                         'kind': 'band', 'value': v['current'], 'delta': None, 'gap': None,
                         'threshold': cut, 'band_key': v['band_key'], 'band_label': v['band_label']}
                cells = _part_cells(bpart)
                items.append({'date': None, 'family': 'band',
                              'family_label': FAMILY_LABEL['band'],
                              'scale_key': s['key'], 'scale_keys': [s['key']],
                              'scale_label': s['short'], 'series': key,
                              'series_label': SERIES_BY_KEY[key]['label'],
                              'kind': 'band', 'value': v['current'], 'threshold': cut,
                              'alert_type': cells['alert_type'],
                              'parts': [_alert_part(bpart, phrase, short)],
                              'parts_count': 1,
                              'head': f'当期档（不对应某一天）——{s["short"]}·{SERIES_BY_KEY[key]["label"]}：',
                              'body': phrase, 'tail': '',
                              'text': f'当期档：{phrase}'})

    items.sort(key=lambda i: (i['date'] or '', i['scale_key'], i['series']), reverse=True)
    shown = items[:ALERT_CAP]
    if items:
        alert_text = (f'两把尺里至少有一条越界（异动，或当期走到这一条历史分位的最高/最低档）——'
                      f'合并后共 {len(items)} 个事件（原始条目 {len(raw)} 条：两把尺 × 两条占比线，'
                      f'同一天同一个动作只报一遍），按日期从新到旧列前 {len(shown)} 个'
                      + (f'（另有 {len(items) - len(shown)} 个没列出，图上每一颗菱形都是一个）'
                         if len(items) > len(shown) else '')
                      + '。任一越界都提示，两把尺不相减、不取平均。')
    else:
        alert_text = ('两把尺在本窗口内都没有越界读数。注意：这不等于「国家队没有动作」—— '
                      '这一条只认"单日跳变/极值过了这一把尺自己的 p95"那一种形状，'
                      '慢慢累积上去或一点点赎掉的，它不会响；那种要看图上那条粗均线与顶/底标记（趋势线是另一把尺）。')
    alert = {'fired': bool(items), 'count_all': len(items), 'raw_entries': len(raw),
             'items': shown, 'truncated': max(0, len(items) - len(shown)), 'text': alert_text}

    denom = _coverage_dates(rows)
    if not denom['ratio_sessions']:
        caveats.append({'code': 'denominator_missing', 'hard': True,
                        'text': '整段区间里一个占比都算不出来 —— 分母（当日日线反推的 300 成分流通市值）'
                                '在这一段一天都没求出来。这是「日线/换手率没到这段」，不是「占比等于 0」。'})
    if denom['ratio_sessions'] < denom['share_sessions']:
        caveats.append({'code': 'ratio_shorter_than_share', 'hard': True,
                        'text': f'份额线有 {denom["share_sessions"]} 场（{denom["share_from"]} 起），'
                                f'占比只有 {denom["ratio_sessions"]} 场（{denom["ratio_from"]} 起）——'
                                '差的不是缺采，是那一天求不出分母（成分日线整段没取到）。'
                                '所以早期那一档要看份额与净申赎那两条线，'
                                '占比线在断掉的那一段就是断的（界面上读作 —），不插值、不拿指数涨跌反推。'})
    # 备用尺（占沪深300总市值）的分母仍然只从 `stock_snapshot` 来 —— 那把尺的覆盖必然短，
    # 必须单独说一句，否则读者切到那一条会以为"这一把尺也是断的/没接上"。
    if denom['ratio_total_sessions'] and denom['ratio_total_sessions'] < denom['ratio_sessions']:
        caveats.append({'code': 'ratio_total_shorter', 'hard': True,
                        'text': f'备用尺「占沪深300总市值」只有 {denom["ratio_total_sessions"]} 场'
                                f'（{denom["ratio_total_from"]} ~ {denom["ratio_total_to"]}），'
                                f'比主尺的 {denom["ratio_sessions"]} 场短一大截 —— '
                                '因为总股本不在日线里（换手率只覆盖流通股本），这一条的分母仍然只能取快照。'
                                '它照旧只当对照，历史档位与趋势线对它多半读作「判不了」。'})
    if denom['lag_note']:
        caveats.append({'code': denom['lag_note'][0], 'text': denom['lag_note'][1]})
    for s in out_scales:
        for key, v in s['series_view'].items():
            if not v['bands']['available'] and not v['bands'].get('not_applicable'):
                caveats.append({'code': 'band_unjudged',
                                'text': f'{s["short"]}·{SERIES_BY_KEY[key]["label"]}：'
                                        f'{v["bands"]["reason"]}'})
            if v['anomaly_meta'].get('rule'):
                caveats.append({'code': 'anomaly_unjudged',
                                'text': f'{s["short"]}·{SERIES_BY_KEY[key]["label"]}：'
                                        f'{v["anomaly_meta"]["rule"]}'})
        missing_days = sum(1 for p in s['points'] if p.get('missing'))
        if missing_days:
            caveats.append({'code': 'partial_missing',
                            'text': f'{s["short"]}在本窗口有 {missing_days} 场带着缺数标记 —— '
                                    '那一句会说清是哪一种：那天没有收盘快照行（占比算不出）、'
                                    '成分里有票当天没快照（分母偏小）、或清单里有成员没收盘价（规模偏小）。'
                                    '缺数那一格读作 —，不是 0。'})

    return {
        'available': True,
        'spec': SPEC,
        'table': TABLE,
        'range': range_key,
        'range_label': range_label,
        'range_options': [{'key': k, 'label': l, 'months': m} for k, l, m in RANGES],
        'series_options': [{k: s[k] for k in ('key', 'label', 'unit', 'field', 'kind', 'judge',
                                              'stock', 'read')} for s in SERIES],
        'anchor': anchor,
        'window': {'from': win_dates[0], 'to': win_dates[-1],
                   'requested_from': win_from, 'sessions': len(win_dates)},
        'coverage': denom,
        'thresholds': {'anomy_q': ANOMY_Q, 'band_cuts': {k: v for k, v, _l in BAND_CUTS},
                       'min_samples': {'band': MIN_SAMPLES_BAND, 'jump': MIN_SAMPLES_JUMP,
                                       'flow': MIN_SAMPLES_FLOW, 'trend': MIN_SAMPLES_TREND},
                       'floor_pp': 0.02, 'floor_pct': 1.0},
        'levels': dict(BAND_LABELS),
        'rule': RULE,
        'definitions': dict(DEFINITIONS),
        'units': dict(UNITS),
        'scales': out_scales,
        'alert': alert,
        'generated_at': dt.datetime.now(dt.timezone.utc).astimezone().isoformat(timespec='seconds'),
        'caveats': caveats,
    }


# ------------------------------------------------------------ 参数归一与小工具

def _norm_date(raw: Any) -> tuple[str | None, str | None]:
    s = str(raw or '').strip()
    if not s:
        return None, None
    try:
        d = dt.date.fromisoformat(s[:10])
    except ValueError:
        return None, f'锚定日期要写成 YYYY-MM-DD，收到的是「{s}」——已退回库里最晚一场'
    return d.isoformat(), None


def _month_before(day_iso: str, months: int) -> dt.date:
    d = dt.date.fromisoformat(str(day_iso)[:10])
    y, m = d.year, d.month - months
    while m <= 0:
        m += 12
        y -= 1
    try:
        return d.replace(year=y, month=m)
    except ValueError:          # 31 日落到没有 31 日的月份
        return d.replace(year=y, month=m, day=28)


def _coverage_dates(rows: list[dict]) -> dict:
    share = sorted(r['trade_date'] for r in rows if _valid(r.get('share_yi')))
    ratio = sorted(r['trade_date'] for r in rows if _valid(r.get('ratio_float_pct')))
    ratio_total = sorted(r['trade_date'] for r in rows if _valid(r.get('ratio_total_pct')))
    denom = sorted(r['trade_date'] for r in rows if _valid(r.get('float_mv_wy')))
    lag = None
    if share and ratio and share[-1] > ratio[-1]:
        lag = ('share_lag',
               f'份额源比市值线晚：份额最近一场 {share[-1]}，占比只算到 {ratio[-1]} —— '
               '最新那一天是「还没到」，不是「没有」；等 `etf` 与 `national_team` 两步补上就会长出来。')
    return {'share_sessions': len(set(share)), 'share_from': share[0] if share else None,
            'share_to': share[-1] if share else None,
            'ratio_sessions': len(set(ratio)), 'ratio_from': ratio[0] if ratio else None,
            'ratio_to': ratio[-1] if ratio else None,
            # 备用尺（占总市值）的分母仍然只从快照来 ⇒ 覆盖必然短，界面要 separately 报
            'ratio_total_sessions': len(set(ratio_total)),
            'ratio_total_from': ratio_total[0] if ratio_total else None,
            'ratio_total_to': ratio_total[-1] if ratio_total else None,
            'denom_sessions': len(set(denom)), 'denom_from': denom[0] if denom else None,
            'denom_to': denom[-1] if denom else None,
            'lag_note': lag}
