# -*- coding: utf-8 -*-
"""
**每一张展示卡片都必须有说明。**

2026-08-30 盘点出来的事实：39 张卡里有 16 张点不出任何说明 —— 其中 9 张是
`stat` / `task_list` / `markdown` 这三类卡**渲染器压根没接 note 参数**
（首页那 6 个指标块是全站唯一没有说明入口的一批），另外 7 张是 `CARD_HELP`
里没写。数字越短越容易误读（"涨停家数 41"到底是按 `limit_up` 判的还是按
9.8% 判的，差一大截），所以这条用测试锁住：

- 每张可见卡片都要能解析出一段说明（布局层 `options.note` → `CARD_HELP`
  → `config.chart.options.note` 三级兜底任意一级命中）；
- 说明里要能看出**数据来源**（提到表名或脚本名），不是只有一句"这是什么"。

`markdown` 卡例外：它的正文本身就是说明。
"""
from __future__ import annotations

import re

import pytest

from app.db import engine
from app.web import ui

# 说明里至少要出现一处数据来源的痕迹：库表名或脚本名。
SOURCE_RE = re.compile(r'(md\.|fct\.|config\.|ops\.|ref\.|\.py|q_[a-z_]+|k_[a-z_]+)')

FALLBACK_KEY = {'task_list': 'q_task_recent', 'score_panel': 'q_score_parts'}


def _cards() -> list[dict]:
    return engine.query(
        'SELECT page_key, slot, card_kind, card_ref, title, options '
        'FROM config.page_layout WHERE visible ORDER BY page_key, slot')


def _charts() -> dict[str, dict]:
    return {c['chart_key']: c for c in engine.query('SELECT * FROM config.chart')}


def _note_of(row: dict, charts: dict[str, dict]) -> str | None:
    """
    跟 `ui.render_card` 里那几级兜底保持一致。

    **`config.help_note` 排在最前**（2026-08-31 补）：`render_card` 早就改成
    库优先了，这里却只看 `options.note` / `ui.CARD_HELP` / 图自己的 note ——
    结果是"说明只写进库、没在代码里留一份"的新卡在这里假红，而页面上明明点得出
    说明。判据必须和渲染路径同源，否则这条测试会逼着人把长说明再往代码里抄一遍。
    """
    from app.web import help as help_repo
    ref = row['card_ref'] or ''
    opts = row['options'] or {}
    key = ref or FALLBACK_KEY.get(row['card_kind'], '')
    note = (help_repo.for_card(key)
            or help_repo.for_card(f"{row['page_key']}.{row['slot']}")
            or opts.get('note')
            or ui.CARD_HELP.get(key))
    if not note and row['card_kind'] == 'chart':
        note = ((charts.get(ref) or {}).get('options') or {}).get('note')
    return note


def _ids(cards):
    return [f"{c['page_key']}#{c['slot']}:{c['card_ref'] or c['card_kind']}"
            for c in cards]


CARDS = [c for c in _cards() if c['card_kind'] != 'markdown']
CHARTS = _charts()


@pytest.mark.parametrize('card', CARDS, ids=_ids(CARDS))
def test_every_card_has_a_note(card):
    note = _note_of(card, CHARTS)
    assert note, (f"{card['page_key']}#{card['slot']}「{card['title']}」"
                  f"（{card['card_kind']} {card['card_ref']}）没有任何说明")


@pytest.mark.parametrize('card', CARDS, ids=_ids(CARDS))
def test_note_says_where_the_data_comes_from(card):
    note = _note_of(card, CHARTS) or ''
    assert SOURCE_RE.search(note), (
        f"{card['page_key']}#{card['slot']}「{card['title']}」的说明没提数据来源"
        '（库表名 / 命名查询 / 脚本名至少要有一个）')


def test_note_capable_kinds_actually_render_it():
    """
    渲染器必须真的把 note 传下去。只检查 `CARD_HELP` 有条目是不够的 ——
    2026-08-30 之前 `stat` 分支根本没有 note 参数，说明写了也显示不出来。
    """
    import inspect
    src = inspect.getsource(ui.render_card)
    for kind in ('stat', 'task_list', 'score_panel', 'markdown'):
        i = src.index(f"kind == '{kind}'")
        # 该分支到下一个 `if kind ==` 之前的片段里必须出现 note=
        j = src.find("if kind == '", i + 10)
        seg = src[i:j if j > 0 else len(src)]
        assert 'note=note' in seg, f'{kind} 分支没有把 note 传给渲染'
    assert 'note' in inspect.signature(ui.stat).parameters
