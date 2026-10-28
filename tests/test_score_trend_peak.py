# -*- coding: utf-8 -*-
"""窗口趋势那一列「高点在窗口第几天」（用户 2026-10-15 点名要的数字＋词）。

钉的三件事：
① **数的是有评分日，不是日历日** —— 与斜率同一把尺子；长假一压，日历日会把"第 8 天"算成"第 3 天"。
② **定位不了就交 None**，页面显示 `—`；猜一个档就是把"不知道"卖成结论。
③ **词的阈值只住后端一处**（`_peak_position`），页面转述；所以句子内容也在后端断言，
   改措辞要连着改这里，不许悄悄把屏幕上的话换掉。

最后一条不打桩：真库现查一只票，序号必须真的落在 1..window 内。
"""
import pytest

from duanxian import quant as Q

# 2026-09-01 起的一段"有评分日"，故意中间断掉几天（模拟停牌/缺分），
# 再模拟一段长假：日历上隔 9 天，序列里只隔 1 场。
SEQ = ['2026-09-21', '2026-09-22', '2026-09-23', '2026-09-24', '2026-09-25',
       '2026-09-28', '2026-09-29', '2026-09-30',
       '2026-10-08', '2026-10-09', '2026-10-12', '2026-10-13', '2026-10-14']


@pytest.fixture
def patch_rows(monkeypatch):
    def _apply(extra_empty=False):
        def fake_rows(sql, params=None):
            if 'distinct trade_date from analytics.score' in sql:
                # producer 侧那条是 `order by trade_date desc limit N`，这里照 desc 交
                return [{'trade_date': d} for d in sorted(SEQ, reverse=True)]
            return []
        monkeypatch.setattr(Q, '_rows', fake_rows)
    return _apply


def test_序号按有评分日数_长假不算空档(patch_rows):
    patch_rows()
    rows = [{'trade_date': '2026-10-14', 'x_plus_y_max_date': '2026-10-12'},
            # 峰值在 09-30：日历上离 10-14 有 14 天，序列里只差 5 场 ⇒ 第 5 天
            {'trade_date': '2026-10-14', 'x_plus_y_max_date': '2026-09-30'},
            # 峰值就是基准日自己
            {'trade_date': '2026-10-13', 'x_plus_y_max_date': '2026-10-13'}]
    reason, out = Q._peak_day_no(rows, 10, '000601.SZ')
    assert [r['x_plus_y_max_day_no'] for r in out] == [8, 5, 10]
    assert reason is None


def test_峰值不在窗口内或序列不够长时交空不猜档(patch_rows):
    patch_rows()
    rows = [{'trade_date': '2026-09-22', 'x_plus_y_max_date': '2026-09-21'},   # 窗口起点在拿到的序列之前
            {'trade_date': '2026-10-14', 'x_plus_y_max_date': None},           # 后端没交峰值日
            {'trade_date': '2026-10-14', 'x_plus_y_max_date': '2026-08-03'}]   # 峰值日不在这段序列里
    reason, out = Q._peak_day_no(rows, 10, 'X')
    assert out[0]['x_plus_y_max_day_no'] is None, \
        '基准日离序列开头不足 10 场时窗口起点算不出来 ⇒ 交空，不许把"第 2 天"当成窗口第 2 天'
    assert out[1]['x_plus_y_max_day_no'] is None      # 词由视图那一层补（`x_plus_y_max_position`）
    assert out[2]['x_plus_y_max_day_no'] is None
    assert reason and '2 场' in reason, '定位不了的场次要数出来并说出为什么，不能整列静默变 `—`'


def test_词与阈值():
    assert Q._peak_position(10, 10) == '就在这一场（刚创新高）'
    assert Q._peak_position(9, 10) == '最近两天'
    assert Q._peak_position(3, 10) == '起点附近（高点早已过去）'
    assert Q._peak_position(6, 10) == '中途见顶'
    # 窗口只有 2 场时不能出现两个词抢同一档，也不能出现空档
    assert Q._peak_position(2, 2) == '就在这一场（刚创新高）'
    assert Q._peak_position(1, 2) in ('最近两天', '起点附近（高点早已过去）')
    assert Q._peak_position(None, 10) is None
    assert Q._peak_position(1, 1) is None      # 窗口不足 2 场：没有"位置"可说


def test_屏幕那句阈值是后端交的_not由页面拼():
    # 视图返回体里必须有 peak_rule 与两个新 definitions 键，页面只转述
    import inspect
    src = inspect.getsource(Q.score_trend)
    assert "'peak_rule'" in src and "'x_plus_y_max_day_no'" in src and "'x_plus_y_max_position'" in src


def test_真库现查_序号要真的落在窗口里():
    """不打桩：桩绿不代表接线对（10-10 那次 18 小时假绿就是这么欠的）。"""
    try:
        d = Q.score_trend('000601.SZ', window=10, sessions=12)
    except Exception as e:  # noqa: BLE001
        pytest.skip(f'本机库不可用：{e}')
    assert d.get('available'), d.get('reason')
    rows = d['rows']
    got = [r.get('x_plus_y_max_day_no') for r in rows if r.get('x_plus_y_max_date')]
    assert got, '这一场窗口里应该有能定位的峰值'
    assert all(1 <= g <= d['window_days'] for g in got), got
    assert any(r.get('x_plus_y_max_position') for r in rows), '词没交出来，屏幕上就只剩日期'
    assert '第几个有评分日' in d['definitions']['x_plus_y_max_day_no']
