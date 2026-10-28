# -*- coding: utf-8 -*-
"""首页「涨停与连板」读侧（`duanxian/limit_up_board.py`）的闸 —— 含一条**不打桩**的真库对账。

为什么要有这个文件：四个用桩的测试（`test_board_span` / `test_seal_strength` /
`test_session_date_honesty` / `test_first_board_sentiment`）现在都吃
`tests/support_limit_up_board.py` 造的假读数。**假读数与真读侧分叉时，那四份会一起绿**
（10-10 那次 18 小时假绿就是这个形状：桩把字段打成另一种类型，两边一起错）。
所以这里补三条：假读数的键必须盖住真读数的键；界面读到的家数必须等于库里现算的家数；
出口不许有 NaN/NaT，也不许出现"6.0 板"这种带小数点的名次。
"""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
# 追加而不是插 0：仓库根有 `app/` 包、vr 里有个 `app.py`，插到前面会让
# `from app.compute import …` 拿到 vr/app.py 这个模块而报"不是包"。
sys.path.append(str(ROOT / "vr"))

from app.compute import limit_up_pool as L                # noqa: E402
from app.db import engine                                 # noqa: E402
from duanxian import limit_up_board as B                  # noqa: E402
from duanxian import pg                                   # noqa: E402


@pytest.fixture(scope='module')
def landed_day():
    """库里已经铺到的最近一场（没铺过就整文件 skip，不拿"没跑过"当"通过"）。"""
    if not pg.enabled():
        pytest.skip('本机没有 PG 读侧配置')
    d = engine.scalar(f"select max(trade_date) from {L.TABLE} where spec_version = %s",
                      (L.SPEC_VERSION,))
    if not d:
        pytest.skip(f'{L.TABLE} 还没有行 —— 先跑 python -m app.compute.limit_up_pool')
    return str(d)


# ---------------------------------------------------------------- 桩形与真契约

def test_假读数的键必须盖住真读数的键(landed_day):
    """四条用桩的测试都吃 `support_limit_up_board.fake_board`。
    它少一个键 ⇒ 那四条一起绿、真界面一起瞎。这一条就是防这件事的。"""
    import support_limit_up_board as sup

    real, fake = B.board_on(landed_day), sup.fake_board([])
    少了 = set(real) - set(fake)
    assert not 少了, f"桩里没有这些键，读侧真会交出来：{sorted(少了)}"


# ---------------------------------------------------------------- 真库对账（不打桩）

def test_界面读到的家数等于库里现算的家数(landed_day):
    out = B.board_on(landed_day)
    n_sealed = engine.scalar("select count(*) from " + L.TABLE +
                             " where spec_version = %s and trade_date = %s and sealed",
                             (L.SPEC_VERSION, landed_day))
    n_rows = engine.scalar("select count(*) from " + L.TABLE +
                           " where spec_version = %s and trade_date = %s",
                           (L.SPEC_VERSION, landed_day))
    assert out['sealed_count'] == int(n_sealed)
    assert out['rows_total'] == int(n_rows) == len(out['rows']), "行数被悄悄筛过就要在这里红"
    assert out['broken_count'] == int(n_rows) - int(n_sealed), \
        "母体是触过板的全部：封住 + 收盘没封住 = 全部，少一边就是分母冒充"

    # 再往上一层：库里的封住数必须等于日线现算的那一个数（同一把半分容差）
    from app.compute.limit_prices import LIMIT_EPS

    now = engine.scalar("select count(*) from market_data.stock_daily "
                        "where trade_date = %s and limit_up is not null "
                        "and abs(close - limit_up) <= %s", (landed_day, LIMIT_EPS))
    assert int(n_sealed) == int(now), "落库行与日线现算不一致 = 写侧的尺和读侧的尺分叉了"


def test_连板家数与最高连板是从同一批行数出来的(landed_day):
    """`lianban` 必须正好是"封住里 consec≥2 的那批"：漏一只或混进 1 板，
    界面上那一句「连板 12 家」与「最高 6 板」就会互相对不上。"""
    out = B.board_on(landed_day)
    assert out['lianban'] == [r for r in out['sealed'] if (r['consec'] or 0) >= 2]
    assert all((r['consec'] or 0) >= 2 for r in out['lianban']), "连板榜里混进 1 板就是母体串了"
    if out['sealed']:
        top = max(r['consec'] or 0 for r in out['sealed'])
        assert top >= 2 and top == max(r['consec'] for r in out['lianban']), \
            "最高连板那只必须就在榜里第一档，否则界面上「最高 N 板」与「连板 M 家」是两批数"


def test_出口不许有NaN_NaT且名次必须是整数(landed_day):
    out = B.board_on(landed_day)
    text = json.dumps(out, ensure_ascii=False, allow_nan=False)
    for bad in ('NaN', 'NaT', 'Infinity'):
        assert bad not in text, f"非法 JSON 字面量 {bad} 会让浏览器 JSON.parse 直接炸"
    row = (out['lianban'] or [{}])[0]
    for k in ('consec', 'reopen_count', 'at_limit_minutes', 'minute_bars', 'seal_raw'):
        v = row.get(k)
        assert v is None or isinstance(v, int), f"{k} 读出来是 {type(v).__name__}：屏幕会念成 6.0 板"
    assert ':' in (row.get('seal_time') or '00:00'), "首封必须是 HH:MM 字符串而不是 time 对象"


def test_库里没有的那一场说得出该跑哪一步():
    out = B.board_on('1999-01-01')
    assert out['available'] is False and 'limit_up_pool' in out['reason']
    assert 'rows' not in out, "不可用时不许带一个空的 rows 键 —— 空数组会被读成「这一场真的没有」"
