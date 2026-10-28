# -*- coding: utf-8 -*-
"""交易体系榜（三条组合判据）的判据与接线测试。

判据那几例全是纯函数（`judge` 吃合成日线，不碰库）—— 三条都是"时序形状"，
只能靠摆出来的日子验，靠读 SQL 读不出方向错。
另外钉住几类写错了不报错的事：占位符与参数数量、列名与 DDL 对不上、
除权日拿未复权价判跳空、`lag` 冒充次日、交叉源清单与页面读的两边走散。
"""
from __future__ import annotations

import datetime as dt
import json
import re

import pytest

from app.compute import system_entry as SE


def series(rows):
    """[(开, 高, 低, 收, 量)] → 判据要的那个数组形状，前收/涨跌/涨停价自动推（10% 档）。"""
    out, prev, d = [], None, dt.date(2026, 1, 2)
    for (o, h, l, c, v) in rows:
        pre = 10.0 if prev is None else prev
        out.append([d.isoformat(), o, h, l, c, pre,
                    round((c / pre - 1) * 100, 4), round(pre * 1.1, 2), v, 1.0])
        prev = c
        d += dt.timedelta(days=1)
    return out


def happy():
    """一条完整的形状：19 根平盘 → 涨停 → 回落 → 跳空缺口 → 三天缩量阴线不回补 → 今日阳线。"""
    rows = [(10.0, 10.05, 9.95, 10.0, 200)] * 19
    rows += [(11.0, 11.0, 11.0, 11.0, 900)]                      # 19：封住涨停（门票①）
    rows += [(10.8, 11.0, 10.7, 10.8, 300)] * 3                  # 20~22：回落，最高 11.0 = 未来缺口下沿
    rows += [(11.6, 11.9, 11.3, 11.7, 800)]                      # 23：整段跳空（低 11.3 > 前高 11.0，+2.7%）
    rows += [(11.6, 11.65, 11.4, 11.5, 300),                     # 24：阴线 + 缩量
             (11.5, 11.55, 11.35, 11.4, 250),                    # 25
             (11.4, 11.45, 11.3, 11.35, 200)]                   # 26：三天都没回补下沿
    rows += [(11.3, 11.9, 11.25, 11.8, 500)]                    # 27：今日阳线确认
    return series(rows)


# ---------------------------------------------------------------- 三条各自成不成立
def test_完整形状三条全过并给出止损位():
    r = SE.judge('X', happy())
    assert r is not None
    assert (r['c1_ok'], r['c2_ok'], r['c3_ok'], r['confirm_yang'], r['entry_ok']) == (
        True, True, True, True, True)
    assert r['watch_ok'] is False
    assert r['limit_up_ago'] == 8 and r['limit_up_count20'] == 1
    assert r['gap_ago'] == 4 and r['gap_unfilled'] == 4
    assert r['gap_pct'] == pytest.approx(2.727, abs=0.01)
    assert r['pullback_down_cnt'] == 3 and r['pullback_max_vol_ratio'] == pytest.approx(0.375)
    # 止损位 = 缺口下沿 = 22 号那根的最高（后复权），且一定在现价下方
    assert r['stop_ref_hfq'] == pytest.approx(11.0)
    assert r['stop_dist_pct'] < 0
    assert r['missing'] is None


def test_门票不过就不进表():
    # 把 19 号那根涨停换成普通 K 线，其余形状照旧
    rows = [(10.0, 10.05, 9.95, 10.0, 200)] * 28
    assert SE.judge('X', series(rows)) is None          # 20 场内没涨停 ⇒ 门票都没有，不进表


def test_缺口不足一个点不算跳空():
    rows = [(10.0, 10.05, 9.95, 10.0, 200)] * 19 + [(11.0, 11.0, 11.0, 11.0, 900)]
    rows += [(10.8, 11.0, 10.7, 10.8, 300)] * 3
    rows += [(11.2, 11.9, 11.04, 11.7, 800)]              # 最低价只高出前高 0.36 个点
    rows += [(11.6, 11.65, 11.4, 11.5, 300), (11.5, 11.55, 11.35, 11.4, 250),
             (11.4, 11.45, 11.3, 11.35, 200), (11.3, 11.9, 11.25, 11.8, 500)]
    assert SE.judge('X', series(rows)) is None


def test_三天里回补过就不再是证据():
    rows = [(10.0, 10.05, 9.95, 10.0, 200)] * 19 + [(11.0, 11.0, 11.0, 11.0, 900)]
    rows += [(10.8, 11.0, 10.7, 10.8, 300)] * 3
    rows += [(11.6, 11.9, 11.3, 11.7, 800)]
    rows += [(11.6, 11.65, 11.4, 11.5, 300), (11.5, 11.55, 11.35, 11.4, 250),
             (11.4, 11.45, 10.9, 11.35, 200)]             # 26 号最低 10.9 摸回下沿 11.0 之下
    rows += [(11.3, 11.9, 11.25, 11.8, 500)]
    assert SE.judge('X', series(rows)) is None


def test_今天回补了也不算():
    rows = [(10.0, 10.05, 9.95, 10.0, 200)] * 19 + [(11.0, 11.0, 11.0, 11.0, 900)]
    rows += [(10.8, 11.0, 10.7, 10.8, 300)] * 3
    rows += [(11.6, 11.9, 11.3, 11.7, 800)]
    rows += [(11.6, 11.65, 11.4, 11.5, 300), (11.5, 11.55, 11.35, 11.4, 250),
             (11.4, 11.45, 11.3, 11.35, 200)]
    rows += [(11.3, 11.9, 10.8, 11.8, 500)]               # 今日最低 10.8 已跌破下沿
    assert SE.judge('X', series(rows)) is None


def test_回调放量只进观察池():
    rows = [(10.0, 10.05, 9.95, 10.0, 200)] * 19 + [(11.0, 11.0, 11.0, 11.0, 900)]
    rows += [(10.8, 11.0, 10.7, 10.8, 300)] * 3
    rows += [(11.6, 11.9, 11.3, 11.7, 800)]
    rows += [(11.6, 11.65, 11.4, 11.5, 300), (11.5, 11.55, 11.35, 11.4, 1500),
             (11.4, 11.45, 11.3, 11.35, 200)]             # 25 号放量：段内最大量 1500 > 缺口日 800
    rows += [(11.3, 11.9, 11.25, 11.8, 500)]
    r = SE.judge('X', series(rows))
    assert r is not None and r['c2_ok'] and not r['c3_ok']
    assert r['entry_ok'] is False and r['watch_ok'] is True


def test_今日没收阳就只有观察池():
    rows = [(10.0, 10.05, 9.95, 10.0, 200)] * 19 + [(11.0, 11.0, 11.0, 11.0, 900)]
    rows += [(10.8, 11.0, 10.7, 10.8, 300)] * 3
    rows += [(11.6, 11.9, 11.3, 11.7, 800)]
    rows += [(11.6, 11.65, 11.4, 11.5, 300), (11.5, 11.55, 11.35, 11.4, 250),
             (11.4, 11.45, 11.3, 11.35, 200)]
    rows += [(11.9, 11.95, 11.3, 11.32, 500)]             # 阴线
    r = SE.judge('X', series(rows))
    assert r['c3_ok'] is True and r['confirm_yang'] is False and r['entry_ok'] is False


# ---------------------------------------------------------------- 口径本身

def test_跳空必须用后复权判_除权日不算缺口():
    """未复权看是"跳空"，后复权看是除权 —— 尺度混用的话这里会造出一个假信号。"""
    s = happy()
    for r in s[:23]:
        r[9] = 2.0                       # 缺口日之前复权因子 2.0，之后 1.0 ⇒ 前高在 hfq 尺度上是 22.0
    assert SE.judge('X', s) is None


def test_取最近那个合格缺口而不是最老的():
    rows = [(10.0, 10.05, 9.95, 10.0, 200)] * 19 + [(11.0, 11.0, 11.0, 11.0, 900)]
    rows += [(10.8, 11.0, 10.7, 10.8, 300)] * 3
    rows += [(11.6, 11.9, 11.3, 11.7, 800)]                # 23：第一个缺口（下沿 11.0）
    rows += [(12.2, 12.5, 12.05, 12.4, 700)]               # 24：又跳空一次（下沿抬到 11.9）
    rows += [(12.4, 12.5, 12.0, 12.3, 200), (12.3, 12.4, 12.05, 12.1, 180),
             (12.1, 12.2, 12.0, 12.05, 170)]               # 25~27 缩量阴线、都没回补 11.9
    rows += [(12.05, 12.6, 12.0, 12.5, 400)]               # 28 今日阳线确认
    r = SE.judge('X', series(rows))
    assert r is not None and r['gap_ago'] == 4, f"该取 24 号那个缺口：{r and r['gap_ago']}"
    assert r['gap_floor_hfq'] == pytest.approx(11.9)        # 下沿是 24 号前一根的最高，不是 11.0


def test_日线不足20根时门票不成立():
    s = happy()[:15]
    assert SE.judge('X', s) is None


def test_缺涨停价的那几天不冒充门票():
    s = happy()
    for r in s:
        r[7] = None                                        # 整段都没有涨停价（库里缺 limit_up）
    assert SE.judge('X', s) is None


# ---------------------------------------------------------------- 写侧接线

def test_列名与DDL对得上():
    ddl = open('app/db/sql/33_system_entry.sql', encoding='utf-8').read()
    for c in SE._COLUMNS:
        assert f'{c} ' in ddl or f'{c}\t' in ddl or f'{c}  ' in ddl, f'{c} 不在 DDL 里'
    assert 'primary key (security_code, trade_date, spec_version)' in ddl
    assert SE.UPSERT.count('{cols}') == 1 and 'on conflict' in SE.UPSERT


def test_每条SQL的占位符与参数数量对齐(monkeypatch):
    seen = []
    monkeypatch.setattr(SE.engine, 'query',
                        lambda sql, params=None, *, role='reader': (seen.append((sql, params or ())) or []))
    assert SE.compute_day(dt.date(2026, 10, 13)) == []
    assert seen, '一条 SQL 都没发'
    for sql, params in seen:
        assert sql.count('%s') == len(params), sql[:80]


def test_筛空了不能说成这一场没有():
    src = open('duanxian/system_entry_view.py', encoding='utf-8').read()
    assert "'system_entry_filtered_out'" in src and "'system_entry_empty'" in src
    seg = src[src.index("'system_entry_filtered_out'"):src.index("'system_entry_empty'")]
    assert 'min_gap_pct' in seg, '要看是不是缺口下限这一档把它们筛空了'
    assert 'b["entries"]' in seg and 'b["watching"]' in seg, '要交出全场的两只数，别让人读成没数'
    assert '库里都有数' in seg


def test_判据阈值只住一处且被读侧引用():
    from duanxian import system_entry_view as V
    t = V.thresholds()
    assert t['limit_lookback'] == SE.LIMIT_LOOKBACK and t['gap_min_pct'] == SE.GAP_MIN_PCT
    assert t['gap_unfilled_days'] == SE.GAP_UNFILLED_DAYS
    assert set(V.calibers()) >= {'mother', 'gap', 'stop', 'timing', 'history', 'path', 'no_fish_pond'}


# ---------------------------------------------------------------- 读侧诚实性

def test_分布的SQL用未来根自连接而不是窗口帧内自比():
    """窗口帧里 `case when high*af >= close*af*1.2` 的 close 是**帧内那一根**的收盘，不是信号日的收盘：
    那样量出来的是"某天盘中冲高又回落"，不是"20 场内先碰到 +20%"。实测 hit_take 只有 0.1~0.4%。"""
    src = open('app/compute/system_entry.py', encoding='utf-8').read()
    sql = src[src.index('STATS_SQL = """'):src.index('_STATS_MEASURES')]
    assert 'lag(' not in sql, '前瞻用 lag 是把前一天当成后一天'
    assert 'row_number() over (partition by security_code order by trade_date) bar_no' in sql
    assert 'b.bar_no between a.bar_no + 1 and a.bar_no + 20' in sql
    assert 'b.hi >= a.ac * 1.20' in sql and 'b.lo <= a.ac * 0.93' in sql
    # 反例钉死：帧内自比的那种写法不许回来
    assert 'rows between 1 following and 20 following' not in sql
    for k in ("'baseline'", "'c12'", "'entry'"):
        assert k in sql, f'三档少了一档：{k}'
    assert "'①② 门票 + 缺口未回补（观察池母体）'" in src and "'基线（同期全部票·日）'" in src


def test_历史分布读落库表且读侧不现算():
    src = open('duanxian/system_entry_view.py', encoding='utf-8').read()
    assert 'SE.STATS_TABLE' in src and 'percentile_cont(' not in src, '读侧不该再自己算分布'
    assert 'statement_timeout' in src, '为什么落库这件事得写在读侧，不然下一个人会改回现算'
    body = src[src.index('def history('):src.index('def thresholds(')]
    assert 'spec_version = %s' in body and 'window_days = %s' in body
    assert 'available' in body and '--stats-only' in body, '没算过的时候要给出口，不等于"没有数据"'


def test_分布列名与DDL对得上():
    """列的出处是**两份** DDL：34 建表、51 加「后 2/3/4/5 日均值」与它们的分母。
    只读 34 会把新增列报成"没落库"，只读 51 会漏掉原有列。"""
    ddl = (open('app/db/sql/34_system_entry_stats.sql', encoding='utf-8').read()
           + open('app/db/sql/51_system_entry_avg_days.sql', encoding='utf-8').read())
    for c in SE._STATS_COLUMNS:
        assert f'{c} ' in ddl or f'{c}\t' in ddl or f'{c}  ' in ddl, f'{c} 不在 DDL 里'
    assert 'primary key (bucket_key, window_to, window_days, spec_version)' in ddl
    assert SE.STATS_UPSERT.count('{cols}') == 1 and 'on conflict' in SE.STATS_UPSERT
    # 主键没把新列卷进去：同一档同一窗还是只有一行，加列不会让历史批次互相覆盖
    assert SE._STATS_PK == {'bucket_key', 'window_to', 'window_days', 'spec_version'}


def test_列头清单与落库列一一对应且每列都有说明():
    """`STATS_COLUMNS_SPEC` 是那张表的中文列头与逐列说明的唯一出处（用户 2026-10-22 点名要说明）。
    它一旦与 `_STATS_COLUMNS` 分叉，读侧就会 select 一个库里不存在的列 —— 那不是报错，
    是整块历史分布读不出来、页面把"没算出来"说成"这一场没有"。"""
    spec = SE.stats_columns()
    keys = [c['key'] for c in spec]
    assert len(keys) == len(set(keys)), '列键重复：前端按 key 渲染会撞 key'
    assert keys[0] == 'bucket' and keys[1] == 'n', '档位与样本数要排在最前（后面每一列的分母就是它）'
    for k in keys:
        assert k in SE._STATS_COLUMNS, f'{k} 不在落库列里'
    for k in SE._STATS_MEASURES:
        assert k in keys, f'{k} 落了库却没上屏：只端半边结论'
    for c in spec:
        assert c.get('note'), f'{c["key"]} 没有逐列说明'
        assert isinstance(c.get('signed'), bool) and isinstance(c.get('versus_base'), bool), \
            f'{c["key"]} 没交代带不带正号、要不要与基线比色'
    # 后 N 日均值四列必须各带自己的分母（窗口越长分母越小，不交就会被当成同一批样本）
    for n in SE.AVG_HORIZONS:
        c = next(x for x in spec if x['key'] == f'avg_{n}d')
        assert c.get('denom_key') == f'n_{n}d', f'avg_{n}d 没点名自己的分母'
        assert c['denom_key'] in SE._STATS_COLUMNS and c['denom_key'] in SE._STATS_COUNTS
    # 说明里不许写死会漂的数（那些数每天随窗口滚，写在说明里就会过期；数在格子里）
    for c in spec:
        assert not re.search(r'\d[\d,]{3,}', c['note']), f'{c["key"]} 的说明里抄了一个会漂的大数'


def test_后N日均值是复利累计而不是第N天单日():
    """语义钉死：`Π(1+pct)` 而不是取第 N 根那一天。写成后者，四个数摆在一排就会被读成
    "持有越久越赚"，而且与「次日均值」「后20复利中位」不再是同一把尺。"""
    src = open('app/compute/system_entry.py', encoding='utf-8').read()
    sql = src[src.index('STATS_SQL = """'):src.index('_STATS_MEASURES')]
    for n in SE.AVG_HORIZONS:
        assert f"sum(ln(1 + b.pc/100.0)) filter (where b.bar_no between a.bar_no + 1 " \
               f"and a.bar_no + {n}) s{n}" in sql, f'后 {n} 日不是复利累加'
        assert f'case when nx.nb{n} = {n} then 100.0 * (exp(nx.s{n}) - 1) end r{n}' in sql, \
            f'后 {n} 日没要求这 {n} 根一根不缺'
        assert f'count(r{n})::int n_{n}d' in sql, f'后 {n} 日没数自己的分母'
    # 分母的别名必须是 nb*：把它写成 c2…c5 会和 `e` 里的判据列（c12/c3）撞名，
    # lateral 里那句 `c12 and c3 and yang` 就变成"列名引用不明确"——整条查询报错，页面读不到分布
    assert ') c2,' not in sql and ') c5,' not in sql, '分母别名占用了判据列的名字'
    assert 'nb2' in sql and 'nb5' in sql


def test_算完自己验分母不变量():
    """后 N 日均值共用一条铁律：窗口越长分母越小，且不超过这一档的样本数。
    反了就是 `nbN = N` 或 bar_no 序号坏了，屏幕上看不出任何异常。"""
    good = {'bucket_key': 'entry', 'n': 100, 'win_next': 45.0, 'avg_next': -0.1,
            'n_2d': 99, 'n_3d': 98, 'n_4d': 97, 'n_5d': 96}
    assert SE.check_stats([good]) == []
    up = dict(good, n_3d=120)
    assert SE.check_stats([up]), '分母随窗口变长而变大却没被抓到'
    over = dict(good, n_5d=150)
    assert SE.check_stats([over]), '分母超过这一档样本数却没被抓到'
    assert SE.check_stats([dict(good, n_4d=None)]), '分母缺数要出声'
    assert SE.check_stats([dict(good, avg_next=None)]), '次日均值空、次日胜率有数 = 两批样本'
    # 缺数不是 0：整列 None 时不能报"没有分母"就完事，也要念出来
    assert any('分母缺数' in b for b in SE.check_stats([dict(good, n_2d=None, n_3d=None,
                                                              n_4d=None, n_5d=None)]))


def test_分布没自检过就不算这一遍成功():
    """`--stats-only` 与每日链末尾那一步都要把 `check_stats` 的结论念出来并按失败收码：
    坏数落进库以后，屏幕上只有一排正常的数字。"""
    src = open('app/compute/system_entry.py', encoding='utf-8').read()
    body = src[src.index('def refresh_stats('):src.index('def run(')]
    assert 'check_stats(rows)' in body and "'bad': bad" in body
    run = src[src.index('def run('):src.index('def main(')]
    assert run.count("res['bad']") >= 3, '两个出口（--stats-only 与每日链）都要读自检结论'
    assert '⚠️' in run, '自检不过要在日志里出声'


def test_读侧次日用lead不用lag且三档并排基线():
    src = open('duanxian/system_entry_view.py', encoding='utf-8').read()
    assert 'lag(pct_change)' not in src
    assert 'bucket_key' in src, '档位要按稳定 key 认，不靠中文串匹配'
    assert "'baseline' then 0 when 'c12' then 1 else 2" in src, '基线必须排在第一行'


def test_读侧把列头清单一起交出且缺数的列自己念出来():
    """页面不许手抄中文列名：列头、说明、带不带正号、比不比基线、分母是哪一列，都从这份清单渲染。
    旧批次落库前没有的那几列必须整列念 —，并附一句"这几列没跑过"，否则会被读成"持有 N 天没凑齐"。"""
    src = open('duanxian/system_entry_view.py', encoding='utf-8').read()
    body = src[src.index('def history('):src.index('def thresholds(')]
    assert 'SE.stats_columns()' in body, '列头没读后端那份清单'
    assert "'columns': cols" in body and "'uncomputed_columns': uncomputed" in body
    assert 'is None' in body, '判"这一列没数"要按 None，不能按假值（0.0 是合法读数）'
    read = src[src.index('def read('):]
    assert "_cav('stats_columns_missing'" in read, '缺列要作为 caveat 出声，且 code 带 missing ⇒ 前端不折'


def test_字典上屏念中文标题不念机器键():
    """10-22 那条「看不懂」的头号原因是屏幕上摆标识符：`calibers()` 的键留着做内部引用，
    字典那份必须另交中文标题（少配一条标题就会把 `no_fish_pond` 念给用户看）。"""
    from duanxian import system_entry_view as V
    items = V.caliber_items()
    cals = V.calibers()
    assert [i['key'] for i in items] == list(cals), '标题清单与句子清单分叉了'
    for i in items:
        assert i['title'] and i['title'] != i['key'], f'{i["key"]} 没有中文标题，屏幕会念机器码'
        assert i['text'] == cals[i['key']], f'{i["key"]} 的标题那份与句子那份不同源'
        assert not i['title'].startswith('cal'), i['title']
    r = V.read()
    assert [i['key'] for i in r['caliber_items']] == list(cals), 'read() 没把字典那份交出去'


def test_筛条件与版本条件都在该在的位置():
    src = open('duanxian/system_entry_view.py', encoding='utf-8').read()
    body = src[src.index('def board('):src.index('def history(')]
    assert body.index('s.entry_ok or not %s') < body.index('limit %s'), '筛发生在截断之后就白筛了'
    assert body.count('s.spec_version = %s') >= 1


def test_日历只列有数的日子():
    src = open('duanxian/system_entry_view.py', encoding='utf-8').read()
    assert 'group by 1 order by 1 desc limit' in src
    assert 'calendar' in src[src.index('def read('):]


# ---------------------------------------------------------------- 交叉命中那一侧

def test_体系榜登记为第6个交叉源且带版本条件():
    from app.compute import confluence as CF
    keys = [s.key for s in CF.SOURCES]
    assert keys == ['voldbl', 'quadrant', 'ambush', 'first_break', 'dgt', 'sysentry']
    src = next(s for s in CF.SOURCES if s.key == 'sysentry')
    assert 'entry_ok' in src.sql and SE.SPEC_VERSION in src.sql
    assert src.limit is None, '体系榜本来就窄，不该再截断'
    assert CF.SPEC_VERSION == 'confl-2', '交叉源从 5 个变 6 个 = 分母变了，必须升号'


def test_端点的命中上限跟着源数放开():
    src = open('vr/app.py', encoding='utf-8').read()
    i = src.index('/api/quant/confluence')
    head = src[i:i + 700]
    assert 'le=6' in head, 'min_hits 的上限还钉在 5，选"命中 6 榜"会被 422 挡掉'
    assert 'sysentry' in head, 'sources 参数说明里没登记新源'


# ---------------------------------------------------------------- 真库（不打桩）

def test_库里真的算过并且止损都在下方():
    from app.db import engine
    r = engine.query_one(
        "select count(distinct trade_date) sessions, min(trade_date)::text a, max(trade_date)::text z, "
        "count(*) n, count(*) filter (where entry_ok) e, "
        "count(*) filter (where stop_dist_pct is not null and stop_dist_pct > 0) bad_stop "
        "from analytics.system_entry where spec_version = %s", (SE.SPEC_VERSION,), role='reader')
    assert r and r['n'] > 0 and r['e'] > 0, f'表里没有可用的行：{r}'
    assert r['sessions'] >= 200, f'只算了 {r["sessions"]} 场：{r["a"]} ~ {r["z"]}'
    assert r['bad_stop'] == 0, f'{r["bad_stop"]} 行的止损位在现价上方 —— 缺口下沿必须在下方'
    assert r['z'] == engine.scalar("select max(trade_date)::text from market_data.stock_daily"), \
        '体系榜没算到最新一场（每日链那一步没接上）'


def test_分布表落库有三档且路径那两列不是假读数():
    """三档都在、基线排第一、且 hit_take 不是"帧内自比"那种 0.1% 的假读数。

    ⚠️ 这张表的主键含 `window_to`（每批次留一行，10-14 每日链就写出了第二批），
    所以"整整齐齐三档"必须按**最新那一批**数；不带 `window_to` 去数会数出 6 行，
    而那不等于判据变了 —— 读侧 `system_entry_view.history()` 取的也是最新那一批。
    """
    from app.db import engine
    from duanxian import system_entry_view as V
    r = engine.query_one(
        f"select count(*) rows, count(distinct bucket_key) k, "
        f"max(n) filter (where bucket_key = 'baseline') base_n, "
        f"max(n) filter (where bucket_key = 'entry') entry_n, "
        f"max(window_to)::text w "
        f"from {SE.STATS_TABLE} where spec_version = %s and window_days = %s "
        f"  and window_to = (select max(window_to) from {SE.STATS_TABLE} "
        f"                    where spec_version = %s and window_days = %s)",
        (SE.SPEC_VERSION, SE.WINDOW_DAYS, SE.SPEC_VERSION, SE.WINDOW_DAYS), role='reader')
    assert r and r['rows'] == 3 and r['k'] == 3, f'最新一批（window_to={r and r.get("w")}）不是整整齐齐三档：{dict(r) if r else None}'
    assert r['base_n'] > r['entry_n'] > 0, f'分母反了：基线 {r["base_n"]}、全过 {r["entry_n"]}'

    hs = V.history()
    assert hs.get('available'), hs.get('reason')
    b = hs['buckets']
    assert b[0]['bucket_key'] == 'baseline', '基线不在第一行，配色就成了拿两档互比'
    take = b[0]['hit_take']
    assert take is not None and take > 5, \
        f'基线"先碰 +20%"只有 {take} —— 那是窗口帧内自比的假读数形状，不是真路径'

    # 后 2/3/4/5 日均值：这一窗真跑过（不是整列 —），分母单调不增且不超过该档样本数
    assert hs['columns'] and [c['key'] for c in hs['columns']] == \
        [c['key'] for c in SE.stats_columns()], '读侧交出的列序与后端清单分叉了'
    assert hs['uncomputed_columns'] == [], \
        f'这几列在这一窗还没数：{hs["uncomputed_columns"]}（补一发 --stats-only）'
    for x in b:
        seq = [x[f'n_{n}d'] for n in SE.AVG_HORIZONS]
        avgs = [x[f'avg_{n}d'] for n in SE.AVG_HORIZONS]
        assert all(isinstance(v, int) for v in seq), f'{x["bucket_key"]} 的分母不是整数：{seq}'
        assert all(v is not None for v in avgs), \
            f'{x["bucket_key"]} 的后 N 日均值缺数：{avgs}'
        assert seq == sorted(seq, reverse=True), f'{x["bucket_key"]} 的分母没随窗口变长而单调不增：{seq}'
        assert x['n_5d'] <= x['n_2d'] <= x['n'], \
            f'{x["bucket_key"]} 的分母方向反了：n={x["n"]} n_2d={x["n_2d"]} n_5d={x["n_5d"]}'
    assert SE.check_stats(b) == [], f'落库这一批没过自检：{SE.check_stats(b)}'


def test_序列化不会遇到None就炸():
    r = SE.judge('X', happy())
    json.dumps({'rows': [dict(r, trade_date=str(r['trade_date']),
                              gap_date=str(r['gap_date']),
                              last_limit_up_date=str(r['last_limit_up_date']))]},
               allow_nan=False)
