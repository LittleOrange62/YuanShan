# -*- coding: utf-8 -*-
"""「首板 · 概念板块 重复出现的类别」那张榜（用户 2026-10-29 点名：按重复只数从高到低、列前 10）。

钉的三件事，每一件都对应本项目的一档老毛病：
1. **只列前 N 是截断**，被截掉的条数必须一起交回（不然"只剩十条"看着像今天只有十类题材）；
2. **查询坏了 ≠ 这批票没有题材**（`failed` 要单独一条路，界面据此说不同的话）；
3. 数只数、按名字去重、成员数与集中度这些**只在 `pg_sources.concept_rollup` 一份**，
   本模块只挑前 N 并转述分母 —— 这里再排一次序、再算一次比例，就是第二份真相源。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
VR = ROOT / "vr"
if str(VR) not in sys.path:
    # 追加而不是插到 0：仓库根有 `app/` 包，vr 里有个 `app.py`，插到 0 会让
    # `from app.compute import …` 拿到 vr/app.py 这个模块而报"不是包"。
    sys.path.append(str(VR))

import firstboard            # noqa: E402
from duanxian import pg_sources   # noqa: E402


def _rows(n: int):
    return [{'concept': f'概念{i}', 'stocks': max(1, 12 - i), 'members_total': 100 + i * 7,
             'pool_share_pct': round(100 * max(1, 12 - i) / 30, 1),
             'lift_pctile': round(100 * max(1, 12 - i) / (100 + i * 7), 1),
             'sample_codes': '000001、000002'} for i in range(n)]


ROLLS = {}


@pytest.fixture(autouse=True)
def stub(monkeypatch):
    def fake(codes):
        return dict(ROLLS, pool_size=len(codes))
    monkeypatch.setattr(pg_sources, 'concept_rollup', fake)


def test_只列前十条而总数与分母一起交回(monkeypatch):
    ROLLS.update(rows=_rows(23), covered=19, failed=False)
    monkeypatch.setattr(firstboard, 'CONCEPT_TOP', 10)
    b = firstboard.concept_board([f'{i:06d}' for i in range(30)])
    assert len(b['rows']) == 10 and b['rows_total'] == 23 and b['top'] == 10
    assert b['pool_size'] == 30 and b['covered'] == 19, '分母与"有几只挂得上"要交回，否则十条会被读成十只票'
    # 顺序照后端那一份（只数从高到低），这里不许再排一次
    assert [r['stocks'] for r in b['rows']] == sorted([r['stocks'] for r in b['rows']], reverse=True)
    assert b['rows'][0]['concept'] == '概念0'


def test_榜不满十条时不谎报有截断(monkeypatch):
    ROLLS.update(rows=_rows(4), covered=4, failed=False)
    monkeypatch.setattr(firstboard, 'CONCEPT_TOP', 10)
    b = firstboard.concept_board(['000001', '000002'])
    assert len(b['rows']) == 4 and b['rows_total'] == 4
    assert b['pool_size'] == 2


def test_查询坏了要单独出声而不是空榜(monkeypatch):
    """`failed` 与"这批票没有概念"是两句话：前者是读不到，后者是事实。"""
    ROLLS.update(rows=[], covered=None, failed=True)
    monkeypatch.setattr(firstboard, 'CONCEPT_TOP', 10)
    b = firstboard.concept_board(['000001'])
    assert b['failed'] is True and b['rows'] == []
    assert b['covered'] is None, '查询坏了时不许把"覆盖几只"兜成 0（0 只＝一只都没挂上，那是另一件事）'


def test_口径文字里必须带着那条会骗人的分母(monkeypatch):
    ROLLS.update(rows=_rows(2), covered=2, failed=False)
    b = firstboard.concept_board(['000001', '000002'])
    # 一只票可挂多个概念 ⇒ 各条加起来大于分母；不写这句，屏上那几列就会被读成百分比（534.0% 那个鬼数犯过）
    assert '不是百分比' in b['caliber'] and '大于分母' in b['caliber']
    assert '东财' in b['caliber'], '要说清这一张与旁边那张「所属行业」是两套归属体系，不能互证'


def test_TOP_只在一处定义不许界面再截一刀():
    src = (ROOT / 'frontend' / 'src' / 'pages' / 'FirstBoard.tsx').read_text(encoding='utf-8')
    card = src[src.index('function ConceptCard'):]
    assert 'concept_board' in src and '.slice(0,' not in card[:4000], \
        '界面又自己截前 N 条 ⇒ 与后端那份 CONCEPT_TOP 会漂'
    home = (ROOT / 'frontend' / 'src' / 'components' / 'home' / 'MarketPanorama.tsx').read_text(encoding='utf-8')
    assert 'to="/first-board?to=first-board-daily"' in home, '首页那块搬走后要留一个能点到锚点的入口'
    assert '<FirstBoardDailyPanel' not in home, '首页不许再把那一块本身挂回来（同一块屏两处＝改判据只改一边）'
    # 「短线复盘」这一个词只准指 /first-board 那一屏：复盘报告页的 h1 不许再蹭
    review = (ROOT / 'frontend' / 'src' / 'pages' / 'AgentReview.tsx').read_text(encoding='utf-8')
    assert '短线复盘看板' not in review, '页内 h1 又写回「短线复盘看板」＝与 /first-board 抢同一个名字'
