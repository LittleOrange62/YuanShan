# -*- coding: utf-8 -*-
"""⑪ 最强模式（19:19 那一栏）的钉住用例。

用户 11-10 的原话：「加个推送 11，每天 19:19 分准时跑。计算最强模式，你可以预设一些，
然后分析方式和方法参考上面昨天的算法，格式也按照上面的进行飞书推送。
并且列出最近 5 天最强模式的变化和趋势」。所以这一族要钉死的不是文案，是**四件事**：

1. **六档预设模式的归属**（`_mode_predicates`）：一只票同时属于几档是设计如此（样例那句
   「C+D 高位反复二波/异动规避反包」本来就是两档并列），但每一档的判据必须能单独复现；
   `pos250` 判不了的票**不进高位反复**，不能被当成 0 分位（那等于次新股永远不算高位）。
2. **「最强」按相对强度排，不按只数排**：`first_spread`（首板扩散）每天都是只数最多的一档
   （11-04 实测 77/123），按只数排等于天天报同一个词。这里用一份假历史把这条钉死：
   今日只数更多的档位因为贴近自己的常态而落选，只数少的档位因为远高于常态而当选。
3. **三段式证据的措辞在模板里**，正文里不出现 markdown 星号，一波流只剩 1 只时要点名"属个案"。
4. **宁缺毋假**：库里没有今天那场涨停池 ⇒ 一条都不发，只记 `strong_mode_empty`；
   趋势不足两场 ⇒ 说"先不说变化"，不说"没有变化"。

写库那一层（`SM.write`/`backfill`）本文件一次都不碰 —— `engine.cursor(role='etl')` 没有
测试闸，打过去就往真表里写行了。表结构与 `_UPSERT` 的列对账放在最后那条真库用例里（只读）。
"""
from __future__ import annotations

import datetime as dt
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest

from app.intraday import tasks                                    # noqa: E402
from duanxian import strong_mode as SM                            # noqa: E402
from ingest.jobs.push import t11_strong_mode as T                 # noqa: E402

CFG = dict(SM.PARAM_DEFAULTS)


def _cfg(**over) -> dict:
    """只改测试要的那几格阈值，其余跟着出厂值（改一份假数据先把尺说清）。"""
    return {**CFG, **over}


def _ctx(spec: list[dict], pos: dict | None = None, names: dict | None = None) -> dict:
    """`spec` = 每场一个 dict：`{代码: 今日连板数}`，只列**封住**的那些（炸板那天不算板）。"""
    out_days = [dt.date(2026, 1, 1) + dt.timedelta(days=i) for i in range(len(spec))]
    by_day = {}
    for i, one in enumerate(spec):
        by_day[out_days[i]] = {code: {'security_code': code, 'trade_date': out_days[i],
                                      'consec': consec, 'order_amount': None,
                                      'reason': None, 'float_cap_yuan': None,
                                      'board_kind': None, 'close': None, 'pct_change': None}
                               for code, consec in one.items()}
    return {'by_day': by_day, 'all_days': out_days,
            'pos': {(out_days[i], code): v
                    for i, one in enumerate(spec) for code, v in (pos or {}).get(i, {}).items()},
            'names': names or {c: f'票{c}' for one in spec for c in one}}


# ─────────────────────────────────────────────────── 一、六档归属

def _stock(**over) -> dict:
    base = {'sealed_today': True, 'consec': 1, 'zt_days': 1, 'max_consec': 1,
            'round_days': 1, 'round_boards': 1, 'pos250': 90.0, 'kind': '换手板',
            'name': '甲', 'code': '600001.SH', 'reason': None,
            'float_cap_yuan': None, 'order_amount': None, 'label': '1天1板'}
    return {**base, **over}


def test_六档判据各自能单独复现():
    ok = lambda s: {k for k, v in SM._mode_predicates(s, CFG) if v}
    assert ok(_stock(consec=3, zt_days=3, max_consec=3, round_days=3, round_boards=3)) \
        == {'wave_single'}, '本轮 3 天 3 板 ⇒ 一波流'
    assert ok(_stock(consec=5, zt_days=5, max_consec=5, round_days=5, round_boards=5)) \
        == {'wave_single'}
    assert ok(_stock(consec=2, zt_days=2, max_consec=2, round_days=3, round_boards=2)) \
        == {'second_wave'}, '本轮断过一天又连上 2 板 ⇒ 二波接力'
    assert ok(_stock(consec=1, zt_days=4, max_consec=2, round_days=1, round_boards=1)) \
        == {'reversal'}, '今日首板而窗口内板过 4 次 ⇒ 断板反包'
    assert ok(_stock(zt_days=9, max_consec=4, consec=1)) == {'repeat_high', 'reversal'}, \
        '样例里冰山冷热那种（9 次仅 4 连板、今天再首板）两档都算，不互斥'
    assert ok(_stock(zt_days=1, max_consec=1, consec=1, round_days=1, round_boards=1)) \
        == {'first_spread'}, '窗口内第一次封板 ⇒ 首板扩散'
    assert ok(_stock(consec=2, zt_days=2, max_consec=2, round_days=2, round_boards=2,
                     kind='一字板')) == {'one_word'}, '一字板且已 2 板 ⇒ 一字锁仓'


def test_一波流的下限是sm_wave_min_两板不算():
    got = {k for k, v in SM._mode_predicates(
        _stock(consec=2, zt_days=2, max_consec=2, round_days=2, round_boards=2), CFG) if v}
    assert 'wave_single' not in got, '2 天 2 板够不上"一波流"（出厂 sm_wave_min=3）'
    assert {k for k, v in SM._mode_predicates(
        _stock(consec=2, zt_days=2, max_consec=2, round_days=2, round_boards=2),
        _cfg(sm_wave_min=2)) if v} == {'wave_single'}


def test_pos250_判不了不进高位反复():
    """缺分位 ≠ 0 分位：按 0 处理的话次新股永远不算"高位"，而正文不会说这件事。"""
    s = _stock(zt_days=9, max_consec=4, pos250=None)
    got = {k for k, v in SM._mode_predicates(s, CFG) if v}
    assert 'repeat_high' not in got and 'reversal' in got


def test_今天没封住的票一档都不进():
    s = _stock(sealed_today=False, consec=None)
    assert not any(v for _k, v in SM._mode_predicates(s, CFG))


# ─────────────────────────────────────────────────── 二、归属来自窗口统计（假场次）

def test_窗口统计用同一把尺且含今天没封住的那些():
    """证据1 的母体是**窗口内出现过涨停的全部票**：用户那条样例里皇台酒业当天没封住也在名单上。
    而连板次数/最长连板/几天几板三个数必须来自同一批行，否则"N 次涨停仅 M 连板"底下是两把尺。"""
    ctx = _ctx([{}, {'A': 1}, {'A': 2}, {}, {'A': 1, 'B': 1}])
    cfg = _cfg(sm_window_sessions=5)
    got = SM._classify(ctx, ctx['all_days'], 4, cfg)
    a, b = got['per']['A'], got['per']['B']
    assert got['n_sealed'] == 2
    assert (a['zt_days'], a['max_consec'], a['round_days'], a['round_boards']) == (3, 2, 4, 3), \
        'A：窗口内 3 次封板、最长那波 2 连板、这一轮 4 天 3 板（中间空一天仍算同一轮）'
    assert a['label'] == '4天3板' and a['consec'] == 1 and a['sealed_today']
    assert (b['zt_days'], b['max_consec'], b['label']) == (1, 1, '1天1板'), 'B 今天第一次板'
    assert a['modes'] == ['reversal'] and b['modes'] == ['first_spread']


def test_最长连板按窗口内数的那一段_句子不会自相矛盾():
    """一轮从窗口外起手时，表里的连板会比窗口内数出来的次数还大 ⇒
    「近 2 场 2 次涨停仅 4 连板」成病句。这里数的是窗口内那一段，`zt_days >= max_consec` 恒成立。"""
    ctx = _ctx([{'A': 3}, {'A': 4}, {'A': 2}])
    got = SM._classify(ctx, ctx['all_days'], 2, _cfg(sm_window_sessions=2))
    assert got['per']['A']['max_consec'] == 2, '窗口只有 2 场，最长那波就只数得出 2 根'
    assert got['per']['A']['consec'] == 2 and got['per']['A']['zt_days'] == 2
    assert got['per']['A']['zt_days'] >= got['per']['A']['max_consec']


# ─────────────────────────────────────────────────── 三、最强按相对强度排

def test_只数多但贴近常态的档落选_只数少但远高于常态的档当选():
    """这一条钉的是「最强模式」这四个字的本义：减掉自己的常态才读得出今天像不像这一档。
    首板扩散那三只今天占 75%，但它平时就占 100% ⇒ 强度是负的；反包那只只有 1 只（25%），
    而基准里反包从没出现过 ⇒ 强度 +25，胜出。"""
    ctx = _ctx([{}, {}, {'R': 1}, {}, {'F1': 1, 'F2': 1, 'F3': 1, 'R': 1}])
    cfg = _cfg(sm_window_sessions=3, sm_baseline_sessions=3, sm_min_n=1, sm_reversal_min=2)
    days = ctx['all_days']
    today = SM._classify(ctx, days, 4, cfg)
    assert today['tally']['first_spread']['n_stocks'] == 3 > today['tally']['reversal']['n_stocks'] == 1
    ranked = SM._rank_modes(ctx, days, 4, today, cfg)
    by_key = {r['mode_key']: r for r in ranked['modes']}
    assert by_key['first_spread']['base_share_pct'] == 100.0, '基准里只有 d2 那场有数（R 当时算首板扩散）'
    assert by_key['first_spread']['lift_pp'] == -25.0
    assert by_key['reversal']['base_share_pct'] == 0.0
    assert by_key['reversal']['lift_pp'] == 25.0
    assert [w['mode_key'] for w in ranked['winners']] == ['reversal'], '只数多的那档不该当选中'
    assert ranked['winner_label'] == '断板反包'


def test_两档强度接近就报复合模式且按在册顺序念():
    """用户那条样例写的是「C+D 高位反复二波/异动规避反包」——两档并列是设计里的常态：
    一只票可以同时是高位反复与反包，所以模式**不互斥**，复合时按 MODES 的顺序念，不按谁先算出来。"""
    ctx = _ctx([{}, {}, {'A': 1}, {}, {'A': 1, 'B': 1}], pos={4: {'A': 95.0}})
    cfg = _cfg(sm_window_sessions=3, sm_baseline_sessions=3, sm_min_n=1, sm_reversal_min=2,
               sm_repeat_min=2, sm_repeat_ratio=2, sm_high_pos=70.0, sm_tie_pp=3.0)
    days = ctx['all_days']
    today = SM._classify(ctx, days, 4, cfg)
    assert today['per']['A']['modes'] == ['repeat_high', 'reversal'], \
        'A：窗口 2 次、最长那波 1 根、95 分位 ⇒ 高位反复；今日首板而窗口内板过 ⇒ 也算反包'
    ranked = SM._rank_modes(ctx, days, 4, today, cfg)
    lifts = {r['mode_key']: r['lift_pp'] for r in ranked['modes']}
    assert lifts['repeat_high'] == 50.0 and lifts['reversal'] == 50.0
    assert lifts['first_spread'] == -50.0, 'B 那档今天 50%，但基准那场它是 100%'
    assert [w['mode_key'] for w in ranked['winners']] == ['repeat_high', 'reversal'], \
        '强度并列 ⇒ 两档一起报，顺序跟 MODES（不是按 lift 谁先算出来）'
    assert ranked['winner_label'] == '高位反复二波／断板反包'


def test_命中只数不足门槛就不评最强():
    ctx = _ctx([{}, {}, {}, {}, {'R': 1}])
    cfg = _cfg(sm_window_sessions=3, sm_baseline_sessions=3, sm_reversal_min=2)  # sm_min_n=3
    days = ctx['all_days']
    today = SM._classify(ctx, days, 4, cfg)
    ranked = SM._rank_modes(ctx, days, 4, today, cfg)
    assert ranked['winners'] == [] and ranked['no_mode'] is True
    assert ranked['winner_label'] is None
    assert ranked['modes'], '六档读数照给，屏上/正文说"没有压倒性模式"而不是不给数'


def test_凑不满基准场数时强度交None而不是硬排():
    ctx = _ctx([{}, {'R': 1}])
    cfg = _cfg(sm_window_sessions=2, sm_baseline_sessions=2, sm_reversal_min=2)
    today = SM._classify(ctx, ctx['all_days'], 1, cfg)
    ranked = SM._rank_modes(ctx, ctx['all_days'], 1, today, cfg)
    assert all(m['base_share_pct'] is None for m in ranked['modes']), \
        '前一场全场没封住 ⇒ 一场基准都没有，占比减不出强度'
    assert ranked['winners'] == [] and ranked['no_mode'] is True


# ─────────────────────────────────────────────────── 四、趋势那一节

def _trow(day, share, n_sealed=10, height=3):
    return {'day': day, 'n_sealed': n_sealed, 'max_consec': height,
            'modes': {k: {'n': int(round(v * n_sealed / 100)), 'share': v, 'lift': None}
                      for k, v in share.items()}}


def test_趋势报在扩与在收且不足两场就不说话():
    keys = dict(SM.MODES)
    first = _trow('2026-11-02', {'reversal': 20.0, 'wave_single': 30.0})
    last = _trow('2026-11-06', {'reversal': 45.0, 'wave_single': 20.0})
    notes = SM._trend_notes([first, last], CFG)
    assert [x['mode_key'] for x in notes['expanding']] == ['reversal']
    assert notes['expanding'][0]['delta_pp'] == 25.0
    assert notes['expanding'][0]['from_share'] == 20.0 and notes['expanding'][0]['to_share'] == 45.0
    assert [x['mode_key'] for x in notes['shrinking']] == ['wave_single']
    assert notes['height'] == {'from': 3, 'to': 3} and keys
    only = SM._trend_notes([last], CFG)
    assert only == {'expanding': [], 'shrinking': [], 'height': None, 'sealed': None}, \
        '一场比不出变化 ⇒ 交空，让排版走"先不说变化"那句，不许说成"没有变化"'


def test_全场封板为0的那场在趋势里点名而不是跳过():
    rows = [{'day': '2026-11-05', 'n_sealed': 0, 'winner_label': None, 'modes': {}, 'max_consec': None}]
    txt = T.render({'day': dt.date(2026, 11, 6), 'window': 20, 'window_start': dt.date(2026, 10, 9),
                    'n_sealed': 3, 'params': CFG, 'params_unread': None,
                    'winners': [], 'winner_label': None, 'no_mode': True,
                    'modes': [dict(mode_key=k, mode_label=v, n_stocks=0, share_pct=0.0,
                                   base_share_pct=None, base_sessions=0, lift_pp=None,
                                   max_consec=None, order_amount_yi=None, order_lack_n=0, stocks=[])
                              for k, v in SM.MODES],
                    'undecided': {'no_mode_n': 0, 'pos_missing_n': 0, 'consec_missing_n': 0},
                    'evidence': {'top': [], 'not_single_n': 0, 'not_single': [], 'broken': [],
                                 'broken_n': 0, 'reseal': [], 'reseal_n': 0, 'single': [],
                                 'single_n': 0, 'is_case': True},
                    'trend': {'span': 2, 'sessions': rows + [
                        {'day': '2026-11-06', 'n_sealed': 3, 'max_consec': 2, 'winner_label': None,
                         'modes': {}, 'winners': [], 'no_mode': True}],
                        'notes': SM._trend_notes([], CFG)}}, dt.datetime(2026, 11, 6, 19, 19))
    assert '11-05 全场无封板（0 只）' in txt


# ─────────────────────────────────────────────────── 五、排版（三段式 + 措辞在模板里）

def _data(**over) -> dict:
    """一份与 `SM.collect()` 同形状的手工读数：测排版不用把库拖进来。
    `winners=[]`（评不出最强）与"没传 winners"是两种情况，所以用哨兵区分。"""
    day = dt.date(2026, 11, 9)
    modes = over.pop('modes', None) or [
        dict(mode_key='reversal', mode_label='断板反包', n_stocks=26, share_pct=44.8,
             base_share_pct=27.2, base_sessions=20, lift_pp=17.6, max_consec=1,
             order_amount_yi=20.883, order_lack_n=0, stocks=[]),
        dict(mode_key='first_spread', mode_label='首板扩散', n_stocks=22, share_pct=37.9,
             base_share_pct=48.1, base_sessions=20, lift_pp=-10.2, max_consec=1,
             order_amount_yi=13.122, order_lack_n=0, stocks=[]),
    ]
    sentinel = object()
    win = over.pop('winners', sentinel)
    win = [modes[0]] if win is sentinel else win
    sessions = over.pop('trend_sessions', None) or [
        {'day': '2026-11-06', 'n_sealed': 33, 'max_consec': 5,
         'winner_label': '一波流连板', 'winners': [], 'no_mode': False,
         'modes': {'reversal': {'n': 9, 'share': 27.3, 'lift': None},
                   'wave_single': {'n': 7, 'share': 21.2, 'lift': None}}},
        {'day': '2026-11-09', 'n_sealed': 58, 'max_consec': 6,
         'winner_label': '断板反包', 'winners': [], 'no_mode': False,
         'modes': {'reversal': {'n': 26, 'share': 44.8, 'lift': None},
                   'wave_single': {'n': 1, 'share': 1.7, 'lift': None}}}]
    return {'day': day, 'window': 20, 'window_start': dt.date(2026, 10, 13),
            'n_sealed': 58, 'params': CFG, 'params_unread': None,
            'modes': modes, 'winners': win, 'winner_label': '断板反包', 'no_mode': not win,
            'undecided': {'no_mode_n': 0, 'pos_missing_n': 0, 'consec_missing_n': 0},
            'evidence': {'top': [{'name': '甲', 'zt_days': 9, 'max_consec': 4, 'consec': 1,
                                  'days': 1, 'boards': 1, 'label': '1天1板', 'pos250': 100.0,
                                  'reason': None, 'float_cap_yi': None, 'kind': None,
                                  'modes': [], 'security_code': 'A'}],
                         'not_single_n': 1, 'not_single': [{'name': '甲', 'zt_days': 9,
                                                             'max_consec': 4}],
                         'broken': [{'name': '乙', 'label': '4天3板'}], 'broken_n': 1,
                         'reseal': [{'name': '甲', 'max_consec': 4}], 'reseal_n': 1,
                         'single': [{'name': '丙', 'consec': 6, 'reason': '题材一+题材二',
                                     'float_cap_yi': 47.5}], 'single_n': 1, 'is_case': True},
            'trend': {'span': len(sessions),
                      'sessions': sessions,
                      'notes': SM._trend_notes(sessions, CFG)},
            **over}


def test_正文是三段式且措辞全部来自模板():
    txt = T.render(_data(), dt.datetime(2026, 11, 9, 19, 19, 30))
    assert txt.splitlines()[0].startswith('【最强模式 19:19:30】')
    assert '最强模式:断板反包' in txt
    for tag in ('证据1|', '证据2|', '证据3|'):
        assert txt.count(tag) == 1, f'{tag} 该出现且只出现一次'
    assert '甲9次涨停仅4连板' in txt and '乙4天3板' in txt and '甲4连板结束后今日再首板' in txt
    assert '丙6连板（题材一+题材二，47.5亿）——属个案而非模式' in txt
    assert '最近 2 场最强模式的变化：' in txt and '11-09 断板反包（封板 58 只 · 最高 6 板）' in txt
    assert '趋势（11-06→11-09）：断板反包 27.3%→44.8%（+17.5 点，在扩）' in txt
    assert '一波流连板 21.2%→1.7%（-19.5 点，在收）' in txt
    assert '全场最高板 5→6、封板只数 33→58' in txt
    assert '**' not in txt, '群里与屏上都是纯文本，不许漏 markdown 星号'
    assert '不预测涨跌、不给建议' in txt.splitlines()[-1]
    # 句子不许长在代码里：正文里那些措辞必须逐个能在模板文件里找到
    tpl = T._tpl()
    for frag in ('证据1|', '证据2|', '证据3|', '属个案而非模式', '结束后今日再首板'):
        assert any(frag in str(v) for v in tpl.values()), f'「{frag}」不在 templates.json 里'
    src = (ROOT / 'ingest' / 'jobs' / 'push' / 't11_strong_mode.py').read_text(encoding='utf-8')
    assert '"证据1|' not in src and "'证据1|" not in src, '在代码里拼句子：改措辞要改代码'


def test_一波流不只一只时不说属个案():
    d = _data()
    d['evidence']['single_n'] = 4
    d['evidence']['is_case'] = False
    txt = T.render(d, dt.datetime(2026, 11, 9, 19, 19))
    assert '属个案' not in txt and '真·一波流（本轮一天没断 ≥3 板）4 只' in txt


def test_判不了的那几格自己点名_齐了就不出现():
    txt = T.render(_data(), dt.datetime(2026, 11, 9, 19, 19))
    assert '另有' not in txt, '没有缺数就不要凭空写一句"另有 N 只判不了"'
    d = _data()
    d['undecided'] = {'no_mode_n': 4, 'pos_missing_n': 2, 'consec_missing_n': 0}
    d['modes'][0]['order_lack_n'] = 5
    d['params_unread'] = '配置文件读不出来（FileNotFoundError），走代码默认值'
    txt2 = T.render(d, dt.datetime(2026, 11, 9, 19, 19))
    assert '另有 4 只没落进这六档预设模式' in txt2
    assert '另有 2 只的 250 日分位判不了' in txt2
    assert '封单额有 5 只源站没给' in txt2 and '下限' in txt2
    assert '走的是代码默认阈值' in txt2


def test_没有压倒性模式时报最高的一档与门槛():
    d = _data(winners=[], no_mode=True, winner_label=None)
    txt = T.render(d, dt.datetime(2026, 11, 9, 19, 19))
    assert '今天没有压倒性模式' in txt and '断板反包' in txt
    assert f'命中不足 {CFG["sm_min_n"]} 只的档不参与排名' in txt


def test_基准一场都没有时不硬报强度():
    """减不出"比平时强几个点" ⇒ 走「判不了」那句，别念出"None 个百分点"，也别把 None 当 0 排下去。"""
    d = _data(winners=[], modes=[dict(mode_key='reversal', mode_label='断板反包', n_stocks=26,
                                      share_pct=44.8, base_share_pct=None, base_sessions=0,
                                      lift_pp=None, max_consec=1, order_amount_yi=None,
                                      order_lack_n=0, stocks=[])])
    txt = T.render(d, dt.datetime(2026, 11, 9, 19, 19))
    assert '库里凑不满' in txt and 'None' not in txt, '拿 None 去格式化会念出"None 个百分点"'
    assert '最强模式:断板反包' not in txt, '判不了却报出当选档，读的人以为那是结论'


def test_模板键在册_缺了当场红():
    t = T._tpl()
    need = [k for k in t if k.startswith('strong_mode_')]
    for key in ('strong_mode_header', 'strong_mode_winner', 'strong_mode_no_mode',
                'strong_mode_winner_row', 'strong_mode_winner_row_flat', 'strong_mode_no_base',
                'strong_mode_e1', 'strong_mode_e1_all', 'strong_mode_e1_none',
                'strong_mode_e1_row', 'strong_mode_e1_row_single',
                'strong_mode_e2', 'strong_mode_e2_broken_only', 'strong_mode_e2_reseal_only',
                'strong_mode_e2_none', 'strong_mode_e2_row', 'strong_mode_e2_reseal_row',
                'strong_mode_e3', 'strong_mode_e3_none', 'strong_mode_e3_row',
                'strong_mode_e3_row_noreason', 'strong_mode_e3_row_nocap',
                'strong_mode_e3_row_bare', 'strong_mode_e3_case',
                'strong_mode_trend_title', 'strong_mode_trend_row', 'strong_mode_trend_row_no_mode',
                'strong_mode_trend_row_empty', 'strong_mode_trend_expand', 'strong_mode_trend_shrink',
                'strong_mode_trend_note', 'strong_mode_trend_flat', 'strong_mode_trend_short',
                'strong_mode_undecided', 'strong_mode_pos_missing', 'strong_mode_order_lack',
                'strong_mode_missing_pool', 'strong_mode_params_unread', 'strong_mode_footer'):
        assert key in t, f'app/intraday/templates.json 少了 {key}'
    assert len(need) >= 38


# ─────────────────────────────────────────────────── 六、壳与调度

def test_出厂默认关着_and_第十一行():
    assert tasks.DEFAULTS['strong_mode_enabled'] is False
    assert tasks.DEFAULTS['strong_mode_times'] == '19:19'
    assert tasks.task_args('strong_mode', tasks.DEFAULTS) == \
        '-X utf8 -m ingest.jobs.push.t11_strong_mode --send'
    keys = list(tasks.SPEC)
    assert keys[-1] == 'strong_mode' and keys.index('strong_mode') == 10
    cfg = {**tasks.DEFAULTS, 'strong_mode_enabled': True}
    assert tasks.want_boundaries('strong_mode', cfg)[0].endswith('T19:19:00')
    assert '每天 19:19' in tasks.schedule_text('strong_mode', cfg)


def test_库里没有今天那场就一条都不发(monkeypatch):
    codes, sent = [], []
    monkeypatch.setattr(SM, 'collect',
                        lambda day=None, cfg=None: {'day': day, 'missing': 'no_pool_rows',
                                                    'latest': dt.date(2026, 11, 6)})
    monkeypatch.setattr(T, '_log', lambda now, what, **kw: codes.append(what))
    monkeypatch.setattr(T, '_send', lambda now, text, keys, **kw: sent.append(text))
    rc = T.one_round(dry_run=False, send=True, force=True, channel=None,
                     resend=False, backfill=None, day=None)
    assert rc == 1 and codes == ['strong_mode_empty'] and sent == []


def test_非交易日静默退不发(monkeypatch):
    sent, codes = [], []
    monkeypatch.setattr(T.zt_watch, 'is_trading_day', lambda now: False)
    monkeypatch.setattr(T, '_send', lambda now, text, keys, **kw: sent.append(text))
    monkeypatch.setattr(T, '_log', lambda now, what, **kw: codes.append(what))
    assert T.one_round(dry_run=False, send=True, force=False, channel=None,
                       resend=False, backfill=None, day=None) == 0
    assert sent == [] and codes == []


def test_有正文时先落留痕与逐档表再发_补发只读库(monkeypatch):
    order, sent = [], []
    monkeypatch.setattr(T, 'collect', lambda now: _data())
    monkeypatch.setattr(T, '_log', lambda now, what, **kw: order.append((what, 'body' in kw)))
    monkeypatch.setattr(T, '_send', lambda now, text, keys, **kw: order.append(('sent', text)))
    monkeypatch.setattr(SM, 'write', lambda res: order.append(('tally', res['day'])) or 6)
    rc = T.one_round(dry_run=False, send=True, force=True, channel=None,
                     resend=False, backfill=None, day=None)
    assert rc == 0
    assert order[0] == ('strong_mode', True), '正文没先落库'
    assert order[1] == ('tally', dt.date(2026, 11, 9)), '逐档读数没落表'
    assert order[2][0] == 'sent' and '证据1|' in order[2][1]


def test_补发没有正文时不重算冒充(monkeypatch):
    codes, sent = [], []
    monkeypatch.setattr(T, 'today_body', lambda day: None)
    monkeypatch.setattr(T, '_log', lambda now, what, **kw: codes.append(what))
    monkeypatch.setattr(T, '_send', lambda now, text, keys, **kw: sent.append(text))
    rc = T.one_round(dry_run=False, send=True, force=True, channel=None,
                     resend=True, backfill=None, day=None)
    assert rc == 1 and codes == ['strong_mode_no_body'] and sent == []


def test_backfill不发任何东西只落表(monkeypatch):
    seen, sent = [], []
    monkeypatch.setattr(SM, 'backfill',
                        lambda end_day=None, sessions=30: seen.append(sessions) or
                        {'want': 30, 'written_days': 30, 'first': 'x', 'last': 'y'})
    monkeypatch.setattr(T, '_send', lambda now, text, keys, **kw: sent.append(text))
    monkeypatch.setattr(T, 'collect', lambda now: seen.append('collect') or {})
    rc = T.one_round(dry_run=False, send=True, force=False, channel=None,
                     resend=False, backfill=30, day=None)
    assert rc == 0 and seen == [30] and sent == [], '补历史不该顺手发一条到群里'


def test_dryrun不写库不发送(monkeypatch):
    codes, sent = [], []
    monkeypatch.setattr(T, 'collect', lambda now: _data())
    monkeypatch.setattr(T, '_log', lambda now, what, **kw: codes.append(what))
    monkeypatch.setattr(T, '_send', lambda now, text, keys, **kw: sent.append(text))
    monkeypatch.setattr(SM, 'write', lambda res: codes.append('tally'))
    assert T.one_round(dry_run=True, send=True, force=True, channel=None,
                       resend=False, backfill=None, day=None) == 0
    assert codes == [] and sent == []


def test_只读库这一路一个源站都不碰():
    """与 ③⑦⑨⑩ 同一条纪律：正文里出现现抓源站的字样就是"重跑内容会变"的开始。"""
    src = (ROOT / 'ingest' / 'jobs' / 'push' / 't11_strong_mode.py').read_text(encoding='utf-8')
    for bad in ('requests', 'push2', 'ths', 'akshare', 'tqclient'):
        assert bad not in src, f'⑪ 里出现了 {bad}：这一路只许读库里已加工的结果'
    judge = (ROOT / 'duanxian' / 'strong_mode.py').read_text(encoding='utf-8')
    for bad in ('requests', 'push2', 'akshare'):
        assert bad not in judge, f'判据里出现了 {bad}'


# ─────────────────────────────────────────────────── 七、参数与表结构（不写库，只读）

def test_兜底默认与在册配置逐键相等():
    cfg = json.loads((ROOT / 'app' / 'intraday' / 'broadcast_config.json')
                     .read_text(encoding='utf-8'))
    got = {k: v for k, v in cfg.items() if k.startswith('sm_') and k != 'sm_note'}
    assert set(got) == set(SM.PARAM_DEFAULTS), \
        f'配置文件与代码默认值的键集不一样：{set(got) ^ set(SM.PARAM_DEFAULTS)}'
    for k, want in SM.PARAM_DEFAULTS.items():
        assert got[k] == want, f'{k} 兜底值 {want} 与配置里的 {got[k]} 不等 —— 换尺了'
        assert type(got[k]) is type(want), f'{k} 类型漂了：{type(want).__name__} vs {type(got[k]).__name__}'


def test_模式族与屏上那六个词只有一份():
    keys = [k for k, _ in SM.MODES]
    assert len(set(keys)) == len(keys) == 6
    assert set(SM.MODE_LABELS) == set(keys)
    for _k, label in SM.MODES:
        assert label and '*' not in label and '／' not in label, f'{label}：复合分隔符／不能出现在档名里'


def test_落库列数与_upsert_占位符一致_and_库里真是那些列():
    engine = pytest.importorskip('app.db.engine')
    cols = SM._UPSERT.split('values')[0]
    n_cols = cols.count(',') + 1
    assert n_cols == SM._UPSERT.count('%s') + 1, 'calculated_at 走 now()，不留占位符'
    try:
        rows = engine.query(
            "select column_name from information_schema.columns"
            " where table_schema='analytics' and table_name='strong_mode_daily'", role='reader')
    except Exception:  # noqa: BLE001  库不通时这条不算红（判据与文案那些用例照样跑）
        pytest.skip('库不通，表结构对账留到有库的环境')
    in_db = {r['column_name'] for r in rows}
    listed = {c.strip().split()[0] for c in
              cols.split('(', 1)[1].rsplit(')', 1)[0].split(',')}
    assert listed <= in_db, f'插入语句里这些列库里没有：{sorted(listed - in_db)}'
    assert {'trade_date', 'spec_version', 'mode_key'} <= listed, '主键三件必须在插入列里'


def test_真库最新那场判据跑通且三条证据自洽():
    """**不打桩**的一条（假数据能钉住判据形状，钉不住"这张表真有这些列、这个键真有这些值"）。

    只读，不写：`collect` 里全是 select。库里最新那场没有封板票（长假后第一天那种）时
    各档给 0，这条照样过 —— 它要的是"跑通且自洽"，不是"今天必须有涨停"。
    """
    engine = pytest.importorskip('app.db.engine')
    day = SM.latest_day()
    if day is None:
        pytest.skip('涨停池里没有行（这张表还没回填）')
    res = SM.collect(day)
    assert res['missing'] is None and res['day'] == day
    assert [m['mode_key'] for m in res['modes']] == [k for k, _ in SM.MODES], '六档一行都不能少'
    assert res['n_sealed'] == len(res['stocks'])
    # 自洽三件：占比就是只数÷封板数、最强那档必须是池里强度最高且过了门槛的、证据名单都真在窗口里
    for m in res['modes']:
        if res['n_sealed']:
            assert m['share_pct'] == pytest.approx(round(m['n_stocks'] * 100.0 / res['n_sealed'], 1))
    if res['winners']:
        top = max((m for m in res['modes'] if m['lift_pp'] is not None
                   and m['n_stocks'] >= CFG['sm_min_n']), key=lambda m: m['lift_pp'])
        assert top['mode_key'] in [w['mode_key'] for w in res['winners']]
    codes = set(res['per'])
    for row in res['evidence']['top']:
        assert row['security_code'] in codes and row['zt_days'] >= 1
    assert res['trend']['sessions'][-1]['day'] == str(day)
    assert res['trend']['span'] == CFG['sm_trend_sessions']
    txt = T.render(res, dt.datetime.combine(day, dt.time(19, 19, 30)))
    assert txt and txt.count('证据1|') == 1 and '**' not in txt
    assert f'近 {res["window"]} 场涨停天数 TOP' in txt
    # 同一把尺的自洽条件：窗口内次数不可能小于窗口内最长那波
    for s in res['stocks']:
        assert s['zt_days'] >= (s['max_consec'] or 0), \
            f"{s['name']}：{s['zt_days']} 次却 {s['max_consec']} 连板，两把尺混了"
