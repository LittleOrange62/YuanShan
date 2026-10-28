# -*- coding: utf-8 -*-
"""
`config.help_note` 与 `app/web/help.py`：说明入库之后，"说明"本身也要能被测。

盯三件事：
1. 字段是**按固定顺序拼**的 —— 顺序一乱，全站说明的读法就不一致了
2. `includes` 能展开、**成环要报错**（静默截断的话页面上只是少一段话，没人发现）
3. 占位符只替换配了的，没配的原样留着（悄悄删掉数字会让句子读起来像真的）
"""
from __future__ import annotations

import pytest

from app.db import engine
from app.web import help as help_repo

TMP = ('__t_help_a__', '__t_help_b__')


@pytest.fixture
def tmp_notes():
    """造几条临时说明，用完删掉 —— 别在库里留测试垃圾。"""
    yield
    engine.execute('DELETE FROM config.help_note WHERE note_key = ANY(%s)',
                   (list(TMP),), role='app')
    help_repo.reload()


def test_fields_render_in_a_fixed_order(tmp_notes):
    help_repo.save(TMP[0], {
        'scope': 'card', 'target': '__t_target__',
        'purpose': '为什么有它', 'content': '怎么读',
        'source_query': 'q_demo', 'source_tables': 'md.a, fct.b',
        'script': 'scripts/demo.py',
        'derive': '上游 → 谁算的', 'formula': '分 = 甲 + 乙', 'caveat': '有个坑'})
    txt = help_repo.text(TMP[0])
    order = ['为什么有它', '怎么读', 'q_demo', '上游 → 谁算的', '分 = 甲 + 乙',
             '有个坑']
    pos = [txt.index(x) for x in order]
    assert pos == sorted(pos), f'说明段落顺序不对：{txt}'
    # 取数三件套拼在同一段里
    assert '`md.a`' in txt and '`fct.b`' in txt and 'scripts/demo.py' in txt
    # caveat 自动补 ⚠️
    assert '⚠️ 有个坑' in txt
    # 标题不重复显示（卡片标题就在说明徽标左边）
    assert '**二次加工**' in txt and '**口径**' in txt


def test_includes_are_expanded(tmp_notes):
    help_repo.save(TMP[1], {'scope': 'snippet', 'formula': '公共口径一句话'})
    help_repo.save(TMP[0], {'scope': 'card', 'target': '__t_target__',
                            'content': '正文', 'includes': TMP[1]})
    txt = help_repo.text(TMP[0])
    assert '正文' in txt and '公共口径一句话' in txt


def test_include_cycle_raises_instead_of_looping(tmp_notes):
    help_repo.save(TMP[0], {'scope': 'snippet', 'content': 'A',
                            'includes': TMP[1]})
    help_repo.save(TMP[1], {'scope': 'snippet', 'content': 'B',
                            'includes': TMP[0]})
    with pytest.raises(ValueError, match='循环引用'):
        help_repo.render(TMP[0])
    # 但页面入口不能因此崩：拿不到就返回 None，卡片照旧走兜底
    assert help_repo.text(TMP[0]) is None


def test_placeholders_only_replace_what_is_configured(tmp_notes):
    help_repo.save(TMP[0], {'scope': 'card', 'target': '__t_target__',
                            'content': '窗口 {win} 天，阈值 {miss}'})
    engine.execute("UPDATE config.help_note SET params = '{\"win\": 20}'::jsonb "
                   ' WHERE note_key = %s', (TMP[0],), role='app')
    help_repo.reload()
    txt = help_repo.text(TMP[0])
    assert '窗口 20 天' in txt
    assert '{miss}' in txt, '没配的占位符要原样留着，不能悄悄删成空'


def test_target_lookup_is_scoped(tmp_notes):
    help_repo.save(TMP[0], {'scope': 'stat', 'target': '__t_stat__',
                            'content': '指标块说明'})
    assert help_repo.for_stat('__t_stat__')
    # 同一个 target 在别的 scope 下查不到 —— 卡片和指标块可能同名
    assert help_repo.for_card('__t_stat__') is None
    assert help_repo.for_page('__t_stat__') is None


def test_snippet_needs_no_target_but_others_do(tmp_notes):
    help_repo.save(TMP[0], {'scope': 'snippet', 'content': '片段'})   # 不报错
    with pytest.raises(ValueError, match='target 必填'):
        help_repo.save(TMP[1], {'scope': 'card', 'content': '没 target 的卡'})


# ---------------------------------------------------------------- 覆盖率

def test_every_visible_card_and_page_has_a_note_in_the_db():
    """
    库里必须覆盖**所有**可见卡片与所有菜单页。

    代码兜底（`ui.CARD_HELP`）不算覆盖：兜底是过渡状态，算成覆盖就永远迁不完。
    新加一张卡不写说明，这条会红。
    """
    missing = [f"{r['kind']} {r['where']}（{r['target']}）"
               for r in help_repo.coverage() if not r['has_note']]
    assert not missing, '这些窗口在 config.help_note 里没有说明：' + ' / '.join(missing)


def test_notes_say_where_the_data_comes_from():
    """
    每条卡片/页面说明都要能看出数据来源：命名查询、库表、脚本，至少一个。
    引用的公共片段里带来源也算 —— 组合本来就是为了不抄。
    """
    notes = help_repo.notes()
    bad = []
    for key, r in notes.items():
        if r['scope'] == 'snippet':
            continue
        chain = [r] + [notes[i] for i in (r['includes'] or []) if i in notes]
        if not any(c['source_query'] or c['source_tables'] or c['script']
                   for c in chain):
            bad.append(key)
    assert not bad, '这些说明没写数据来源（库表/命名查询/脚本至少一个）：' + str(bad)


def test_page_notes_cover_every_menu_route():
    routes = {r['route'] for r in engine.query(
        'SELECT route FROM config.menu WHERE visible')}
    have = {r['target'] for r in help_repo.notes().values()
            if r['scope'] == 'page'}
    assert routes <= have, f'这些路由没有整页说明：{routes - have}'
