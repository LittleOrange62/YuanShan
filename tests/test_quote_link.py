# -*- coding: utf-8 -*-
"""行情外链（`app/quote_link.py`）的口径测试 + 前后端共同真值表。

链接写错不会抛异常，点开才是 404。三条最容易被"顺手统一"掉的：

- 北交所是 `bj/920000`（**带斜杠**），`bj920000` 实测 404；
- 指数走 `zs`（`000001.SH` 是上证指数，`000001.SZ` 才是平安银行个股）——
  代码段同一串，市场不同结论就不同，所以判定只能有一处；
- 板块（`880/881`）与通达信伪代码（`999xxx`）**没有**行情页，必须回 `None`
  让调用方退回纯文本，不能拼出一个看着像真链接的地址。

`frontend/src/lib/quoteLink.ts` 是浏览器里的镜像（60 个渲染点不可能逐个回后端要地址），
两边钉在 `frontend/test/emUrl.fixtures.json` 上 —— 所以本文件既测 Python 本体，
也测这张表有没有落后于本体。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app import quote_link as ql

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "frontend/test/emUrl.fixtures.json"


@pytest.fixture(autouse=True)
def _出厂口径(tmp_path, monkeypatch):
    """下面那批真值表钉的是**出厂**地址。用户把某行改成别的站点，不该让这些测试变红，
    也不该让它们去读真人的 `jobs/config.json`（那才是"测试环境不一样就复现不了"的老形状）。
    所以：配置目录指向 tmp，前后各清一次模板缓存。"""
    monkeypatch.setenv("ASTOCK_DATA_HOME", str(tmp_path / "home"))
    ql.reset_template_cache()
    yield
    ql.reset_template_cache()


@pytest.mark.parametrize(('code', 'url'), [
    ('600367.SH', 'https://quote.eastmoney.com/sh600367.html'),
    ('600519.SH', 'https://quote.eastmoney.com/sh600519.html'),
    ('002827.SZ', 'https://quote.eastmoney.com/sz002827.html'),
    ('000001.SZ', 'https://quote.eastmoney.com/sz000001.html'),
    ('300750.SZ', 'https://quote.eastmoney.com/sz300750.html'),
    ('302132.SZ', 'https://quote.eastmoney.com/sz302132.html'),   # 创业板新增段
    ('510300.SH', 'https://quote.eastmoney.com/sh510300.html'),   # ETF
    ('159915.SZ', 'https://quote.eastmoney.com/sz159915.html'),   # ETF
    ('920000.BJ', 'https://quote.eastmoney.com/bj/920000.html'),  # 带斜杠
    ('920047.BJ', 'https://quote.eastmoney.com/bj/920047.html'),  # 换码后的北交所新码段
    ('000001.SH', 'https://quote.eastmoney.com/zs000001.html'),   # 指数用 zs
    ('399001.SZ', 'https://quote.eastmoney.com/zs399001.html'),
    ('899050.BJ', 'https://quote.eastmoney.com/zs899050.html'),   # 北证50 走 zs，不走 bj/
    # 输入形态：带前缀、裸 6 位、小写，都要归到同一个地址
    ('sh600367', 'https://quote.eastmoney.com/sh600367.html'),
    ('600367', 'https://quote.eastmoney.com/sh600367.html'),
    ('sz000001', 'https://quote.eastmoney.com/sz000001.html'),
])
def test_em_url(code, url):
    assert ql.em_url(code) == url


@pytest.mark.parametrize('code', [
    '880501.SH',    # 通达信板块，没有个股行情页
    'TDX_880501',   # 板块主键
    '999999.SH',    # 通达信伪代码
    '430047.BJ',    # 北交所老码段：换码后 bj/430047 实测 404，那就宁可不给链接
    '830799.BJ',
    '837174.BJ',
    '60051',        # 位数不对
    '', None, '一段中文',
])
def test_no_url(code):
    assert ql.em_url(code) is None


def test_markdown_falls_back_to_plain_text():
    assert ql.em_markdown('002827.SZ', '高争民爆') == (
        '[高争民爆](https://quote.eastmoney.com/sz002827.html)')
    # 没有行情页时保留文字本身，不能变成空
    assert ql.em_markdown('880501.SH', '半导体') == '半导体'


def test_bare_code_guesses_market_by_segment():
    """裸 6 位只能猜市场，所以同一串在两个市场结论相反时以带后缀的为准。"""
    assert ql.em_url('000001') == 'https://quote.eastmoney.com/sz000001.html'
    assert ql.em_url('000001.SH') == 'https://quote.eastmoney.com/zs000001.html'


def test_fixtures_table_matches_the_python_rule():
    """前后端真值表必须由本体现在生成一遍仍然相等（否则前端镜像在测一份旧规则）。"""
    cases = json.loads(FIXTURES.read_text(encoding='utf-8'))['cases']
    assert cases, '真值表是空的'
    stale = {code: (url, ql.em_url(code)) for code, url in cases.items()
             if ql.em_url(code) != url}
    assert not stale, ("跑一次 python scripts/ops/gen_em_link_fixtures.py 重新生成："
                       + "; ".join(f"{c}: 表={a} 本体={b}" for c, (a, b) in stale.items()))


# ------------------------------------------------------- 按市场可配置（用户 2026-10-08）
# 「除了代码以外的网址写在哪里，代码用 ****** 替换；北证写 https://quote.eastmoney.com/bj/******.html」
# 钉的四件事：默认逐字复刻今天、改一行真的生效、非法整笔拒且仍按出厂拼、每行都有可点的样例。

def test_默认表逐字复刻今天的四条路径():
    for rid, label, default, sample in ql.LINK_ROUTES:
        got = ql.em_url(sample)
        assert got == ql.em_url(sample, templates=ql.DEFAULT_TEMPLATES), rid
        assert ql.PLACEHOLDER in default and default.startswith('https://'), f'{rid} 出厂模板不像话'
        assert got == default.replace(ql.PLACEHOLDER, sample.split('.')[0]), (rid, got)
    # 北证这一行是用户点名要的格式
    assert ql.em_url('920779.BJ') == 'https://quote.eastmoney.com/bj/920779.html'


def test_每一行都属于它该属的那一类():
    assert [ql.route_id(c) for c in ('000001.SH', '399001.SZ', '899050.BJ')] == ['index'] * 3
    assert ql.route_id('920779.BJ') == 'bj'
    assert ql.route_id('430047.BJ') is None        # 老码段没页面，宁可不给链接
    assert ql.route_id('300750.SZ') == 'cyb' and ql.route_id('302132.SZ') == 'cyb'
    assert ql.route_id('688981.SH') == 'kcb'
    assert ql.route_id('600519.SH') == 'sh' and ql.route_id('000001.SZ') == 'sz'
    assert ql.route_id('510300.SH') == 'sh' and ql.route_id('159915.SZ') == 'sz'   # 基金跟交易所
    assert ql.route_id('880501.SH') is None and ql.route_id('') is None


def test_改一行就真的生效不必重启(tmp_path, monkeypatch):
    from duanxian import jobs as jstore
    why, _resp = jstore.set_setting('quote_links', via='test',
                                    bj='https://example.test/bj/' + ql.PLACEHOLDER + '.html')
    assert why is None
    ql.reset_template_cache()                       # 15 秒 TTL 立刻作废
    assert ql.em_url('920779.BJ') == 'https://example.test/bj/920779.html'
    assert ql.em_url('600519.SH') == 'https://quote.eastmoney.com/sh600519.html', '别的行不受影响'


@pytest.mark.parametrize('bad', [
    'http://x/' + '******' + '.html',                         # 非 https
    'https://x/y.html',                                       # 没有占位符 → 全站同一个地址
    'https://x/' + '******' + '/' + '******' + '.html',       # 两个占位符，替换谁？
    'https://x /' + '******' + '.html',                       # 带空白
])
def test_非法模板整笔拒并说清原因(bad):
    from duanxian import jobs as jstore
    why, _resp = jstore.set_setting('quote_links', via='test', bj=bad)
    assert why and ('https' in why or '占位' in why or '空白' in why), (bad, why)
    assert '整条没写入' in why, '要说清这一笔没落盘，而不是让人以为存了个空值'
    assert ql.em_url('920779.BJ') == 'https://quote.eastmoney.com/bj/920779.html', '拒了就要仍按原值运行'


def test_留空是交还默认而不是存一个空地址():
    """写入面早就有这条语义（空值 = 弹出这个键），外链这一组必须跟着它走 ——
    否则"改坏了想撤回"会被取值校验挡掉，用户只能去翻文件。"""
    from duanxian import jobs as jstore
    jstore.set_setting('quote_links', via='test', bj='https://tmp/' + ql.PLACEHOLDER + '.html')
    ql.reset_template_cache()
    assert ql.em_url('920779.BJ') == 'https://tmp/920779.html'
    why, resp = jstore.set_setting('quote_links', via='test', bj='')
    assert why is None, why
    # `merged` 是"出厂值合并后的生效值"，所以清空之后它显示的是**出厂那条**，不是 None
    assert resp['merged']['bj'] == ql.DEFAULT_TEMPLATES['bj'], resp['merged']
    assert resp['changed']['bj'] != 'https://tmp/920779.html', resp['changed']
    ql.reset_template_cache()
    assert ql.em_url('920779.BJ') == 'https://quote.eastmoney.com/bj/920779.html'


def test_库里已经存着非法值时也不拼死链(tmp_path, monkeypatch):
    """合法只是写入那一刻的事：手改过文件、或换了口径都可能留下坏值 —— 拼链接这一侧必须兜住。"""
    monkeypatch.setattr(ql, 'configured_templates',
                        lambda **k: {'bj': 'https://x/y.html', 'sh': 'https://quote.eastmoney.com/sh' + ql.PLACEHOLDER + '.html'})
    assert ql.em_url('920779.BJ') == 'https://quote.eastmoney.com/bj/920779.html'


def test_这组参数在册且只有一行一次改(tmp_path, monkeypatch):
    """`quote_links` 是一组参数（没有开关、不自己跑）：CLI 与写入面都得认得它，
    但页面上一行一次改 —— 一次改多件被拒是既有的规矩。"""
    import importlib.util
    spec = importlib.util.spec_from_file_location('jobs_cli', ROOT / 'scripts' / 'jobs.py')
    jobs_cli = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(jobs_cli)
    assert 'quote_links' in jobs_cli._known()
    assert jobs_cli._is_param_group('quote_links'), '它不是一条作业，不该有 enable/run-now'
    from duanxian import jobs as jstore
    why, _ = jstore.set_setting('quote_links', via='test', bj='https://a/' + ql.PLACEHOLDER + '.html',
                               sh='https://b/' + ql.PLACEHOLDER + '.html')
    assert why is None                              # 两行一起提交是允许的（同一种键）
    import sys

    sys.path.append(str(ROOT / 'vr'))     # 末尾：插最前会让 vr/app.py 抢走顶层 app/ 包
    import jobsboard
    code, resp = jobsboard.set_quote_link('nope', 'https://x/' + ql.PLACEHOLDER + '.html')
    assert code == 404 and '没有这一行' in resp['reason']
