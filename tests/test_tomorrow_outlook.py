# -*- coding: utf-8 -*-
"""明日展望那一栏（`ingest/jobs/push/t10_tomorrow_outlook.py`，19:00）的钉住用例。

用户 11-04 的原话是「每日复盘有了，再增加个明日展望，定时19:00」。这边交付的是与 ⑨ 同一形态的壳，
当晚他把自己写的三节正文也放了进来（多指标靠前／趋势最正／明日观察清单）。
所以这里钉的是**壳的四条纪律**（正文本身不归测试管，那是他改的脚本）：
①三节全空就一条都不发（不往群里发一句空展望）；②正文先落库再发（于是 `--resend` 能一字不差补发）；
③默认关着 ⇒ 连任务都不注册（关着的语义是注销，不是留个每天空跑的壳）；④措辞在模板里，不在代码里拼。
另加两条这一路自己的：号必须排在第 ⑩（行序＝文件名序号）、时点必须落在 19:00。
"""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import pytest

from app.intraday import store, tasks
from ingest.jobs.push import t10_tomorrow_outlook as T

ROOT = Path(__file__).resolve().parents[1]


def test_默认关着并且册上给得出这一路():
    assert tasks.DEFAULTS['tomorrow_enabled'] is False
    assert tasks.DEFAULTS['tomorrow_times'] == '19:00'
    assert tasks.SPEC['tomorrow']['task_name'] == 'YuanShan-TomorrowOutlook'
    assert tasks.task_args('tomorrow', tasks.DEFAULTS) == \
        '-X utf8 -m ingest.jobs.push.t10_tomorrow_outlook --send'


def test_这一路排在第十行():
    """行序＝屏上那个圈号＝文件名里的 `t10`；插到别处会让"改第 ⑩ 行"去找错文件。
    （11-10 加了第 ⑪ 路最强模式，⑩ 不再是最后一行 —— 钉的是它仍排在第 10、⑪ 排在它后面。）"""
    keys = list(tasks.SPEC)
    assert keys.index('tomorrow') == 9
    assert keys[9] == 'tomorrow' and keys[10] == 'strong_mode'


def test_关掉的那一路是注销任务不是留个空跑的壳(monkeypatch):
    seen: list[str] = []
    monkeypatch.setattr(tasks, 'run_powershell',
                        lambda script, **kw: seen.append(script) or (0, 'GONE', ''))
    row = tasks.register_one('tomorrow', tasks.DEFAULTS, want_on=False)
    assert row['action'] == 'removed' and 'Unregister-ScheduledTask' in seen[0]


def test_开着时生成的任务定义落在1900():
    cfg = {**tasks.DEFAULTS, 'tomorrow_enabled': True}
    assert tasks.want_boundaries('tomorrow', cfg)[0].endswith('T19:00:00')
    assert '每天 19:00' in tasks.schedule_text('tomorrow', cfg)


def test_全部缺数时一条都不发(monkeypatch):
    """正文用户 11-04 也自己写上了（三节：多指标靠前／趋势最正／明日观察清单），
    所以这一条不再依赖「`collect()` 还是空的」这个前提 —— 它钉的是纪律：
    **三节全空 ⇒ 一条都不发**，只记一行 `tomorrow_empty`，不许往群里发一句空展望。"""
    codes: list[str] = []
    sent: list[str] = []
    monkeypatch.setattr(T, 'collect', lambda now: {'sections': []})
    monkeypatch.setattr(T, '_log', lambda now, what, **kw: codes.append(what))
    monkeypatch.setattr(T, '_send', lambda now, text, keys, **kw: sent.append(text))
    monkeypatch.setattr(tasks, 'send_channels', lambda c=None: ['feishu-main'])
    rc = T.one_round(dry_run=False, send=True, force=True, channel=None, resend=False)
    assert rc == 1 and codes == ['tomorrow_empty'] and sent == []


def test_有内容时先落正文再发_补发只读库(monkeypatch):
    """顺序必须是"落库 → 发送"：那样 `--resend` 才拿得到**同一条正文**，而不是重新算一遍。"""
    order: list[str] = []
    monkeypatch.setattr(T, 'collect', lambda now: {'title': '明日展望', 'sections': ['甲段', ' 乙段 ', '']})
    monkeypatch.setattr(T, '_log',
                        lambda now, what, **kw: order.append((what, kw.get('body') is not None)))
    monkeypatch.setattr(T, '_send', lambda now, text, keys, **kw: order.append(('sent', text)))
    rc = T.one_round(dry_run=False, send=True, force=True, channel=None, resend=False)
    assert rc == 0
    assert order[0] == ('tomorrow', True), '正文没先落库'
    assert order[1][0] == 'sent' and '甲段' in order[1][1] and '乙段' in order[1][1]
    assert '【明日展望' in order[1][1] and '**' not in order[1][1]
    assert '不预测涨跌' in order[1][1]          # 页脚由模板给，不在代码里拼句子


def test_非交易日静默退不发一句空展望(monkeypatch):
    """19:00 在盘后，判据只判交易日（这一道闸与其余九路同源，不是自己兜的一份）。"""
    sent: list[str] = []
    codes: list[str] = []
    monkeypatch.setattr(T.zt_watch, 'is_trading_day', lambda now: False)
    monkeypatch.setattr(T, '_send', lambda now, text, keys, **kw: sent.append(text))
    monkeypatch.setattr(T, '_log', lambda now, what, **kw: codes.append(what))
    assert T.one_round(dry_run=False, send=True, force=False, channel=None, resend=False) == 0
    assert sent == [] and codes == []


def test_补发没有正文时不重算冒充(monkeypatch):
    codes, sent = [], []
    monkeypatch.setattr(T, 'today_body', lambda day: None)
    monkeypatch.setattr(T, '_log', lambda now, what, **kw: codes.append(what))
    monkeypatch.setattr(T, '_send', lambda now, text, keys, **kw: sent.append(text))
    rc = T.one_round(dry_run=False, send=True, force=True, channel=None, resend=True)
    assert rc == 1 and codes == ['tomorrow_no_body'] and sent == []


def test_模板键在册_缺了当场红():
    t = T._tpl()
    for key in ('tomorrow_header', 'tomorrow_footer',
                'tomorrow_s1', 'tomorrow_s2', 'tomorrow_s3',
                'tomorrow_hit_row', 'tomorrow_hit_zt', 'tomorrow_hit_zt_noreason',
                'tomorrow_hit_zt1', 'tomorrow_hit_zt1_noreason',
                'tomorrow_trend_row', 'tomorrow_trend_nh', 'tomorrow_trend_pos',
                'tomorrow_trend_sealed', 'tomorrow_trend_pct', 'tomorrow_trend_pct_missing',
                'tomorrow_overlap', 'tomorrow_overlap_none', 'tomorrow_watch', 'tomorrow_bad',
                'tomorrow_missing_ind', 'tomorrow_missing_trend',
                'tomorrow_hits_none', 'tomorrow_trend_none',
                'tomorrow_char_na'):
        assert key in t, f'app/intraday/templates.json 少了 {key}'


def test_两榜SQL都把股性判据钉在查询里():
    """股性过滤（用户 11-04 追加）住在 SQL 层而不是 Python 事后删——
    事后删会把 LIMIT 先取走的 middle/weak 换成别人，名单语义就变了。
    这里钉的是：两个短名单查询都 join 了 stock_character_daily 且只留 t10_char_keep 里的档。"""
    import inspect
    for fn in (T._hits_shortlist, T._trend_shortlist):
        src = inspect.getsource(fn)
        assert 'stock_character_daily' in src, f'{fn.__name__} 没接股性表'
        assert 'char_level' in src and 't10_char_keep' in src, f'{fn.__name__} 没用 t10_char_keep 判据'


def test_配置里股性只留strong():
    cfg = json.loads((ROOT / 'app' / 'intraday' / 'broadcast_config.json')
                     .read_text(encoding='utf-8'))
    assert cfg.get('t10_char_keep') == ['strong'], 't10_char_keep 默认应是 ["strong"]（只留股性强）'


def test_collect_股性判不了的票照留但行尾点名(monkeypatch):
    """char_level=None（上市不足 120 场）⇒ 不删，行尾加"股性判不了"；char_level='strong' ⇒ 不加点名。"""
    hits = [{'security_code': '600001.SH', 'name': '甲', 'board': '主板',
             'hits_today': 4, 'days_in_top': 1, 'rank_sum': 10, 'inds': [], 'zt': None,
             'char_level': 'strong'},
            {'security_code': '600002.SH', 'name': '乙', 'board': '主板',
             'hits_today': 4, 'days_in_top': 1, 'rank_sum': 20, 'inds': [], 'zt': None,
             'char_level': None}]
    monkeypatch.setattr(T, '_hits_shortlist', lambda day, cfg: (hits, dt.date(2026, 11, 4)))
    monkeypatch.setattr(T, '_trend_shortlist', lambda day, cfg: ([], dt.date(2026, 11, 4)))
    monkeypatch.setattr(T, '_top_boards', lambda day: [])
    text = T.render(T.collect(dt.datetime(2026, 11, 4, 19, 0)), dt.datetime(2026, 11, 4, 19, 0))
    assert '股性判不了' in text, 'char_level=None 的票行尾要点名"股性判不了"'
    # 只应出现一次（乙那行），甲那行（strong）不该带
    assert text.count('股性判不了') == 1
    assert '仅留股性强' in text, '节标题要点明已按股性过滤'


# ───────────────────────────────────────────────────────────────── collect() 新口径（11-04 写）

_HIT = {'security_code': '600001.SH', 'name': '甲', 'board': '主板',
        'hits_today': 5, 'days_in_top': 3, 'rank_sum': 100,
        'inds': ['10日涨幅', '250日位置'], 'zt': None}
_TREND = {'security_code': '600001.SH', 'name': '甲', 'board': '主板',
          'pct_change': '2.00', 'ma5_streak': 9, 'nh_20': True,
          'sealed': False, 'pos250': '80.0'}


def test_collect_两榜交集只念一只且计数去重(monkeypatch):
    """两榜都有 600001.SH ⇒ "两榜都进"只念一次；观察行按**并集**数家数（3 只，不是 2+3）。"""
    monkeypatch.setattr(T, '_hits_shortlist',
                        lambda day, cfg: ([{**_HIT}, {'security_code': '600002.SH', 'name': '乙',
                                                     'board': '主板', 'hits_today': 4, 'days_in_top': 2,
                                                     'rank_sum': 200, 'inds': [], 'zt': None}],
                                          dt.date(2026, 11, 4)))
    monkeypatch.setattr(T, '_trend_shortlist',
                        lambda day, cfg: ([{**_TREND}, {'security_code': '600003.SH', 'name': '丙',
                                                        'board': '创业板', 'pct_change': None,
                                                        'ma5_streak': 6, 'nh_20': False,
                                                        'sealed': True, 'pos250': None}],
                                          dt.date(2026, 11, 4)))
    monkeypatch.setattr(T, '_top_boards',
                        lambda day: [{'name': '丁', 'consec': 3}])
    data = T.collect(dt.datetime(2026, 11, 4, 19, 0))
    text = T.render(data, dt.datetime(2026, 11, 4, 19, 0))
    assert '两榜都进：甲' in text and text.count('两榜都进：') == 1
    assert '上面 3 只' in text, '交集股被数了两次（应数并集）'
    assert '今封板' in text, 'sealed 且无涨停池记录也要念"今封板"以外的封板标志'
    assert '· 今日缺数' in text, 'pct_change 为空的票要念"今日缺数"，不写 0'
    assert '20 日新高' in text and '250 日位置 80%' in text


def test_collect_两榜都缺场次时返回空_一条不发(monkeypatch):
    """indicator 与 trend_stock 最新场次都不是今天 ⇒ collect() 返回 {} ⇒ 今天不发。"""
    yesterday = dt.date(2026, 11, 3)
    monkeypatch.setattr(T, '_hits_shortlist', lambda day, cfg: (None, yesterday))
    monkeypatch.setattr(T, '_trend_shortlist', lambda day, cfg: (None, yesterday))
    assert T.collect(dt.datetime(2026, 11, 4, 19, 0)) == {}


def test_collect_只有一榜缺场次时照发并念缺数(monkeypatch):
    """指标榜缺场次、趋势榜有 ⇒ 一、只念缺数，二、三、照常（不许整条不发，也不许拿旧场次顶）。"""
    monkeypatch.setattr(T, '_hits_shortlist', lambda day, cfg: (None, dt.date(2026, 11, 3)))
    monkeypatch.setattr(T, '_trend_shortlist',
                        lambda day, cfg: ([_TREND], dt.date(2026, 11, 4)))
    monkeypatch.setattr(T, '_top_boards', lambda day: [])
    text = T.render(T.collect(dt.datetime(2026, 11, 4, 19, 0)), dt.datetime(2026, 11, 4, 19, 0))
    assert '多指标靠前缺数' in text and '2026-11-03' in text
    assert '二、趋势最正' in text and '站上 5 日线 9 日' in text
    assert '什么算坏了' in text and '指标榜前排' not in text, '缺的那一节不许出现在"算坏了"里'


def test_collect_指标行按连板数念_1板念今封板(monkeypatch):
    """涨停池里 1 连板 ⇒ "今封板"；2 连板 ⇒ "2 连板"（"1 连板"是自相矛盾的措辞）。"""
    hits = [{'security_code': '600001.SH', 'name': '甲', 'board': '主板',
             'hits_today': 4, 'days_in_top': 1, 'rank_sum': 10, 'inds': [],
             'zt': {'consec': 1, 'reason': 'A题材'}},
            {'security_code': '600002.SH', 'name': '乙', 'board': '主板',
             'hits_today': 4, 'days_in_top': 1, 'rank_sum': 20, 'inds': [],
             'zt': {'consec': 2, 'reason': None}}]
    monkeypatch.setattr(T, '_hits_shortlist', lambda day, cfg: (hits, dt.date(2026, 11, 4)))
    monkeypatch.setattr(T, '_trend_shortlist', lambda day, cfg: ([], dt.date(2026, 11, 4)))
    monkeypatch.setattr(T, '_top_boards', lambda day: [])
    text = T.render(T.collect(dt.datetime(2026, 11, 4, 19, 0)), dt.datetime(2026, 11, 4, 19, 0))
    assert '今封板（题材归因：A题材）' in text
    assert '2 连板' in text and '1 连板' not in text
    assert '今日无站上 5 日线 ≥5 日的票' in text


def test_留痕分类把这一路算进针数而不是缺数():
    """`fold_ledger` 那一格里没登记 `tomorrow` 的话，屏上会把正常一轮读成"缺数/降级"。"""
    got = store.fold_ledger([{'task': 'tomorrow', 'what': 'tomorrow', 'n': 1,
                              'first_at': dt.datetime(2026, 11, 4, 19, 0),
                              'last_at': dt.datetime(2026, 11, 4, 19, 0),
                              'all_ok': True, 'last_err': None}])
    assert got['tomorrow']['rounds'] == 1 and got['tomorrow']['notes'] == []


def test_pytest_进程里硬挡真写留痕表():
    with pytest.raises(RuntimeError, match='analytics 表'):
        store.write_broadcast(dt.datetime(2026, 11, 4, 19, 0), task='tomorrow',
                              what='tomorrow', body='x')
