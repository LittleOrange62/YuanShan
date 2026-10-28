# -*- coding: utf-8 -*-
"""提示词槽位（系统配置页改的那些话术）的回归。

要钉住三件事，件件都是"错了不会报错、只会静默给出别的答案"：
 1. **内置默认 = 页面原来那份话术**。话术从前端组件里搬进注册表时，一个字都不许在搬运中变味，
    尤其那几句产品边界的措辞（不给点位/不构成投资建议）—— 少一句不会被任何测试发现，
    只会让模型开始给建议。
 2. **未知占位符存不进去**。写错的字段名如果静默通过，运行时会留下一个没换掉的 `{pice}`，
    用户看到的是"数据没带进去"，而真实原因是他三天前打错的一个字母。
 3. **"用的是哪一份"必须交回页面**。库里那份和内置那份长得不一样时，
    只有 `source`/`edited` 这一句能让人分辨自己改的到底生效没有。
"""
from __future__ import annotations

import pytest

from duanxian import prompt_slots as ps

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------- 内置默认的话术本身

def test_连板槽位默认话术保留了产品边界那几句():
    tpl = ps.SLOTS['lianban_dive']['default']
    for phrase in ('不给交易动作、点位、仓位或涨跌预测', '不构成投资建议',
                   '已知事实', '尚未核实', '后续核验条件'):
        assert phrase in tpl, f'搬运话术时丢了「{phrase}」—— 这句是红线，不是修辞'


def test_两个槽位的默认话术都带得上数据的占位符():
    for slot, meta in ps.SLOTS.items():
        used = set(ps.PLACEHOLDER.findall(meta['default']))
        assert used, f'{slot} 的默认话术里一个占位符都没有 = 数据不会进去'
        allowed = set(ps.fields_of(meta['scope']))
        assert used <= allowed, f'{slot} 默认话术用了没登记的字段：{used - allowed}'


def test_占位符白名单不会把给模型的JSON约定吃掉():
    """`{"answer":"正文"}` 这种必须**不算**占位符，否则一句合法话术会被误判成字段不存在。"""
    assert ps.unknown_placeholders('只返回 {"answer":"正文"}，标的 {name}', 'lianban_dive') == []


# ---------------------------------------------------------------- 校验与渲染

def test_未知占位符存不进去并且点名():
    with pytest.raises(ValueError) as exc:
        ps.save('lianban_dive', '{name} 的位置是 {pice}')
    msg = str(exc.value)
    assert '{pice}' in msg and '{close}' in msg, '要把错的点名、并把可用的列出来'


def test_空话术存不进去():
    with pytest.raises(ValueError):
        ps.save('lianban_dive', '   ')


def test_没有这个槽位就抛KeyError而不是默默建一个():
    with pytest.raises(KeyError):
        ps.effective('no_such_slot')
    with pytest.raises(KeyError):
        ps.save('no_such_slot', '随便写')


def test_渲染时缺值给未提供而不是空或零():
    out = ps.render('lianban_dive', {
        'date': '2026-09-24', 'name': '某股', 'code': '002827.SZ', 'em_line': '',
        'close': 11.0, 'pct': 9.98, 'boards': 2, 'quote_line': '',
        'amount': '1.9亿', 'float_cap': '20亿', 'industry': '未知', 'reason': None})
    assert '涨停原因题材：未提供' in out
    assert '（2 连板）' in out
    # 整句开关：没有报价就没有那句"另附行情"，但也不能冒出"未提供"
    assert '另附行情' not in out and '未提供；' not in out
    assert not ps.PLACEHOLDER.search(out.split('\n\n')[0]), '第一段不许留没换掉的占位符'


# ---------------------------------------------------------------- 库里那份优先、来源如实报

@pytest.fixture
def tmp_slot(monkeypatch):
    """另开一个临时类型+槽位测读写，**绝不碰用户真在用的那两个**（他可能已经改了话术）。"""
    from app.db import engine
    key = ps.SETTING_PREFIX + 'zz_test_slot'
    engine.execute('DELETE FROM config.setting WHERE key = %s', (key,), role='app')
    monkeypatch.setitem(ps.SCOPES, 'zz_test', {"label": "测试类型", "page": "测试", "fields": {"name": "名称"}})
    monkeypatch.setitem(ps.SLOTS, 'zz_test_slot', ps._slot('测试槽', 'zz_test', '默认：{name}'))
    yield 'zz_test_slot'
    engine.execute('DELETE FROM config.setting WHERE key = %s', (key,), role='app')


def test_没存过时用内置默认并且说明来源(tmp_slot):
    row = ps.effective(tmp_slot)
    assert row['template'] == '默认：{name}'
    assert row['edited'] is False and row['source'].startswith('内置默认')
    assert row['updated_at'] is None


def test_存过之后生效的是库里那份并且交回时间(tmp_slot):
    from duanxian import pg
    if pg.fetch_df("select 1 x") is None:
        pytest.skip('本机 PG 未起来，这条只测"有库时谁优先"')
    row = ps.save(tmp_slot, '自定义：{name}，只说事实，不给交易动作与点位。')
    assert row['edited'] is True and row['source'].startswith('config.setting')
    assert row['updated_at'], '必须交回存的时间，页面要显示年龄'
    assert ps.render(tmp_slot, {'name': '某股'}) == '自定义：某股，只说事实，不给交易动作与点位。'
    # 恢复默认 = **删掉键**，不是把默认文本再存一份（存进去的那份以后就算"自定义"了）
    back = ps.reset(tmp_slot)
    assert back['edited'] is False and back['template'] == '默认：{name}'
    assert ps.effective(tmp_slot)['source'].startswith('内置默认')


def test_整段没有不给点位的话术存不进去(tmp_slot):
    """话术可编辑之后，"这一档不给交易动作与点位"不能变成配置页上随手可关的开关。

    **按意思拦、不按措辞拦**：换个说法可以，一句约束都没有不行。
    """
    with pytest.raises(ValueError) as exc:
        ps.save(tmp_slot, '自定义：{name}，请给出买卖建议。')
    assert '不给交易动作' in str(exc.value) or '不构成投资建议' in str(exc.value)
    ps.save(tmp_slot, '{name} 的情况如何？仅客观陈述，不构成投资建议。')   # 换了措辞就该过
    ps.reset(tmp_slot)


# ---------------------------------------------------------------- 用户自建模板（类型 + 话术）

@pytest.fixture
def clean_customs():
    """每条自建模板一个键，测试结束按前缀清干净，不给用户留下一堆 zz。"""
    from app.db import engine
    yield
    engine.execute("DELETE FROM config.setting WHERE key LIKE %s", (ps.CUSTOM_PREFIX + "%",), role='app')


def test_自建模板存进去后能在清单里读到并渲染(clean_customs):
    from duanxian import pg
    if pg.fetch_df("select 1 x") is None:
        pytest.skip('本机 PG 未起来')
    row = ps.save_custom('只看梯队结构', 'lianban',
                         '{date} 的 {name}（{code}）已经 {boards} 板。只陈述已发生的客观事实，不给交易动作与点位。')
    assert row['kind'] == 'custom' and row['scope'] == 'lianban'
    assert row['id'] in [r['id'] for r in ps.list_slots()], '清单里没有它 = 页面下拉选不到'
    assert ps.render_text(row['template'], {'date': '2026-09-24', 'name': '某股',
                                            'code': '002827.SZ', 'boards': 2}) == \
        '2026-09-24 的 某股（002827.SZ）已经 2 板。只陈述已发生的客观事实，不给交易动作与点位。'
    # 恢复内置默认**不许顺手删掉用户手打的模板**（两段前缀分开就是为了这件事）
    ps.reset('lianban_dive')
    assert row['id'] in [r['id'] for r in ps.list_slots()]


def test_自建模板的校验与内置槽位同一套(clean_customs):
    """四类都存不进去，而且错误里要说清是哪一类 —— 页面靠这些字串提示。"""
    cases = (
        ({'title': '  ', 'scope': 'lianban', 'template': '{name}，不构成投资建议'}, '名字'),
        ({'title': 'X', 'scope': 'no_such_scope', 'template': '{name}，不构成投资建议'}, '类型'),
        ({'title': 'X', 'scope': 'lianban', 'template': '{pice}，不构成投资建议'}, '{pice}'),
        ({'title': 'X', 'scope': 'lianban', 'template': '随便聊聊 {name}'}, '不给交易动作'),
    )
    for bad, needle in cases:
        with pytest.raises(ValueError) as exc:
            ps.save_custom(bad['title'], bad['scope'], bad['template'])
        assert needle in str(exc.value), f'{needle} 没在报错里出现：{exc.value}'


def test_按id取模板时内置与自建走同一个入口(clean_customs):
    from duanxian import pg
    if pg.fetch_df("select 1 x") is None:
        pytest.skip('本机 PG 未起来')
    row = ps.save_custom('换手率视角', 'lianban', '{name} 当日换手 {quote_line}，不构成投资建议。')
    assert ps.template_of(row['id'])['title'] == '换手率视角'
    assert ps.template_of('lianban_dive')['kind'] == 'builtin'
    assert ps.template_of('没有这个id') is None
    ps.delete_custom(row['id'])
    assert ps.template_of(row['id']) is None


def test_删除模板的id要合法不然连别人的键都会被拼掉(clean_customs):
    with pytest.raises(ValueError):
        ps.delete_custom('../../etc')
    with pytest.raises(ValueError):
        ps.delete_custom('')
