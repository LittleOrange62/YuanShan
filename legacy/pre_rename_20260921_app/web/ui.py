# -*- coding: utf-8 -*-
"""
共享 UI 组件与卡片渲染。

页面布局不写死在代码里，而是读 `config.page_layout`：每行一张卡片，
带栅格宽度、卡片类型、引用的图表/查询。改布局是改表，不是改代码 ——
这就是"积木城堡"在展示层的落地方式。

卡片类型（config.page_layout.card_kind）：
    stat        指标数字块
    chart       引用 config.chart
    table       引用命名查询，出表格
    score_panel 评分面板
    task_list   任务列表
    markdown    静态说明
    iframe      外部页面
渲染不了的卡片显示"未实现"而不是抛异常整页白屏 —— 页面上能看见缺口，
比静默空白更有用。
"""
from __future__ import annotations

import re
import time
from decimal import Decimal

import dash_bootstrap_components as dbc
import plotly.graph_objects as go
from dash import dash_table, dcc, html

from app import quote_link
from app.db import engine
from app.web import queries

GRID = 12

COLORS = {'up': '#d64545', 'down': '#2e9e5b', 'flat': '#888',
          'accent': '#2f6feb', 'muted': '#6c757d'}


# ---------------------------------------------------------------- 表头中文名

# 表格抬头以前直接用 SQL 的字段名，满屏 `turnover_rate_pct` / `delta_yi_yuan`
# 这种东西，复盘时得先翻译一遍才能看。这里做一层字段名 → 中文名的映射，
# **只改显示的 name，不改 id** —— DataTable 的排序、筛选、回调都认 id，
# 把 id 换成中文会把 `q_review_stocks` 的下钻和筛选一起弄坏。
#
# 兜底表放在代码里，是因为它必须对同名字段给出唯一答案：`value` 在
# `fct.score` 里是"评分"、在 `fct.indicator_value` 里是"指标值"，注册表里
# 两条都存在，靠注册表选不出来。所以下面这张表对已知字段是权威；
# 注册表（config.field_spec.label_cn）只用来补它没覆盖的字段，
# 这样以后新加字段仍然可以在前台改名字，不用动代码。
_FALLBACK_LABEL: dict[str, str] = {
    # 标识
    'ts_code': '代码', 'name': '名称', 'board': '上市板', 'trade_date': '交易日',
    'sector_code': '板块代码', 'sector_type': '板块类型', 'group_name': '分组',
    'is_focus': '重点关注', 'note': '备注', 'hint': '说明', 'detail': '详情',
    # 行情
    'open': '开盘', 'high': '最高', 'low': '最低', 'close': '收盘',
    'pct_chg': '涨跌幅%', 'amount_yi': '成交额(亿)', 'amount_yuan': '成交额(元)',
    'vol_shares': '成交量(股)', 'turnover_rate_pct': '换手率%',
    # 家数 / 分布
    'up': '上涨家数', 'down': '下跌家数', 'total': '总家数',
    'up_count': '上涨家数', 'down_count': '下跌家数',
    'up_ratio': '上涨占比%', 'member_count': '成分股数', 'members': '成员数',
    'label': '区间', 'lo': '区间下界', 'hi': '区间上界', 'cnt': '只数', 'n': '只数',
    'stocks': '股票数',
    # 多日聚合列（2026-08-29 从旧系统自选股表移植）。**都是"日均"**：
    # chg10_avg = 近 10 个交易日每日涨跌幅的均值，不是"10 日区间涨幅"（那是 chg10）。
    # 同名不同义这件事在页面上也要说清楚，见 CARD_HELP['q_watchlist']。
    'chg10_avg': '10日涨跌%', 'amp10_avg': '10日振幅%',
    'chg20_avg': '20日涨跌%', 'amp20_avg': '20日振幅%',
    'amp_pred': '明日振幅%',
    # 组内 10 / 20 日**日均**涨跌幅的组平均，**已 ×100**（用户 2026-09-01 指定）。
    # ×100 之后单位正好是基点（1bp = 0.01%），所以抬头写 bp 不写 % ——
    # 标成 % 的话 −25 会被读成 −25%，量级差 100 倍。
    'avg_chg10_bp': '组内10日均涨跌bp', 'avg_chg20_bp': '组内20日均涨跌bp',
    'chg10': '10日区间涨幅%', 'chg20': '20日区间涨幅%',
    'avg_price': '均价', 'mid_price': '中价',
    # 评分
    'value': '数值', 'oper': '操作分', 'oper_score': '操作分',
    'x_quality': 'X 个股质量', 'x_score': 'X 个股质量',
    'y_env': 'Y 环境共振', 'y_score': 'Y 环境共振', 'xy_total': 'X+Y 合计',
    'quadrant': '象限', 'rank': '排名', 'pct_rank': '分位%',
    # 排行榜里的板块对照。hot_sector 是"所属板块里当日最热的那个"，
    # 不是"主营行业"——行业板块的成分股还没入库（见 q_score_rank 的说明）
    'hot_sector': '最热板块', 'sector_heat': '板块热度',
    'other_sectors': '其它板块',
    'cap_key': '功能点', 'cap_name': '功能点', 'part_key': '分项',
    'part_label': '分项',
    'part_value': '分项分值', 'weight': '权重', 'contribution': '贡献值',
    'raw_weight': '原始权重', 'eff_weight': '有效权重',
    'script_key': '脚本', 'script_name': '脚本名', 'seq': '顺序',
    # 权重表上每个脚本的内容说明（来自各 builtin 的 EXPLAIN，见 app/compute/explain.py）
    'basis': '判据', 'terms_brief': '子项（内部权重）', 'combine': '合成方式',
    'caveat': '注意',
    # 前 666 条的板块出现次数（q_hot_sector_stats）
    'hot_cnt': '最热板块次数', 'other_sector': '其它板块',
    'other_cnt': '其它板块次数',
    # 两榜前三交叉（q_hot_sector_cross）。cross_n 是宽松口径（同时属于这两个
    # 板块），strict_n 是上一张卡的严格口径（A 是这只票最热的、B 在它前三里）。
    # 两个数放在同一行上，是因为它们经常差一个数量级（19 只 vs 0 只），
    # 只给一个数会让人以为另一个口径也是这个量。
    'cross_n': '交叉只数', 'strict_n': '严格只数',
    'cross_avg_oper': '交叉平均操作分', 'cross_avg_chg': '交叉平均涨跌幅',
    'cross_names': '个股（按操作分降序）',

    'avg_oper': '平均操作分', 'avg_pct': '平均涨跌幅%',
    # 自选组聚合列（2026-08-29 学旧系统 sector_y_score.calc_group_heat 与
    # score_amp_trend 的思路做的）。w_oper 与 avg_oper 要一起看：前者按成交额
    # 加权、后者等权，差得多说明这组的分是被成交额最大的一两只票拉出来的。
    'w_oper': '加权操作分', 'wscore': '加权操作分', 'scored': '有分只数',
    'trend_score': '趋势分数', 'pred_oper': '预测操作分',
    'trend_days': '趋势天数',
    # 四象限趋势表（q_quadrant_trend）。**r2 和 shape 必须跟 trend_score 一起看**：
    # 斜率只说往哪斜，r2 说这条斜线有多可信，shape 是两者加"距最高"的结论。
    'r2': '拟合度R²', 'shape': '形态', 'spark': '趋势图',
    'xy_max10': '窗口最高X+Y', 'xy_max_date': '最高那天',
    'gap_from_max': '距最高', 'up_streak': '连升天数',
    'quad_path': '象限迁移', 'quad_up': '升格',

    'avg_amp10': '组内10日振幅%', 'avg_amp20': '组内20日振幅%',
    'avg_amp_pred': '组内明日振幅%',
    # 量能
    'vol_stage': '量能阶段', 'vol_temp': '量能温度', 'stage': '量能阶段',
    'avg_temp': '平均量能温度',
    # 倍量震荡榜（q_vol_double）。**dbl_count 是排序键**，基准是"前 5 日均量×2"；
    # dbl_prev_count 是"对昨日量翻倍"的对照口径，两者差得大说明量本身在剧烈跳动。
    # 震荡与否看 cross_count / above_ratio / dev_med / ma60_chg 四个一起，
    # 单看偏离度会把"缓慢单边、股价一直压着均线"的趋势票当成震荡。
    'dbl_count': '倍量次数', 'dbl_prev_count': '倍量次数(对昨日)',
    'cross_count': '穿越60线次数', 'dev_med': '偏离60线中位%',
    'dev_max': '最大偏离%', 'above_ratio': '线上天数占比%',
    'ma60_chg': '60线变化%', 'ma60_last': 'MA60', 'close_last': '收盘',
    'avg_amt_yi': '日均成交额(亿)', 'ma60_days': 'MA60有效天数',
    'bars': '有行情天数',

    # 资金
    'delta_yi_yuan': '净申赎(亿元)', 'delta_yi_units': '份额变动(亿份)',
    'etf_count': 'ETF 只数', 'rz_yi': '融资余额(亿)', 'rq_yi': '融券余额(亿)',
    'rzrq_yi': '两融余额(亿)', 'lhb_wan': '龙虎榜成交(万)',
    'net_buy_wan': '净买额(万)', 'net_buy_ratio': '净买占比%',
    'lhb_count': '上榜次数', 'reason': '上榜原因',
    # 情绪
    'mood_source': '情绪口径', 'ma5': '5日均线', 'delta': '日变化',
    # 作业 / 运维
    'job_key': '作业', 'kind': '类型', 'trigger_kind': '触发方式',
    'cron_expr': '定时表达式', 'after_job': '后续作业', 'enabled': '启用',
    'only_trade_day': '仅交易日', 'timeout_sec': '超时(秒)',
    'batch_id': '批次号', 'source': '来源', 'target': '目标对象',
    'started_at': '开始时间', 'finished_at': '结束时间', 'status': '状态',
    'row_count': '行数', 'message': '消息', 'run_at': '执行时间',
    'rule_key': '规则', 'passed': '通过', 'severity': '级别',
    'schema': '模式', 'table': '表', 'approx_rows': '估算行数',
    'has_limit': '有涨跌停价', 'has_turnover': '有换手率',
    # 字段字典
    'table_name': '表', 'column_name': '字段', 'label_cn': '中文名',
    'unit': '单位', 'dtype': '类型', 'value_range': '取值范围',
    'sane_range': '合理区间', 'formula': '口径', 'source_key': '数据源',
    'display': '是否展示',
}

_LABEL_CACHE: dict[str, object] = {'at': 0.0, 'val': None}
_LABEL_TTL = 300.0


def col_labels() -> dict[str, str]:
    """
    字段名 → 中文抬头。缓存 5 分钟，避免每张表都去查一次注册表。

    注册表里同名字段给出多个不同中文名时**放弃使用注册表的那一条**：
    宁可退回兜底表，也不要在两个含义之间随机挑一个。
    """
    now = time.monotonic()
    cached = _LABEL_CACHE['val']
    if cached is not None and now - float(_LABEL_CACHE['at']) < _LABEL_TTL:
        return cached                                    # type: ignore[return-value]
    reg: dict[str, set[str]] = {}
    try:
        for r in engine.query(
                'SELECT column_name, label_cn FROM config.field_spec '
                'WHERE label_cn IS NOT NULL'):
            reg.setdefault(r['column_name'], set()).add(r['label_cn'])
    except Exception:
        reg = {}                # 注册表读不到就只用兜底表，不能让表格渲染不出来
    out = {k: next(iter(v)) for k, v in reg.items() if len(v) == 1}
    out.update(_FALLBACK_LABEL)
    _LABEL_CACHE['val'] = out
    _LABEL_CACHE['at'] = now
    return out


def col_label(key: str) -> str:
    return col_labels().get(key, key)


# ---------------------------------------------------------------- 基础件

def stat(label: str, value, *, hint: str | None = None, tone: str = 'accent',
         note: str | None = None, note_id: str | None = None,
         help_key: str | None = None):
    """
    指标块。有说明就在标签右边挂一个「说明」。

    ★指标块也必须能挂说明：「大盘温度 23.6」「涨停家数 41」这种数字，
    不看口径根本不知道是怎么数出来的（涨停是按 `limit_up` 判还是按 9.8% 判，
    差一大截）。原来 `stat` 没有 note 参数，于是首页那 6 个指标块是**全站
    唯一没有任何说明入口**的一批卡片。

    `help_key` 走 `config.help_note` 的 `scope='stat'`，优先级同 `card()`：
    库 > 显式 note > 代码兜底。
    """
    note = help_text(help_key, note, scope='stat')
    head = [html.Span(label)]
    if note:
        hid = note_id or f'note-{abs(hash((label, note))) % 10 ** 9}'
        head.append(html.Span('说明', id=hid,
                              className='ms-1 badge bg-light text-primary',
                              style={'cursor': 'pointer', 'fontWeight': 400,
                                     'fontSize': '.6rem'}))
        head.append(dbc.Popover(
            dbc.PopoverBody(dcc.Markdown(note, className='small mb-0'),
                            className='py-2'),
            target=hid, trigger='click hover focus', placement='bottom',
            style={'maxWidth': '460px'}))
    return dbc.Card(dbc.CardBody([
        html.Div(head, className='text-muted small'),
        html.Div(value, style={'fontSize': '1.6rem', 'fontWeight': 600,
                               'color': COLORS.get(tone, COLORS['accent'])}),
        html.Div(hint or '', className='text-muted', style={'fontSize': '.75rem'}),
    ]), className='h-100 shadow-sm')




# ---------------------------------------------------------------- 行情外链

# 表格里哪些列是"代码"、哪些是"名称"。命名查询的列名有中文有英文，两套都认。
CODE_KEYS = ('代码', 'ts_code', '股票代码', '证券代码', 'code', '成分股代码')
NAME_KEYS = ('名称', 'name', '股票名称', '证券简称', '简称', '成分股名称')


def _first_key(row: dict, keys) -> str | None:
    return next((k for k in keys if k in row), None)


def linkify_rows(rows: list[dict]) -> tuple[list[dict], set[str]]:
    """
    把每行的代码列和名称列换成指向东方财富行情页的 Markdown 链接。

    返回 `(新的行, 需要按 markdown 渲染的列名集合)`。同一只票点代码或点名字
    都能过去 —— 复盘时眼睛落在名字上的时候多，只给代码加链接等于没加。

    板块表也有「代码」列（880501 之类），`quote_link.em_url()` 对这些返回
    None，于是原样留着不加链接：宁可少一个链接，也不能给出一个点开是 404
    的链接。
    """
    if not rows:
        return rows, set()
    code_key = _first_key(rows[0], CODE_KEYS)
    if code_key is None:
        return rows, set()
    name_key = _first_key(rows[0], NAME_KEYS)
    md_cols: set[str] = set()
    out = []
    for r in rows:
        row = dict(r)
        raw_code = r.get(code_key)
        if quote_link.em_url(raw_code):
            row[code_key] = quote_link.em_markdown(raw_code)
            md_cols.add(code_key)
            if name_key and r.get(name_key) not in (None, ''):
                linked = quote_link.em_markdown(raw_code, r.get(name_key))
                if linked != str(r.get(name_key)):   # 名字里有 []() 就不加
                    row[name_key] = linked
                    md_cols.add(name_key)
        out.append(row)
    return out, md_cols


def detail_link(ts_code, label: str = '看详情') -> str:
    """
    站内深链，实现在 `app/quote_link.py`（那个模块不依赖 dash，`queries.py`
    也要用它，写在这里会和 `queries` 循环 import）。这里只是给页面一个顺手的
    入口 —— 页面已经在 import ui 了。

    表格里当普通字符串列塞进去即可，`table()` 会认出它是 Markdown 链接。
    """
    return quote_link.detail_link(ts_code, label)





# 涨跌与振幅的配色。红涨绿跌是 A 股习惯，别按国际惯例反过来。
TONE_UP, TONE_DOWN, TONE_FLAT = '#dc3545', '#198754', '#6c757d'
TONE_WARN = '#fd7e14'
# 一行内 5 个收盘价按自身高低排名的配色：最高中红 → 最低绿。
RANK_TONES = ('#c0392b', '#e08a7d', '#8fc9a0', '#4caf7d', '#198754')
# 排名列的列名前缀。这些列只为配色而存在，用 hidden_cols 藏起来
RANK_PREFIX = '_rk_'

# 振幅档位（%）：>3 红、>1.5 橙、其余灰。沿用旧系统阈值，换阈值等于换语义
AMP_HOT, AMP_WARM = 3.0, 1.5


def rules_pct(cols) -> list[dict]:
    """涨跌类列：>0 红、<0 绿、=0 灰。只染文字色，不染底色 —— 一行里有五六个
    这样的列，全部染底色整张表会花得看不出重点。"""
    out = []
    for c in cols:
        out += [
            {'if': {'column_id': c, 'filter_query': f'{{{c}}} > 0'},
             'color': TONE_UP, 'fontWeight': 600},
            {'if': {'column_id': c, 'filter_query': f'{{{c}}} < 0'},
             'color': TONE_DOWN, 'fontWeight': 600},
            {'if': {'column_id': c, 'filter_query': f'{{{c}}} = 0'},
             'color': TONE_FLAT},
        ]
    return out


def rules_amp(cols) -> list[dict]:
    """振幅类列：>3% 红、>1.5% 橙、其余灰。振幅没有正负，所以不能复用 rules_pct。"""
    out = []
    for c in cols:
        out += [
            {'if': {'column_id': c, 'filter_query': f'{{{c}}} > {AMP_HOT}'},
             'color': TONE_UP, 'fontWeight': 600},
            {'if': {'column_id': c,
                    'filter_query': f'{{{c}}} > {AMP_WARM} && {{{c}}} <= {AMP_HOT}'},
             'color': TONE_WARN},
            {'if': {'column_id': c, 'filter_query': f'{{{c}}} <= {AMP_WARM}'},
             'color': TONE_FLAT},
        ]
    return out


def rules_flag(cols, value: str = '↑') -> list[dict]:
    """
    标记列：等于某个符号时红字加粗（`quad_up` 的 `↑`）。

    **用文本等值判断，不用布尔**：DataTable 的 `filter_query` 对布尔列的写法在
    版本之间不稳，匹配不上是静默失效（页面不报错、只是不亮）。所以产生这一列的
    查询直接给符号，配色只做一次字符串比较。
    """
    return [{'if': {'column_id': c, 'filter_query': f'{{{c}}} = "{value}"'},
             'color': TONE_UP, 'fontWeight': 700} for c in cols]


def rules_below(cols, threshold: float) -> list[dict]:
    """
    低于阈值就压灰：用在"这个数不可信"的列上（趋势表的 `r2` < 0.5）。

    为什么不复用 `rules_amp`：那是振幅的三档固定阈值（3 / 1.5），语义是"热不热"；
    这里是"信不信"，阈值来自调用方。共用一个函数会让改振幅阈值把可信度也改了。
    """
    return [{'if': {'column_id': c, 'filter_query': f'{{{c}}} < {threshold}'},
             'color': TONE_FLAT, 'fontStyle': 'italic'} for c in cols]


def rules_rank(pairs) -> list[dict]:
    """
    「这一行里这几个值谁高谁低」的配色。`pairs` 是 `(值列, 排名列)`。

    为什么要一列排名：DataTable 的 `filter_query` 只能拿同一行的列相互比较，
    没法表达"在这 5 个数里排第几"。所以排名在 Python 里算好、单独放一列，
    再用 `hidden_cols` 藏起来 —— 规则数就从 5×行数 降到 5×5 条。
    """
    out = []
    for value_col, rank_col in pairs:
        for i, tone in enumerate(RANK_TONES, 1):
            out.append({'if': {'column_id': value_col,
                               'filter_query': f'{{{rank_col}}} = {i}'},
                        'color': tone, 'fontWeight': 600})
    return out


def rank_within(row: dict, cols, prefix: str = RANK_PREFIX) -> dict:
    """
    把一行里 `cols` 这几列的高低排名写成 `prefix+列名` 的新键，返回新行。

    **必须全部有效才排名**（旧系统同样口径）：5 个收盘价里缺一个，剩下 4 个
    的"第几高"就换了含义，染出来的颜色会骗人 —— 宁可这一行不染色。
    """
    vals = [row.get(c) for c in cols]
    out = dict(row)
    if any(v in (None, '') for v in vals):
        for c in cols:
            out[prefix + c] = None
        return out
    order = sorted(range(len(vals)), key=lambda i: float(vals[i]), reverse=True)
    for place, i in enumerate(order, 1):
        out[prefix + cols[i]] = place
    return out


# ---------------------------------------------------------------- 列宽自适应
# 单元格字体是 `Consolas .82rem`（≈13.1px）。等宽字体的好处是宽度能**算**出来：
# ASCII 一格约 7.3px，中日韩全角字约 13.2px。不用浏览器量，Python 侧就能定宽。
_MD_LINK = re.compile(r'\[([^\]]*)\]\([^)]*\)')


def _display_text(v) -> str:
    """单元格**看得见**的文字。Markdown 链接列显示的是方括号里的标签，
    不是 `[600519.SH](https://…)` 整串 —— 按整串算宽度会宽出三四倍。"""
    if v is None:
        return ''
    return _MD_LINK.sub(r'\1', str(v))


def _text_px(s: str, *, bold: bool = False) -> float:
    """
    一段文字大概占多少像素。只为算列宽用，不追求精确。

    **块字符按全宽算**（U+2500–U+259F：制表符与 `▁▂▃▄▅▆▇` 这些方块）：
    它们的东亚宽度是"歧义"，在中文字体里实际按全角渲染。按半宽算的话，
    10 格的迷你走势量出 73px、列宽给到 85px，而它真正要 130px 左右 ——
    页面上表现为趋势图被挤掉最后几格，看起来像"数据只有 6 天"。
    """
    w = 7.9 if bold else 7.3          # 表头 font-weight:600，同字号略宽
    cjk = 13.4 if bold else 13.2
    return sum(cjk if ord(ch) > 0x2E7F or 0x2500 <= ord(ch) <= 0x259F else w
               for ch in s)


def col_widths(rows: list[dict], keys, *, editable=(), dropdowns=(),
               lo: int = 44, hi: int = 300) -> list[dict]:
    """
    按内容算每列宽度，返回 `style_cell_conditional`。

    为什么不用 DataTable 的自适应：**表头冻结**（`fixed_rows`）的实现是把抬头和
    表体拆成两个 `<table>`，两张表各自按自己那部分内容算宽度，算出来不一样，
    抬头就跟数据错位。所以宽度必须写死 —— 但"写死"不等于"全站一个 120px"：
    这里按**实际内容**算完再写死，既保住对齐，又不让「涨跌幅」那种四字列
    占着 120px 的地。

    **2026-08-31 收紧了内边距与下限**（用户："单元格紧凑一些，列紧凑一些，
    很多都是很空的"）：`pad` 18 → 12、`lo` 54 → 44，配合 `style_cell` 的
    padding 从 `4px 8px` 收到 `2px 5px`。合表之后一行有 18 列，旧尺寸下
    1920 屏要横向滚，而"名次""象限"这种两三字列白占 54px。

    **同日再改：宽度只看单元格内容，不看表头**（用户："按照单元格内容自适应
    宽度，而不是按照标题，标题过长的话改为换行显示"）。原来取 `max(表头, 内容)`，
    于是「窗口最高X+Y」这种六字表头把一列 `47.9` 撑到 105px —— 一屏能看的列数
    被表头文案决定，而表头只需要看一眼、数据要看一整屏。表头放不下的**换行**
    （`style_header` 的 `whiteSpace: normal`），最多三行，`HEAD_PX` 按三行留。
    下限 `lo` 保留：太窄时表头的排序箭头会把字挤成一列一个字。
    """
    out = []
    for k in keys:
        body = max((_text_px(_display_text(r.get(k))) for r in rows), default=0)
        pad = 12                                        # padding 5+5 + 边框
        if k in editable:
            pad += 14        # 就地编辑时格子里是输入框，比纯文本要挤一点
        if k in dropdowns:
            pad += 26        # 下拉箭头
        w = min(hi, max(lo, round(body + pad)))
        out.append({'if': {'column_id': k}, 'minWidth': f'{w}px',
                    'width': f'{w}px', 'maxWidth': f'{w}px'})
    return out


# 表格的行高与抬头高度，`fit_height()` 与各页面共用一份。
# 行高 23px 是 `style_cell` padding `2px 5px` + 字号 .82rem 实际渲染出来的高度
# （2026-08-31 之前 padding 是 `4px 8px`，行高 27px）。
# 抬头 54px：同日去掉了 `filter_action='native'`（那一行逐列筛选框占 32px），
# 剩下的只有抬头本身；抬头现在允许换行，54px 够放三行（行高 1.15 × 13px + padding）。
ROW_PX, HEAD_PX, TAIL_PX = 23, 54, 12


def fit_height(n_rows: int, cap: int = 1240) -> int:
    """
    表格高度按行数长，长到 `cap` 就封顶。

    写死一个高度两头都不对：★重点关注只有 2 只票，720px 会拖出一大片空白；
    43 个组给 320px 又要翻页。抬头 `HEAD_PX`（54 = 换行后最多三行）+ 每行
    `ROW_PX`，末尾留 12px 免得最后一行贴着边框。


    **行高是算出来的常量，不是猜的**：`style_cell` 的 padding 一改，这里必须跟着
    改，否则"一屏铺完 40 行"就会差出几十像素 —— 表格自己会滚，页面上看不出是
    这里算错了。所以两个页面都调这一个函数，不各写一份。
    """
    return min(cap, HEAD_PX + ROW_PX * max(n_rows, 1) + TAIL_PX)


def table(rows: list[dict], *, page_size: int = 15, height: int | None = None,
          max_height: int | None = None,
          id_: str | None = None, link_codes: bool = True,
          color: list[dict] | None = None, hidden_cols=(),
          editable_cols=(), row_selectable: str | None = None,
          selected_rows=(), dropdowns: dict | None = None):
    """
    全站唯一的表格入口 —— 所有页面都走这里，所以**表头冻结和行情外链在这里改
    一次就全站生效**。

    `fixed_rows={'headers': True}`：滚轮往下滑时抬头留在原地。没有它的时候，
    表格一滚就只剩一片数字，"这一列到底是 X 分还是 Y 分"得滚回顶部去看。

    两个必须一起设的配套项，少一个冻结就会翻车：

    - **列宽要写死**（`minWidth/width/maxWidth`）。DataTable 冻结表头的实现是把
      抬头和表体拆成两个 `<table>`，列宽靠各自内容自适应的话两张表算出来的宽度
      不一样，抬头就会跟数据**错位**。★但"写死"是按**实际内容**算出来再写死的
      （`col_widths()`），不是全站一个 120px —— 后者会让「涨跌幅」这种四字列
      白占一倍宽，一屏塞不下几列。
    - **滚动条必须在表格自己身上**（`overflowY` + 高度上限）。卡片外层
      `card()` 的 CardBody 也有 `overflowY: auto`，没给高度时滚的是卡片而不是
      表格，那样 sticky 的参照物不对，抬头照样被滚走 —— 所以没传 height 时
      也给一个 `maxHeight` 兜底。

    `link_codes=True`（默认）时代码列和名称列变成东方财富行情页的链接，
    `link_target='_blank'` 另开标签页 —— 点一下就跳走会把当前看的复盘页顶掉。

    `color` 是 `style_data_conditional` 规则（用 `color_rules()` 生成），
    `hidden_cols` 是要参与计算但不显示的列（例如排名列，见 `rules_rank`）。

    ---- 可编辑 / 可选行（2026-08-29 为自选维护台加的） ----

    - `editable_cols`：哪些列可以**双击就地改**。整表 `editable=True` 太粗 ——
      改了个算出来的列（盈亏、操作分）根本没地方存，只会误导人；所以是按列开。
      markdown 列（代码/名称/详情那种带链接的）**不会**被设成可编辑：
      DataTable 的 markdown 单元格没有编辑态，设了也编不动。
    - `row_selectable`：`'single'` / `'multi'`，加一列勾选/单选框。
      配 `selected_rows`（`data` 里的行下标）给默认选中。
      ★注意下标是 `data` 的原始下标，不是排序后看到的顺序 —— 原生排序只改视图
      （`derived_virtual_data`），`data` 本身不动，所以下标一直有效。
    - `dropdowns`：`{列名: [可选值...]}`，把那一列做成下拉（`presentation:
      'dropdown'`）。用在只有两三个合法值的列上（比如 ★ / 空），比让人手打
      一个特殊字符靠得住。
    """
    if not rows:
        return html.Div('无数据', className='text-muted p-3')
    md_cols: set[str] = set()
    if link_codes:
        rows, md_cols = linkify_rows(rows)
    # 页面自己塞进来的 Markdown 链接列（`detail_link()` 造的「详情」列）也要按
    # markdown 渲染，否则表格里显示的是 `[看详情](/stock?code=…)` 这串字面量。
    # 按值认而不是按列名认：列名是各页面自己起的，写死一个「详情」下次就漏。
    md_cols |= {k for k, v in rows[0].items()
                if isinstance(v, str) and v.startswith('[') and '](' in v}
    editable = {c for c in editable_cols if c not in md_cols}
    dropdowns = dropdowns or {}
    cols = []
    for k in rows[0]:
        c: dict = {'name': col_label(k), 'id': k}
        if k in md_cols:
            c['presentation'] = 'markdown'
        elif k in dropdowns:
            c['presentation'] = 'dropdown'
        if k in editable:
            c['editable'] = True
        cols.append(c)
    data = [{k: ('' if v is None else v) for k, v in r.items()} for r in rows]
    kwargs = {'id': id_} if id_ else {}
    if hidden_cols:
        kwargs['hidden_columns'] = list(hidden_cols)
    if color:
        kwargs['style_data_conditional'] = color
    if editable:
        # 整表 editable 打开、再靠每列的 `editable` 关掉不该改的：DataTable 的
        # 列级 editable 只在表级为 True 时才被看。可编辑列给个淡黄底，
        # 不然人不知道哪一格能改。
        kwargs['editable'] = True
        kwargs['style_data_conditional'] = (kwargs.get('style_data_conditional')
                                            or []) + [
            {'if': {'column_id': c}, 'backgroundColor': '#fffdf0'}
            for c in sorted(editable)]
    if row_selectable:
        kwargs['row_selectable'] = row_selectable
        kwargs['selected_rows'] = list(selected_rows)
    if dropdowns:
        kwargs['dropdown'] = {
            c: {'options': [{'label': (v if v != '' else '（空）'), 'value': v}
                            for v in vals]}
            for c, vals in dropdowns.items()}
    return dash_table.DataTable(
        columns=cols, data=data, page_size=page_size,
        # `filter_action='native'` 2026-08-31 去掉：它会在抬头下面多出**一整行
        # 逐列筛选输入框**，看着像"第二行标题"（用户问"第二行是不是数据库字段
        # 名"—— 不是，那是筛选框）。占一行高度、且每张表都白占，代价比收益大；
        # 要恢复逐列筛选把 `filter_action='native'` 加回来、`HEAD_PX` 回 76 即可。
        sort_action='native',
        fixed_rows={'headers': True},
        markdown_options={'link_target': '_blank'},
        # markdown 单元格里内容会被包进 <p>，`<p>` 自带下边距会把行高顶高、
        # 让带链接的行比别的行高一截（表头冻结时看着尤其乱）。清掉。
        css=[{'selector': '.dash-cell .cell-markdown p',
              'rule': 'margin: 0; padding: 0;'}],
        style_table={'overflowX': 'auto', 'overflowY': 'auto',
                     # **默认不给高度＝自然高度**（2026-09-01 改）：一页有几行就
                     # 画多高，既不会在表格里滚、也不会在表格下面留白。
                     #
                     # 原来这里是"给了 height 就固定住、没给就封顶 420px"，两头都
                     # 会骗人：固定高度比一页内容矮 → 想看更多得**在表格里**滚
                     # （用户报的"数据部分还是跟以前一样大"）；比内容高 → 表格
                     # 下面一片空白（用户报的"下面部分是空的"）。而"一页多少行"
                     # 已经由 `page_size` 定了，高度是它的**结果**，不该再手填一遍
                     # —— 手填两个数就一定会漂（实测库里 18 张表格卡，13 张的
                     # 高度比一页内容矮 11~1,386px）。
                     #
                     # 要限高就显式传 `max_height`（页面上会变成"表格内部滚动"）。
                     'height': f'{height}px' if height else None,
                     'maxHeight': f'{max_height}px' if max_height else None},
        # 字号/字体/换行是全表一致的；**宽度按列各算一份**（见 col_widths）。
        # 隐藏列不参与：它们不占宽，算了也是白算。
        # padding 2026-08-31 从 `4px 8px` 收到 `2px 5px`：行高从 27px 降到 23px，
        # 同一个高度多显示三四行，横向也省下十几列 × 6px。再小就点不准单元格了
        # （十字高亮靠点格子生效）。
        style_cell={'fontSize': '.82rem', 'padding': '2px 5px',
                    'fontFamily': 'Consolas, Menlo, monospace',
                    'whiteSpace': 'nowrap', 'overflow': 'hidden',
                    'textOverflow': 'ellipsis'},
        style_cell_conditional=col_widths(
            data, [c['id'] for c in cols if c['id'] not in set(hidden_cols)],
            editable=editable, dropdowns=set(dropdowns)),
        # 表头**允许换行**（2026-08-31）：列宽只按单元格内容算，长标题
        # （「窗口最高X+Y」「板块出现次数」）放不下就折成两三行，而不是反过来
        # 让标题把整列撑宽。`height: auto` 是必须的 —— 不给的话换行的字会溢出
        # 抬头、和第一行数据叠在一起。
        style_header={'fontWeight': 600, 'backgroundColor': '#f4f6f8',
                      'whiteSpace': 'normal', 'height': 'auto',
                      'lineHeight': '1.15'},
        **kwargs)




def alert(msg: str, color: str = 'secondary'):
    # dbc 必须显式 import —— 旧系统 web/helpers.py 用了 dbc.Alert 却没 import
    # dash_bootstrap_components，四处调用全是 NameError。ruff 的 F821 就是为
    # 拦这个而开的。
    return dbc.Alert(msg, color=color, className='mb-2 py-2')


def card(title: str | None, body, *, height: int | None = None,
         footer=None, note: str | None = None, note_id: str | None = None,
         help_key: str | None = None):
    """
    卡片。有说明就在标题右边挂一个「说明」，点一下（或悬停）弹出解释。

    为什么要有这个：卡片标题只有四五个字，"操作分排行""四象限散点"这种名字
    不看口径根本不知道在说什么。

    说明的来源按优先级：**`config.help_note`（库，按字段拆开存）> 显式传进来的
    `note` > `ui.CARD_HELP`（代码兜底）**。给了 `help_key` 就会去库里查 ——
    库里改完刷新页面就生效，不用改代码不用重启。手写卡传自己的稳定别名
    （如 `stock.kline`），配置卡传 `card_ref`。

    用 Popover + trigger 而不是 Collapse：Collapse 要回调，而卡片是按
    config.page_layout 动态生成的，回调数量会跟着配置变，容易撞 id。
    """
    note = help_text(help_key, note)
    parts = []
    if title:
        head = [html.Span(title)]
        if note:
            hid = note_id or f'note-{abs(hash((title, note))) % 10 ** 9}'
            head.append(html.Span('说明', id=hid,
                                  className='ms-2 badge bg-light text-primary',
                                  style={'cursor': 'pointer', 'fontWeight': 400}))
            head.append(dbc.Popover(
                dbc.PopoverBody(dcc.Markdown(note, className='small mb-0'),
                                className='py-2'),
                target=hid, trigger='click hover focus', placement='bottom',
                style={'maxWidth': '460px'}))
        parts.append(dbc.CardHeader(head, className='py-2 fw-semibold'))
    parts.append(dbc.CardBody(body, className='p-2',
                              style={'height': f'{height}px',
                                     'overflowY': 'auto'} if height else None))
    if footer:
        parts.append(dbc.CardFooter(footer, className='py-1 small text-muted'))
    return dbc.Card(parts, className='h-100 shadow-sm')


def note_badge(note: str | None, *, note_id: str, label: str = '说明',
               placement: str = 'bottom'):
    """
    一个独立的「说明」徽标 + 弹层。卡片之外的地方（页面抬头、tab 标签）要挂说明
    就用它 —— 不然只有 `card()` 里面能挂，整页级的说明没有落脚点。
    """
    if not note:
        return None
    return html.Span([
        html.Span(label, id=note_id,
                  className='ms-2 badge bg-light text-primary',
                  style={'cursor': 'pointer', 'fontWeight': 400}),
        dbc.Popover(
            dbc.PopoverBody(dcc.Markdown(note, className='small mb-0'),
                            className='py-2'),
            target=note_id, trigger='click hover focus', placement=placement,
            style={'maxWidth': '520px'}),
    ])


def help_text(help_key: str | None, note: str | None = None,
              *, scope: str = 'card') -> str | None:
    """
    解析一个窗口的说明文字：**库 > 显式 note > 代码兜底**。

    库优先是这张表的意义所在 —— 说明是内容，不是代码。代码兜底留着是为了
    不倒退：存量 34 条还在 `CARD_HELP` 里，库里没有就照旧显示那份，
    而不是显示空白。
    """
    if help_key:
        from app.web import help as help_repo
        db = (help_repo.for_page(help_key) if scope == 'page' else
              help_repo.for_stat(help_key) if scope == 'stat' else
              help_repo.for_card(help_key))
        if db:
            return db
    return note or (CARD_HELP.get(help_key) if help_key else None)


# 按 card_ref（命名查询名或 config.chart.chart_key）兜底的口径说明。
# config.page_layout.options.note 优先 —— 那是前台能改的一层。
CARD_HELP: dict[str, str] = {
    'q_pattern_streak': (
        '**前 N 到前 M 个交易日这段窗口里，涨停若干次的票。**\n\n'
        '`涨停次数`＝**窗口内涨停的天数**（用户 2026-09-01 定的口径）：窗口里'
        ' D1、D2、D3 连着涨停，隔一天 D5、D6 又连着，算 **5 次** —— 不是 2 段，'
        '也不是 3 板。口语里的"连板 3 次"通常指后两种，所以这一列叫「涨停次数」，'
        '另给一列 `连板高度`＝最长那一段的板数（上例是 3）。'
        '**两个数一起看**：涨停次数 5 而连板高度 2，是"反复涨停但接不上"；'
        '两个都是 5，是一口气 5 连板，完全不同的两种票。\n\n'
        '`涨停日期` 把那几天列出来，用来判断是"窗口头部涨完就歇了"还是'
        '"贴着窗口右边还在涨" —— 只看次数看不出这件事。\n\n'
        '窗口两端都含：填 3 和 7 ＝ 倒数第 3 到第 7 个交易日；填 0 ＝ 含基准日本身。'
        '按**交易日**数不按自然日，长假不会把窗口挤掉。\n\n'
        '**涨停判据用库里的真实涨停价** `md.stock_daily.limit_up`'
        '（`收盘 >= 涨停价 - 0.005`），不是"涨幅 > 9.8%"：阈值口径会把创业板'
        ' 20%、北交所 30%、主板 ST 5% 全判错，而且错的方向是**漏掉** ——'
        '页面上表现为"这票明明涨停了却没筛出来"。老数据 `limit_up` 为空时'
        '才退回按板块的涨幅阈值。\n\n'
        '可以作为什么的参考：**找刚炒过一轮、正在回抽的票**（配合下面那张'
        '箱体表）；也可以用来数"这一轮的连板梯队里还剩谁"。\n'
        '⚠️ 不能当什么用：① 它只数历史，不含任何"接下来会不会涨"的判断；'
        '② 一字板与放量板在这里长得一样，要区分得看分时或量能；'
        '③ `操作分 / 象限` 来自 `fct.score`，基准日**还没算评分**时这两列是空的'
        '（当天收盘后因子链没跑就会这样），不是没有这只票。\n\n'
        '取数 `q_pattern_streak` → `app/compute/pattern.py:limit_streak_scan`'
        '（`md.stock_daily` 逐日打涨停标记，gaps-and-islands 数连续段）。'),

    'q_pattern_box': (
        '**断板之后走箱体、当日又跌破均线的票。**\n\n'
        '三段判据，缺一段都不算：\n\n'
        '1. `最后涨停日` / `断板日` —— 回看窗口内最后一个涨停日，它的**次日**'
        '就是断板日。窗口内没涨停过的票根本不参与：这个形态的前提是"曾经有过板"。'
        '`连板高度` 是断板前那一段的板数。\n'
        '2. `箱体天数` / `箱体上沿` / `箱体下沿` / `箱体振幅` —— 断板日到基准日'
        '之间的最高与最低，`箱体振幅`＝(上沿−下沿)/下沿×100 要 ≤ 阈值（默认 15），'
        '天数要 ≥ 阈值（默认 3），**且这期间不再涨停**（再涨停就是二波，'
        '不是箱体）。上下沿**不含断板前那根涨停** —— 含进去的话振幅永远等于'
        '那根板的涨幅，判据就废了。\n'
        '3. `均线` / `破线幅度` —— 基准日的收盘价（或盘中最低，可切）低于'
        ' 5/10/20 日线。`破线幅度`＝(价−均线)/均线×100，负得越多破得越狠，'
        '表格默认按它从最狠往上排。\n\n'
        '**均线是现算的**（`md.stock_daily.close` 的 N 日均值），不读'
        ' `fct.indicator_value`：2026-09-01 实测两者逐票完全相等（5,544 只，'
        '最大差 0），而现算不依赖因子链有没有跑到基准日 —— 读表的写法在'
        '"因子只算到昨天"时会安静地返回 0 行，而 0 行跟"今天真没有这种票"'
        '在页面上长得一模一样。\n\n'
        '可以作为什么的参考：**炒过一轮之后的缩量整理带**，破线是整理失败的'
        '第一个信号；反过来，破线后能快速收回也是强势的证据。\n'
        '⚠️ 不能当什么用：① 箱体只用"振幅够小 + 不再涨停"来近似，'
        '**不判断形态是不是真的平**（一根长上影 + 一路阴跌也可能振幅 12%）；'
        '② 跌破均线本身没有方向含义，它是**观察起点**不是卖点；'
        '③ 除权那天前后价格不连续，箱体上下沿会被除权跳空撑大（库里存的是'
        '不复权价）。\n\n'
        '筛出来之后要盯的（用户给的后续动作，不是这里的筛选条件）：'
        '**后一天对 10 日线一次反包，这次反包必须打板。然后观察后面两天的'
        '箱体突破，必须是一字板或加速秒板。**\n\n'
        '取数 `q_pattern_box` → `app/compute/pattern.py:box_break_scan`。'),


    'q_vol_double': (
        '**倍量震荡**：近 40 个交易日里**成交量反复翻倍**、但价格**一直贴着 '
        '60 日均线上下摆**的票，按倍量次数从高到低取前 50。\n\n'
        '找的是"主力在 60 线附近反复进出"这一种形态：量能一次次翻倍说明有资金在动，'
        '而价格既没突破也没破位 —— 所以它的用法是**等突破**，不是当天就买。\n\n'
        '**倍量的判据是"当日量 ≥ 前 5 个交易日均量 × 2"**，不是"≥ 昨日量 × 2"。'
        '用昨日量当基准的话，昨天缩量、今天回到正常量就会被算成一次倍量。'
        '对昨日的口径也一起算了，摆在 `倍量次数(对昨日)` 那一列作对照：'
        '两个数差得多，说明这只票的成交量本身就在剧烈跳动。\n\n'
        '**"沿 60 日线震荡"是四个条件同时成立**，不是只看贴得紧：\n\n'
        '- `偏离60线中位%` ≤ 8 —— 贴着线（用中位数，中间一根涨停拉不动它）\n'
        '- `线上天数占比%` 在 20~80 之间 —— 上下两侧都待过。87% 那种是'
        '"一直在线上"，属于趋势不是震荡\n'
        '- `穿越60线次数` ≥ 2 —— 真的来回穿过\n'
        '- `60线变化%` 的绝对值 ≤ 20 —— **60 线自己得是走平的**。'
        '均线在陡涨时，"股价贴着均线"讲的是趋势，不是震荡\n\n'
        '`最大偏离%` 配着中位数看：中位很小而最大值很大，说明中间被拉出去过一次'
        '又回来了。`日均成交额(亿)` 是流动性门槛（默认 ≥0.5 亿）——'
        '北交所小票能选出很漂亮的形态，但一天成交几百万，进出都成问题。\n\n'
        '`MA60有效天数` 说明这 40 天里有几天真算得出 MA60：**不满 60 根 K 线的'
        '那天记 NULL 不凑数**，所以次新股不会挤进来（默认要求 ≥35 天）。\n\n'
        '**阈值是可调的**：全套默认值在 `app/compute/vol_double.py` 的 '
        '`FILTER_DEFAULTS`，想改就往 `config.setting[\'vol_double.filter\']` 里'
        '写覆盖项，比如放宽成 `{"min_dbl": 3, "dev_med_max": 10}`。改完'
        '**立刻生效、不用重算** —— 库里 (`fct.vol_double`) 存的是全市场每只票的'
        '原始读数，不是"入选名单"。\n\n'
        '⚠️ 这张表读的是预算好的表，不是现算：现算一天要 10 秒（MA60 需要 60 根 '
        'K 线暖机，再往前推 40 天观察窗，一次要扫 55 万行）。所以**日线到了但 '
        '`calc_vol_double` 还没跑的那段时间，这张卡显示的是上一个交易日** ——'
        '以 `交易日` 那一列为准，它不会假装是今天的。'
    ),
    # 2026-08-31：原来的 `q_score_rank`（操作分排行）和 `q_review_stocks`

    # （个股明细）两张卡合成了一张 `q_stock_scan`。两条存量说明合并到这里，
    # 旧键不再保留 —— 卡下线了还留着说明，只会让"缺口清单"数不清。
    'q_stock_scan': (
        '**个股扫描**：当天该看的票一屏拉完 —— 五个评分 + 量能阶段 + 行情 + '
        '板块三列，按操作分从高到低，默认一页 30 行、取前 300 只。\n\n'
        '这张表是原来「个股明细」和「操作分排行」的合并：同一天、同一批票、'
        '同一个排序键，区别只是一张多量能列、一张多板块列和名次。分成两张卡的'
        '结果是同一只票要在上下两个表格里各找一次，才能同时看到"它的量能阶段"'
        '和"它所在的板块热不热"，而这两件事是同一个判断的两半。\n\n'
        '操作分 = 0.40 × X个股质量 + 0.60 × Y环境共振，取值 −100 ~ +100，'
        '**不是涨幅预测，是排序用的相对分**。\n\n'
        '- X 个股质量：形态、趋势、位置、筹码、量能五项合成，讲的是"这只票'
        '自己好不好"\n'
        '- Y 环境共振：0.65 × 板块热度 + 0.35 × 大盘情绪，讲的是"它踩在'
        '风口上没有"\n\n'
        'Y 占 60% 是有意的：同样质量的票，板块在风口上和在退潮里，'
        '结果完全不同。\n\n'
        '`名次` 是**当日全市场名次**（`fct.score.rank_in_market`），不是这张表的'
        '行号；`象限` 是 X/Y 的四格分类（Q1 双正最优、Q4 双负最差）。\n\n'
        '`量能阶段` 里**背离**最该盯 —— 天量之后缩量上涨，价格新高但量接不上。\n\n'
        '**板块三列怎么用**：`最热板块` 是这只票所属板块里当日热度最高的那个，'
        '`板块热度` 读 `fct.sector_heat_daily` 的 `src=\'sector\'`（板块级直算口径，'
        '不是旧的成员均值广播），`其它板块` 按热度依次再列两个。'
        '同一个板块反复出现（比如 18 只票的最热板块都是粮食概念）那是**板块效应**，'
        '孤零零一只高分票多半是个股事件 —— 这件事下面那张 Top10 表直接给答案。\n\n'
        '⚠️ `板块热度` 是 **−100 ~ +100 的相对强弱**，不是 0~100 的评分：'
        '实测当日 652 个板块里 329 个是负的、均值 0.8。看到 `−33.1` 不是脏数据，'
        '而是"这个板块今天弱于市场"。\n\n'
        '板块按**名称**去重：同一个板块在三个源下各有一个 code'
        '（`TDX_880642` / `GN_880642` / `THS_D522` 都叫「供销社」），不去重的话'
        '这三列会写成「供销社 / 新材料、供销社」，Top10 里这只票还会给同一个板块'
        '投三票 —— 页面上像"板块行情"，其实是同一个板块被数了三遍。'
        '热度取三份里最高的那份（某个源当天没算出来还能取到另一个源）。\n\n'
        '注意板块列显示的基本都是**概念板块** —— 申万式行业板块（880001–880499）'
        '的成分股还没入库，所以没有"主营行业"这一列可给。\n\n'
        '取数 `q_stock_scan` → `fct.score`（`d2_scores.py` 算的评分）'
        '⋈ `md.stock_daily`（行情）⋈ `ref.sector_member`/`ref.sector_info`'
        '⋈ `fct.sector_heat_daily`（板块热度）。'
        '基准日取**最近一个有评分的交易日**，不是日历上的今天。'),
    'q_hot_sector_stats': (
        '**前 666 只票里哪些板块反复出现**，两列各取前 10 名。\n\n'
        '- `最热板块` —— 每只票**那一个**最热板块的出现次数，'
        '所以这一列的次数加起来 ≤ 666；\n'
        '- `其它板块` —— 每只票的第 2、第 3 个板块**逐个计数**，一只票贡献多个，'
        '所以**这一列的总数必然超过 666**（实测 1,328）。两列不同量纲，不要横着比。\n\n'
        '"第 3、7、12 名都在同一个概念里"这件事，靠眼睛在几百行里扫是扫不出来的，'
        '而它正是"今天是不是板块行情"的直接答案。\n\n'
        '**2026-09-01 从前 100 只放大到前 666 只**：100 只只占全市场 5,545 只的'
        ' 1.8%，一个真在扩散的板块在里面往往只露 3~5 只，跟噪声分不开；'
        '666 只是 12%。上面那张扫描表的行数**同时**跟着放大到 666 —— '
        '两张卡必须是同一批票，否则统计里会有一批票在表上根本找不到。\n\n'
        '取数 `q_hot_sector_stats` → 与上面那张扫描表**同一段 SQL 取票**'
        '（`fct.score` ⋈ `md.stock_daily`），再展开 `ref.sector_member` 逐行计数。'
        '页面上「其它板块」显示成 `化肥、煤炭开采加工` 是 `string_agg` 拼的，'
        '这里**回到源头逐行数**，等价于把那串拆开数，且不依赖"板块名里不含 `、`"。\n\n'
        '计数按**板块名**去重：同一个板块在三个源下各有一个 code（`TDX_` / `GN_` / '
        '`THS_`），不去重的话一只票给同一个板块投三票，次数会虚高两三倍 —— '
        '2026-08-31 修掉之前「乡村振兴」是 34 次，实际 26 次。'),

    'q_hot_sector_cross': (
        '**上一张卡两列各取前 3 名，交叉成 9 种组合，逐个把票列出来。**\n\n'
        '上一张卡只说"智能家居 32 次、机器人概念 23 次"，看不出这两个概念'
        '**是不是同一批票撑起来的** —— 而这正是"今天是一个板块在动，'
        '还是两个概念叠在一起动"的分界。叠在一起的那批票，才是当天的题材交集。\n\n'
        '**口径：同时属于这两个板块就算，不看这两个板块在这只票里排第几。**'
        '这和上一张卡的列口径**不一样**，必须说清楚：\n\n'
        '- 上一张卡的「最热板块」是每只票**那一个**热度最高的板块，'
        '「其它板块」是它的第 2、第 3 个。按那个严格口径去交叉，'
        '2026-09-01 实测 **9 个组合里只有 1 个有票**（AIGC概念 × 机器人概念 ＝ '
        '迈瑞医疗）—— "A 是这只票最热的板块"和"B 也在它前三里"同时成立本来就极少见，'
        '那样的表没有信息量。\n'
        '- 所以这里放宽成"同时属于 A 和 B"。同一天 9 个组合里 8 个有票，'
        '最大的智能家居 × 机器人概念 19 只。`严格只数` 那一列仍然给出严格口径的数，'
        '**两个口径的差就摆在同一行上**，不用在两张卡之间来回换算。\n\n'
        '`交叉只数`＝这个组合里的票数；`个股` 按操作分从高到低列，带代码；'
        '`交叉平均操作分 / 交叉平均涨跌幅` 是这批票当日的均值 —— '
        '同样是 19 只，平均涨 3.7% 和平均跌 1% 是两回事。\n\n'
        '**9 行一行都不省**：某个组合 0 只票也占一行。'
        '"这两个概念完全不重叠"和"这一行漏了"是两件事，省掉行就分不出来了。'
        '两榜可能出现同一个板块（乡村振兴既在最热榜也在其它榜），'
        '那一行会写明"不与自己交叉"，不是算错。\n\n'
        '可以作为什么的参考：**题材共振的候选池** —— 两个都在榜上的概念，'
        '交集里的票同时被两条叙事覆盖，资金分歧小。\n'
        '⚠️ 不能当什么用：① 它不含任何强弱判断，只回答"谁同时属于这两个板块"，'
        '要不要买还得看操作分与量能；② 板块成分来自 `ref.sector_member`（通达信 ＋ '
        '同花顺），概念板块的成分本身就有"沾一点边也算"的问题，'
        '交集里出现看起来不相关的票（比如迈瑞医疗在 AIGC概念 × 机器人概念）'
        '是成分数据如此，不是这张卡算错；③ 只统计**前 666 只票**里的成员，'
        '不是全市场交集。\n\n'
        '取数 `q_hot_sector_cross` → 与上一张卡**同一段票池 SQL**'
        '（`_SCAN_BASE` + `_SECTOR_PICK`），所以 `top_n` / `sectors` '
        '两张卡要一起改，否则数的不是同一批票。'),



    'quadrant_scatter': (
        '**四象限**：横轴 X 个股质量，纵轴 Y 环境共振，一点一只票。\n\n'
        '- Q1 右上（X≥0, Y≥0）：自己好 + 风口上，主攻区\n'
        '- Q2 右下（X≥0, Y<0）：自己好但没风口，等板块\n'
        '- Q3 左上（X<0, Y≥0）：风口热但票本身弱，容易一日游\n'
        '- Q4 左下（X<0, Y<0）：双低，回避\n\n'
        '⚠️ 2026-08-29 更正：这段说明原来把 **Q3 和 Q4 写反了**（写成"Q4 左上 / '
        'Q3 左下"）。逐条核对当日 6,547 只票的 X / Y 符号后确认，库里的标签是'
        '上面这个对应关系。`/scores` 的选票框用的是核对后的口径。\n\n'
        '取数 `q_quadrant` → `fct.score` 三个 cap（`stock_quality` / '
        '`env_resonance` / `oper_score`）⋈ `ref.security`，上限 3000 点 —— '
        '5,500 个点画上去就是一团墨，看不出密度。'
        '两个轴都是 `d2_scores.py` 算出来的分，不是行情。'),

    'q_quadrant_pick': (
        '**按象限选票。** X 是个股质量、Y 是环境共振，两条零线把票切成四格；'
        '选一格，把里面的票列出来。\n\n'
        '### 四个格子怎么用\n\n'
        '- **Q1（X≥0, Y≥0）双正**：票自己好、板块也在风口上。**主攻区，默认就是它。**\n'
        '- **Q3（X<0, Y≥0）风口热、票暂时弱**：板块起来了但这只还没跟上。'
        '可试探，也最容易一日游。\n'
        '- **Q2（X≥0, Y<0）好票没风口**：基本面 / 形态没问题，缺的是环境。'
        '等板块，别急。\n'
        '- **Q4（X<0, Y<0）双负**：两头都不占，躲。\n\n'
        '按钮上的只数是**当日实际分布**，本身就是盘面信息：实测（2026-08-31）'
        'Q1 2,152 只、Q3 2,857 只、Q2 只有 172 只 —— Q2 这么少说明当天板块普涨，'
        '"好票"基本都落在风口里了。\n\n'
        '⚠️ 按钮上的数是**该象限全部**（不含 X/Y 门槛），所以它必然 ≥ 结果表的'
        '命中数。结果表上面那行会把当前门槛一起写出来。\n\n'
        '### 门槛与排序\n\n'
        '- **X / Y 下限默认都是 30**（2026-09-01 起，**推翻了原来"默认留空"的'
        '设定**）：原来下半截另有一张「X>30 且 Y>30 的双高个股」卡，和这张表'
        '同一天、同一批票、同一个口径，只是门槛和排序写死 —— 同一个问题摆两张表。'
        '现在合成一张，默认就是双高（Q1 + X≥30 + Y≥30，按 X+Y 排序），'
        '想看整个象限的全貌把两个下限清空。\n'
        '- **排序默认按 `X + Y 合计`**。想看加权口径就选操作分'
        '（`0.40·X + 0.60·Y`，Y 的话语权更大）—— **这两个不是一回事**。\n'
        '- 换象限、换排序**点一下立刻重算**；X / Y 下限和日期是边试边调的数字，'
        '按回车或点「选票」才生效。\n\n'
        '结果表每行带**站内深链「详情」**（`/stock?code=…`），挑中一只一步就能'
        '看它的 K 线和评分归因。'),
    'q_quadrant_strong': (
        '⚠️ **这张卡已在 2026-09-01 合并进「选票结果」**（那张表默认就是 Q1 + '
        'X≥30 + Y≥30，按 X+Y 排序）。查询本身还登记着，注册表里加回来即可用；'
        '说明留在这里是为了解释口径。\n\n'
        '**X 和 Y 同时 > 30 的票**，也就是散点图右上角 Q1 里最靠外的那一簇，'
        '按 `X+Y 合计` 从高到低。\n\n'
        '两条线是「且」不是「或」：X 高 Y 低 = 好票在冷板块，'
        'Y 高 X 低 = 热板块里的差票，都不在这张表里。\n\n'
        '⚠️ 和合并后的结果表有一处细微差别：这里是 `> 30`，那边是 `>= 30`'
        '（`q_quadrant_pick` 的门槛是闭区间），恰好 30.00 分的票只在那边出现。\n\n'
        '`X+Y 合计` 是直接相加，**跟操作分不是一回事** —— 操作分是 '
        '0.40·X + 0.60·Y，Y 的话语权更大。想按加权看就点「操作分」列排序。\n\n'
        '取数 `q_quadrant_strong` → `fct.score`（`stock_quality` / '
        '`env_resonance` / `oper_score` 三个 cap）⋈ `md.stock_daily` ⋈ '
        '`ref.security`。门槛 `min_x` / `min_y` 和条数上限在'
        '`config.page_layout.options.params` 里，改成 40 不用动代码。'),

    'q_sector_heat': (
        '**板块热度** = 0.30 × 涨幅 + 0.30 × 广度 + 0.25 × 动量 '
        '+ 0.15 × 成交额，取值 −100 ~ +100。\n\n'
        '广度指板块内上涨家数占比 —— 三只票拉涨停带起来的板块，'
        '涨幅高但广度低，热度不会给高分。\n\n'
        '**趋势列全部是"分/天"，向上为正、向下为负、0 为走平。**\n\n'
        '- `日趋势` = 今日热度 − 上一交易日热度\n'
        '- `N日趋势` = 最近 N 个交易日热度的**最小二乘斜率**。'
        '不是"(今日 − N 日前) ÷ N" —— 那个式子只用首尾两点，中间怎么走全丢，'
        '一根插针就能把趋势判反\n'
        '- 横着看一行就是这个板块的节奏：`日趋势` 高而 `21日趋势` 为负 = '
        '刚异动的退潮股；两头都为正 = 中期在升且今天还在加速\n'
        '- 点数不足窗口长度的格子**留空，不补 0**（0 在这里表示"走平"，'
        '把"算不出来"伪装成"没在动"）\n\n'
        '窗口列表来自 `config.setting[sector_trend.windows]`，加一个 13 日趋势'
        '改那个数组就行。数据由 `calc_sector_trend` 作业落进 '
        '`fct.sector_trend`，**不是查询时现算的**。\n\n'
        '`偏离均值` = 今日热度 − 近 5 日均值，正数说明还在加速。'
        '`有分只数` 是当天真有评分的成员数 —— 热度是均值，'
        '13 只票的均值和 130 只票的均值可信度差一个量级。'
        '**取的是最新有数据的交易日**（就是标题里那个日期），不是日历上的今天。\n\n'
        '`阶段` 列（2026-09-01 加）来自 `fct.ignition`：'
        '`① 点火 / ② 延续 / ③ 蓄势 / ④ 退潮 / ⑤ 无`。'
        '**热度高 + ④ 退潮 = 已经涨完的**，热度中等 + `① 点火` 才是刚起来的 ——'
        '完整口径和回测数字见上面那张「启动候选」卡的说明。'),
    'q_sector_stage_dist': (
        '**板块阶段分布**：四档各有几个板块，以及**与上一交易日的差**。\n\n'
        '取数 `q_sector_stage_dist` → `fct.ignition`（`scope=\'sector\'`，'
        '由 `scripts/compute/d4_ignition.py` 每日落库）⋈ `ref.sector_info`，'
        '只算行业与概念板块（风格板块是"昨日涨停/连板"这类信号板块，'
        '天生极端，混进来会把分布带偏）。\n\n'
        '**这张卡回答"现在整体处在什么阶段"**，下面那张候选表回答"具体是哪几个"。'
        '单看"今天有 3 个点火"没有参照物，`变化` 那一列才是市场层面的信息：\n\n'
        '- `① 点火` 在增加 = 资金在找新方向，可以往候选表里挑\n'
        '- `④ 退潮` 在增加 = 在撤，这时候点火的少数板块要更谨慎\n'
        '- 四档全在减少而 `⑤ 无` 在涨 = 没有主线，观望\n\n'
        '实测（2026-08-31 vs 08-28，行业+概念 448 个）：'
        '点火 1（+1）· 蓄势 21（−1）· 退潮 23（−28）· 无 403（+28）——'
        '退潮少了 28 个但点火只多 1 个，是"跌完了但还没人接"。\n\n'
        '⚠️ 两天都取 `fct.ignition` 里**真有数据的交易日**，不是日历日减一，'
        '所以长假之后比较的仍然是相邻两个交易日。'),

    'q_sector_ignite': (
        '**启动候选**：把"哪个板块刚起来"单独摆出来 —— '
        '**热度榜回答不了这件事**。热度是当日横截面分位，榜首永远是'
        '"已经涨完的那几个"；启动是**状态转变**。\n\n'
        '取数 `q_sector_ignite` → `fct.ignition`（`scope=\'sector\'`）'
        '⋈ `fct.sector_heat_daily` ⋈ `fct.sector_trend` ⋈ `md.sector_daily`。'
        '模型在 `app/compute/ignition.py`，`scripts/compute/d4_ignition.py` '
        '每日落库，阈值全在 `config.setting[ignition.params]` 里（改配置就生效）。\n\n'
        '### 四档怎么读\n\n'
        '- **① 点火 ignite**：前几天在蓄势、今天点火/量能/广度三条同时到位 ——'
        '**核心产出**。样本外（2025-07 起）n=196 · 胜率 57.5% · T+5 超额 '
        '**+0.455**（全板块等权对照组 +0.074）\n'
        '- **② 延续 follow**：点火后仍在走，回答"错过第一天还能不能上"\n'
        '- **③ 蓄势 coil**：波动 + 低位都成立、点火还没出现 —— '
        '**只是观察名单**\n'
        '- **④ 退潮 fade**：高位 + 斜率转负 —— 反向清单（默认这张卡不显示，'
        '要看就把卡片 `options.params.stages` 里加上 `fade`）\n\n'
        '⚠️ **`③ 蓄势` 不是买入清单**：它样本外 T+5 超额 **−0.107**、'
        '胜率 46.3%（对照组 +0.074 / 48.7%）——"只蓄势没点火"本身是负期望。'
        '它唯一的用途是当 `① 点火` 的前置、把名字提前放进视野。'
        '当天蓄势的板块通常有二十几个，**别当二十几个推荐位看**。\n\n'
        '### 五个要素（各 0~100，越高越符合）\n\n'
        '- `点火` 热度 3 日斜率**由负转正**（40）+ 斜率仍在**加速**（30）'
        '+ 热度 5 日均线**上穿** 20 日（30）\n'
        '- `量能` 板块成交额 ÷ 自身 20 日均额，**并且**该板块占全市场成交额的'
        '**份额**在抬升。原始数就是 `量比20日` / `份额倍数` 两列 ——'
        '份额比绝对放量重要：大盘普涨那天所有板块都放量\n'
        '- `广度` 板块内上涨家数占比的水平 + 相对 10 日均值的抬升。'
        '**最难造假的一维**：指数可以被几只权重股拉起来，上涨家数骗不了人\n'
        '- `低位` 当日热度在自身 120 日分位里有多低（`热度分位` 是那个分位的原始值）。'
        '**这一条专治追高**：分位 0.9 的板块再放量也不是启动，是末段\n'
        '- `波动` 波动是否已在放大（20 日热度标准差与日内振幅的 120 日分位）。'
        '⚠️ 方向是"放大得高分"，和"压紧的弹簧"那个直觉**相反** ——'
        '回测里沉寂方向 T+5 −0.209 / 胜率 42.7%，比对照组还差\n\n'
        '`综合分` 是五要素的加权合成，只用来在**同一档内**排序；'
        '`有效要素` 说明这一行真算了几条，**<5 就是有缺口**。\n\n'
        '⚠️ **行业板块永远不会是 `① 点火`**：`md.sector_daily` 里 230 个行业板块'
        '一条涨跌家数都没有（概念 268/268 都有），缺了广度就凑不满五要素。'
        '实测放宽（缺广度就跳过这一条）是负期望（行业 OOS 胜率 40.3% · '
        'T+5 −0.318），所以不放宽 —— 这是**数据缺口**，靠补采解决，'
        '不是口径选择。表里留着 `sector_type` 就是为了让这件事看得见。\n\n'
        '⚠️ **只用到 T 日收盘为止的信息**，做不到盘中预警（分钟线还是空的）。'
        '它也**不预测涨幅**：给的是"这个板块处在启动形态"，'
        '不是"明天涨多少"。'),

    'sector_heat_trend': (
        '**板块热度趋势**：当天热度前 6 名板块的**近 10 个交易日**热度曲线。\n\n'
        '线往上抬 = 热度还在积累；掉头向下 = 已经在退潮。'
        '只看当天的热度榜看不出这个区别。\n\n'
        '**窗口 2026-09-01 从 20 日改成 10 日**（用户指定）：20 个点铺在一张 '
        '360px 的图里，最近几天的转折被压成一小段，"接下来往哪走"反而看不出来。'
        '要看更长的斜率，`板块明细` 那张表里有 3/5/10/21 日的最小二乘斜率。\n\n'
        '取数 `q_sector_heat_trend` → `fct.sector_heat_daily`'
        '（板块日热度预聚合表）⋈ `ref.sector_info`。'
        '纵轴是**二次加工的热度分**（−100 ~ +100，'
        '`d2_scores.py` 算成员的 `sector_heat` → `d3_sector_trend.py` 取成员均值），'
        '不是涨幅。窗口和条数在 `config.chart.sector_heat_trend.params` 里'
        '（`lookback` / `top`），卡片标题里的日期是**这条曲线的右端点**。\n\n'
        '⚠️ 板块是**按当天热度选**的，所以左边那几天它们可能还很低 ——'
        '曲线整体往上翘是选择效应，要比的是**彼此之间**谁翘得更早、更陡。\n\n'
        '⚠️ `fct.score[sector_heat]` 目前只有 2026-07-29 起的数据，'
        '再往前是空的 —— 曲线左端断掉是缺数据，不是热度为 0。'),

    'sector_rotation_heat': (
        '**板块轮动**：行 = 板块，列 = 交易日，格子里是当天涨跌幅%。\n\n'
        '### 格子里的数怎么来的\n\n'
        '**这张图不打分**，格子里就是原始涨跌幅，没有归一化、没有权重、'
        '没有任何映射：\n\n'
        '`md.sector_daily.pct_chg = (close − pre_close) ÷ pre_close × 100`\n\n'
        '`close` 是**通达信板块指数**（880xxx / 881xxx）的当日收盘，'
        '`pre_close` 是同一板块上一交易日收盘 —— 不是成分股价格现场加权算的。'
        '全库 719,170 行逐行核对过这个恒等式，**0 例外**。\n\n'
        '**通达信这个指数是等权的**（2026-08-30 实测）：概念板块 268 个里 240 个，'
        '板块涨跌幅与「成分股涨跌幅的简单平均」差在 0.02 个百分点以内，'
        '平均绝对差 0.012。所以读这张图要记住 —— '
        '**一只 20 亿的小票涨停，和一只 2000 亿的大票涨停，对板块的贡献一样大**。'
        '成分少的板块（十几只）一两只票就能把整行拉红。\n\n'
        '⚠️ 行业板块目前**没有成员名单**（`ref.sector_member` 只覆盖 418/652），'
        '所以行业那几行没法用成分股自查，只能信通达信的指数值本身。\n\n'
        '### 哪些板块能上榜（行怎么选）\n\n'
        '按**窗口内累计涨幅** `sum(pct_chg)` 取前 15，**不是按当天涨幅**。'
        '只看当天会选进一日异动的板块，图上就是一行孤立的红格子，看不出轮动。\n\n'
        '代价是这个排序天生偏向"过去两周的赢家"，右下角那几行往往已经在退潮 —— '
        '要看"今天谁在起来"，配合板块热度榜的日趋势列看。\n'
        '缺日的板块累计值天生偏小（少加了几天），会吃亏。\n\n'
        '### 列怎么选\n\n'
        '最近 15 个**有涨跌幅**的交易日（`pct_chg IS NOT NULL`）。'
        '迁移进来的板块日线曾经只有 close、没有 pre_close，'
        '2026-08-27 实测 22 个交易日里只有 2 天有值，图上 13 列全空白像坏了。'
        '现已回算补齐，1,231 个交易日里 1,230 天有值（只有最早的 2021-08-02 没有，'
        '它没有前一天）。这道过滤留着，保证以后再出现同样的洞时是"少几列"'
        '而不是"整张图空"。\n\n'
        '### 颜色怎么映射\n\n'
        '红热绿冷，色阶上下界**固定**在 ±5%（`config.chart.options.zmin/zmax`），'
        '所以**跨日、跨板块可比**。让 plotly 自动定色阶的话，平静的一天和暴跌的'
        '一天会渲染出同样的红绿深浅，比较就失去意义了。\n\n'
        '超出 ±5% 的格子**只是颜色饱和、数字照写**，不截断数值：'
        '2026-08-28 那 225 格里有 10 格 > +5%（最高 +13.54%）、5 格 < −5%'
        '（最低 −7.23%）。所以**深红不等于同一个涨幅**，具体多少看格子里的数字。\n'
        '空格子留空而不是写 0 —— 0 是"平盘"，空是"那天这个板块没数"。\n\n'
        '### 为什么只取行业和概念\n\n'
        '`sector_type=\'风格\'` 装的是"昨日涨停/昨日连板/昨日突涨"这类**信号板块**，'
        '成分股就是刚涨停的票，天生涨幅巨大。实测不过滤的话，最近 15 日累计前 15 名'
        '里有 6 个是风格板块，「昨日连板」+41.8% 排第一，真正的行业轮动挤不进来。\n'
        '这类板块的成分股每天全换，等权口径也不成立（平均绝对差 0.72，'
        '概念是 0.012）—— 拿它跟行业放在同一张色阶上比本身就不对。'),

    'q_sector_parts': (
        '把板块热度拆成四个分项，回答"这个板块凭什么热"。\n\n'
        '涨幅高但广度低 = 少数票拉起来的；成交分高而动量低 = 放量但没走出来。\n\n'
        '取数 `q_sector_parts` → `fct.score_detail`（`part_key` = '
        '`sector_zhangfu` / `sector_guangdu` / `sector_dongliang` / '
        '`sector_chengjiao`）按 `ref.sector_member` 取成员均值，'
        '再拼上 `md.sector_daily` 的当日涨幅。四个分项由 `d2_scores.py` 调 '
        '`app/compute/builtin/sector_*.py` 算，**每项都是 −100 ~ +100**，'
        '加权 0.30 / 0.30 / 0.25 / 0.15 之后就是板块热度。'),
    'q_vol_stage_dist': (
        '**量能阶段分布**：当天全市场每个量能阶段各有多少只票，附该档的平均量能温度。\n\n'
        '取数 `q_vol_stage_dist` → `fct.score` 的两个 cap：`vol_stage`'
        '（1~5 的**离散阶段码**，不是分数，不能排序也不能求平均）和 `vol_temp`'
        '（−100 ~ +100 的连续温度），都由 `d2_scores.py` 调 '
        '`app/compute/builtin/vol_stage.py` / `vol_phase.py` 算，'
        '量能中间量两者**共用** `vol_phase.EXPRS`，所以温度和阶段不会分叉。\n\n'
        '### 为什么要有"阶段"，温度不够吗\n\n'
        '温度回答"多热"，阶段回答"处在生命周期的哪一段"。**这两个不能合成一个数**：'
        '温度 85 度可能是主升高潮（该持有），也可能是天量之后的缩量新高（该跑），'
        '操作方向正好相反。表里那一列 `平均温度` 就是这件事的证据 —— '
        '背离档和高潮档的温度往往差不多，能把它们分开的只有阶段。\n\n'
        '### 五档与各自的判据\n\n'
        '- `1 冷却`：以上都不成立，量在 20 日均量以下，没人管\n'
        '- `2 酝酿`：**温和放量**，量比 1.05~2.0、量斜率朝上、实体占全幅 ≥45%、站上 MA5。'
        '量比高于 2 就不再是"悄然进场"；长上影的放量是试盘或出货，所以要看实体\n'
        '- `3 高潮`：量比 ≥2 **或**近 10 日有 ≥3 天 1.5 倍量，且价格在 20 日高点的 95% 以内、'
        '站上 MA5。放量但价格在半山腰是反弹，不是主升\n'
        '- `4 背离`：**天量之后缩量上涨** —— 收盘 ≥20 日最高收盘的 99.5%（新高），'
        '但成交量 ≤20 日天量的 60%，且量斜率朝下、价斜率朝上。'
        '要求价斜率 > 0 是为了把高位横盘的正常缩量排除掉\n'
        '- `5 退潮`：跌破 MA10、量斜率朝下且量在 20 日均量的 90% 以下\n\n'
        '### 判定顺序 背离 → 高潮 → 酝酿 → 退潮 → 冷却，为什么背离必须最先\n\n'
        '因为**这几档的条件是重叠的，不是互斥的**，重叠时谁先判就标成谁。'
        '背离要求"价格新高"，而高潮只要求"价格在 20 日高点 95% 以内"—— '
        '新高必然满足它；量那一侧，高潮的"近 10 日有 ≥3 天放量"是**历史**，'
        '今天缩量并不会让它变假。于是"天量后缩量新高"这批票**同时满足两档**。\n\n'
        '实测（2026-08-28 全市场）：满足背离条件 223 只、满足高潮条件 521 只，'
        '**两者同时满足 50 只**（占背离的 22.4%）。先判高潮的话，这 50 只会被标成'
        '"高潮＝主升该持有"，而它们真实的样子是量已经接不上的缩量新高。'
        '这套判别唯一的价值就在这里 —— 按涨幅或按温度排序永远排不出 4 和 3 的区别。\n\n'
        '代码里是**反向盖章**实现的（`out.mask(ebb) → mask(incub) → mask(climax) → '
        '`mask(diverge)`），最后盖的赢，等价于 4 → 3 → 2 → 5 → 1。'
        '改这个顺序等于改结论，不要动。\n\n'
        '⚠️ 上市不足 20 个交易日、中间量算不出来的票**留空，不判成"冷却"** —— '
        '"没法判"和"没人管"是两件事，所以这张表的合计通常小于当日有行情的只数。'
        '阈值（1.05 / 2.0 / 0.45 / 0.95 / 0.995 / 0.60 / 0.90）全部走 '
        '`config.binding.params`，改数不用改代码，也不用改这段说明。'),

    'q_etf_flow': (
        '**ETF 净申赎**：用份额变动折算的资金流，单位亿元，默认近 30 个交易日。\n\n'
        '取数 `q_etf_flow` → `md.etf_share`（沪深两所官方每日**存量总份额**，'
        '`scripts/ingest/c4_etf_share.py` 采集）⋈ `md.fund_daily`（当日收盘价）。'
        '**二次加工**：净申赎 ≈ (今日份额 − 昨日份额) × 当日收盘价 —— '
        '所以它是**推算值**，不是官方公布的申赎金额。\n\n'
        '**只算股票型** —— 货币 ETF 是 100 元/份、债券 ETF 有 1000 元/份的，'
        '混在一起金额会虚高好几倍（实测 08-25 全类别 +85.88 亿份 → +2,629 亿元，'
        '隐含 30.6 元/份）。分类是按基金名称和 `etf_type` 用正则**推断**的'
        '（货币 / 债 / 跨境 / 商品 / 其余算 equity），不是官方字段。\n\n'
        '`份额变动亿份` 是净份额差，`净申赎亿元` 是折算金额，'
        '`只数` 是当天两边都有数据的 ETF 只数。'),


    'q_group_heat': (
        '一行一个自选组。**列序按复盘的提问顺序排**：先"明天先看谁"，再"今天'
        '什么状态"，再"波动多大"，最后才是判断这些数可不可信的元信息'
        '（趋势天数 / 成员数 / 有分只数 / 重点关注）。\n\n'
        '**默认按「预测操作分」从高到低。**\n\n'
        '### 每一列大小怎么读\n\n'
        '**预测操作分**（默认排序列）\n'
        '把这个组近 20 个交易日的加权分做最小二乘回归，再把那条线**外推一天**'
        '（第 21 点）。它同时含了"今天多少分"和"在往哪走"，所以比单看当日分'
        '更接近"明天先看谁"。\n'
        '- 越大：这条趋势线照原样再走一天会更高 —— 组里的票又好又在向上。'
        '实测最高约 +10 分。\n'
        '- 越小 / 负得多：线在往下掉，明天大概更差。实测最低约 −18 分。\n'
        '- ⚠️ **它不是预测涨跌方向**，也不含明天的任何新信息，只是趋势外推。'
        '一根大阳线或一条突发消息，这个数完全不知道。\n\n'
        '**加权操作分**（当日，成交额加权）\n'
        '`Σ(操作分 × 当日成交额) ÷ Σ当日成交额`，分母只算有分的票。个股操作分'
        '当日全市场实测 −41 ~ +57，九成落在 −19 ~ +29。\n'
        '- 越大（>20）：钱扎堆的那几只票分也高，是当天最该看的组。\n'
        '- 越接近 0：组内多空对半，没有方向。\n'
        '- 越小（<−15）：主力成交额集中在评分最差的票上，最该躲。\n\n'
        '**平均操作分**（当日，等权）\n'
        '每只票一票一票地平均，不看成交额。\n'
        '- **要和加权分对着看**：加权 ≫ 等权 = 这组的强只强在成交额最大的那'
        '一两只票上，"整组都强"不成立；两个数接近 = 组里齐刷刷，那才是真的齐。'
        '实测 `抗日` 加权 17.6 / 等权 13.5（被带的），`算力金属` 两者都 14.8'
        '（真齐）。\n'
        '- 加权 ≪ 等权 = 大票拖后腿，小票撑着好看。\n\n'
        '**趋势分数**\n'
        '最小二乘斜率 × 10，读作"这组的加权分**每 10 个交易日**变化多少分"。'
        '做法学旧系统 `score_amp_trend.py`（那边对个股 20 日振幅做一元回归）。\n'
        '- 正得多（>+5）：在起来的组，10 天涨了 5 分以上。\n'
        '- 约 0（±1）：横着走，没趋势。\n'
        '- 负得多（<−5）：在退潮。**分数高但趋势为负 = 退潮中的强组**，是这张'
        '表最该抓的一类；分数低但趋势为正 = 刚起来的弱组，值得盯。\n\n'
        '**平均涨跌幅**（当日）组内每票当日涨跌幅的平均。越大＝今天整组在涨，'
        '越负＝在跌。单日数，别当趋势用。\n\n'
        '**组内10日均涨跌bp / 组内20日均涨跌bp**\n'
        '组内每票"近 10（20）个交易日每日涨跌幅的均值"再取组平均，**是日均不是'
        '区间涨幅**，而且**已经 ×100 用基点显示**（1bp = 0.01%，用户 '
        '2026-09-01 指定）。为什么换单位：组一平均只剩 ±1% 以内'
        '（实测 −1.11 ~ +1.08），写成两位小数的百分数满屏都是"−0.25"这种'
        '看不出差别的数；×100 正好是基点。⚠️ **别把 −25 读成 −25%**，'
        '它是 −0.25%/日，10 天累计约 −2.5%。\n'
        '- 越大：这 10（20）天平均每天都在涨。越负：一直在跌。\n'
        '- 10 日比 20 日负得多 = 最近这 10 天在加速下跌；反过来是在止跌。\n'
        '- 左栏组列表那一列 `10日均涨跌bp` 就是这个数（同一段口径），'
        '组表默认按它**从低到高**排。\n'
        '- 旧系统同一个数除以固定的 10，但 10 天两两相减只有 9 个涨跌幅，'
        '所以旧值系统性偏小约 1/10 —— **新旧数值不可直接对比**。\n\n'
        '**上涨占比**（当日）组里今天收红的票占比。\n'
        '- >70 = 普涨，整组在动；≈50 = 分化；<30 = 普跌。\n'
        '- 和平均涨跌幅配着看：占比高但平均涨跌为负 = 多数小涨、少数大跌。\n\n'
        '**组内明日振幅 / 组内10日振幅 / 组内20日振幅**\n'
        '组内个股同名指标取均值。振幅**只有大小没有正负**，说的是"日内波动'
        '多大"，不说方向。日振幅＝`(最高 − 最低) / 前收 × 100`。\n'
        '- 越大（>5）：组里的票天天大幅震荡，适合做波动、也更容易被打止损。\n'
        '- 越小（<2）：走得平，没人炒。\n'
        '- `明日振幅` 是对近 20 日每日振幅做回归外推第 21 点，同样是趋势外推。'
        '它比 10 日振幅明显小 = 波动在收敛，明显大 = 在放大。\n\n'
        '**上榜次数** 组里今天上龙虎榜的票数。越大＝这个组当天越被资金关注'
        '（也越可能是已经炒过一波）；0 = 没有异动。\n\n'
        '### 后面几列是元信息，用来判断上面那些数可不可信\n\n'
        '- **趋势天数**：回归实际用到几个交易日。越接近 20 越可信；'
        '**不足 10 天的组，趋势 / 预测两列直接留空不补 0** —— 两三个点也能'
        '拟合出斜率，但那个数没意义，而 0 会被读成"横着走"。\n'
        '- **成员数**：组里几只票。**越小，上面所有均值越不稳** —— 3 只票的组'
        '一只涨停就能把整组的平均涨跌拉高 3 个点，别拿它和 17 只票的组比。\n'
        '- **有分只数**：这些票里当天真有操作分的有几只。**比成员数小得多时，'
        '上面的分只代表了组里的一部分票**（新股、停牌、历史不够都会没分）。\n'
        '- **重点关注**：`config.watchlist_group.is_focus` 那个标记，是你自己打的'
        '标，不参与任何计算。'),
    'group_score_trend': (
        '上面那张表给的是**今天这一个点**和一条斜率，这张图给的是**这 20 个'
        '交易日到底怎么走过来的** —— 斜率一样的两个组，一个稳步爬升、一个'
        '暴涨后回落，只看数字分不出来。\n\n'
        '**纵轴 = 组的加权操作分**（口径和表里的 `加权操作分` 完全一致，两处'
        '共用同一段 SQL）。横轴是交易日，一个点一天。\n\n'
        '取数 `q_group_trend` → `config.watchlist_stock` ⋈ '
        '`fct.score[oper_score]` ⋈ `md.stock_daily`（成交额做权重），'
        '**二次加工**：`Σ(操作分 × 当日成交额) ÷ Σ当日成交额`，'
        '分母只算当天有分的票。窗口 `win=20`、默认显示条数 `top=3`、'
        '最少天数 `min_n=10`（不够长的组不画线）。\n\n'

        '### 怎么读\n\n'
        '- **线在往上抬**：这个组的加权分一天天变高，组里的票在变好 / 钱在往'
        '好票上集中。\n'
        '- **线在往下走**：在退潮。\n'
        '- **线的高低**是"今天多强"，**线的斜率**是"在往哪走"，两件事分开看：'
        '一条低位往上抬的线是**刚起来的弱组**（值得盯），一条高位往下拐的线是'
        '**退潮中的强组**（该减）。\n'
        '- 线**忽上忽下**：组里票太少（3 只票一只涨停就能把整组拉高几分），'
        '配合表里的 `成员数` 看。\n\n'
        '### 默认只画 3 条，其余点图例展开\n\n'
        '**所有够长度的组都在图里**（当前 24 条），但默认只画**趋势最好的 3 个**，'
        '其余 21 条收在图例里 —— **在图例上点一下组名就画出来，再点一下收回去**，'
        '可以点开任意多条互相比。\n\n'
        '"趋势最好"按**斜率**排，不是按当日分：这张图问的是"谁在起来"。所以'
        '默认这 3 条**很可能分还是负的** —— 比如 `存储 看HBM` 从 −15.2 抬到 −3.6，'
        '是跌得最狠之后反弹最快的那个，不是最强的那个。想看"谁最强"看上面'
        '那张表的第一行。\n\n'
        '不足 10 个交易日的组不在图里：两三个点连不成趋势。'),
    'q_watchlist': (
        '**自选池明细**：自选组里每只票的当日行情 + 多日聚合 + 当日评分。\n\n'
        '取数 `q_watchlist` → `config.watchlist_stock` / `config.watchlist_group`'
        '（组和成员）⋈ `md.stock_daily`（当日行情）⋈ `fct.score[oper_score]`'
        '（评分）⋈ `fct.indicator_value`（多日列：`chg10_avg` / `chg20_avg` / '
        '`amp10_avg` / `amp20_avg` / `amp_pred`，由 '
        '`scripts/compute/d1_indicators.py` 算）。\n\n'
        '多日列都是**日均**，不是区间涨幅：\n\n'
        '- `10日涨跌%` = 近 10 个交易日每日涨跌幅的平均（旧系统那一列的口径）\n'
        '- `10日区间涨幅%`（另一个指标 `chg10`）= (今收 − 10 日前收) / 10 日前收\n'
        '- `明日振幅%` = 对近 20 日每日振幅做一元回归、外推第 21 个点。'
        '**是趋势外推，不是预测涨跌方向**，振幅没有正负。\n\n'
        '不足 10（20）个交易日的票留空，不用固定分母把它稀释成一个偏小的数 ——'
        '旧系统这种情况给 0，看起来像"很平静"，其实是"算不出"。\n\n'
        '**"10 日"数的是有行情的交易日**：停牌那几天库里没有行，窗口会自动往前'
        '多伸几天（实测 920138.BJ 的 10 日窗口跨了 12 个自然交易日）。'
        '停牌日补 0 会把均值往 0 拽、补前值会造出假横盘，两种都比"窗口伸长"更差。\n\n'
        '默认**全表按 10 日振幅从高到低**（2026-09-01 用户指定，不先按组分块），'
        '一页 100 行 —— 这张表问的是"全池里现在谁在动"，振幅小的票操作分再高也'
        '做不出价差。同振幅按操作分、再按代码兜底。想按组看就点「组名」表头排一下。'),

    'q_margin_trend': (
        '**两融余额趋势**：全市场合计，单位亿元，默认近 30 个交易日。\n\n'
        '取数 `q_margin_trend` → `md.margin_detail` 按交易日汇总'
        '（`scripts/ingest/c5_margin.py` 采集，源头是沪深两所官方）。'
        '`融资亿元` / `融券亿元` / `两融合计亿元` 分别是 `rz_balance` / '
        '`rq_balance` / `rzrq_balance` 之和，**原始余额、不做加工**。\n\n'
        '怎么读：融资余额升 = 加杠杆的钱在进，是情绪的确认；'
        '融资余额掉而指数还在涨，是资金在撤。\n'
        '⚠️ 深交所盘中就发、上交所是 T+1，所以最后一天可能只含单边，'
        '别拿最后一根柱子做对比。'),

    'pct_hist': (
        '**涨跌幅分布**：全市场当日涨跌幅落在各个区间的**只数**。\n\n'
        '取数 `q_daily_pct` → `app/db/repo/quote.py::pct_distribution()` → '
        '`md.stock_daily ⋈ ref.security`（只算 `asset_type=\'stock\'`）。'
        '**原始行情、不打分**，就是按 `pct_chg` 分箱数行数。\n\n'
        '**分箱边界写死**（跌停附近 / −10~−7 / −7~−5 / −5~−3 / −3~−1 / −1~0 / '
        '0~1 / 1~3 / 3~5 / 5~7 / 7~10 / 涨停附近），所以**跨日可比** —— '
        '自适应分箱会让不同日期的图形状相似却不可比。\n\n'
        '怎么读：形状比数字重要。右侧厚 = 普涨，两头厚中间薄 = 分化，'
        '"涨停附近"那一格明显高 = 有资金在打板。'),

    'mood_series': (
        '**情绪时序**，窗口 **40 个交易日 ≈ 两个月**（`config.chart.mood_series.params`）。\n\n'
        '取数 `q_market_mood`（`app/web/queries.py`），图里两条线：`数值` 是逐日'
        '情绪分，`5日均线` 是它的 5 日平均 —— 日线在 ±10 分之间来回跳，'
        '是抬头还是掉头要看均线。\n\n'
        '### 情绪分怎么算的\n\n'
        '这条线不是查出来的原始行情，是**二次加工**的评分：'
        '`fct.score[cap_key=market_mood]`，由 `scripts/compute/d2_scores.py`'
        '调 `app/compute/builtin/market_mood.py` 逐日算，输入是 `md.stock_daily`'
        '（全市场个股日线）。**四项加权，落在 −100 ~ +100**：\n\n'
        '- **涨跌家数比 0.35** —— 上涨家数 ÷ (上涨 + 下跌) × 100，'
        '30 ~ 70 线性映射到 −100 ~ +100。最直接的情绪温度，50% 附近≈0 分。\n'
        '- **涨停家数 0.25** —— `count(close >= limit_up − 0.01)`，'
        '20 ~ 120 家映射到 −100 ~ +100。情绪的高度。\n'
        '- **中位涨幅 0.25** —— `median(pct_chg)`，−1.5% ~ +1.5% 映射到 −100 ~ +100。'
        '用中位数而非均值：几只妖股能把均值拉高、拉不动中位数。\n'
        '- **成交额环比 0.15** —— 当日总成交额 ÷ 5 日均成交额，'
        '0.8 ~ 1.3 映射到 −100 ~ +100。增量资金是否进场。\n\n'

        '映射都是**线性 + 截断**：低于下界给 −100、高于上界给 +100，'
        '所以极端日会顶到 ±100 不再变化。\n\n'
        '**数值怎么读**：0 分 ≈ 上涨占比 50%、涨停 70 家、中位涨幅 0%、'
        '成交额与 5 日均量持平。正数是赚钱效应在扩散，负数是在收缩。'
        '**它不预测涨跌**，只描述当天的赚钱难易度。\n\n'
        '几条口径提醒：\n\n'
        '- **统计的是全市场个股，不是指数** —— 指数被权重股主导，反映不了赚钱效应。\n'
        '- 涨停用回算出的 `limit_up` 判定，不是"涨幅 > 9.8%"：后者会漏掉'
        '创业板 20%、北交所 30% 的涨停。\n'
        '- 情绪是**环境属性**，`fct.score` 里每只票存的是同一个值'
        '（为了让"一只票一天一个功能点一行"的结构不开特例）。\n'
        '- 没算过评分的日子退回**上涨占比**（0~100），`情绪口径` 列标明用的是'
        '哪一种 —— 两个量纲不同，**跨口径的那一段均线没有意义**。'),

    'q_mood_recent': (
        '最近 7 个交易日的情绪**震荡**明细，最近的在最上面。\n\n'
        '取数 `q_mood_recent` → 直接复用 `q_market_mood`'
        '（`fct.score[market_mood]`，没算过的日子退回 `md.stock_daily` '
        '的上涨占比），不另写一段 SQL —— 两张卡的情绪分必须同口径。\n'
        '情绪分本身的四项算法见「情绪时序」那张卡的说明。\n\n'
        '`日变化` 正负交替 = 在震荡；连着同号 = 在走单边。'
        '两个月那条线回答"往上还是往下"，这张表回答"最近是在震还是在走"。\n\n'
        '跨口径的日变化留空：一天是真情绪分、前一天是上涨占比时相减'
        '出来是假的暴涨暴跌，宁可空着。'),
    'mood_7d': (
        '**大盘温度近 7 个交易日**。和右边那张近 20 日的图**共用同一个 y 轴范围'
        '（−100 ~ +100）并画了 0 线**，所以两张可以直接对着比 —— 自动缩放会让'
        '同样 20 分的波动在 7 日图上看着剧烈震荡、在 20 日图上看着一条平线。\n\n'
        '取数 `q_market_mood`（`lookback=7`）→ `fct.score[market_mood]`，'
        '由 `d2_scores.py` 调 `app/compute/builtin/market_mood.py` 算，'
        '四项加权见「情绪时序」那张卡的说明。'),
    'mood_20d': (
        '**大盘温度近 20 个交易日**。y 轴固定 −100 ~ +100 且带 0 线，'
        '和左边近 7 日那张图可比。20 日看的是**这一轮情绪的位置**：'
        '一直在 0 以上是持续赚钱，翻到 0 以下就是环境变了。\n\n'
        '取数 `q_market_mood`（`lookback=20`）→ `fct.score[market_mood]`，'
        '算法同「情绪时序」那张卡。'),


    # ---- 首页 6 个指标块。数字最短、最容易被误读，所以每个都要写清口径 ----
    'k_mood': (
        '**大盘温度**：当天的市场情绪分，取值 **−100 ~ +100**，0 分 ≈ '
        '上涨占比 50% / 涨停 70 家 / 中位涨幅 0% / 成交额与 5 日均量持平。\n\n'
        '取数 `k_mood`（`app/web/queries.py`）→ `fct.score[cap_key=market_mood]`'
        '当天的值。**这是二次加工的分**，不是行情：由 `scripts/compute/d2_scores.py`'
        '调 `app/compute/builtin/market_mood.py` 算，输入是 `md.stock_daily`，'
        '四项加权 —— 涨跌家数比 0.35 / 涨停家数 0.25 / 中位涨幅 0.25 / '
        '成交额环比 0.15，每项都先线性映射到 −100~+100 再加权。\n\n'
        '⚠️ 那天**没跑评分**的话，这里退回显示"上涨占比"（0~100），'
        '副标题会写明 —— 两个量纲不同，别拿 60 和 −60 混着比。'),
    'k_up_count': (
        '**上涨家数**：当天 `pct_chg > 0` 的个股只数，副标题给下跌家数。\n\n'
        '取数 `k_up_count` → `md.stock_daily`（通达信日线，`c1_day.py` / '
        '`c12_tq_day.py` 采集），**不做二次加工**，就是数行数。\n\n'
        '怎么读：涨跌家数比 = 上涨 ÷ (上涨 + 下跌)，是"赚钱效应"最直接的指标，'
        '也是大盘温度里权重最大的那一项（0.35）。'
        '**平盘的票不计入任何一边**，所以上涨 + 下跌 ≠ 全部只数。'),
    'k_limit_up': (
        '**涨停家数**：当天收盘触及涨停价的只数（`close >= limit_up − 0.001`），'
        '副标题给跌停家数。\n\n'
        '取数 `k_limit_up` → `md.stock_daily` 的 `limit_up` / `limit_down` 列。'
        '这两列是**回算出来的**（`quote_repo.recompute_limits`，按板块涨跌幅'
        '限制 × 前收盘），不是行情源直接给的。\n\n'
        '★**不用"涨幅 > 9.8%"判涨停**：那样会把创业板 20%、北交所 30% 的涨停'
        '全部漏掉。副标题里如果出现"N 只缺涨跌停价"，说明那 N 只票算不出来'
        '（多为上市首日，没有前收盘），不是真的没涨停。'),
    'k_amount': (
        '**成交额**：当天全部个股 `amount_yuan` 之和，换成亿元。\n\n'
        '取数 `k_amount` → `md.stock_daily`，**只算个股**，不含指数和板块'
        '（那两类在 `md.index_daily` / `md.sector_daily`，重复计入会翻倍）。\n\n'
        '口径提醒：通达信 TQ 接口的成交额单位是**万元**，入库时已 ×10000 '
        '（见 `c12_tq_day.py`）—— 这个错了不会报错，只会让金额差一万倍。\n'
        '这张卡自己就把单位换算好并带了千位分隔，所以 `config.page_layout.options` '
        '里**不能再配 `scale`/`suffix`**，否则渲染器会去 `float("21,177 亿")` 报错。'),
    'k_score_avg': (
        '**平均操作分**：当天全市场 `oper_score` 的算术平均，副标题给已评分只数。\n\n'
        '取数 `k_score_avg` → `fct.score[cap_key=oper_score]`。**二次加工**：'
        '`d2_scores.py` 逐票算 `操作分 = 0.40 × X个股质量 + 0.60 × Y环境共振`，'
        '两个输入分别来自 `fct.indicator_value`（`d1_indicators.py` 算的因子）'
        '和板块热度 / 大盘情绪。\n\n'
        '怎么读：单票操作分实测 −41 ~ +57，**全市场均值**通常在 ±15 以内 —— '
        '它是"今天全市场好不好做"的一个粗指标，不是选票依据。'
        '看"还没跑评分"就说明那天 `d2_scores` 没跑。'),
    'k_health': (
        '**系统健康**：一句话回答"这套库现在能不能信"。\n\n'
        '取数 `k_health` → `md.stock_daily` 的最新交易日 + `ops.quality_result`'
        '近两天失败的规则数。**不看时钟看数据日期** —— 这条链路平时交给智能体跑、'
        '会换模型，几点跑完没意义，数据到哪天才是真的。\n\n'
        '三种取值：\n'
        '- `N 项告警` —— 近 2 天有 N 条质量规则没通过，去 `/data → ② 数据质量` '
        '看是哪条；\n'
        '- `正常` —— 最新数据距今 ≤ 3 天且没有失败规则；\n'
        '- `滞后 N 天` —— 数据落后 N 天了，去 `/jobs` 点「一键追平」。'),
    'q_task_recent': (
        '**任务执行历史**：`ops.task` 倒序，最近的在最上面。\n\n'
        '取数 `q_task_recent` → `ops.task ⋈ config.job`。一行 = 一次执行，'
        '状态 `pending / running / succeeded / failed / cancelled / skipped`。\n\n'
        '★**这里没有"定时执行"的记录，因为系统没有调度器进程** —— '
        '`config.job` 的 `cron_expr` / `after_job` 只是声明，没人读（说明书 5.5）。'
        '能出现在这张表里的只有：`/jobs` 或 `/datasource` 上手点的、'
        '以及命令行直接跑脚本的。\n\n'
        '卡在 `running` 的行会被 `runner.reap_orphans()` 在下次刷新前收成 '
        '`failed`（判据是发起进程的 pid 已经不在了）—— 不这么做的话网站一重启，'
        '那行会永远显示"正在跑"。'),

    # ---- 缺说明的表 / 图，2026-08-30 补齐 ----
    'index_line': (
        '**指数走势**：默认上证综指（`000001.SH`）近 250 个交易日收盘价。\n\n'
        '取数 `q_index_daily` → `md.index_daily`（通达信指数日线，'
        '`c1_day.py` / `c12_tq_day.py` 采集），**原始行情、不做加工**。'
        '换指数或改天数在 `config.chart.index_line.params` 里改'
        '（`ts_code` / `lookback`），不用动代码。\n\n'
        '⚠️ **指数不代表赚钱效应** —— 它被权重股主导，2,000 只票下跌、'
        '几只大票拉升时指数照样红。看盘面强弱要看「大盘温度」和「上涨家数」，'
        '这条线只用来对时间轴、看大势位置。'),
    'q_lhb_recent': (
        '**龙虎榜**：所选交易日的上榜明细，按净买额从大到小，默认 100 条。\n\n'
        '取数 `q_lhb_recent` → `md.lhb_detail ⋈ ref.security`。'
        '数据由 `scripts/ingest/c6_lhb.py` 采集，源头是 **akshare 转发的东方财富**'
        '（`stock_lhb_detail_em`）；Tushare 的 `top_list` 在本项目 token 下'
        '无权限（40203），所以没有第二个源。\n\n'
        '列的含义：`净买万元` = 龙虎榜买入 − 卖出；`龙虎榜成交万元` = 上榜席位'
        '买卖合计；`净买占比` = 净买额 ÷ 当日总成交额。\n\n'
        '两条口径：\n'
        '- **一票一天可能有多行** —— 上榜原因（`reason`）是主键的一部分，'
        '"日涨幅偏离值达 7%"和"连续三日涨幅达 20%"会各占一行，这里不去重。\n'
        '- ⚠️ **库里目前只有 2026-08-17 起的 10 个交易日**，往前是空的。'
        '要补：`python scripts\\ingest\\c6_lhb.py --start … --end …`。'),
    'q_effective_binding': (
        '**当前有效权重**：每个功能点（`cap_key`）下挂了哪些计算脚本、'
        '各占多少权重 —— 这张表就是"评分现在是按什么口径算的"的唯一事实来源。\n\n'
        '取数 `q_effective_binding` → 视图 `config.v_effective_binding`'
        '（`config.capability ⋈ config.binding ⋈ config.script`）。\n\n'
        '两个权重列的区别：\n'
        '- `raw_weight` —— 注册表里填的原始值（`config.binding.weight`）；\n'
        '- `eff_weight` —— **归一化后**真正参与加权的值，'
        '同一个 cap 下所有启用脚本的 `eff_weight` 之和恒为 1。'
        '所以填 2 和 3 与填 0.4 和 0.6 等价。\n\n'
        '改这里的权重**不会自动重算历史** —— 改完要跑 '
        '`d2_scores.py --recent N` 才生效。个股详情页的「评分归因」会在'
        '存下来的权重与这张表不一致时标红提醒。\n\n'
        '右边四列**不来自数据库**，是从每个脚本源码里的 `EXPLAIN` 常量读出来的'
        '（`app/compute/explain.py` 汇总，各 `app/compute/builtin/*.py` 自己声明）：\n'
        '- `判据` —— 这个脚本到底看什么（量、价、位置…）；\n'
        '- `子项（内部权重）` —— 脚本内部还怎么拆。括号里的数是**脚本内部**的相对'
        '权重，和左边 `eff_weight` 不是一个层级：先用它算出这个脚本的分，'
        '再用 `eff_weight` 把各脚本合成 cap 分；\n'
        '- `合成方式` —— 子项怎么合成一个分；\n'
        '- `注意` —— 已知口径缺陷与坑（例如某数据缺失时会退化成什么）。\n\n'
        '**为什么写在代码里而不入库**：算法改了、说明忘了改，是最常见的骗人来源。'
        '`EXPLAIN` 和实现在同一个文件里同批改，`tests/test_explain.py` 拿源码核对'
        '每个已注册脚本都有 `EXPLAIN` —— 少一个就红。\n'
        '`base_factors_v1` / `review_report_v1` 这两行的 `合成方式` 写的是'
        '「不参与加权」：它们是产数据/出表的脚本（`output_kind = table`），'
        '挂在 cap 下只为排产顺序，不贡献分数。'),
    'q_screen_rules': (
        '**条件类型**：筛选器支持哪些条件，来自 `config.screen_rule`。\n\n'
        '取数 `q_screen_rules` → `config.screen_rule WHERE enabled`。'
        '`参数` 列是 JSON Schema（`param_schema`），前台**按它渲染表单**；'
        '真正拼 SQL 用的是同一行里的 `sql_template`，'
        '由 `app/compute/screener.py` 按声明的参数名做占位替换 —— '
        '模板里出现未声明的 `:参数` 会直接报错。'
        '**所以加一种筛选条件 = 往这张表插一行，不改前端也不改引擎代码。**\n\n'
        '⚠️ 这里列的是**条件类型**（能筛什么），不是你的筛选方案（怎么筛）。'
        '方案在下面那张「我的方案」里。\n'
        '2026-08-29 删掉过 6 条从没被任何引擎读过、而且写法不对的旧模板 —— '
        '**这张表里出现的每一条都必须真能跑**，声明与实现要同批交付。'),
    'q_screen_presets': (
        '**我的方案**：存下来的筛选组合，来自 `config.screen_preset`。\n\n'
        '取数 `q_screen_presets` → `config.screen_preset`，倒序。'
        '`条件` 列是这套方案的 JSON —— 存的是**条件 + 逻辑(and/or) + 候选集**，'
        '不是只存条件：同一组条件用 and 还是 or 是两个完全不同的方案。'
        '`归属` / `共享` 决定谁能看见。\n\n'
        '**默认是空的** —— 这是你自己攒的东西，系统不预置。'
        '在上面筛出满意的结果后填个名字点「存为方案」就会多一行（同名覆盖）；'
        '选中后点「载入」把整套条件填回表单，点「删除」清掉。'),

    'q_top_movers': (
        '**当日涨幅前列**：所选交易日按 `pct_chg` 降序的前 20 只个股，'
        '给筛选器一个"不设条件也先有东西看"的落点。\n\n'
        '取数 `q_top_movers` → `md.stock_daily ⋈ ref.security`，'
        '只取 `asset_type=\'stock\'`（把指数、ETF、板块挡在外面），'
        '**原始行情、不做加工**。`成交额` 列已换算成亿元。\n\n'
        '⚠️ 它就是个涨幅榜，**不含任何评分或质量判断** —— 涨幅榜前列常年是'
        '小盘、ST、一日游的票。要按质量选票用「全市场个股分析」那一页的'
        '四象限选票框，或者自己配筛选条件。'),
    'sector_rank_bar': (
        '**板块热度榜（横条）**：当天热度前 20 的板块，条长就是热度分。\n\n'
        '取数 `q_sector_heat` → `fct.sector_heat_daily`（板块日热度预聚合表）。'
        '**这是二次加工的分，不是涨幅**：`d2_scores.py` 先给每只票算 '
        '`sector_heat` 分（四项加权 —— 板块涨幅 0.30 / 上涨广度 0.30 / '
        '动量 0.25 / 成交 0.15，见 `app/compute/builtin/sector_*.py`），'
        '再由 `scripts/compute/d3_sector_trend.py` 按 `ref.sector_member` '
        '取成员均值落到板块上。取值 **−100 ~ +100**。\n\n'
        '同一份数据在复盘首页是**表格**形态（带日趋势、3/5/…/21 日趋势、'
        '涨跌家数、有分只数），这里只留一个量级对比。'
        '要看"热度在积累还是在退潮"看表格里的趋势列，或者「板块热度趋势」那张图。\n\n'
        '⚠️ 只统计有评分成员的板块 —— 行业板块的成员名单目前只覆盖 '
        '`ref.sector_member` 的一部分（418/652），缺成员的板块进不了这张榜。'),
    # 下面两条给的是 /watchlist **维护台**的两张手写卡（不在 config.page_layout 里），
    # 由 `watchlist.render()` 直接 `ui.card(..., note=CARD_HELP[...])` 挂上。
    'wl_groups': (
        '**自选组**：一行一个组，回答"先翻哪个组的票"。\n\n'
        '这张表是**维护台的左栏**，不是配置卡 —— 代码在 '
        '`app/web/pages/watchlist.py` 的 `group_rows()` / `groups_table()`。\n\n'
        '**取数**：组和成员来自 `config.watchlist_group` / '
        '`config.watchlist_stock`（**手工维护，不是采集来的**）；'
        '`10日均涨跌bp` 来自 `queries.group_chg10_map()`、'
        '`预测操作分` 来自 `queries.group_pred_map()`，两列都和下半截'
        '「自选组热度」卡**同一个口径**（预测分共用 `_GROUP_WSCORE_CTE`），'
        '两处数字必须一致。\n\n'
        '**操作分是什么**（这一页所有"分"的底座）：个股的 '
        '`操作分 = 0.40 × 个股质量X ＋ 0.60 × 环境共振Y`，'
        '由 `scripts/compute/d2_scores.py` 收盘后算好存 `fct.score[oper_score]`，'
        '量纲 −100 ~ +100（2026-08-31 全市场中位 +18.9、九成落在 −24 ~ +34）。'
        'X = 形态/趋势/位置/筹码/量能，Y = 板块热度 0.65 ＋ 市场情绪 0.35，'
        '**Y 占六成 ⇒ 它更像"顺不顺势"而不是"质地多好"**。'
        '逐列怎么用见右栏「组内成员维护」的说明。\n\n'
        '**二次加工（三步）**：\n\n'
        '1. 组的**成交额加权操作分** = Σ(成员操作分 × 当日成交额) ÷ '
        'Σ(有分成员的成交额)。上游是 `fct.score[oper_score]` 和 '
        '`md.stock_daily.amount_yuan`。'
        '**分母只算有评分的行** —— 否则组里没评分的票越多，加权分被压得越低\n'
        '2. 取最近 **20 个有数的交易日**（不是 20 个自然日）做最小二乘拟合，'
        '用 PG 的 `regr_slope` / `regr_intercept`\n'
        '3. 外推一步：**`预测操作分 = 截距 + 斜率 × (n+1)`** —— '
        '"照这 20 天的趋势，下一个交易日这个组的加权分大概落在哪"\n\n'
        '**列含义**：`组名` 前面的 **★ ＝重点关注**'
        '（库里是 `config.watchlist_group.is_focus`，2026-08-30 从独立的「重点」列'
        '并进组名单元格）· `成员数` 组里几只票 · '
        '`10日均涨跌bp` 组内每票近 10 个交易日**日均**涨跌幅的组平均、**×100 以'
        '基点显示**（1bp = 0.01%，别把 −25 读成 −25%；口径同热度卡那一列）· '
        '`预测操作分` 见上 · `排序` 手工序号（只在同值时起作用）· `备注` 自己写的。\n\n'
        '**可以作为什么的参考**：`10日均涨跌bp` 负得多 = 这个组这 10 天在被砸，'
        '是"去翻低位"的入口；`预测操作分` 高 = 这个组的加权分还在往上走，'
        '是"顺势跟"的入口。两列**方向相反**，按哪一列排就是在问哪一个问题 ——'
        '默认排的是前者。\n\n'
        '**排序**：`10日均涨跌bp` **升序**（2026-09-01 用户指定，此前是预测操作分'
        '降序）→ 算不出来的沉底 → ★ 优先 → `排序` → 组名。'
        '先看被砸得最狠的组，再决定去翻哪个组的票。行序必须是确定的，一抖就会选错组。\n\n'
        '⚠️ **留空不是 0**：历史不足 10 个有效交易日的组算不出斜率，'
        '`预测操作分` 留空并排在最后 —— 填 0 会被读成"预测明天走平"。'
        '空组（一只票都没有）`10日均涨跌bp` 同样留空、沉底。\n\n'
        '**怎么用**：点行里**任意一格**就换组，右栏成员整块跟着变；'
        '`组名` / `排序` / `备注` 双击就地改；在组名里加上或删掉 ★ 就是切换重点。'),
    'wl_members': (
        '**组内成员维护**：当前组里的票 + 近 5 日收盘 + 多窗口涨跌与振幅 + '
        '明日振幅预测 + 操作分。代码在 `watchlist.py` 的 `members_table()`。\n\n'
        '**取数**：一条 CTE 取完四张表 —— `config.watchlist_stock`（成本/备注）'
        '⋈ `md.stock_daily`（近 5 日收盘、当日涨跌幅）'
        '⋈ `fct.indicator_value`（10/20 日涨跌与振幅、明日振幅预测）'
        '⋈ `fct.score`（操作分、象限）。不在 Python 里循环查库。\n\n'
        '### `操作分` 是什么\n\n'
        '**操作分 = 0.40 × 个股质量X ＋ 0.60 × 环境共振Y**，由 '
        '`scripts/compute/d2_scores.py` 收盘后算好存进 `fct.score[oper_score]`，'
        '页面只取不算。两层权重都在 `config.binding` 里（`/权重` 页可改，不用动代码）：\n\n'
        '- **X 个股质量**（这只票自己好不好）= 形态 0.25 ＋ 趋势 0.25 ＋ '
        '位置 0.20 ＋ 筹码 0.20 ＋ 量能 0.10\n'
        '- **Y 环境共振**（它待的环境好不好）= 板块热度 0.65 ＋ 市场情绪 0.35\n'
        '- Y 占六成 ⇒ **这个分更像"顺不顺势"，不是"这票质地多好"**。'
        '一只普通票踩在最热的板块里，分会比一只形态漂亮却没人炒的票高。\n\n'
        '量纲 −100 ~ +100。2026-08-31 全市场 5,545 只实测：最低 −52.0 · '
        '中位 +18.9 · 最高 +47.1，九成落在 −24 ~ +34。所以**+35 以上就是当天最强的'
        '那 5%**，见到负分是少数情况。\n\n'
        '**可以作为什么的参考**：\n\n'
        '- **今天这个组先看谁**：几十只票按操作分从高到低，就是翻 K 线的顺序，'
        '不用逐只点开找\n'
        '- **组内比强弱**：同组的票环境 Y 接近，分差主要来自个股质量 X ——'
        '排在最后那几只是这个组的拖累项，要减先减它们\n'
        '- **和「自选组热度」卡对齐**：那张卡的 `加权操作分` 就是这一列按成交额加权、'
        '`预测操作分` 是它的 20 日趋势外推，所以"组强不强"和"组里哪只强"用的是同一个数\n'
        '- **配合象限决定动作**（见下）—— 分数说"值不值得看"，象限说"该怎么动"\n\n'
        '⚠️ **不能当什么用**：\n'
        '① 它**不是"明天会涨多少"**，也不含任何未来信息 —— 所有分项都是'
        '**当日收盘后**的形态 / 位置 / 量能 / 板块状态。分高只说明"这只票现在处在'
        '一个好位置"，不保证第二天不跌。\n'
        '② **别拿两天的操作分直接相减**：X / Y 里多个分项是**当日横截面分位**，'
        '全市场一起涨的时候整体分会被压平。要看一只票自己在变好还是变差，'
        '用 `/scores` 那张「四象限趋势分」（近 10 个交易日回归）。\n'
        '③ 没算出分的票留空、不补 0：新股、停牌、历史不够都会没分，'
        '0 会被读成"很平庸"。\n\n'
        '### `象限` 比总分更该先看\n\n'
        'X / Y 各分正负，交叉出四格（编号口径以 `config.setting.quadrant_labels` '
        '为准）：\n\n'
        '- **Q1 买入**　X≥0 且 Y≥0 —— 票好、环境也好，趋势性买入\n'
        '- **Q2 持有**　X≥0 且 Y<0 —— 强势股逆着环境独走，持有不加\n'
        '- **Q3 试探**　X<0 且 Y≥0 —— 板块热但个股弱，小仓位试探\n'
        '- **Q4 回避**　X<0 且 Y<0 —— 避开\n\n'
        '同样 30 分，Q1 是顺风顺水、Q2 是逆势独走，动作完全不同，'
        '**所以别只按分数排序做决定**。2026-08-31 全市场：'
        'Q1 2,152 · Q2 172 · Q3 2,857 · Q4 364。\n\n'
        '### 其它列的含义与用法\n\n'
        '- **日期列**（`08-28` 这样的月-日）= 那天的收盘价，列序**从早到晚**，'
        '一行读过去就是一条走势（写 `d1..d5` 隔一天就不知道是哪天了）\n'
        '- `涨跌幅` 当日涨跌幅；`10日涨跌 / 20日涨跌` 是**日均**（近 N 个交易日'
        '每日涨跌幅的平均），**不是区间涨幅** —— 日均 +1 约等于 10 天累计 +10。'
        '当参考：看的是"这段时间平均每天在涨还是在跌"，比单日更稳\n'
        '- `10日振幅 / 20日振幅` 同理是**日均振幅**'
        '（`(最高 − 最低) ÷ 前收 × 100`），**只有大小没有正负**。'
        '当参考：它回答"这只票给不给价差" —— 日均振幅 <2 的票做不出短差，'
        '>5 的票天天大幅震荡，适合做波动也更容易被打止损\n'
        '- `明日振幅` 对近 20 日每日振幅做一元回归、外推第 21 点，'
        '**是趋势外推不是预测方向**；比 10 日振幅明显小 = 波动在收敛，'
        '明显大 = 在放大\n'
        '- `操作分` / `象限` 见上；`成本` 是**手工维护的买入价**'
        '（`config.watchlist_stock.cost`，双击就地改；从同花顺覆盖自选时会带入 '
        '`SelfStockInfo.json` 里的 `P`）—— 系统不自己算成本，也不跟券商对账\n'
        '- `盈亏` = (今日收盘 − 成本) ÷ 成本 × 100，**只有填了成本的行才有值**，'
        '没填的留空（写 0 会被看成"刚好回本"）\n'
        '- `详情` 是站内深链，一步跳到 `/stock` 看这只票的评分归因；'
        '`备注` 双击就地改\n\n'
        '**默认排序**：`10日振幅` 从高到低（2026-09-01 用户指定，此前是操作分）——'
        '先看"谁在动"，同振幅再按操作分、最后按代码兜底。表头可点着换列排序。'),
}




# ---------------------------------------------------------------- 图表

def _line(rows, x, ys, title=None, color=None, visible_col=None):
    """
    折线图。给了 `color` 就按那一列分组，一组一条线。

    `config.chart.index_line` 的 encoding 里早就写着 `"color":"ts_code"`，但这里
    以前没实现 —— 三个指数的点被按行顺序串成**一条**来回折的锯齿线，
    看着像数据坏了。板块热度趋势（`sector_heat_trend`）也依赖这个分组。

    `visible_col` 指定**哪一列决定这条线默认画不画**（真值画、假值收起）。
    收起用的是 Plotly 的 `visible='legendonly'`：**数据仍然在图里**，图例点
    一下就出来。这样"默认只看趋势最好的 3 个组，剩下的想看再点"不用把另外
    二十条线从查询里删掉 —— 删掉的话想看第 4 名得改 config 或改代码。
    """
    fig = go.Figure()
    if color and rows and color in rows[0]:
        y = ys[0]
        groups: dict[object, list[dict]] = {}
        for r in rows:
            groups.setdefault(r.get(color), []).append(r)
        for gname, grp in groups.items():
            vis = True
            if visible_col and visible_col in grp[0]:
                vis = True if grp[0].get(visible_col) else 'legendonly'
            fig.add_trace(go.Scatter(x=[r[x] for r in grp],
                                     y=[r.get(y) for r in grp],
                                     mode='lines+markers',
                                     visible=vis,
                                     name=str(gname)))
    else:
        for y in ys:
            fig.add_trace(go.Scatter(x=[r[x] for r in rows],
                                     y=[r.get(y) for r in rows],
                                     mode='lines', name=col_label(y)))
    fig.update_layout(_layout(title))
    return fig



def _bar(rows, x, y, title=None, *, horizontal: bool = False,
         tone_col: str | None = None, show_values: bool = False):
    """
    柱状图。`horizontal=True` 时 x/y 互换 —— 板块名有六七个汉字，
    竖着放标签会被压成"新能源…"，横条才读得完（config.chart 里那条
    `no_truncate_label` 备注就是这个意思）。

    `tone_col` 指定**按哪一列判红绿**。默认按柱高本身，但计数类的柱子
    （涨跌幅分布的"家数"）永远是正数，按柱高判就是**整张图全红**，
    跌的那几箱也是红的 —— A 股口径是红涨绿跌，这张图必须能一眼看出
    左边一片绿、右边一片红。分布图传的是分箱下界 `lo`。

    `show_values=True` 在柱子外侧写出具体数字：柱高只能看出相对高低，
    "跌停附近到底是 6 只还是 60 只"得有数字才知道。
    """
    vals = [r.get(y if not horizontal else x) for r in rows]
    labels = [r.get(x if not horizontal else y) for r in rows]
    tones = [r.get(tone_col) for r in rows] if tone_col else vals
    colors = [COLORS['up'] if (_num(v) or 0) >= 0 else COLORS['down']
              for v in tones]
    kw: dict = {}
    if show_values:
        # cliponaxis=False：不然最高那根的数字会被画布上边缘裁掉
        kw = {'text': ['' if _num(v) is None else f'{_num(v):,.0f}'
                       for v in vals],
              'texttemplate': '%{text}', 'textposition': 'outside',
              'textfont': {'size': 10}, 'cliponaxis': False}
    if horizontal:
        # 排序后最大的在最上面：plotly 的 y 轴是自下而上排的，要反转
        fig = go.Figure(go.Bar(x=vals[::-1], y=labels[::-1], orientation='h',
                               marker_color=colors[::-1], **kw))
        lay = _layout(title)
        lay['margin'] = {'l': 110, 'r': 12, 't': 34 if title else 8, 'b': 32}
        fig.update_layout(lay)
        return fig
    fig = go.Figure(go.Bar(x=labels, y=vals, marker_color=colors, **kw))
    fig.update_layout(_layout(title))
    return fig


def _heatmap(rows, x, y, z, title=None, opts=None):
    """
    长表 → 热力图。行是板块、列是交易日。

    输入是"每行一个 (日期, 板块, 值)"的长表而不是宽矩阵 —— SQL 出长表
    最自然，转置放在这里做。色阶上下界写死在 config.chart.options 里
    （`zmin/zmax`），**这一点很重要**：让 plotly 自动定色阶的话，
    平静的一天和暴跌的一天会用同一套颜色渲染出同样的红绿深浅，
    跨日比较就失去意义了。
    """
    opts = opts or {}
    xs = list(dict.fromkeys(str(r.get(x)) for r in rows))
    ys = list(dict.fromkeys(str(r.get(y)) for r in rows))
    idx = {(str(r.get(x)), str(r.get(y))): r.get(z) for r in rows}
    matrix = [[_num(idx.get((cx, cy))) for cx in xs] for cy in ys]
    # 格子里直接写数字。只有颜色的话，"这块到底涨了 1% 还是 6%"看不出来，
    # 而板块轮动要的就是量级对比。空格子留空而不是写 0。
    text = [['' if v is None else f'{v:.1f}' for v in row] for row in matrix]
    fig = go.Figure(go.Heatmap(
        z=matrix, x=xs, y=ys,
        text=text, texttemplate='%{text}', textfont={'size': 9},
        colorscale=opts.get('color_scale', 'RdYlGn_r'),
        zmin=opts.get('zmin'), zmax=opts.get('zmax'),
        hovertemplate='%{y} · %{x}<br>%{z:.2f}%<extra></extra>',
        colorbar={'thickness': 10}))

    lay = _layout(title)
    lay['margin'] = {'l': 120, 'r': 12, 't': 34 if title else 8, 'b': 40}
    fig.update_layout(lay)
    return fig


def _num(v):
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _candlestick(rows, title=None):
    fig = go.Figure(go.Candlestick(
        x=[r['trade_date'] for r in rows],
        open=[r['open'] for r in rows], high=[r['high'] for r in rows],
        low=[r['low'] for r in rows], close=[r['close'] for r in rows],
        increasing_line_color=COLORS['up'], decreasing_line_color=COLORS['down'],
        name='K'))
    fig.update_layout(_layout(title), xaxis_rangeslider_visible=False)
    return fig


def _hist(rows, title=None, enc=None):
    """
    固定分箱的计数图。**不在这里二次分箱** —— 分箱边界由 SQL 定，
    保证跨日可比。

    列名从 encoding 里取，取不到再按"标签列 + 计数列"猜。
    以前这里是 `_bar(rows, 第一列, 第二列)`，而 `q_daily_pct` 返回的是
    `label / lo / hi / cnt` —— 第二列是**分箱下界** `lo`，于是画出来的
    柱高是 -100、-10、-7… 这串边界值，看着就像"有一大堆 -100 的票"。
    真实计数在 `cnt` 里（跌停附近那根其实只有 6 只）。
    """
    if not rows:
        return go.Figure(layout=_layout(title))
    cols = list(rows[0])
    enc = enc or {}
    x = enc.get('x') if enc.get('x') in cols else None
    y = enc.get('y') if isinstance(enc.get('y'), str) else (
        (enc.get('y') or [None])[0])
    y = y if y in cols else None
    if x is None:
        x = next((c for c in ('label', 'bucket', 'name') if c in cols), cols[0])
    if y is None:
        y = next((c for c in ('cnt', 'n', 'count', 'value') if c in cols),
                 None)
    if y is None:
        # 兜底也不要撞上 lo/hi 这类边界列
        y = next((c for c in cols[1:] if c not in ('lo', 'hi', x)), cols[-1])
    # 红绿按**分箱下界**判，不是按柱高：柱高是家数，永远是正的。
    # 数字直接写在柱子上 —— 分布图看的就是"哪一箱多少只"。
    return _bar(rows, x, y, title, tone_col='lo' if 'lo' in cols else None,
                show_values=True)



def _scatter(rows, title=None):
    fig = go.Figure(go.Scatter(
        x=[r.get('x_score') for r in rows], y=[r.get('y_score') for r in rows],
        mode='markers', text=[r.get('name') for r in rows],
        marker={'size': 6, 'opacity': .6, 'color': COLORS['accent']}))
    fig.update_layout(_layout(title), xaxis_title='X 个股质量',
                      yaxis_title='Y 环境共振')
    return fig


def _layout(title=None) -> dict:
    return {'title': title, 'margin': {'l': 44, 'r': 12, 't': 34 if title else 8,
                                       'b': 32},
            'height': None, 'template': 'plotly_white',
            'legend': {'orientation': 'h', 'y': -.18},
            'font': {'size': 11}}


def figure(chart: dict, rows: list[dict]):
    """
    按 config.chart 的 chart_type + encoding 出图。

    `options` 里两个跨图种的开关（**都在卡片配置里改，不用改代码**）：

    - `yrange: [lo, hi]` 把 y 轴钉死。分数类的图必须钉 —— 纵轴自适应的话，
      "近 7 日" 和 "近 20 日" 两张温度图并排放，同样一段 20 分的波动在
      短窗口里画成满屏震荡、在长窗口里画成一条平线，得出的强弱印象是反的。
    - `zero_line: true` 画一条 0 基准线。-100~100 的量纲里 0 是"中性"这个
      分界，没有这条线看不出柱子是在零上还是零下。
    """
    ctype = chart.get('chart_type')
    enc = chart.get('encoding') or {}
    opts = chart.get('options') or {}
    fig = _figure_body(ctype, enc, opts, rows)
    rng = opts.get('yrange')
    if isinstance(rng, (list, tuple)) and len(rng) == 2:
        fig.update_layout(yaxis={'range': [float(rng[0]), float(rng[1])]})
    if opts.get('zero_line'):
        fig.add_hline(y=0, line_width=1, line_dash='dot', line_color='#adb5bd')
    return fig


def _figure_body(ctype, enc, opts, rows: list[dict]):
    title = None    # 标题放在卡片头部，图里不重复
    if not rows:
        return go.Figure(layout=_layout(title))
    x = enc.get('x') or list(rows[0])[0]
    ys = enc.get('y') or [k for k in list(rows[0])[1:3]]
    if isinstance(ys, str):
        ys = [ys]
    if ctype == 'candlestick':
        return _candlestick(rows, title)
    if ctype == 'bar':
        return _bar(rows, x, ys[0], title,
                    horizontal=str(opts.get('orient', '')).lower() == 'h')
    if ctype == 'hist':
        return _hist(rows, title, enc)
    if ctype == 'scatter':
        return _scatter(rows, title)
    if ctype == 'heatmap':
        return _heatmap(rows, x, enc.get('y') if isinstance(enc.get('y'), str)
                        else ys[0], enc.get('z') or 'pct_chg', title, opts)
    return _line(rows, x, ys, title, color=enc.get('color'),
                 visible_col=opts.get('visible_col'))



# ---------------------------------------------------------------- 复合卡片件

def _explain_block(ex: dict, row: dict, binding: dict | None):
    """
    一个分项的「怎么算的」。

    用 `html.Details`（浏览器原生折叠）而不是 dbc.Collapse：折叠要靠回调，
    而这张卡在 `/review` 首页也会出现，一页可能十几个分项 ——
    给每个分项配一个回调既写不动也没必要，原生折叠零回调、点开即时展开。
    """
    body = []
    if ex.get('basis'):
        body.append(dcc.Markdown(f"**判据**：{ex['basis']}",
                                 className='small mb-1'))
    for t in ex.get('terms') or ():
        w = t.get('w')
        head = t.get('name') or ''
        if w is not None:
            head += f'　× {w:g}'
        body.append(html.Div([
            html.Div(head, className='small fw-semibold'),
            html.Div(t.get('expr') or '', className='small font-monospace',
                     style={'color': '#495057', 'wordBreak': 'break-all'}),
            html.Div(t.get('norm') or '', className='small text-muted'),
            html.Div(t.get('note') or '', className='small text-muted'),
        ], className='mb-2 ps-2',
            style={'borderLeft': '2px solid #dee2e6'}))
    if ex.get('combine'):
        body.append(dcc.Markdown(f"**合成**：{ex['combine']}",
                                 className='small mb-1'))
    # 这一行的分值怎么变成对总分的贡献 —— 卡片标题写的就是「分值 × 权重」
    v, w, c = row.get('part_value'), row.get('weight'), row.get('contribution')
    if v is not None and w is not None:
        body.append(html.Div(f'贡献 = 分值 {float(v):+.2f} × 权重 {float(w):g}'
                             + (f' = {float(c):+.3f}' if c is not None else ''),
                             className='small'))
    # ★权重对不上＝改了配置没重算，必须让人看见，否则调完权重看不出没生效
    if binding and binding.get('weight') is not None and w is not None:
        bw = float(binding['weight'])
        if abs(bw - float(w)) > 1e-6:
            body.append(html.Div(
                f'⚠ 注册表当前权重是 {bw:g}，这一行是按 {float(w):g} 算的'
                '——改过权重之后还没重算这一天',
                className='small text-danger'))
    if binding and binding.get('params'):
        body.append(html.Div(f"当前参数（config.binding.params）：{binding['params']}",
                             className='small text-muted'))
    if ex.get('caveat'):
        body.append(dcc.Markdown(f"**口径提醒**：{ex['caveat']}",
                                 className='small text-warning-emphasis'))
    return html.Details([
        html.Summary('怎么算的', className='small text-muted',
                     style={'cursor': 'pointer'}),
        html.Div(body, className='p-2 mt-1',
                 style={'background': '#f8f9fa', 'borderRadius': '4px'}),
    ], className='mt-1')


def _score_panel(rows: list[dict]):
    """
    评分归因面板：每个分项一行，横条表示分值，右边给权重和贡献。

    分值域是 -100~100，所以条形从中线向两侧长 —— 用 0~100 的进度条画
    负分会全部贴在最左边，看不出"负多少"。

    每行下面挂一个折叠的「怎么算的」：公式来自脚本里的 `EXPLAIN`
    （`app/compute/explain.py`），权重和参数来自注册表。原来这张卡只有
    "分项名 + 数字 + 条形"，看不出这个数是哪几个量算出来的 ——
    评分归因的意义正是"让评分不是黑盒"，缺了公式这张卡等于只换了个画法。
    """
    if not rows:
        return html.Div('没有评分明细', className='text-muted p-3')
    # 说明和绑定各查一次，不在循环里查库
    try:
        from app.compute import explain as ex_mod
        explains = ex_mod.explain_map(r.get('script_key') for r in rows)
        bindings = ex_mod.binding_map()
    except Exception:
        # 说明缺失不该让整页打不开
        explains, bindings = {}, {}
    items = []
    for r in rows:
        v = r.get('part_value')
        v = 0.0 if v is None else float(v)
        pct = min(abs(v), 100) / 2          # 半幅，最多占一侧 50%
        tone = COLORS['up'] if v >= 0 else COLORS['down']
        bar = html.Div([
            html.Div(style={'width': '50%', 'display': 'flex',
                            'justifyContent': 'flex-end'},
                     children=html.Div(style={
                         'width': f'{0 if v >= 0 else pct * 2}%',
                         'height': '10px', 'background': tone})),
            html.Div(style={'width': '50%'},
                     children=html.Div(style={
                         'width': f'{pct * 2 if v >= 0 else 0}%',
                         'height': '10px', 'background': tone})),
        ], style={'display': 'flex', 'borderLeft': '1px solid #ccc'})
        ex = explains.get(r.get('script_key'))
        binding = bindings.get((r.get('cap_key'), r.get('script_key')))
        items.append(html.Div([
            html.Div([
                html.Span(r.get('part_label') or r.get('part_key'),
                          className='small'),
                html.Span(f'{v:+.1f}', className='small ms-auto fw-semibold',
                          style={'color': tone}),
                html.Span(f'× {r.get("weight") or 0}',
                          className='small text-muted ms-2'),
            ], className='d-flex'),
            bar,
            _explain_block(ex, r, binding) if ex else _no_explain_hint(),
        ], className='mb-2'))
    return html.Div(items)


def _no_explain_hint():
    """EXPLAIN 还没写的分项：明确说一句，不留空白让人以为加载失败。"""
    return html.Div('（这个分项还没写说明）', className='small text-muted')



_TASK_TONE = {'success': 'success', 'failed': 'danger', 'running': 'primary',
              'pending': 'secondary', 'skipped': 'light', 'timeout': 'warning'}


def _task_list(rows: list[dict]):
    if not rows:
        return html.Div('今天还没有任务记录', className='text-muted p-3')
    return dbc.ListGroup([
        dbc.ListGroupItem([
            dbc.Badge(r.get('state') or '?',
                      color=_TASK_TONE.get(r.get('state'), 'secondary'),
                      className='me-2'),
            html.Span(r.get('name') or r.get('job_key') or '-',
                      className='fw-semibold'),
            html.Span(f' {r.get("cap_key") or ""}', className='text-muted small'),
            html.Span(str(r.get('trade_date') or ''),
                      className='text-muted small ms-auto'),
        ], className='d-flex align-items-center py-1')
        for r in rows], flush=True)


def score_panel(rows: list[dict]):
    """公开入口：给别的页面复用评分归因面板。"""
    return _score_panel(rows)


def plot_layout(title: str | None = None) -> dict:
    """公开入口：统一的 plotly layout，页面自己出图时用它保持风格一致。"""
    return _layout(title)


# ---------------------------------------------------------------- 卡片渲染



def _q_params(opts: dict, params: dict) -> dict:
    """
    命名查询的入参 = 卡片配置里的 `options.params` + 页面级参数。

    **为什么单独开一个 `params` 子对象**：`options` 里混着渲染用的键
    （note / drilldown / scale / color），把它整个抖进 `fn(**opts)` 就是
    把界面配置当查询参数传，命名查询靠 `**_` 吞掉才不报错 —— 能跑，但读代码的人
    分不清哪个键是给谁的。放在 `params` 下面，"这是给查询的"就是自解释的。

    页面级参数（比如复盘页选的 trade_date）**优先级更高**：卡片配的是默认值，
    页面上选的是用户当下要看的东西。
    """
    return {**(opts.get('params') or {}), **params}


def _table_color(opts: dict) -> list[dict]:
    """
    表格卡的条件着色走配置：

        options.color = {
            "pct":  ["trend_score", ...],          涨红跌绿
            "amp":  ["turnover_rate_pct", ...],     振幅三档
            "flag": {"cols": ["quad_up"], "value": "↑"},   等于符号就红字加粗
            "dim":  {"cols": ["r2"], "below": 0.5}         低于阈值压灰
        }

    为什么要有这一层：`rules_pct` / `rules_amp` 以前只有**专属页**能用（页面
    自己调 `ui.table(color=...)`），走 `config.page_layout` 的表格卡一律没有颜色。
    结果是同一张表搬进注册表就掉色，"涨红跌绿"这种最基本的读表习惯要靠代码
    才能给 —— 这与"布局是数据"的前提冲突。

    列名用**字段名（id）**，不是中文抬头：注册表卡片的行是查询原样返回的，
    `table()` 只把抬头翻成中文，`column_id` 仍然是 `trend_score` 这样的 id。
    """
    spec = opts.get('color') or {}
    if not isinstance(spec, dict):
        return []
    out = rules_pct(spec.get('pct') or ()) + rules_amp(spec.get('amp') or ())
    flag = spec.get('flag') or {}
    if isinstance(flag, dict) and flag.get('cols'):
        out += rules_flag(flag['cols'], str(flag.get('value', '↑')))
    dim = spec.get('dim') or {}
    if isinstance(dim, dict) and dim.get('cols'):
        out += rules_below(dim['cols'], float(dim.get('below', 0.5)))
    return out


def _title_date(title: str | None, opts: dict, params: dict) -> str | None:
    """
    标题里的 `{d}` → 这张卡**实际用的数据日期**。

    为什么要有这个：卡片标题是静态文本，而卡片里的数字全是"最新有数据的那个
    交易日"的。用户看到「板块热度榜」时无法判断这是哪天的热度 ——
    盘后数据没摄进来时它是上一个交易日，看着却和今天一模一样
    （2026-09-01 用户反馈："不知道他这个板块热度指的是啥时候的"）。

    日期取值和查询里的 `_d()` 完全一致：页面级 `trade_date` > 卡片
    `options.params.trade_date` > `queries.latest_date()`。所以标题上写的
    就是数据实际来自哪天，不是"今天"。

    只做替换、不自动追加：哪张卡要显示日期由 `config.page_layout.title`
    里有没有 `{d}` 决定 —— 布局仍然是数据。
    """
    if not title or '{d}' not in title:
        return title
    d = (params.get('trade_date')
         or (opts.get('params') or {}).get('trade_date')
         or queries.latest_date())
    return title.replace('{d}', str(d) if d else '无数据')


def render_card(row: dict, charts: dict[str, dict], *, params: dict | None = None):
    """把 config.page_layout 的一行渲染成组件。"""
    params = params or {}
    kind = row['card_kind']
    ref = row.get('card_ref')
    opts = row.get('options') or {}
    title = _title_date(row.get('title'), opts, params)
    height = row.get('height') or 320
    # 说明文字：**库优先，配置次之，代码兜底**。
    # `config.help_note` 按字段拆开存（见 app/web/help.py），改完刷新就生效；
    # `config.page_layout.options.note` 是上一代的做法，留着兼容；
    # `CARD_HELP` 是代码里的存量兜底。
    # `task_list` / `score_panel` 的 card_ref 允许为空（用默认查询），所以
    # 兜底键要按卡片类型补上 —— 否则这两类卡永远查不到 CARD_HELP。
    help_key = ref or {'task_list': 'q_task_recent',
                       'score_panel': 'q_score_parts'}.get(kind, '')
    # 库里按两个 target 找：先 card_ref，再「页面.槽位」——
    # 没有 card_ref 的卡（task_list 等）在库里就是拿后者当 target。
    # 两个都要在代码兜底**之前**查，否则给这类卡在库里写的说明会被
    # CARD_HELP 里的存量文字挡住。
    from app.web import help as help_repo
    note = (help_repo.for_card(help_key)
            or help_repo.for_card(f"{row.get('page_key', '')}.{row.get('slot', 0)}")
            or opts.get('note')
            or (CARD_HELP.get(help_key) if help_key else None))
    note_id = f"note-{row.get('page_key', 'p')}-{row.get('slot', 0)}"


    try:
        if kind == 'markdown':
            return card(title, dcc.Markdown(opts.get('text') or ref or ''),
                        height=height, note=note, note_id=note_id)


        if kind == 'iframe':
            return card(title, html.Iframe(src=ref, style={
                'width': '100%', 'height': f'{height - 20}px', 'border': 0}))

        if kind == 'chart':
            chart = charts.get(ref)
            if not chart:
                return card(title, alert(f'config.chart 里没有 {ref}', 'warning'),
                            height=height)
            fn = queries.get(chart['data_query'])
            if fn is None:
                return card(title or chart['name'], alert(
                    f'命名查询 {chart["data_query"]} 尚未实现', 'warning'),
                    height=height)
            rows = fn(**{**(chart.get('params') or {}), **params})
            # ★说明的第三个来源：config.chart.options.note。原来只认
            # page_layout.options.note 和代码里的 CARD_HELP，所以写在**图**上的
            # note 只有在 CARD_HELP 里也没有对应条目时才有机会显示 ——
            # 而口径说明本来就属于图（同一张图摆在两个页面上，说明不该抄两遍）。
            # 优先级：布局层（就地覆盖）> CARD_HELP（代码兜底）> 图自己的 note。
            note2 = note or (chart.get('options') or {}).get('note')
            return card(title or chart['name'],
                        dcc.Graph(figure=figure(chart, rows),
                                  style={'height': f'{height - 60}px'},
                                  config={'displaylogo': False}),
                        height=height, note=note2, note_id=note_id)

        if kind == 'table':
            fn = queries.get(ref)
            if fn is None:
                return card(title, alert(f'命名查询 {ref} 尚未实现', 'warning'),
                            height=height)
            # 每页几行走配置（`options.page_size`）：两百多行的明细表按默认 15 行
            # 要翻十几页，而"一页看多少"是看的人的习惯，不该写死在代码里。
            # 配色也走配置（`options.color`），见 `_table_color`。
            #
            # ⚠️ **表格卡不吃 `page_layout.height`**（2026-09-01 改）：卡片和表格
            # 都不设固定高度，一页有几行就画多高。原来是"卡片 height、表格
            # height-60"，于是 `height` 和 `page_size` 是两个手填的数，一漂就出
            # 两种病 —— 高度小于一页内容 → 想看更多得**在表格里**滚；大于一页
            # 内容 → 表格下面一片空白（用户两条反馈就是这两个症状）。现在
            # **要多看几行就把 `page_size` 调大**，高度自己跟上。
            # 真要限高（让表格内部滚）就在 options 里写 `max_height`。
            return card(title, table(fn(**_q_params(opts, params)),
                                     max_height=opts.get('max_height'),
                                     page_size=int(opts.get('page_size', 15)),
                                     color=_table_color(opts) or None),
                        note=note, note_id=note_id)


        if kind == 'stat':
            fn = queries.get(ref) if ref else None
            if fn is None:
                return stat(title or ref or '-', '—',
                            hint=f'命名查询 {ref} 未注册')
            rows = fn(**_q_params(opts, params))
            if not rows:
                return stat(title or ref, '—', hint='查询没返回行')
            # 约定：k_* 单值查询返回一行 {value, hint}。options.field 可以
            # 覆盖取哪一列 —— 这样一个普通列表查询也能当指标块用（取第一行）。
            field = opts.get('field')
            row0 = rows[0]
            val = row0.get(field) if field else row0.get('value', len(rows))
            hint = opts.get('hint') or row0.get('hint')
            # **scale/suffix 只对数字生效。** 有些命名查询（k_amount）自己就把单位
            # 换算好、还带了千位分隔，返回的是 '21,177 亿' 这样的字符串；
            # 卡片配置里如果又填了 scale，float(val) 会抛
            # `could not convert string to float`，**整张卡片变成红色报错**。
            # 而 options 是「积木管理」页面里能随手改的，这种组合迟早会再出现，
            # 所以这里按值的类型决定要不要换算，而不是信配置。
            if isinstance(val, (int, float, Decimal)) and not isinstance(val, bool):
                if opts.get('scale'):
                    val = round(float(val) / float(opts['scale']), 2)
                if opts.get('suffix'):
                    val = f'{val}{opts["suffix"]}'
            return stat(title or ref, '—' if val is None else val,
                        hint=hint, tone=opts.get('color') or 'accent',
                        note=note, note_id=note_id)


        if kind == 'score_panel':
            # 评分面板：一行一个分项，带权重和贡献的横条。ref 是命名查询名，
            # 没给就用 q_score_parts（单票归因）。
            fn = queries.get(ref or 'q_score_parts')
            if fn is None:
                return card(title, alert(f'命名查询 {ref} 尚未实现', 'warning'),
                            height=height)
            return card(title, _score_panel(fn(**params)), height=height,
                        note=note, note_id=note_id)


        if kind == 'task_list':
            fn = queries.get(ref or 'q_task_recent')
            if fn is None:
                return card(title, alert(f'命名查询 {ref} 尚未实现', 'warning'),
                            height=height)
            rows = fn(**{'limit': opts.get('limit', 10), **params})
            return card(title, _task_list(rows), height=height,
                        note=note, note_id=note_id)


        return card(title, alert(f'卡片类型 {kind} 还没实现', 'secondary'),
                    height=height)
    except Exception as exc:  # noqa: BLE001
        return card(title, alert(f'渲染失败：{exc}', 'danger'), height=height)



def render_page(layout_rows: list[dict], charts: dict[str, dict],
                *, params: dict | None = None):
    """按 slot 顺序摆卡片，width 是 12 栅格里的份数。"""
    if not layout_rows:
        return alert('这个页面还没有配卡片。到「积木管理」里加。', 'secondary')
    cols = [dbc.Col(render_card(r, charts, params=params),
                    width=12, lg=min(r.get('width') or 6, GRID),
                    className='mb-3')
            for r in layout_rows if r.get('visible', True)]
    return dbc.Row(cols, className='g-3')
