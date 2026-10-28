# -*- coding: utf-8 -*-
"""
行情外链：把库里的 `security_code` 换成东方财富行情页的地址。

**公开可调用**，不依赖 dash、不碰数据库，任何脚本都能 `from app.quote_link
import em_url` 直接用：

    >>> em_url('600367.SH')
    'https://quote.eastmoney.com/sh600367.html'
    >>> em_url('sh600367') == em_url('600367') == em_url('600367.SH')
    True
    >>> em_url('880501.SH') is None          # 板块没有个股行情页
    True

东财的路径规则不是一条，是四条 —— 2026-08-29 逐个真实请求核过（看返回的
`<title>`，不是猜的），2026-09-26 为「股票名全部加外链」又复测并补了两条：

    沪深个股 / ETF   sh600367 / sz000001 / sz159915 / sh510300   200
    指数             zs000001（上证指数）/ zs399001（深证成指）   200
                     `sh000001` 也能打开同一页，但指数统一用 `zs` 前缀
                     北证50 只有 `zs899050` 打得开，`bj/899050` 是 404 —— 指数要排在
                     北交所那段之前判
    北交所           **bj/920000**（带斜杠）                      200
                     `bj920000`、`BJ920000` 都是 404 —— 这是最容易写错的一条
                     老码段 43/83/87 换码后全 404（`bj/430047` 死、`bj/920047` 活），
                     所以非 920 的北交所代码一律不给链接，别拼一条死链出去
    板块 / 伪代码     没有对应页面，返回 None，调用方按"不加链接"处理

品种判定复用 `app/ingest/instrument.py` 的 `classify()`，不在这里另立一套代码段
规则 —— 那种"两处各判一遍"的写法迟早会互相走偏（`000001` 在 SH 是指数、在 SZ
是个股，只有一处判定才不会错）。
"""
from __future__ import annotations

import re

from app.ingest import instrument as ins

EM_TEMPLATE = 'https://quote.eastmoney.com/{path}.html'

# ------------------------------------------------------------------ 可配置的外链模板
# 用户 2026-10-08 要的功能点：「针对不同市场的股票，在系统配置里做一个超链接配置：
# 除了代码以外的网址写在哪里，代码用 ****** 替换」，并点名北证要
# `https://quote.eastmoney.com/bj/******.html`。
#
# 三条设计约束（不然就会变成第二个"改了不生效"）：
#  · **默认值逐字复刻上面四条实测出来的路径** ⇒ 不改配置时，全站输出的地址与今天完全一致
#    （`test_quote_link.py` 与 `emUrl.fixtures.json` 钉的就是这个）；
#  · 行是"越具体越优先"：指数 / 北证 / 创业板 / 科创板 先判，判不上才落到沪、深两条兜底行
#    （基金、债券、B 股这些"跟着交易所走"的品种就归在兜底行里，不再各占一行）；
#  · 模板必须**只含一个 `******` 占位**且是 https 绝对地址，非法一律拒收并说明 ——
#    存进去一个拼不出地址的串，等于全站股票名一起变死链。
PLACEHOLDER = '******'

#: (行 id, 标题, 默认模板, 示例代码)。顺序即优先级，`index`/`bj` 必须在 `sh`/`sz` 之前。
LINK_ROUTES: tuple[tuple[str, str, str, str], ...] = (
    ('index', '指数（上证指数 / 深证成指 / 北证50…）',
     'https://quote.eastmoney.com/zs' + PLACEHOLDER + '.html', '000001.SH'),
    ('bj', '北证（北交所 920 段；老码段 43/83/87 换码后是 404，不给链接）',
     'https://quote.eastmoney.com/bj/' + PLACEHOLDER + '.html', '920779.BJ'),
    ('cyb', '创业板（300 / 301 / 302）',
     'https://quote.eastmoney.com/sz' + PLACEHOLDER + '.html', '300750.SZ'),
    ('kcb', '科创板（688 / 689）',
     'https://quote.eastmoney.com/sh' + PLACEHOLDER + '.html', '688981.SH'),
    ('sh', '上证（600/601/603/605 个股 + 沪市基金 / 债券 / B 股）',
     'https://quote.eastmoney.com/sh' + PLACEHOLDER + '.html', '600519.SH'),
    ('sz', '深证（000/001/002/003 个股 + 深市基金 / 债券 / B 股）',
     'https://quote.eastmoney.com/sz' + PLACEHOLDER + '.html', '000001.SZ'),
)
ROUTE_IDS = tuple(r[0] for r in LINK_ROUTES)
DEFAULT_TEMPLATES: dict[str, str] = {r[0]: r[2] for r in LINK_ROUTES}
ROUTE_LABELS: dict[str, str] = {r[0]: r[1] for r in LINK_ROUTES}
ROUTE_SAMPLES: dict[str, str] = {r[0]: r[3] for r in LINK_ROUTES}

_CFG: dict = {'templates': dict(DEFAULT_TEMPLATES), 'at': 0.0, 'warn': None}
_CFG_TTL_SEC = 15.0        # 页面上改完，下一屏链接就跟着变；不值得每个名字都去读一次文件


def bad_template(text: str | None) -> str | None:
    """模板合法性：None = 可用，否则给一句能照着改的话。"""
    s = str(text or '').strip()
    if not s:
        return '地址不能为空（要恢复默认就删掉这一格，让它回落到出厂值）'
    if any(ch.isspace() for ch in s):
        return '地址里不许有空白（会被截断成半条链接）'
    if not s.startswith('https://'):
        return f'地址要以 https:// 开头，收到 {s[:24]!r}'
    if s.count(PLACEHOLDER) != 1:
        return (f'地址里要**恰好一个** {PLACEHOLDER} 作为代码占位（现在 {s.count(PLACEHOLDER)} 个），'
                f'例如 https://quote.eastmoney.com/bj/{PLACEHOLDER}.html')
    return None


def configured_templates(*, now: float | None = None, refresh: bool = False) -> dict[str, str]:
    """当前生效的模板表（缺的、非法的都回落出厂值，绝不拼半条地址出去）。

    配置住在 `duanxian/jobs.py` 那一份 `jobs/config.json` 的 `quote_links` 里 ——
    与所有可改项同一处、同一种留痕，不在这里另开一个配置文件。
    """
    import time as _time

    now = now if now is not None else _time.monotonic()
    if not refresh and now - _CFG['at'] < _CFG_TTL_SEC:
        return _CFG['templates']
    merged: dict = {}
    try:
        from duanxian import jobs as jstore
        merged, _warns = jstore.settings('quote_links', dict(DEFAULT_TEMPLATES))
    except Exception:  # noqa: BLE001  读不到配置就按出厂值，链接不许因此消失
        merged = {}
    out = {}
    for rid, default in DEFAULT_TEMPLATES.items():
        raw = merged.get(rid)
        out[rid] = default if raw is None or bad_template(raw) else str(raw).strip()
    # 非法值不静默吞掉：回落出厂值的同时把原因留着，界面那一格要显示"你写的这条没收"。
    warn = None
    for rid in ROUTE_IDS:
        raw = merged.get(rid)
        if raw is not None and bad_template(str(raw)):
            warn = f'{rid}：{bad_template(str(raw))}（已按出厂值拼链接）'
            break
    _CFG.update(templates=out, at=now, warn=warn)
    return out


def reset_template_cache() -> None:
    """给测试与"写完立刻生效"用：清掉那份 TTL 缓存。"""
    _CFG.update(templates=dict(DEFAULT_TEMPLATES), at=0.0, warn=None)

# 只有这些品种有个股式行情页
LINKABLE_KINDS = ('stock', 'fund', 'bond', 'bshare', 'index')

_TS = re.compile(r'^(\d{6})\.(SH|SZ|BJ)$', re.I)          # 600367.SH
_PREFIXED = re.compile(r'^(SH|SZ|BJ)(\d{6})$', re.I)      # sh600367
_BARE = re.compile(r'^(\d{6})$')                          # 600367

# 裸 6 位码猜市场。**不只服务手输**：东财行情中心的 clist 榜单（首页「全市场换手率 TOP20」等）
# 交回来的 `f12` 就是裸 6 位，10-21 用户点名"北证的票链接没做好"就是这一张表漏了 920 段。
# 顺序是判据：**长前缀先判**，`920`（北交所新码段）必须排在 `'9'`（沪 B 是 900xxx）之前，
# 否则 920296 会被认成沪市、归类成 unknown、按"认不出就不给链接"退回纯文本。
_BARE_MARKET = ((('920',), 'BJ'),
                (('6', '9', '5'), 'SH'),
                (('0', '1', '2', '3'), 'SZ'),
                (('4', '8'), 'BJ'))


def normalize(code: str | None) -> tuple[str, str] | None:
    """`'sh600367'` / `'600367.SH'` / `'600367'` → `('600367', 'SH')`。"""
    s = str(code or '').strip()
    if not s:
        return None
    m = _TS.match(s)
    if m:
        return m.group(1), m.group(2).upper()
    m = _PREFIXED.match(s)
    if m:
        return m.group(2), m.group(1).upper()
    m = _BARE.match(s)
    if m:
        for heads, market in _BARE_MARKET:
            if s.startswith(heads):
                return s, market
    return None


def em_path(code: str | None, kind: str | None = None) -> str | None:
    """东财行情页的路径段（`sh600367` / `zs000001` / `bj/920000`）。"""
    parts = normalize(code)
    if parts is None:
        return None
    symbol, market = parts
    kind = kind or ins.classify(f'{symbol}.{market}')
    if kind not in LINKABLE_KINDS:
        return None
    if kind == 'index':
        # 指数必须排在北交所那一段之前：北证50 是 `zs899050`（2026-09-26 实测 200），
        # 走 `bj/899050` 是 404。
        return f'zs{symbol}'
    if market == 'BJ':
        # 只有 920xxx 有个股页。老码段 43/83/87 在换码后全部 404（2026-09-26 实测
        # `bj/430047`、`bj/830799`、`bj/837174` 三发全 404，同一只票的新码
        # `bj/920047` 200），硬拼出来的是一条死链，所以宁可不给链接。
        return f'bj/{symbol}' if symbol.startswith('920') else None
    return f'{market.lower()}{symbol}'


def route_id(code: str | None, kind: str | None = None) -> str | None:
    """这行代码属于配置表里的哪一行（`index` / `bj` / `cyb` / `kcb` / `sh` / `sz`）。

    判定顺序与 `em_path` 一致（指数排在北交所之前；北证只有 920 段有页面），
    因为这两件事必须是同一个事实 —— 一处判、一处拼，早晚会对不上。
    """
    parts = normalize(code)
    if parts is None:
        return None
    symbol, market = parts
    kind = kind or ins.classify(f'{symbol}.{market}')
    if kind not in LINKABLE_KINDS:
        return None
    if kind == 'index':
        return 'index'
    if market == 'BJ':
        return 'bj' if symbol.startswith('920') else None
    if kind == 'stock' and symbol.startswith(('300', '301', '302')):
        return 'cyb'
    if kind == 'stock' and symbol.startswith(('688', '689')):
        return 'kcb'
    return market.lower()


def em_url(code: str | None, kind: str | None = None,
           *, templates: dict | None = None) -> str | None:
    """行情页完整地址；没有对应页面（板块、伪代码、认不出的串）时返回 None。

    `templates` 是给测试与前端镜像留的口：不传就用当前配置（`/system` 那张表改的就是它），
    传 `DEFAULT_TEMPLATES` 就是"出厂口径"（真值表 `emUrl.fixtures.json` 钉的是这一份，
    所以用户把某行改成别的站点也不会把测试弄红）。
    """
    rid = route_id(code, kind)
    if rid is None:
        return None
    parts = normalize(code)
    tpl = (templates if templates is not None else configured_templates()).get(rid) \
        or DEFAULT_TEMPLATES[rid]
    if bad_template(tpl):
        tpl = DEFAULT_TEMPLATES[rid]
    return tpl.replace(PLACEHOLDER, parts[0])


def em_markdown(code: str | None, text: str | None = None,
                kind: str | None = None) -> str:
    """
    Markdown 链接，给 `dash_table` 的 `presentation='markdown'` 用。

    没有行情页时**原样返回文字**，不返回空 —— 表格里少一个链接可以接受，
    少一个代码不行。
    """
    label = str(text if text not in (None, '') else (code or ''))
    url = em_url(code, kind)
    if not url:
        return label
    # 竖线会把 Markdown 表格切开，中括号会把链接语法搞乱，出现就退回纯文本
    if any(ch in label for ch in '|[]()'):
        return label
    return f'[{label}]({url})'


def detail_link(code: str | None, text: str = '看详情') -> str:
    """
    站内深链：`/stock?code=600519.SH`。给 `dash_table` 的 markdown 单元格用。

    和 `em_markdown` 的分工：那个是**去东财看行情**，这个是**留在本站看评分
    归因**。名单页两个都要 —— 手上有一只票时，"它现在什么分、为什么这个分"
    只有本站答得上来。

    放在这个模块而不是 `app/web/ui.py`：`queries.py` 也要造这个链接，而
    `ui.py` 反过来会 import `queries`，写在 ui 里就成了循环 import。
    """
    s = str(code or '').strip()
    return f'[{text}](/stock?code={s})' if s else ''

