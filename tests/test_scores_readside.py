# -*- coding: utf-8 -*-
"""「八项能力加权明细」读侧：中文名从注册表来、名次在这一场能不能念。

用户 2026-10-22 指着这一屏说两件事：
① 「这一段完全看不懂啊，请你转成中文的说明」—— 屏上念的是 `env_resonance`、`env_mood_v1`、
   `config.view_effective_binding`、`stale`、`cached_aggregates` 这一串机器键；
② 「market_mood -46.200 分 全市场第 1（前 50.0%）怎么就排名第 1 了？」—— 那一项这一场
   5,568 只全是同一个值，写侧的 `rank(method='min')` 恒给 1、`rank(pct=True)` 恒给 50.009。

所以钉四条：
· 能力名/分量名只从 `config.capability.name` 与 `config.view_effective_binding.script_name`
  （兜底 `config.script.name`）来，**对不上时出声**，不静默回落成机器键当没事发生；
· 同场同值的那一项 ⇒ 名次与分位都交 None（`rank_kind='degenerate'`），并点名；
· 档位化那几项 ⇒ 名次照念，但把并列只数与取值个数一起交（`rank_kind='coarse'`）；
· 每条 caveat 都带中文 `label`，未知 code 退回中性词「口径说明」—— 页面不许按 code 猜中文。

最后一条不打桩：真库现查 2026-10-21 的 300581.SZ。
"""
import datetime as dt

import pytest

from duanxian import quant as Q

DAY = '2026-10-21'
CODE = '300581.SZ'


def _cap(cap, value, rank, pct):
    return {'capability_key': cap, 'value': value, 'rank_in_market': rank,
            'percent_rank': pct, 'quadrant': 'Q4' if cap == 'oper_score' else None,
            'is_stale': False}


def _detail(cap, key, label, value=1.0, weight=0.5):
    return {'capability_key': cap, 'component_key': key, 'component_label': label,
            'component_value': value, 'weight': weight, 'contribution': value * weight,
            'script_key': label, 'script_version': '1.0'}


def _spread(cap, uni, dv, biggest, tie):
    return {'capability_key': cap, 'universe': uni, 'distinct_values': dv,
            'biggest_tie': biggest, 'tie_at_value': tie}


CAPS = [_cap('market_mood', -46.2004, 1, 50.009), _cap('vol_stage', 3.0, 2809, 46.6961),
        _cap('oper_score', -22.3701, 1736, 68.8398)]
DETAIL = [_detail('market_mood', 'market_mood', 'market_mood_v1', -46.2004, 1.0),
          _detail('vol_stage', 'vol_stage', 'vol_stage_v1', 3.0, 1.0),
          _detail('oper_score', 'oper_x', 'oper_x_v1', -20.62, 0.4),
          _detail('oper_score', 'oper_y', 'oper_y_v1', -23.54, 0.6)]
BIND = [{'capability_key': 'market_mood', 'script_key': 'market_mood_v1', 'script_name': '大盘情绪',
         'raw_weight': 1.0, 'eff_weight': 1.0},
        {'capability_key': 'vol_stage', 'script_key': 'vol_stage_v1', 'script_name': '量能阶段',
         'raw_weight': 1.0, 'eff_weight': 1.0},
        {'capability_key': 'oper_score', 'script_key': 'oper_x_v1', 'script_name': 'X 个股质量',
         'raw_weight': 0.4, 'eff_weight': 0.4},
        {'capability_key': 'oper_score', 'script_key': 'oper_y_v1', 'script_name': 'Y 环境共振',
         'raw_weight': 0.6, 'eff_weight': 0.6}]
CAPTBL = [{'capability_key': 'market_mood', 'name': '大盘情绪', 'description': '全市场个股统计（非指数）'},
          {'capability_key': 'vol_stage', 'name': '量能阶段', 'description': '离散码'},
          {'capability_key': 'oper_score', 'name': '操作评分', 'description': 'X/Y 加权 + 四象限'}]
SCRIPTTBL = [{'script_key': 'market_mood_v1', 'name': '大盘情绪'},
             {'script_key': 'vol_stage_v1', 'name': '量能阶段'},
             {'script_key': 'oper_x_v1', 'name': 'X 个股质量'},
             {'script_key': 'oper_y_v1', 'name': 'Y 环境共振'}]
SPREAD = [_spread('market_mood', 5568, 1, 5568, 5568),
          _spread('vol_stage', 5554, 5, 2779, 306),
          _spread('oper_score', 5568, 5533, 2, 1)]


@pytest.fixture
def fake(monkeypatch):
    """按 SQL 里那句特征串分发；数据可以自己覆盖（fake(rows=...) 之后再用 fake(...)）。

    ⚠️ 收尾必须清 `_AGG`：这个桩会给 `coverage:score` 那几条缓存塞进**没经过 `_plain` 的
    `datetime.date`**，而 `_AGG` 是进程级、一小时不过期 —— 不清就是把脏缓存留给排在后面、
    要真库的那些用例（同一族坑见 `tests/test_coverage.py` 的直接赋值）。
    """
    state = {'caps': CAPS, 'detail': DETAIL, 'bind': BIND, 'cap': CAPTBL,
             'script': SCRIPTTBL, 'spread': SPREAD, 'spread_none': False}

    def apply(**over):
        state.update(over)
        Q.clear_agg_cache()

        def _rows(sql, params=()):
            s = ' '.join(sql.split())

            def feed(key):
                v = state[key]
                # None 是"这条查询读不到"那一档（库不通），不是空表 —— 桩必须能演这两种
                return None if v is None else list(v)

            # 先认那句"取数特征串"，再认表名：`... from analytics.score_detail` 既出现在
            # 明细查询里，也出现在 `_coverage` 那句 min/max 里，按表名分支会串成一件事。
            if 'min(trade_date) a, max(trade_date) b' in s:
                return [{'a': dt.date(2026, 1, 27), 'b': dt.date(2026, 10, 21), 'n': 180}]
            if 'biggest_tie' in s:                      # _RANK_SPREAD_SQL
                return None if state['spread_none'] else list(state['spread'])
            if 'from analytics.score where trade_date' in s:
                return feed('caps')
            if 'from analytics.score_detail' in s:
                return feed('detail')
            if 'from config.view_effective_binding' in s:
                return feed('bind')
            if 'from config.script' in s:
                return feed('script')
            if 'from config.capability' in s:
                return feed('cap')
            return []
        monkeypatch.setattr(Q, '_rows', _rows)
    apply()
    yield apply
    Q.clear_agg_cache()


def _by_key(p):
    return {c['capability_key']: c for c in p['capabilities']}


class Test中文名:
    def test_能力名与分量名都从注册表来_机器键照旧交出(self, fake):
        p = Q.scores(CODE, DAY)
        assert p['available'] is True
        caps = _by_key(p)
        assert caps['market_mood']['capability_name'] == '大盘情绪'
        assert caps['oper_score']['capability_note'] == 'X/Y 加权 + 四象限'
        comps = {c['component_key']: c for c in caps['oper_score']['components']}
        assert comps['oper_x']['component_name'] == 'X 个股质量'
        assert comps['oper_x']['component_label'] == 'oper_x_v1', '机器键不许抹掉，对账要读它'
        assert p['names_source'] == 'config.capability（3 项）'
        assert 'view_effective_binding' in p['component_names_source']

    def test_注册表对不上分量名时出声而不是静默回落(self, fake):
        fake(bind=[b for b in BIND if b['script_key'] != 'oper_x_v1'],
             script=[s for s in SCRIPTTBL if s['script_key'] != 'oper_x_v1'])
        p = Q.scores(CODE, DAY)
        comps = {c['component_key']: c for c in _by_key(p)['oper_score']['components']}
        assert comps['oper_x']['component_name'] is None
        codes = {v['code'] for v in p['caveats']}
        assert 'component_name_unmatched' in codes
        text = next(v['text'] for v in p['caveats'] if v['code'] == 'component_name_unmatched')
        assert 'oper_x_v1' in text, '要点名到底是哪个分量没对上'

    def test_注册表没有但config_script有就按脚本名认(self, fake):
        fake(bind=[b for b in BIND if b['script_key'] != 'vol_stage_v1'])
        p = Q.scores(CODE, DAY)
        assert p['capabilities'] and _by_key(p)['vol_stage']['components'][0]['component_name'] == '量能阶段'
        assert not [v for v in p['caveats'] if v['code'] == 'component_name_unmatched']

    def test_能力表读不到时要有一条声明(self, fake):
        fake(cap=None)
        p = Q.scores(CODE, DAY)
        assert _by_key(p)['market_mood']['capability_name'] is None
        assert any('能力中文名读不到' in (v['text'] or '') for v in p['caveats'])


class Test名次能不能念:
    def test_同场同一个值_名次与分位都是None(self, fake):
        c = _by_key(Q.scores(CODE, DAY))['market_mood']
        assert c['rank_kind'] == 'degenerate'
        assert c['rank_in_market'] is None and c['percent_rank'] is None
        assert c['universe'] == 5568 and c['tie_at_value'] == 5568
        assert '没有横截面意义' in c['rank_note']
        line = next(v for v in Q.scores(CODE, DAY)['caveats'] if v['code'] == 'rank_degenerate')
        assert '大盘情绪' in line['text'] and '5,568' in line['text']

    def test_档位化的名次照念但带并列与档位数(self, fake):
        c = _by_key(Q.scores(CODE, DAY))['vol_stage']
        assert c['rank_kind'] == 'coarse'
        assert c['rank_in_market'] == 2809 and c['percent_rank'] == 46.6961
        assert c['distinct_values'] == 5 and c['tie_at_value'] == 306 and c['biggest_tie'] == 2779
        assert any(v['code'] == 'rank_coarse' for v in Q.scores(CODE, DAY)['caveats'])

    def test_正常横截面不贴任何名次标签(self, fake):
        p = Q.scores(CODE, DAY)
        c = _by_key(p)['oper_score']
        assert c['rank_kind'] == 'ok' and c['rank_in_market'] == 1736
        assert c['rank_note'] == '', 'ok 这一档不该给格子塞一句"为什么"'
        # 同屏那两条档位/同值的声明只点自己那几项，不许把 oper_score 也拖进去
        for code_key in ('rank_degenerate', 'rank_coarse', 'no_rank'):
            line = [v for v in p['caveats'] if v['code'] == code_key]
            assert all('操作评分' not in (v['text'] or '') for v in line), code_key

    def test_库里本来没名次那一档叫no_rank(self, fake):
        fake(caps=[_cap('market_mood', -46.2004, None, None)] + CAPS[1:])
        c = _by_key(Q.scores(CODE, DAY))['market_mood']
        assert c['rank_kind'] == 'no_rank' and c['rank_in_market'] is None
        assert any(v['code'] == 'no_rank' for v in Q.scores(CODE, DAY)['caveats'])

    def test_分布查询读不到_不许判成正常也不许闭嘴(self, fake):
        fake(spread_none=True)
        p = Q.scores(CODE, DAY)
        assert all(c['rank_kind'] == 'unknown' for c in p['capabilities'])
        assert any(v['code'] == 'rank_degenerate' and '没数出来' in v['text'] for v in p['caveats'])
        assert p['rank_spread']['sql_rows'] is None, '交出的行数是 None，不是 0（0 会被读成"这一场没数据"）'

    def test_判档阈值只住一处(self, fake):
        assert Q._RANK_COARSE_SHARE == 0.05
        # 5,554 只 / 5 档 ⇒ coarse；5,568 只 / 300 档（>278）⇒ ok
        assert Q._rank_view(_cap('a', 1.0, 9, 90.0), _spread('a', 5554, 300, 3, 1))['rank_kind'] == 'ok'
        assert Q._rank_view(_cap('a', 1.0, 9, 90.0), _spread('a', 5554, 277, 3, 1))['rank_kind'] == 'coarse'

    def test_并列等于母体但取值不止一档也算degenerate(self, fake):
        """dv=2 而这一档占满全场（另一半没值）时，"第 1"同样是并列产物，不许念。"""
        got = Q._rank_view(_cap('a', 1.0, 1, 50.0), _spread('a', 5568, 2, 5568, 5568))
        assert got['rank_kind'] == 'degenerate' and got['rank_in_market'] is None


class Test中文标签:
    def test_每条caveat都带label_且未知code退回中性词(self, fake):
        p = Q.scores(CODE, DAY)
        assert p['caveats'], '这一屏至少有那几条口径声明'
        for v in p['caveats']:
            assert v['label'], f'{v["code"]} 没有上屏的中文'
            assert v['label'] != v['code'], '徽标不许再念机器码'
        assert Q._cav('brand_new_code', 'x')['label'] == Q._CAV_LABEL_FALLBACK
        assert Q._cav('brand_new_code', 'x')['code'] == 'brand_new_code', '机器码照旧交出'

    def test_字段字典随接口交出_页面不自己写(self, fake):
        p = Q.scores(CODE, DAY)
        assert p['definitions'] == Q.SCORE_DEFINITIONS
        assert '全市场第几名' in Q.SCORE_DEFINITIONS
        for k, v in Q.SCORE_DEFINITIONS.items():
            assert '**' not in v, f'{k} 里带了 markdown 粗体（native title 不过 plain()）'
            assert '"' not in v, f'{k} 里嵌了 ASCII 双引号'
        for c in p['capabilities']:
            assert '**' not in (c['rank_note'] or ''), c['capability_key']
        for v in p['caveats']:
            assert '"' not in v['text'], f'{v["code"]} 的文案里嵌了 ASCII 双引号'


@pytest.mark.skipif(not Q.pg.enabled(), reason='本机 PG 不可用')
def test_真库_大盘情绪没有名次而量能阶段带并列():
    """不打桩。这一条管的是"库里今天真的长这样"：
    2026-10-21 全场 5,568 只的大盘情绪都是 -46.2004，而旧屏上写的是「全市场第 1（前 50.0%）」。"""
    p = Q.scores(CODE, DAY)
    if not p.get('available'):
        pytest.skip(f'{CODE} 在 {DAY} 没有评分行：{p.get("reason")}')
    caps = _by_key(p)
    mm = caps.get('market_mood')
    assert mm is not None and mm['rank_kind'] == 'degenerate'
    assert mm['rank_in_market'] is None and mm['percent_rank'] is None
    assert mm['capability_name'] == '大盘情绪'
    vs = caps.get('vol_stage')
    if vs is not None:
        assert vs['rank_kind'] == 'coarse' and vs['distinct_values'] and vs['distinct_values'] < 100
    for c in p['capabilities']:
        for comp in c['components']:
            assert comp['component_name'], f'{comp["component_key"]} 这一场没对上中文名'
