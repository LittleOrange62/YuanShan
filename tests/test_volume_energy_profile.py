# -*- coding: utf-8 -*-
"""倍量加权分那把尺子（`app/compute/volume_energy.py`）的回归。

守四条：

1. **档表只住一处**：界面、SQL、文档都从 `BANDS`/`band_rows()` 取；测试直接数档数与区间，
   改名/改步长会当场红（同页两块用两把尺的教训见《10-每个数怎么算》§4.1）。
2. 2026-10-19 第二稿：次数×分值，无归一化 ——
   **得分 = Σ 每档 ( 这一档在窗口里出现的次数 × 这一档的分值 )**。
   第一稿的「占比＝分值÷Σ分值」连同 `normalize`（sum/top）量纲已被用户当晚否掉
   （「其实不存在系数这个参数，系数这个说法是错的」），本文件按第二稿重钉：
   `points()` 原样交回分值（不做任何除法）；满窗上限＝`40 × 顶档分值`＝40 分
   （**不是 40×占比**），所以"照旧口径填 6"这一档在默认配置下照样筛不出东西，界面必须交 `scale`。
3. **改配置＝换指纹＝要重算**：`fingerprint()` 覆盖作用域与每个市场摊平后的档表
   （第一稿进哈希的 `normalize` 键随量纲一起作废）；`save()` 交回 `needs_rebuild`，
   `status()` 现比库里批次。
4. **测试不许碰真库写入**：`start_rebuild_async` 在 pytest 进程里硬挡（后台线程里
   print／起线程撞捕获是老坑），真算只走 CLI。
"""
import pytest

from app.compute import volume_energy as venc
from app.db.repo import registry as reg_repo


# ------------------------------------------------------------------ ① 档表与占比

def test_档表是每0_1一档到2_5再补三个尾档():
    assert len(venc.BANDS) == 18 == len(venc.BAND_LABELS)
    assert venc.BANDS[0] == (1.0, 1.1)
    assert venc.BANDS[14] == (2.4, 2.5)
    # 尾部不是"一条 ≥2.5"：公开口径把 2.5–5 叫明显放量、5 以上叫极端放量（高位极端是见顶信号），
    # 合成一档就没法表达"越极端越不加分"，所以拆成 2.5-3.5 / 3.5-5.0 / 5.0+
    assert venc.BANDS[15:] == ((2.5, 3.5), (3.5, 5.0), (5.0, None))
    assert venc.BAND_LABELS[-1] == '5.0+'
    # 区间连续无缝：下一档的下界＝上一档的上界
    for (lo, hi), (nlo, _nhi) in zip(venc.BANDS, venc.BANDS[1:]):
        assert abs(nlo - hi) < 1e-9, f'{lo}-{hi} 与下一档 {nlo} 之间有缝'
    # 1.0 以下一分不给（缩量不是放量信号）
    assert venc.BANDS[0][0] == 1.0


def test_默认分值单调上升而极端放量那一档回落():
    sc = venc.DEFAULT_SCORES
    assert set(sc) == set(venc.BAND_LABELS), '每一档都要有默认分值，少一档等于偷偷按 0 算'
    seq = [sc[k] for k in venc.BAND_LABELS]
    assert seq[:16] == sorted(seq[:16]) and len(set(seq[:16])) == 16, \
        '1.0→2.5 那 15 档加顶档必须一路上行（不放回），否则"档"就没有次序含义'
    assert sc['2.5-3.5'] == max(seq), '顶档给在「明显放量」那一档，不是给在极端放量'
    assert sc['3.5-5.0'] < sc['2.5-3.5'] and sc['5.0+'] < sc['3.5-5.0'], \
        '越极端越不加分（公开口径一致认为高位极端放量偏反转）'
    assert sc['1.0-1.1'] < sc['1.5-1.6'], '1.0 出头那种噪声不该和真放量同权重'


# 2026-10-19 第二稿：次数×分值，无归一化 —— 分值原样累加，`shares()`/÷Σ 那层已删（用户当晚否掉「系数」说法）。
def test_分值就是每天计几分直接累加改一档只稀释不了别的档():
    pt = venc.points(None)
    assert pt == {k: float(v) for k, v in venc.DEFAULT_SCORES.items()}, \
        'points 原样交回分值：不做 ÷Σ、不÷顶档，任何除法出现＝第一稿量纲复活'
    assert pt['2.5-3.5'] == 1.00, '顶档一天记满 1 分（第一稿钉的是 1÷Σ分值≈0.092 的占比，随归一化一起作废）'
    ssum = sum(venc.DEFAULT_SCORES.values())
    assert ssum > 1 and abs(pt['2.5-3.5'] - 1.0 / ssum) > 1e-6, \
        '分值之和不为 1、顶档分值不等于 1÷Σ —— 这两个"不等"就是"没有归一化这一层"的算术证据'
    # 改一档只影响那一档：加倍顶档后其余档分值**原样不动**
    # （第一稿「÷总和」会把全表每一档都稀释 —— 那条连锁语义已被用户否掉，这里反钉）
    one = {**venc.DEFAULT_SCORES, '2.5-3.5': venc.DEFAULT_SCORES['2.5-3.5'] * 2}
    got = venc.validate({'scope': 'global', 'global': one})
    assert got['global']['2.5-3.5'] == 2.0
    for lab in venc.BAND_LABELS:
        if lab != '2.5-3.5':
            assert got['global'][lab] == venc.DEFAULT_SCORES[lab], \
                f'{lab} 的分值被顶档的改动牵动了——只有"除以总和"才会这样，那一层已取消'


# 2026-10-19 第二稿：次数×分值，无归一化 —— 顶档一天记 1 分 ⇒ 得分上限＝窗口×顶档分值＝40，与旧「倍量次数」同量级。
def test_顶档一天记一分所以得分与旧的倍量次数同量级():
    top = venc.validate({'scope': 'global', 'global': venc.DEFAULT_SCORES})
    assert max(top['global'].values()) == 1.00, '默认档表的顶档分值＝1：一天顶档放量就是 1 分'
    # scale 里的数按 4 位小数交出（界面上读的就是那个位数），上限＝窗口×最高档分值
    assert venc.scale()['max_score'] == 40 * max(venc.points(None).values()) == 40.0, \
        'scale 的上限必须＝窗口×最高档分值（第一稿是 40×最高档占比＜40，那条随归一化作废）'


# 2026-10-19 第二稿：次数×分值，无归一化 —— scale 交的是数学上限（顶档 1 分×40 场＝40 分），且不再交 `normalize` 键。
def test_scale交出数学上限而不是让人拿次数填数():
    sc = venc.scale()
    assert sc['window_days'] == venc.LOOKBACK == 40
    assert sc['max_per_day'] == 1.0, '默认档表下一天最多 1 分（第一稿钉的 0＜上限＜1 是占比量纲，已作废）'
    assert sc['max_score'] == 40.0, '满窗（40 场全在顶档）的上限＝40 分 —— 与"40 次"同量级，填 6 是可能的'
    assert 'normalize' not in sc, '量纲键随归一化一起取消；scale 再交 normalize＝把第一稿的尺递回界面'
    assert set(sc) == {'window_days', 'max_per_day', 'max_score', 'unit', 'formula', 'note'}
    assert sc['formula'] == '得分 = Σ 每档 ( 这一档在窗口里出现的次数 × 这一档的分值 )', \
        '算式原文只住这一份（界面逐字读），钉死它＝钉死"次数×分值"没有第三种写法'
    assert '分' in sc['unit'] and '顶档一天记 1 分' in sc['unit']
    assert '没有系数' in sc['note'] and '没有占比' in sc['note'] and '归一化' in sc['note'], \
        'note 必须点名"系数/占比/归一化都不存在"（用户原话：系数这个说法是错的）'


# ------------------------------------------------------------------ ② 作用域与校验

# 2026-10-19 第二稿：次数×分值，无归一化 —— 配置形状里没有 normalize；单配市场交的是**分值**（不是占比）。
def test_按交易所配时没单配的市场落回全局(monkeypatch):
    """`effective_scores` 读的是**生效配置**（config.setting），不是 validate 的返回值 ——
    所以这条要把那份 payload 真摆成生效配置，否则测的是"校验洗数据"而不是"解析作用域"。"""
    payload = {'scope': 'market',
               'global': venc.DEFAULT_SCORES,
               'by_market': {'SH': {**venc.DEFAULT_SCORES, '2.5-3.5': 0.5}}}
    monkeypatch.setattr(reg_repo, 'setting', lambda key, default=None, **kw: payload)
    clean = venc.validate(payload)
    assert clean['by_market']['SH']['2.5-3.5'] == 0.5
    assert venc.effective_scores('SH')['2.5-3.5'] == 0.5
    assert venc.effective_scores('SZ') == venc.DEFAULT_SCORES
    assert venc.effective_scores('BJ') == venc.DEFAULT_SCORES
    assert venc.effective_scores(None) == venc.DEFAULT_SCORES, 'market 缺失（新市场号）也得有兜底'
    # 顶档被压低的只能是那一个市场：scale 取的是"各市场里最大的那个上限"，
    # 它不该因为 SH 压低就整体变小（这一格是给用户解释"你这份配置最多能到几分"的）
    assert venc.scale()['max_per_day'] == max(max(venc.points(m).values())
                                              for m in ('SH', 'SZ', 'BJ', 'OTHER'))
    # 摊平的档表把四个具体市场（SH/SZ/BJ/OTHER）都铺满，SQL 里不必再写优先级
    rows = venc.band_rows()
    assert len(rows) == 4 * len(venc.BANDS)
    assert {m for m, _lo, _hi, _w in rows} == {'SH', 'SZ', 'BJ', 'OTHER'}
    sh_top = [w for m, _lo, hi, w in rows if m == 'SH' and hi == 3.5][0]
    sz_top = [w for m, _lo, hi, w in rows if m == 'SZ' and hi == 3.5][0]
    assert sh_top < sz_top, '按交易所配生效了吗：SH 顶档分值被压低，摊平的档表里也该跟着小'


# 2026-10-19 第二稿：次数×分值，无归一化 —— 带 `normalize` 键的旧形状必须**当场拒收**并说清这一层已取消，其余拒绝理由各自独立钉。
def test_校验拒半份配置并说清缺哪一档():
    # 第一稿形状（带 normalize）不许静默兼容：界面拿旧形状保存必须被拒并指路刷新页面
    with pytest.raises(ValueError) as e_norm:
        venc.validate({'normalize': 'sum', 'scope': 'global', 'global': venc.DEFAULT_SCORES})
    assert '已经取消「量纲／占比」这一层' in str(e_norm.value) and '请刷新页面' in str(e_norm.value), \
        'normalize 键必须点名"这一层已取消"并指路刷新（只说"不认识"会让人以为是笔误而不是换尺）'
    missing = {k: v for k, v in venc.DEFAULT_SCORES.items() if k != '2.0-2.1'}
    with pytest.raises(ValueError) as e:
        venc.validate({'scope': 'global', 'global': missing})
    assert '2.0-2.1' in str(e.value) and '少了一档' in str(e.value)
    with pytest.raises(ValueError):
        venc.validate({'scope': 'global',
                       'global': {**venc.DEFAULT_SCORES, '9.9-10.0': 1}})   # 自造档名
    with pytest.raises(ValueError):
        venc.validate({'scope': 'board', 'global': venc.DEFAULT_SCORES})
    with pytest.raises(ValueError):
        venc.validate({'scope': 'global',
                       'global': {k: 0 for k in venc.DEFAULT_SCORES}})       # 全 0：排名无从谈起
    with pytest.raises(ValueError):
        venc.validate({'scope': 'global',
                       'global': {**venc.DEFAULT_SCORES, '1.5-1.6': -1}})
    with pytest.raises(ValueError):
        venc.validate({'scope': 'market', 'global': venc.DEFAULT_SCORES,
                       'by_market': {'NASDAQ': dict(venc.DEFAULT_SCORES)}})


def test_存的配置坏了自己退回默认并说出为什么():
    """退回默认可以，静默当"用户那份生效了"不行 —— `profile_source` 那句要能上界面。"""
    seen = {}

    def fake(key, default=None, **kw):
        seen['key'] = key
        return {'normalize': 'sum', 'scope': 'global', 'global': {'1.0-1.1': 1}}  # 少 17 档

    real = reg_repo.setting
    reg_repo.setting = fake
    try:
        p = venc.profile()
    finally:
        reg_repo.setting = real
    assert seen['key'] == venc.SETTING_KEY
    assert p['global'] == venc.DEFAULT_SCORES, '坏配置必须退回代码默认，不拿半份跑'
    assert p['broken'] is True and '不合法' in p['profile_source'], p['profile_source']


# ------------------------------------------------------------------ ③ 指纹＝要重算的凭据

# 2026-10-19 第二稿：次数×分值，无归一化 —— 指纹 payload 里没有 normalize（「换量纲」这一维随量纲作废），
# 敏感用例改成"会改到摊平档表分值"的三种改法。
def test_指纹对任何会影响数值的变化都敏感():
    base = venc.fingerprint()
    assert base.startswith('ev-')
    seen = {}

    def spy(key, default=None, **kw):
        return seen.get('v')

    real, reg_repo.setting = reg_repo.setting, spy
    try:
        for name, payload in (
            ('顶档加倍', {'scope': 'global',
                          'global': {**venc.DEFAULT_SCORES, '2.5-3.5': 2.0}}),
            ('改一档', {'scope': 'global',
                        'global': {**venc.DEFAULT_SCORES, '2.2-2.3': 51}}),
            ('加单配市场', {'scope': 'market',
                            'global': venc.DEFAULT_SCORES,
                            'by_market': {'BJ': dict(venc.DEFAULT_SCORES)}}),
        ):
            seen['v'] = payload
            assert venc.fingerprint() != base, f'{name} 之后指纹没变＝这一改不会被重算捡起'
        seen['v'] = {'scope': 'global', 'global': dict(venc.DEFAULT_SCORES)}
        assert venc.fingerprint() == base, '改回原样应得到同一个指纹（不必白跑一次全量）'
    finally:
        reg_repo.setting = real


def test_测试进程里绝不起真重算线程():
    """真重算是一条覆盖 115 万行的语句（实测 104~123 秒），且会留下野线程。
    挡住之后 CLI 仍是唯一入口 —— 这条闸坏了要能出声，所以交回 started=False 而不是抛。"""
    out = venc.start_rebuild_async('测试')
    assert out['started'] is False and '测试' in out['reason'], out
    assert venc.job_state()['running'] is False, '被挡下的请求不许把现场标成在跑'


def test_status交出的字段界面都读得到():
    st = venc.status()
    for k in ('fingerprint', 'in_sync', 'profile_source', 'scale', 'distribution', 'bands'):
        assert k in st, k
    assert st['in_sync'] in (True, False, None), '对账判据只有真/假/没读到，不许拿 None 当 True'
    assert st['fingerprint'] == venc.fingerprint()
    assert len(st['bands']) == len(venc.BANDS)
