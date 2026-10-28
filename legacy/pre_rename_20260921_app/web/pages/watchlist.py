# -*- coding: utf-8 -*-
"""
自选股组分析 —— 组的**分析**页，兼维护入口。

2026-08-30 从「自选与关注」改名：这一页的主体是自选组的加权分 / 趋势 / 振幅
三张分析卡（`q_group_heat`、`group_score_trend`、`q_watchlist`），
加票改组只是它的维护入口 —— 原名把顺序说反了。

以前这一页只有两张只读卡片（自选组热度、自选池明细），能看不能改：
组是迁移时带过来的，想加一只票、改个组名、把票从"观察"挪到"重点"，
库里都得手写 SQL。复盘的时候手上正拿着一只票，回头还要去开 SQL 查询台，
这条路根本走不通。所以这一页现在是专属页：上半截维护，下半截还是原来那两张卡。

2026-08-29 第二轮改造：上半截从"三段编号表单 + 两张只读表"改成
**左组 / 右票的两栏操作台**。原来的形态功能齐全但难用 —— 改一个组名要
"②里选组 → 等表单预填 → 改名字 → 点保存"四步，而组明明就列在眼前。

设计取舍：

- **左栏组表就是当前组的选择器**。原来那个 `wl-sgroup` 下拉删了：组已经列在
  眼前，再去下拉里把同一个名字找一遍是纯多余的动作。
  2026-08-30 再放宽一步：**点行里任意一格就换组**（`active_cell` 回写
  `selected_rows`），最左边的圆点退化成"当前组"的指示灯 —— 只有圆点能点的话，
  想看某个组有哪些票得先瞄准一个 12px 的点，而点组名什么都不发生。
- **能就地改的一律就地改。** 组的 组名 / 排序 / 备注、票的 成本 / 备注 都是
  双击单元格填。★ 不再单独占一列（2026-08-30）：它贴在组名前面，
  在组名单元格里加上或删掉那个星就是切换 `is_focus`，一格写两列。
  实现靠 DataTable 的 `data_previous`：**Input 用 `data_previous`、State 用
  `data`**，Output 才是 `data`。同时把 `data` 挂成 Input 和 Output 会被
  Dash 判成 Circular Dependencies，直接起不来。
- **组默认按「10日均涨跌bp」从低到高排**（2026-09-01 改；此前是预测操作分
  从高到低），不是按建组顺序：先看被砸得最狠的组，再决定去翻哪个组的票。
  `预测操作分` 还在，只是不当排序键了 —— 这一列 2026-08-30 从「预测分」改名，
  原名看不出它是"操作分按 20 日趋势外推的下一日值"。
- **成员操作只在"当前选中的组"里做。** 不做跨组的批量勾选 —— 那要处理
  "同一只票在两个组里"的歧义，而自选池就几十只票，选组再操作更直白。
- **删除组要过一道 modal 确认。** `config.watchlist_stock` 是
  `ON DELETE CASCADE`，删组会连着把组里的票全删掉；只有一个按钮的话
  一次误点就没了，而这些是手工攒起来的名单，没有别处可恢复。
  原来那个常驻勾选框换成 modal：勾选框要一直占一行，还容易上次勾了忘取消。
- **加票不用下拉，用"输入框 + 候选单选列表"。** 原来那个 `dcc.Dropdown`
  服务端搜得到、页面上却出不来：Dropdown 会拿浏览器里的输入**再过滤一遍**
  服务端返回的 options，而它默认按 `label` 匹配 —— label 里没有拼音首字母，
  所以 `gzmt` 永远是"无结果"（2026-08-29 用户报的"加不了票"就是这个）。
  现在候选是 `RadioItems`，服务端给什么就显示什么，没有第二道过滤。
  顺带给所有下拉的 options 补了 `search` 字段（见 `stock.search_options`）。
- **每一步都有默认值，回车推进下一步。** ①输入关键词 → 回车（**只命中一只
  时直接跳③**，学旧系统 financeWeb 的做法）→ 回车去④ → 回车加入。
  批量时在①里贴一串代码，一次回车全部加入。焦点是用 clientside 回调按 id
  抓 DOM 元素设的 —— Dash 没有"设置焦点"这种 Output。
- **代码仍然不接受自由文本写库**：批量里的每个 token 都要先在 `ref.security`
  里解析成 `ts_code`，解析不出或不唯一的照实报出来、不写。手打 `600519`
  少个后缀会写进一条永远 join 不上 `ref.security` 的孤行，页面上表现为
  "有这只票但没名字没行情"，比报错更难查。
- 写库统一走 `engine.execute`（app 角色），全部参数化。**唯一拼进 SQL 文本的
  是列名**（PG 不允许列名当参数），而它只能从 `GROUP_EDITABLE` /
  `STOCK_EDITABLE` 这两张写死的白名单里取。
- **库没变就不重画。** `_refresh()` 默认一个 Output 都不刷，各分支按需打开
  `groups` / `members` / `opts` / `head`。这是上一轮修"每次回车整页闪"留下的
  规矩；就地编辑成功时**尤其**不能重建表格，否则光标连同正在编辑的格子一起
  被换掉，连着改两行就没法做了。
"""

from __future__ import annotations

import re

import dash_bootstrap_components as dbc
from dash import Input, Output, State, callback_context, dcc, html, no_update

from app.datasource import ths_sync
from app.db import engine
from app.db.repo import registry as reg_repo
from app.db.repo import watchlist as wl_repo
from app.web import queries, ui
from app.web.pages import stock as stock_page

# 重点关注的标记。2026-08-30 取消了独立的「重点」列，改成**贴在组名前面**：
# 那一列只有两种取值、宽度却占满一个列头，而组表本来就窄（4/12 栅格）。
# 星号直接写在组名单元格里，既省一列，又让"哪个组是重点"和名字一起被读到。
FOCUS_MARK = '★'
FOCUS_HINT = ('组名前面的 ★ ＝重点关注（`is_focus`）；'
              '在组名单元格里手动加上或删掉这个星就能切换')

# 批量输入的分隔符：空格、换行、半角/全角逗号、分号、顿号
_SPLIT = re.compile(r'[\s,，;；、]+')

# 自选池只放能交易的标的。指数（999999 上证指数）和板块加进来之后
# `md.stock_daily` 里 join 不到行，页面上就是"有这只票但没价没分"。
ADDABLE = ('stock', 'etf')
KIND_CN = {'stock': '股票', 'etf': 'ETF', 'index': '指数', 'sector': '板块',
           'other': '其它标的'}


def _fit_height(n_rows: int, cap: int = 1240) -> int:
    """
    表格高度按行数长，长到 `cap` 就封顶。算法搬到了 `ui.fit_height`：
    2026-08-31 把单元格 padding 收窄后行高从 27px 变成 23px，两个页面各存一份
    行高常量的话，改完只有一边跟着走 —— 而算错的表现是"表格自己滚"，
    页面上看不出是哪里错了。这里只留 cap（43 个自选组是本页的取舍）。

    cap 2026-08-30 从 760 提到 **1240**（用户要求"再拉长一点，直接显示更多行"）。
    行高收窄后 43 个组只需 `76 + 23×43 + 12 = 1077`，1240 里还有余量。
    """
    return ui.fit_height(n_rows, cap)




# ---------------------------------------------------------------- 读

def split_focus(cell) -> tuple[str | None, bool]:
    """
    组名单元格 → (组名, 是否重点)。`★ 打板` → `('打板', True)`。

    星号是**显示层的前缀，不进库**：库里 `name` 存的永远是干净的组名，
    `is_focus` 是独立的布尔列。要是把星号一起存进 name，改名、去重、
    按名字 join 全都要先记着剥星号，早晚漏一处。

    半角星 `*` 也认 —— 键盘上打 ★ 要切输入法，用户十有八九会打成 `*`。
    """
    s = str(cell or '').strip()
    focus = s.startswith((FOCUS_MARK, '*'))
    return (s.lstrip(FOCUS_MARK + '* ').strip() or None), focus


# 左栏组表里可以就地改的列 → (库字段, 怎么把单元格里的字变成库里的值)。
# **字段名只能从这张表里取**：下面 UPDATE 的列名是拼进 SQL 文本的（PG 不允许
# 列名当参数），拼一个来自页面的字符串进去就是注入。值本身照旧走参数。
#
# 2026-08-30：`重点` 不再是独立一列，但**仍然留在这张白名单里** ——
# 它是 `update_group_field` 的合法入口（脚本里、测试里都按列名调），
# 只是页面上改星号走的是 `组名` 那一格（见 `split_focus`）。
GROUP_EDITABLE: dict[str, tuple[str, object]] = {
    '组名': ('name', lambda v: (split_focus(v)[0])),
    '排序': ('seq', lambda v: int(float(v))),
    '备注': ('note', lambda v: (str(v or '').strip() or None)),
    '重点': ('is_focus', lambda v: str(v or '').strip() == FOCUS_MARK),
}

# 右栏成员表里可以就地改的列。成本要能解析成数字 —— 手打错一个字母就写进去，
# 盈亏那一列会跟着变成一个查不出来源的数。
STOCK_EDITABLE: dict[str, tuple[str, object]] = {
    '成本': ('cost', lambda v: None if str(v or '').strip() == ''
             else float(str(v).strip())),
    '备注': ('note', lambda v: (str(v or '').strip() or None)),
}

# 成员表里藏着的原始代码列。表里显示的「代码」已经被 `ui.table` 换成了指向
# 东方财富的 Markdown 链接，就地编辑回调没法从那串字面量里可靠地取回 ts_code。
CODE_COL = '_code'
# 左栏组表里那一列的抬头。写成常量是因为排序键、着色规则、测试都要引用同一个
# 字符串 —— 中文列名散在三处，改一次抬头就会有一处忘改。
CHG10_COL = '10日均涨跌bp'


def group_rows() -> list[dict]:
    """
    左栏组表的数据行。**默认按「10日均涨跌bp」从低到高**（用户 2026-09-01 指定）：
    先看跌得最狠的组 —— 这一页的用法是"哪个组已经被砸下来了、该去翻它的票"，
    不是"哪个组已经涨完了"。

    2026-09-01 加的这一列 = 组内每票近 10 个交易日**日均**涨跌幅取组平均，
    **×100 以基点显示**（1bp = 0.01%）。组一平均只剩 ±1 以内，写成两位小数
    的百分数在表里全是"−0.25"这种看不出差别的数；×100 之后正好是基点，
    所以列名写 bp 不写 % —— 标成 % 会把 −25bp 读成 −25%。
    口径与「自选组热度」卡的 `组内10日均涨跌bp` 是同一个数（`group_chg10_map()`）。

    `预测操作分` 还留着一列（来自 `queries.group_pred_map()`，和热度卡同一段
    CTE），只是不再当默认排序键。两列算不出来的组都留空排最后，不补 0 ——
    0 会被读成"走平"。

    2026-08-30 两处改动：
    - **`重点` 不再是一列**，星号贴在 `组名` 前面（`★ 打板`）。组表只有
      4/12 栅格宽，一个只有两种取值的列不值一个列头。
    - `预测分` 改名 `预测操作分`：原名看不出这是"操作分的外推值"，
      被当成"某种预测涨幅"。

    `组号` 留在最后一列并在表里隐藏：选中行要靠它翻回 group_id，
    但它对人没有信息量，摆在最前面只是占地方。
    """
    preds = queries.group_pred_map()
    chg10 = queries.group_chg10_map()
    rows = engine.query(
        """
        SELECT g.group_id, g.name, g.is_focus, g.seq,
               coalesce(g.note, '') AS note,
               (SELECT count(*) FROM config.watchlist_stock w
                 WHERE w.group_id = g.group_id) AS members
          FROM config.watchlist_group g
        """)
    out = []
    for r in rows:
        gid = int(r['group_id'])
        out.append({'组名': (f'{FOCUS_MARK} ' if r['is_focus'] else '')
                            + (r['name'] or ''),
                    '成员数': r['members'],
                    CHG10_COL: chg10.get(gid),
                    '预测操作分': preds.get(gid),
                    '排序': r['seq'],
                    '备注': r['note'],
                    '组号': gid})
    # 10日均涨跌升序、算不出来的沉底；同值时★优先、再按 seq、再按名字，
    # 保证每次刷出来的行序是**确定的** —— 行序一抖，选中行的下标就指错组。
    out.sort(key=lambda r: (r[CHG10_COL] is None,
                            float(r[CHG10_COL] or 0),
                            not str(r['组名']).startswith(FOCUS_MARK),
                            r['排序'] or 100, r['组名'] or ''))
    return out


def groups_table(group_id=None, rows: list[dict] | None = None):
    """
    左栏：可就地编辑 + 单选的组表。

    换组有两条路，**点行里任意一格就换**（2026-08-30 加）：
    - 最左边的圆点（`row_selectable='single'`）—— 还留着，它是"当前组"的指示灯
    - 行里任意单元格（`active_cell`）—— 点一下，右栏成员整块跟着变

    只有圆点能换组的话，"想看这个组有哪些票"要先瞄准那个直径 12px 的点；
    而点组名反倒什么都不发生。`active_cell` 那条路只回写 `selected_rows`，
    **不重画组表**，所以双击进单元格改字不受影响。

    `rows` 可以由调用方传进来 —— 首屏要用同一批行找出"默认选中哪个组"和
    "它的预测操作分是多少"，重算一遍等于白跑一次 20 日回归。
    """
    rows = group_rows() if rows is None else rows
    if not rows:
        return html.Div('还没有自选组。用上面那一行建第一个。',
                        className='text-muted p-2')
    sel = [i for i, r in enumerate(rows) if group_id not in (None, '')
           and int(r['组号']) == int(group_id)][:1]
    return ui.table(rows, id_='wl-gdt', page_size=60,

                    height=_fit_height(len(rows)),
                    link_codes=False, hidden_cols=('组号',),
                    editable_cols=('组名', '排序', '备注'),
                    row_selectable='single', selected_rows=sel,
                    color=ui.rules_pct(('预测操作分', CHG10_COL)))




def _last_days(n: int = 5) -> list:
    """最近 n 个有行情的交易日，降序。表头要显示日期，所以先单独取出来。"""
    rows = engine.query(
        'SELECT DISTINCT trade_date FROM md.stock_daily '
        ' WHERE trade_date > (SELECT max(trade_date) - 20 FROM md.stock_daily) '
        ' ORDER BY trade_date DESC LIMIT %s', (n,))
    return [r['trade_date'] for r in rows]


# 自选股表里这三组列的配色规则不一样：涨跌和盈亏有正负（红/绿/灰），
# 振幅只有大小（>3% 红、>1.5% 橙、其余灰）。口径见 05_seed.sql 里的指标注释。
PCT_COLS = ('涨跌幅', '10日涨跌', '20日涨跌', '盈亏')
AMP_COLS = ('10日振幅', '20日振幅', '明日振幅')


def _group_summary(rows: list[dict], day_cols: list[str]) -> object:
    """
    组内小结一行：只数 / 涨跌家数 / 平均涨跌 / 平均操作分 / 有成本的平均盈亏。

    为什么摆在表格上面而不是让人自己看：一个组三十只票时，"今天这个组整体是
    红的还是绿的"要一行行扫。均值都**按有值的行算**（`n` 各自不同），
    不把空值当 0 —— 那会把平均涨跌往 0 拽。
    """
    def nums(key):
        return [float(r[key]) for r in rows
                if r.get(key) not in (None, '')]

    pct = nums('涨跌幅')
    oper = nums('操作分')
    pnl = nums('盈亏')
    up = sum(1 for v in pct if v > 0)
    down = sum(1 for v in pct if v < 0)
    parts = [f'{len(rows)} 只']
    if pct:
        parts.append(f'涨 {up} / 跌 {down} / 平 {len(pct) - up - down}')
        parts.append(f'平均涨跌 {sum(pct) / len(pct):+.2f}%')
    if oper:
        parts.append(f'平均操作分 {sum(oper) / len(oper):.1f}')
    if pnl:
        parts.append(f'{len(pnl)} 只有成本，平均盈亏 {sum(pnl) / len(pnl):+.2f}%')
    if day_cols:
        # day_cols 是**升序**的（从早到晚），跟表里列序一致
        parts.append(f'收盘列 {day_cols[0]} ~ {day_cols[-1]}')
    return html.Div('　·　'.join(parts), className='text-muted small mb-1')



def members_table(group_id):
    """
    组内成员 + 近 5 日收盘 + 多日涨跌/振幅 + 明日振幅预测 + 操作分。

    列来自三处，一条 SQL 取完，不在 Python 里循环查库：
    `config.watchlist_stock`（成员与成本）、`md.stock_daily`（近 5 日收盘、当日涨跌）、
    `fct.indicator_value`（日均涨跌/振幅、振幅预测）、`fct.score`（操作分）。

    近 5 日收盘用 `FILTER (WHERE trade_date = ...)` 横过来，列名直接就是
    `08-28` 这样的月-日 —— 表头写"收d1..d5"的话，隔一天再看就不知道 d1 是哪天。

    没数据的列留空，不用 0 填：0 和"没算出来"在涨跌幅上是两个意思。
    """
    if group_id in (None, ''):
        return html.Div('先在上面的「选组」里挑一个自选组', className='text-muted p-2')
    days = _last_days(5)
    if not days:
        return html.Div('md.stock_daily 还没有数据', className='text-muted p-2')
    latest = days[0]
    # 5 个日期是从库里查出来的 date 对象，不是外部输入；仍然只用它做 SELECT 的
    # 参数、不拼进 SQL 文本 —— 列名在 Python 侧改。
    # 日期不足 5 个（新库刚灌完几天）时用 latest 填满参数位：同一天被 FILTER
    # 命中两次只是重复取同一个收盘价，不会报错，也不会造出假的历史列。
    rows = engine.query(
        """
        WITH mem AS (
            SELECT ts_code, cost, note, added_at FROM config.watchlist_stock
             WHERE group_id = %(g)s
        ), px AS (
            SELECT d.ts_code,
                   max(d.close) FILTER (WHERE d.trade_date = %(d1)s) AS c1,
                   max(d.close) FILTER (WHERE d.trade_date = %(d2)s) AS c2,
                   max(d.close) FILTER (WHERE d.trade_date = %(d3)s) AS c3,
                   max(d.close) FILTER (WHERE d.trade_date = %(d4)s) AS c4,
                   max(d.close) FILTER (WHERE d.trade_date = %(d5)s) AS c5
              FROM md.stock_daily d
             WHERE d.ts_code IN (SELECT ts_code FROM mem)
               AND d.trade_date IN (%(d1)s, %(d2)s, %(d3)s, %(d4)s, %(d5)s)
             GROUP BY 1
        ), ind AS (
            SELECT ts_code,
                   max(value) FILTER (WHERE ind_key = 'chg10_avg') AS chg10_avg,
                   max(value) FILTER (WHERE ind_key = 'amp10_avg') AS amp10_avg,
                   max(value) FILTER (WHERE ind_key = 'chg20_avg') AS chg20_avg,
                   max(value) FILTER (WHERE ind_key = 'amp20_avg') AS amp20_avg,
                   max(value) FILTER (WHERE ind_key = 'amp_pred')  AS amp_pred
              FROM fct.indicator_value
             WHERE trade_date = %(d1)s
               AND ind_key IN ('chg10_avg','amp10_avg','chg20_avg','amp20_avg','amp_pred')
               AND ts_code IN (SELECT ts_code FROM mem)
             GROUP BY 1
        )
        SELECT m.ts_code AS 代码, s.name AS 名称,
               px.c1, px.c2, px.c3, px.c4, px.c5,
               d.pct_chg AS 涨跌幅,
               round(i.chg10_avg, 2) AS "10日涨跌",
               round(i.amp10_avg, 2) AS "10日振幅",
               round(i.chg20_avg, 2) AS "20日涨跌",
               round(i.amp20_avg, 2) AS "20日振幅",
               round(i.amp_pred, 2)  AS 明日振幅,
               round(o.value, 1) AS 操作分, o.quadrant AS 象限,
               m.cost AS 成本,
               -- 浮动盈亏（百分比）：只有填了成本的行才有值。成本为 0 的行用
               -- nullif 挡掉，不然是 division by zero；没填成本的行留空、不写 0
               -- —— 一个 0 看起来像"刚好回本"，其实是"没记成本"。
               -- 注意这段注释里**不能出现百分号**：psycopg 连注释一起扫占位符，
               -- 一个裸的百分号会让整条 SQL 报 incomplete placeholder（刚栽过）。
               round((d.close - m.cost) / nullif(m.cost, 0) * 100, 2) AS 盈亏,
               coalesce(m.note, '') AS 备注
          FROM mem m
          LEFT JOIN ref.security s ON s.ts_code = m.ts_code
          LEFT JOIN px ON px.ts_code = m.ts_code
          LEFT JOIN ind i ON i.ts_code = m.ts_code
          LEFT JOIN md.stock_daily d ON d.ts_code = m.ts_code
               AND d.trade_date = %(d1)s
          LEFT JOIN fct.score o ON o.ts_code = m.ts_code
               AND o.cap_key = 'oper_score' AND o.trade_date = %(d1)s
         -- 默认按**10 日振幅**从高到低（用户 2026-09-01 指定，改回 08-30 之前的
         -- 口径）。振幅是"这只票这段时间给不给机会"的直接度量：日均振幅小的票
         -- 无论操作分多高都做不出价差，所以打开这张表先看的是波动，操作分退成
         -- 同振幅时的次级键。算不出振幅的排最后（NULLS LAST），末尾用代码兜底，
         -- 保证行序确定 —— 否则每次刷新同值的几只票顺序会变，看着像数据在跳。
         ORDER BY i.amp10_avg DESC NULLS LAST, o.value DESC NULLS LAST, m.ts_code
        """,
        {'g': group_id, 'd1': latest, 'd2': days[1] if len(days) > 1 else latest,
         'd3': days[2] if len(days) > 2 else latest,
         'd4': days[3] if len(days) > 3 else latest,
         'd5': days[4] if len(days) > 4 else latest})
    if not rows:
        return html.Div('这个组还是空的，用上面的搜索框加票。',
                        className='text-muted p-2')

    # c1..c5 换成真实日期做列名，**列序按时间从早到晚**（左边 08-24，右边 08-28）：
    # 读一行收盘价是在读一条走势，从左往右就该是时间往前走。`days` 本身是降序
    # （SQL 参数要用 d1=最新那天做 JOIN），所以这里单独倒一份给列用。
    # 「详情」列是站内深链（/stock?code=…）：代码和名称那两个链接是去东方财富
    # 看行情的，而从名单里最常要的其实是本站的评分归因，两者都要留。
    day_cols = [d.strftime('%m-%d') for d in reversed(days)]
    # 列名 -> 原始列：day_cols[0] 是最早那天，对应 c{len(days)}
    px_keys = {c: f'c{len(days) - i}' for i, c in enumerate(day_cols)}
    out = []
    for r in rows:
        row = {}
        for k, v in r.items():
            if k in ('c1', 'c2', 'c3', 'c4', 'c5'):
                continue                      # 收盘价列在「名称」后面统一插
            row[k] = v
            if k == '名称':
                row['详情'] = ui.detail_link(r['代码'])
                for c in day_cols:
                    row[c] = r[px_keys[c]]
        # 原始代码单独留一列（表里隐藏）：显示的「代码」已经是 Markdown 链接，
        # 就地编辑成本/备注时没法从那串字面量里可靠地取回 ts_code。
        row[CODE_COL] = r['代码']
        out.append(ui.rank_within(row, day_cols))

    rank_cols = [ui.RANK_PREFIX + c for c in day_cols]
    color = (ui.rules_rank(list(zip(day_cols, rank_cols, strict=True)))
             + ui.rules_pct(PCT_COLS) + ui.rules_amp(AMP_COLS))
    # 默认按 10 日振幅降序（SQL 里已排）：先看"谁在动"，操作分是同振幅时的次级键。
    # 表头可点，再点一次反向 —— DataTable 的 sort_action='native'。

    # 成本 / 备注**就地改**：点开单元格就能填，不用回到上面的表单再走一遍加票
    # 流程。勾选框（multi）配下面那两个按钮做移出 / 移动 —— 票已经列在眼前了。
    return html.Div([
        _group_summary(out, day_cols),
        ui.table(out, id_='wl-mdt', page_size=200,

                 height=_fit_height(len(out)),
                 color=color, hidden_cols=[*rank_cols, CODE_COL],
                 editable_cols=('成本', '备注'), row_selectable='multi'),
    ])



def group_options():
    return stock_page.group_options()


def _first_group():
    return engine.scalar(
        'SELECT group_id FROM config.watchlist_group '
        'ORDER BY is_focus DESC, seq, name LIMIT 1')



# ---------------------------------------------------------------- 两栏维护台

def _cur_head(group_id, pred=None):
    """
    右栏抬头：当前正在改哪个组、有几只票、预测操作分多少。

    一直摆在眼前是有原因的：左栏一点就换组，右栏整块跟着变，如果不写着组名，
    改完一轮很容易搞不清刚才那只票加进了哪个组。
    """
    if group_id in (None, ''):
        return html.Span('还没选组 —— 在左边的组表里点一下任意一格',
                         className='text-warning small')
    r = engine.query_one(
        'SELECT g.name, g.is_focus, '
        '       (SELECT count(*) FROM config.watchlist_stock w '
        '         WHERE w.group_id = g.group_id) AS n '
        '  FROM config.watchlist_group g WHERE g.group_id = %s',
        (int(group_id),)) or {}
    bits = [html.Span('当前组　', className='text-muted'),
            html.Strong(('★ ' if r.get('is_focus') else '')
                        + str(r.get('name') or f'#{group_id}')),
            html.Span(f'　·　{r.get("n") or 0} 只', className='text-muted')]
    if pred not in (None, ''):
        bits.append(html.Span(f'　·　预测操作分 {float(pred):+.1f}',
                              className='text-muted'))
    return html.Div(bits)


def _left_pane(group_id, rows=None):
    """左栏：一行建组 + 可就地编辑的组表 + 删组（modal 确认）。"""
    return html.Div([
        # 当前组存 Store 而不是只靠表格的 selected_rows：建组/删组后表格会重画，
        # 行下标全变，Store 里的 group_id 不受影响。
        dcc.Store(id='wl-gid', data=group_id),
        dbc.InputGroup([
            dbc.Input(id='wl-gnew-name', size='sm', autocomplete='off',
                      debounce=True, placeholder='新组名，回车即建'),
            dbc.Button('+ 建组', id='wl-gnew', color='success', size='sm',
                       n_clicks=0),
        ], size='sm', className='mb-2'),
        html.Div(groups_table(group_id, rows), id='wl-gwrap'),
        dbc.Row([
            dbc.Col(html.Div('组名 / 排序 / 备注 双击就地改；组名里加删 ★ '
                             '＝切换重点；点任意一格就换组，圆点是当前组',
                             className='text-muted small'),
                    className='d-flex align-items-center'),
            dbc.Col(dbc.Button('从同花顺导入', id='wl-ths', color='primary',
                               outline=True, size='sm', n_clicks=0,
                               title='读同花顺安装目录的 stockblock.ini，'
                                     '整体替换这里的分组'),
                    width='auto'),
            dbc.Col(dbc.Button('删除选中组', id='wl-gdel', color='danger',
                               outline=True, size='sm', n_clicks=0),
                    width='auto'),
        ], className='g-2 mt-2'),
        # 从同花顺导入是**全量覆盖**：先删掉这里所有的组（成员级联删除）再照
        # 同花顺重建。所以和删组一样要过一道 modal，而且弹窗里必须把
        # "将删几组、将建几组、备份写到哪"说清楚 —— 光写"确认导入？"等于没问。
        dbc.Modal([
            dbc.ModalHeader(dbc.ModalTitle('从同花顺导入（全量覆盖）')),
            dbc.ModalBody(id='wl-ths-body'),
            dbc.ModalFooter([
                dbc.Button('取消', id='wl-ths-no', color='secondary',
                           outline=True, size='sm', n_clicks=0),
                dbc.Button('确认覆盖', id='wl-ths-yes', color='danger',
                           size='sm', n_clicks=0),
            ]),
        ], id='wl-ths-modal', is_open=False, centered=True, size='lg'),
        # 删组必须问一次：config.watchlist_stock 是 ON DELETE CASCADE，删组会把
        # 组里的票一起带走，而这些名单是手工攒的、别处没有副本。
        # 用 modal 而不是常驻勾选框：勾选框要一直占一行，还容易上次勾了忘了取消。
        dbc.Modal([
            dbc.ModalHeader(dbc.ModalTitle('删组确认')),
            dbc.ModalBody(id='wl-gdel-body'),
            dbc.ModalFooter([
                dbc.Button('取消', id='wl-gdel-no', color='secondary',
                           outline=True, size='sm', n_clicks=0),
                dbc.Button('确认删除', id='wl-gdel-yes', color='danger',
                           size='sm', n_clicks=0),
            ]),
        ], id='wl-gdel-modal', is_open=False, centered=True),
    ])


def _right_pane(group_id, pred=None):
    """右栏：抬头 + 四段回车加票 + 成员表（成本/备注就地改、勾选移出/移动）。"""
    return html.Div([
        dcc.Store(id='wl-focus'),
        # 回车走到第几步：记着"这段文本已经搜过了"。同一个框里第一次回车＝搜，
        # 第二次回车＝往下走一步，靠的就是这个 Store，不是猜。
        dcc.Store(id='wl-step', data={'text': None}),
        html.Div(id='wl-focus-sink', style={'display': 'none'}),
        html.Div(_cur_head(group_id, pred), id='wl-cur', className='mb-2'),

        html.Div('① 搜票：代码 / 名称 / 拼音首字母 / 中间几个字都行，'
                 '**打完按回车才搜**（中文可以正常输入）；'
                 '一次贴多个代码＝批量加（只收股票和 ETF）',
                 className='text-muted small mb-1'),
        # debounce=True：不打一个字就往服务端送一次 value。
        # 边打边搜有两个真问题：中文输入法**打不完一个字**就被回传的
        # value 打断（用户报的"连一个完整的中文都输入不了"），而且每次
        # 回调都要重画候选块、光标会被搅掉。现在只在回车/失焦时回传。
        dbc.Input(id='wl-q', size='sm', autocomplete='off', debounce=True,
                  placeholder='600519 / 贵州茅台 / gzmt / 茅台'
                              '　　批量：600519 000001 300750'),

        html.Div(['② 候选（回车后出现，默认选中第一条；换一只直接点它）　',
                  html.Span(id='wl-cand-hint', className='text-muted',
                            children='（在①里打字，按回车搜）')],
                 className='fw-semibold small mt-2 mb-1'),
        html.Div(dbc.RadioItems(id='wl-cand', options=[], value=None,
                                labelClassName='small d-block mb-0'),
                 style={'maxHeight': '132px', 'overflowY': 'auto'},
                 className='border rounded p-2'),

        dbc.Row([
            dbc.Col(dbc.InputGroup([
                dbc.InputGroupText('③ 成本', className='py-1'),
                dbc.Input(id='wl-cost', type='number', size='sm',
                          placeholder='可空，回车＝去④备注'),
            ]), width=12, lg=4),
            dbc.Col(dbc.Input(id='wl-snote', size='sm',
                              placeholder='④ 备注（可空），回车＝加入本组'),
                    width=12, lg=5),
            dbc.Col(dbc.Button('加入本组', id='wl-sadd', color='success',
                               size='sm', n_clicks=0), width='auto'),
        ], className='g-2 mt-1'),
        html.Div('键盘流（回车一路到底，全程不用碰鼠标）：①里打字 → 回车＝搜出'
                 '候选并默认选中第一条（**只搜到一只时直接跳③**）→ 回车＝跳到'
                 '③成本 → 回车＝跳到④备注 → 回车＝加入本组，三个框清空、'
                 '光标回到①。批量：在①里贴一串代码，第一次回车就全部加入。'
                 '成本和备注也可以先不填，加进来之后在下面表里双击就地改。',
                 className='text-muted small mt-1'),

        html.Hr(className='my-2'),
        dbc.Row([
            dbc.Col(html.Div('勾选下面表格左边的框，再用这两个按钮；'
                             '成本 / 备注双击就地改',
                             className='text-muted small'),
                    className='d-flex align-items-center'),
            dbc.Col(dbc.Button('移出本组', id='wl-srm', color='danger',
                               outline=True, size='sm', n_clicks=0),
                    width='auto'),
            dbc.Col(dcc.Dropdown(id='wl-tgroup', options=group_options(),
                                 placeholder='移动到哪个组'), width=12, lg=3),
            dbc.Col(dbc.Button('移过去', id='wl-smove', color='primary',
                               size='sm', n_clicks=0), width='auto'),
        ], className='g-2 mb-2'),
        html.Div(members_table(group_id), id='wl-mwrap'),
    ])


def render():
    # 首屏只算一遍组行：预测操作分是一段 20 日回归，`groups_table` 和抬头都要用。
    rows = group_rows()
    gid = _first_group()
    pred = next((r['预测操作分'] for r in rows
                 if gid is not None and int(r['组号']) == int(gid)), None)
    charts = {c['chart_key']: c for c in reg_repo.charts()}
    return html.Div([
        ui.alert('左边是组、右边是组里的票：**点组表里任意一格就换组**，'
                 '右边整块跟着变（最左边的圆点是"当前组"的指示灯）。'
                 '组名 / 排序 / 备注 和票的 成本 / 备注 都是**双击就地改**，'
                 '不用回到表单走一遍。组默认按**10日均涨跌bp**从低到高排 —— '
                 '那是"组内每票近 10 个交易日日均涨跌幅的组平均、乘 100 以基点显示"，'
                 '先看被砸得最狠的组；票默认按**10日振幅**从高到低排。'
                 '每列的含义、能当什么参考，点卡片标题右边的「说明」。'
                 f'{FOCUS_HINT}。', 'light'),
        html.Div(id='wl-msg', className='small mb-2'),
        dbc.Row([
            dbc.Col(ui.card(f'自选组（{len(rows)} 个 · 按10日均涨跌升序）',
                            _left_pane(gid, rows),
                            help_key='watchlist.groups',
                            note_id='note-wl-groups'),
                    width=12, lg=4, className='mb-3'),
            dbc.Col(ui.card('组内成员维护', _right_pane(gid, pred),
                            help_key='watchlist.members',
                            note_id='note-wl-members'),
                    width=12, lg=8, className='mb-3'),
        ], className='g-3'),
        html.Hr(),
        html.H6('自选看板（卡片来自 config.page_layout）', className='text-muted'),
        ui.render_page(reg_repo.layout('watchlist'), charts),
    ])



# ---------------------------------------------------------------- 写

# 写操作单独拎成函数，而不是埋在回调里：回调是闭包，埋在里面就只能靠
# 点页面来验证。拎出来之后可以直接在脚本里跑一遍增删改再回滚，SQL 有没有写错
# 当场就知道（`ON CONFLICT` 里的表名限定、`= ANY(%s)` 传列表，都栽过）。

def create_group(name: str, seq: int = 100, is_focus: bool = False,
                 note: str | None = None) -> int:
    """
    建组，**返回新组的 group_id**（不是影响行数）。

    要这个 id 是因为建完立刻要切到新组 —— 建一个空组本身没意义，下一步一定是
    往里面加票。返回行数的话页面还得再查一次"最后建的那个是谁"，重名时就猜错了。

    `owner` **必须显式写**，不能让它吃库里的默认值 `current_user`：网站连库用的
    是 ft_app 角色，而历史 24 个组都是 'fin'。唯一约束是 `(owner, name)`，两个
    owner 就是两套命名空间 —— 实测（2026-08-29）不写 owner 时，把新组改成和老组
    同名**不报错**，列表里就出现两行同名的组。
    """
    r = engine.query_one(
        'INSERT INTO config.watchlist_group (name, owner, seq, is_focus, note) '
        'VALUES (%s, %s, %s, %s, %s) RETURNING group_id',
        (name, wl_repo.DEFAULT_OWNER, int(seq), bool(is_focus),
         (note or '').strip() or None),
        role='app')
    return int(r['group_id'])


def update_group_field(group_id: int, col: str, value) -> int:
    """
    改组的**一个**字段（就地编辑一格就调一次）。

    `col` 是表格里的中文列名，必须命中 `GROUP_EDITABLE` —— 库字段名要拼进
    SQL 文本（PG 不允许把列名当参数），所以只能从那张写死的白名单里取，
    不能用页面送上来的字符串。值本身照旧走参数。

    **`组名` 是唯一一个一格改两列的**（2026-08-30）：单元格里的 `★` 前缀
    就是 `is_focus`，所以 `★ 打板` 要同时写 `name='打板'` 和 `is_focus=true`。
    只写 name 的话，星号一提交就消失（下一次刷新按库里的 false 重画），
    看起来像"点了没反应"。
    """
    if col not in GROUP_EDITABLE:
        raise ValueError(f'「{col}」这一列不能改')
    field, cast = GROUP_EDITABLE[col]
    val = cast(value)
    if field == 'name':
        if not val:
            raise ValueError('组名不能为空')
        return engine.execute(
            'UPDATE config.watchlist_group SET name = %s, is_focus = %s '
            ' WHERE group_id = %s',
            (val, split_focus(value)[1], int(group_id)))
    return engine.execute(
        f'UPDATE config.watchlist_group SET {field} = %s WHERE group_id = %s',
        (val, int(group_id)))


def update_stock_field(group_id: int, ts_code: str, col: str, value) -> int:
    """改组内一只票的成本或备注。列名白名单同上，理由一样。"""
    if col not in STOCK_EDITABLE:
        raise ValueError(f'「{col}」这一列不能改')
    field, cast = STOCK_EDITABLE[col]
    val = cast(value)
    if field == 'cost' and val is not None and val < 0:
        raise ValueError('成本不能是负数')
    return engine.execute(
        f'UPDATE config.watchlist_stock SET {field} = %s '
        ' WHERE group_id = %s AND ts_code = %s',
        (val, int(group_id), ts_code))


def delete_group(group_id: int) -> tuple[int, int]:

    """返回 (删掉的组数, 连带删掉的成员数)。成员数要在删之前数。"""
    n = engine.scalar(
        'SELECT count(*) FROM config.watchlist_stock WHERE group_id = %s',
        (int(group_id),)) or 0
    g = engine.execute('DELETE FROM config.watchlist_group WHERE group_id = %s',
                       (int(group_id),))
    return g, int(n)


def add_stock(group_id: int, ts_code: str, cost=None,
              note: str | None = None) -> int:
    """
    加票。已经在组里就更新成本/备注，不报"重复加入" ——
    重复点一下不该是个错误。
    """
    return engine.execute(
        """
        INSERT INTO config.watchlist_stock (group_id, ts_code, cost, note)
        VALUES (%s, %s, %s, %s)
        ON CONFLICT (group_id, ts_code) DO UPDATE
           SET cost = coalesce(EXCLUDED.cost, watchlist_stock.cost),
               note = coalesce(EXCLUDED.note, watchlist_stock.note)
        """, (int(group_id), ts_code, cost, (note or '').strip() or None))


def parse_tokens(text: str | None) -> list[str]:
    """把输入框里的内容切成 token。一个 token＝单只，多个＝批量。"""
    return [t for t in _SPLIT.split((text or '').strip()) if t]


def resolve_code(token: str) -> tuple[str | None, str]:
    """
    一个 token → `ts_code`。返回 `(代码, 名称)`；解析不了时返回 `(None, 原因)`。

    先按完整代码/6 位数字精确匹配，再退回搜索。**6 位数字可能同时命中股票和
    指数**（`000001` = 平安银行 000001.SZ / 上证指数 000001.SH，库里有 45 个这样
    的重码），所以按 `stock → etf → 其它` 分档，同一档里还有多个才算不唯一 ——
    静悄悄挑一个是最坏的结果：名单里会多出一只你没打算加的票。
    """
    t = (token or '').strip()
    if not t:
        return None, '空'
    rows = engine.query(
        """
        SELECT ts_code, name, asset_type FROM ref.security
         WHERE upper(ts_code) = upper(%(t)s) OR symbol = %(t)s
         ORDER BY CASE asset_type WHEN 'stock' THEN 0 WHEN 'etf' THEN 1
                                  ELSE 2 END,
                  is_active DESC, ts_code
        """, {'t': t})
    addable = [r for r in rows if r['asset_type'] in ADDABLE]
    if rows and not addable:
        kind = KIND_CN.get(rows[0]['asset_type'], rows[0]['asset_type'])
        return None, f"{t}：{rows[0]['name']} 是{kind}，自选池只收股票和 ETF"
    if addable:
        top = [r for r in addable if r['asset_type'] == addable[0]['asset_type']]
        if len(top) == 1:
            return addable[0]['ts_code'], addable[0]['name'] or ''
        return None, f'{t}：{len(top)} 个同码标的，请写完整代码'
    hits = stock_page.search_rows(t, limit=3, kinds=ADDABLE)
    if len(hits) == 1:
        return hits[0]['ts_code'], hits[0]['name'] or ''
    if not hits:
        return None, f'{t}：找不到'
    return None, f'{t}：不唯一（{len(hits)} 个以上候选）'


def add_stocks(group_id: int, tokens: list[str],
               note: str | None = None) -> tuple[list[str], list[str]]:
    """
    批量加票，返回 `(成功列表, 失败说明列表)`。

    **批量不写成本**：成本是每只票各自的买入价，把同一个数字按到 N 只票上
    就是造假数据。备注会一起写（同一批的备注通常就是同一句）。
    """
    ok, bad = [], []
    for t in tokens:
        code, info = resolve_code(t)
        if code is None:
            bad.append(info)
            continue
        add_stock(group_id, code, None, note)
        ok.append(f'{code} {info}'.strip())
    return ok, bad


def remove_stocks(group_id: int, codes: list[str]) -> int:
    return engine.execute(
        'DELETE FROM config.watchlist_stock '
        'WHERE group_id = %s AND ts_code = ANY(%s)',
        (int(group_id), list(codes)))



def move_stocks(src_group: int, dst_group: int, codes: list[str]) -> int:
    """
    换组。**先插后删**，不是 UPDATE group_id —— 目标组里已经有同一只票时
    UPDATE 会撞主键 (group_id, ts_code) 直接报错；插入用
    `ON CONFLICT DO NOTHING` 吞掉"目标组已有"，再把源组那份删掉。
    """
    engine.execute(
        """
        INSERT INTO config.watchlist_stock (group_id, ts_code, cost, note)
        SELECT %s, ts_code, cost, note FROM config.watchlist_stock
         WHERE group_id = %s AND ts_code = ANY(%s)
        ON CONFLICT (group_id, ts_code) DO NOTHING
        """, (int(dst_group), int(src_group), list(codes)))
    return remove_stocks(src_group, codes)


CAND_IDLE = '（在①里打字，按回车搜）'

# 什么都不动：Dash 里"这个 Output 不变"就是 no_update，前端连重画都不会做。
# 数量必须和下面回调的 Output 列表一致 —— 少一个 Dash 直接报长度不匹配。
N_OUT = 17
NOOP = (no_update,) * N_OUT


def _diff_cell(prev, cur):
    """
    两份 DataTable `data` 的差异 → `(行下标, 列名, 新值, 旧值)`，没差异返回 None。

    就地编辑只能这么拿：DataTable 不会告诉你"哪一格被改了"，只会在 `data` 变化
    时把旧值塞进 `data_previous`。行数不一样说明这是**整表重画**（建组/删组/换组）
    而不是有人改了一格，直接当没差异 —— 否则会把重画误判成一次编辑去写库。
    """
    if not prev or not cur or len(prev) != len(cur):
        return None
    for i, (a, b) in enumerate(zip(prev, cur, strict=True)):
        for k, v in b.items():
            if a.get(k) != v:
                return i, k, v, a.get(k)
    return None


def _refresh(msg, group_id, tone='text-success', *, reset=False, focus=None,
             seq=0, clear_new=False, cand=None, hint=None, step=None,
             groups=False, members=False, opts=False, head=no_update,
             gid=no_update, modal=no_update, modal_body=no_update,
             rows=None):
    """
    一次操作之后要刷的东西，**全部在同一个回调里返回**。

    Dash 不允许两个回调写同一个 Output，而这一页里几乎每个动作都要同时动
    好几个地方（加一只票既改成员表又改组的成员数），所以只能合成一个。

    刷哪些是**按需**的 —— 默认一个都不刷。这一条是上一轮修"每次回车整页闪"
    留下的规矩：库没动就别重建 DOM。

    - `groups=True`：重建左栏组表（成员数 / 预测操作分 / 行序可能变了）
    - `members=True`：重建右栏成员表（成员或成本变了，盈亏要重算）
    - `opts=True`：只刷「移动到哪个组」的下拉（组名或组的增删变了）
    - `head=`：右栏抬头（换组、改组名时给）
    - `reset=True`：写库成功后清空①③④和候选
    - `clear_new=True`：建组成功后清空左上那个组名框，好接着建下一个
    - `focus`/`seq`：把光标送到哪个元素；seq 只是让 Store 每次都变、好触发 clientside

    ★就地编辑成功时**一定不要**传 `groups=True`：重建表格会把正在编辑的
    单元格连同光标一起换掉，连着改两行就没法做了。
    """
    cand_opts, cand_val = cand if cand is not None else (no_update, no_update)
    if reset:
        cand_opts, cand_val = [], None
        hint = hint if hint is not None else CAND_IDLE
        step = step if step is not None else {'text': None}
    return (
        groups_table(group_id, rows) if groups else no_update,
        members_table(group_id) if members else no_update,
        html.Span(msg, className=tone) if msg != '' else '',
        head,
        group_options() if (groups or opts) else no_update,
        *(('', None, '') if reset else (no_update, no_update, no_update)),
        {'id': focus, 'n': seq} if focus else no_update,
        '' if clear_new else no_update,
        cand_opts, cand_val,
        no_update if hint is None else hint,
        no_update if step is None else step,
        gid, modal, modal_body)


def search_candidates(text: str | None) -> tuple[list[dict], str | None, str]:
    """
    文本 → (候选 options, 默认选中, 提示)。**服务端搜、服务端显示**，不经过
    任何浏览器端过滤 —— 拼音首字母是库里的列，浏览器匹配不到（原来那个
    `dcc.Dropdown` 就死在这）。默认选中第一条：命中方式已经在 SQL 里排过序。

    拎成模块级函数而不是留在回调里：这样可以直接在脚本里断言"gzmt 能搜到
    贵州茅台"，不用去点页面。
    """
    toks = parse_tokens(text)
    if not toks:
        return [], None, CAND_IDLE
    if len(toks) > 1:
        return [], None, f'批量模式：{len(toks)} 个 token，回车即全部加入'
    rows = stock_page.search_rows(toks[0], limit=20, kinds=ADDABLE)
    if not rows:
        return [], None, '没找到匹配的股票/ETF'
    opts = [{'label': stock_page.label_of(r), 'value': r['ts_code']}
            for r in rows]
    return opts, opts[0]['value'], f'{len(opts)} 个候选，回车确认'


def selected_codes(data, selected_rows) -> list[str]:
    """
    成员表里勾中的行 → `ts_code` 列表。

    代码要从隐藏的 `_code` 列取，**不能从显示的「代码」列取** —— 那一列已经被
    `ui.table` 换成了 `[600519.SH](https://…)` 这样的 Markdown 链接。
    """
    out = []
    for i in (selected_rows or []):
        if 0 <= i < len(data or []):
            code = (data[i] or {}).get(CODE_COL)
            if code:
                out.append(code)
    return out


def register_callbacks(app) -> None:
    # 焦点没法用普通 Output 设置，只能到 DOM 里抓元素。延时一拍是必要的：
    # 回调返回的新 children 还没渲染完就 focus，会打在旧节点上。
    app.clientside_callback(
        """
        function(sig) {
            if (!sig || !sig.id) { return window.dash_clientside.no_update; }
            setTimeout(function () {
                var el = document.getElementById(sig.id);
                if (el) { el.focus(); if (el.select) { el.select(); } }
            }, 80);
            return '';
        }
        """,
        Output('wl-focus-sink', 'children'),
        Input('wl-focus', 'data'),
        prevent_initial_call=True)

    # 点组表里任意一格＝换组（2026-08-30 加）。
    # 这个回调只做一件事：把 active_cell 的行号回写成 selected_rows，
    # 让下面那个大回调的「换组」分支照原样跑一遍 —— 换组的逻辑仍然只有一份。
    #
    # 为什么不在大回调里直接收 active_cell：那样得多返回一个
    # Output('wl-gdt', 'selected_rows')，而大回调里到处 return NOOP，
    # 每个分支都要跟着补一个 no_update；更麻烦的是它已经输出 `wl-gwrap`，
    # 重建组表会把光标从正在编辑的单元格里踢出去。
    @app.callback(Output('wl-gdt', 'selected_rows'),
                  Input('wl-gdt', 'active_cell'),
                  State('wl-gdt', 'selected_rows'),
                  prevent_initial_call=True)
    def _cell_selects_row(cell, sel):
        if not cell or cell.get('row') is None:
            return no_update
        row = int(cell['row'])
        # 已经选中这一行就什么都不做：否则双击进单元格改字时，
        # 第一次点击会白白触发一次换组 + 右栏重建。
        if sel and int(sel[0]) == row:
            return no_update
        return [row]

    @app.callback(Output('wl-gwrap', 'children'),
                  Output('wl-mwrap', 'children'),
                  Output('wl-msg', 'children'),
                  Output('wl-cur', 'children'),
                  Output('wl-tgroup', 'options'),
                  Output('wl-q', 'value'),
                  Output('wl-cost', 'value'),
                  Output('wl-snote', 'value'),
                  Output('wl-focus', 'data'),
                  Output('wl-gnew-name', 'value'),
                  Output('wl-cand', 'options'),
                  Output('wl-cand', 'value'),
                  Output('wl-cand-hint', 'children'),
                  Output('wl-step', 'data'),
                  Output('wl-gid', 'data'),
                  Output('wl-gdel-modal', 'is_open'),
                  Output('wl-gdel-body', 'children'),
                  # ---- 左栏 ----
                  Input('wl-gnew', 'n_clicks'),
                  Input('wl-gnew-name', 'n_submit'),
                  Input('wl-gdel', 'n_clicks'),
                  Input('wl-gdel-no', 'n_clicks'),
                  Input('wl-gdel-yes', 'n_clicks'),
                  # 换组＝点组表最左边那个圆点；就地编辑＝data_previous。
                  # ★两者必须分开认：同一个组件的两个 prop，靠 triggered 的
                  # prop_id 区分，不然改一格会被当成换了一次组。
                  Input('wl-gdt', 'selected_rows'),
                  Input('wl-gdt', 'data_previous'),
                  # ---- 右栏 ----
                  Input('wl-mdt', 'data_previous'),
                  Input('wl-srm', 'n_clicks'),
                  Input('wl-smove', 'n_clicks'),
                  Input('wl-q', 'n_submit'),
                  Input('wl-cost', 'n_submit'),
                  Input('wl-snote', 'n_submit'),
                  Input('wl-sadd', 'n_clicks'),
                  State('wl-gid', 'data'),
                  State('wl-gdt', 'data'),
                  State('wl-mdt', 'data'),
                  State('wl-mdt', 'selected_rows'),
                  State('wl-gnew-name', 'value'),
                  State('wl-q', 'value'), State('wl-cand', 'value'),
                  State('wl-cost', 'value'), State('wl-snote', 'value'),
                  State('wl-tgroup', 'value'),
                  State('wl-focus', 'data'), State('wl-step', 'data'),
                  prevent_initial_call=True)
    def _act(_gnew, _gnew_sub, _gdel, _gdel_no, _gdel_yes,
             gsel_rows, gprev, mprev,
             _srm, _smove, _q_sub, _cost_sub, _snote_sub, _sadd,
             gid, gdata, mdata, msel, new_name,
             qtext, cand, cost, snote, tgroup, fdata, step):
        """
        这一页所有的写操作 + 换组 + 候选搜索，一个回调。

        **回车的楼梯**（用户明确要的操作方式）：①里打完字回车＝搜候选并默认
        选中第一条（只搜到一只时直接跳③，学旧系统 financeWeb 的做法）→
        回车＝跳到③成本 → 回车＝跳到④备注 → 回车＝加入本组。
        "同一个框里第一次回车是搜、第二次是往下走"靠 `wl-step` 里记的 `text`
        判断：文本没变且已经有候选，就是往下走。
        """
        trig = callback_context.triggered_id
        prop = (callback_context.triggered[0]['prop_id'].rsplit('.', 1)[-1]
                if callback_context.triggered else '')
        seq = int((fdata or {}).get('n') or 0) + 1
        try:
            # ---------------- 左栏：换组 ----------------
            if trig == 'wl-gdt' and prop == 'selected_rows':
                if not gsel_rows or not gdata:
                    return NOOP
                row = gdata[gsel_rows[0]] if gsel_rows[0] < len(gdata) else None
                if not row:
                    return NOOP
                new_gid = int(row['组号'])
                # 预测操作分直接从表格那一行读，不再跑一遍 20 日回归 ——
                # 换组要快，一点就换。
                return _refresh('', new_gid, members=True, gid=new_gid,
                                head=_cur_head(new_gid, row.get('预测操作分')))

            # ---------------- 左栏：就地编辑组 ----------------
            if trig == 'wl-gdt' and prop == 'data_previous':
                d = _diff_cell(gprev, gdata)
                if d is None:
                    return NOOP
                i, col, new, old = d
                row_gid = int(gdata[i]['组号'])
                name_now = gdata[i].get('组名')
                try:
                    update_group_field(row_gid, col, new)
                except Exception as exc:  # noqa: BLE001
                    # 写不进去（重名、排序填了个字母）就把表格拉回库里的样子，
                    # 否则页面上留着一个库里并不存在的值，下次刷新才发现。
                    return _refresh(f'没改成：{exc}（已还原）', gid,
                                    'text-danger', groups=True)
                # 改组名要顺带更新右栏抬头和「移动到哪个组」的下拉；
                # 但**绝不重建组表** —— 光标还在那一格里。
                head = (_cur_head(row_gid, gdata[i].get('预测操作分'))
                        if col == '组名' and str(row_gid) == str(gid)
                        else no_update)
                return _refresh(f'「{name_now}」的{col}：{old or "空"} → '
                                f'{new or "空"}', gid, 'text-success',
                                opts=(col == '组名'), head=head)

            # ---------------- 右栏：就地编辑成员 ----------------
            if trig == 'wl-mdt' and prop == 'data_previous':
                d = _diff_cell(mprev, mdata)
                if d is None:
                    return NOOP
                i, col, new, old = d
                code = (mdata[i] or {}).get(CODE_COL)
                if not code:
                    return NOOP
                try:
                    update_stock_field(gid, code, col, new)
                except Exception as exc:  # noqa: BLE001
                    return _refresh(f'没改成：{exc}（已还原）', gid,
                                    'text-danger', members=True)
                # 成本改了盈亏那一列就得重算 —— 留着旧盈亏比空着更误导。
                # 备注改了什么都不用重建。
                return _refresh(f'{code} 的{col}：{old or "空"} → {new or "空"}',
                                gid, 'text-success', members=(col == '成本'))

            # ---------------- 左栏：建组 ----------------
            if trig in ('wl-gnew', 'wl-gnew-name'):
                name = (new_name or '').strip()
                if not name:
                    return _refresh('组名不能为空', gid, 'text-danger',
                                    focus='wl-gnew-name', seq=seq)
                new_gid = create_group(name)
                # 建完直接切到新组：接下来要干的事就是往它里面加票。
                # 排序 / 备注 / ★ 建完在表里就地改，建组时只逼一个必填项。
                return _refresh(f'已建组「{name}」，已切到它',
                                new_gid, groups=True, members=True,
                                gid=new_gid, clear_new=True,
                                head=_cur_head(new_gid),
                                focus='wl-gnew-name', seq=seq)

            # ---------------- 左栏：删组（两步）----------------
            if trig == 'wl-gdel':
                if gid in (None, ''):
                    return _refresh('先在左边点一个组', gid, 'text-danger')
                r = engine.query_one(
                    'SELECT g.name, '
                    '       (SELECT count(*) FROM config.watchlist_stock w '
                    '         WHERE w.group_id = g.group_id) AS n '
                    '  FROM config.watchlist_group g WHERE g.group_id = %s',
                    (int(gid),)) or {}
                return _refresh('', gid, modal=True, modal_body=html.Div([
                    html.Div(['要删掉组 ', html.Strong(r.get('name') or ''),
                              f'，连里面 {r.get("n") or 0} 只票一起。']),
                    html.Div('自选名单是手工攒的，删了别处没有副本，恢复不了。',
                             className='text-danger small mt-1'),
                ]))

            if trig == 'wl-gdel-no':
                return _refresh('', gid, modal=False)

            if trig == 'wl-gdel-yes':
                if gid in (None, ''):
                    return _refresh('没有选中的组', gid, 'text-danger',
                                    modal=False)
                _, n = delete_group(gid)
                left = _first_group()
                return _refresh(f'已删掉这个组，连带 {n} 只成员', left,
                                'text-muted', groups=True, members=True,
                                gid=left, head=_cur_head(left), modal=False)

            # ---------------- 右栏：加票（回车楼梯 / 点按钮）----------------
            if trig in ('wl-q', 'wl-cost', 'wl-snote', 'wl-sadd'):
                if gid in (None, ''):
                    return _refresh('先在左边点一个组', gid, 'text-danger')
                toks = parse_tokens(qtext)
                searched = (step or {}).get('text')

                # 批量：贴了多个 token，第一次回车就全部加入
                if len(toks) > 1:
                    ok, bad = add_stocks(gid, toks, snote)
                    msg = f'批量加入 {len(ok)} 只：' + '、'.join(ok[:8])
                    if len(ok) > 8:
                        msg += f' 等 {len(ok)} 只'
                    if bad:
                        msg += f'；{len(bad)} 个没加：' + '；'.join(bad[:5])
                    tone = 'text-warning' if bad else 'text-success'
                    return _refresh(msg or '一个都没解析出来', gid, tone,
                                    reset=True, focus='wl-q', seq=seq,
                                    groups=True, members=True,
                                    head=_cur_head(gid))

                # ①里回车：文本变了（或还没搜过）＝这一下是"搜"
                if trig == 'wl-q':
                    if not toks:
                        return _refresh('①里还没有内容', gid, 'text-warning',
                                        focus='wl-q', seq=seq,
                                        cand=([], None), hint=CAND_IDLE,
                                        step={'text': None})
                    if qtext != searched or not cand:
                        opts, first, hint = search_candidates(qtext)
                        if not opts:
                            return _refresh(hint, gid, 'text-warning',
                                            focus='wl-q', seq=seq,
                                            cand=([], None), hint=hint,
                                            step={'text': None})
                        # **只搜到一只就不用再确认了**，直接进③。学 financeWeb：
                        # 命中唯一时它连候选下拉都不展开。多一次回车没有信息量。
                        if len(opts) == 1:
                            return _refresh(
                                f'唯一命中 {opts[0]["label"]}，'
                                f'回车去④备注（成本可留空）',
                                gid, 'text-muted', focus='wl-cost', seq=seq,
                                cand=(opts, first), hint=hint,
                                step={'text': qtext})
                        return _refresh(
                            f'{hint}：默认 {opts[0]["label"]}。'
                            f'再回车去③成本，或直接点②里换一只',
                            gid, 'text-muted', focus='wl-q', seq=seq,
                            cand=(opts, first), hint=hint,
                            step={'text': qtext})
                    # 文本没变、候选已就位＝这一下是"往下走"
                    return _refresh(f'已选中 {cand}，回车去④备注（成本可留空）',
                                    gid, 'text-muted',
                                    focus='wl-cost', seq=seq)

                # ③成本里回车：只是往下走一步，不写库
                if trig == 'wl-cost':
                    if not cand:
                        return _refresh('还没有选中的票：回到①按回车搜',
                                        gid, 'text-warning',
                                        focus='wl-q', seq=seq)
                    return _refresh('回车＝加入本组（备注可留空）', gid,
                                    'text-muted', focus='wl-snote', seq=seq)

                # ④备注里回车 / 点按钮＝真的写库。
                # 候选没就位时（手打完整代码后直接点按钮）再解析一次。
                code, info = (cand, '') if cand else resolve_code(
                    toks[0] if toks else '')
                if code is None:
                    return _refresh(f'加不了：{info}', gid, 'text-danger',
                                    focus='wl-q', seq=seq)
                add_stock(gid, code, cost, snote)
                tail = f'，成本 {cost}' if cost not in (None, '') else ''
                return _refresh(f'{code} {info} 已在本组{tail}'.replace('  ', ' '),
                                gid, reset=True, focus='wl-q', seq=seq,
                                groups=True, members=True,
                                head=_cur_head(gid))

            # ---------------- 右栏：勾选后移出 / 移动 ----------------
            if trig == 'wl-srm':
                codes = selected_codes(mdata, msel)
                if gid in (None, '') or not codes:
                    return _refresh('先勾上要移出的票（表格最左边那一列）',
                                    gid, 'text-danger')
                n = remove_stocks(gid, codes)
                return _refresh(f'移出 {n} 只：' + '、'.join(codes[:6]),
                                gid, 'text-muted', groups=True, members=True,
                                head=_cur_head(gid))

            if trig == 'wl-smove':
                codes = selected_codes(mdata, msel)
                if gid in (None, '') or not codes:
                    return _refresh('先勾上要移动的票', gid, 'text-danger')
                if tgroup in (None, ''):
                    return _refresh('还没选「移动到哪个组」', gid, 'text-danger')
                if str(tgroup) == str(gid):
                    return _refresh('目标组和当前组是同一个', gid, 'text-danger')
                n = move_stocks(gid, tgroup, codes)
                return _refresh(f'移走 {n} 只到目标组', gid, groups=True,
                                members=True, head=_cur_head(gid))
        except Exception as exc:  # noqa: BLE001
            # 唯一约束（同 owner 下重名）之类的库层错误直接摊开给人看，
            # 比"操作失败"有用。库要么没动、要么整笔回滚了，所以不重建表。
            return _refresh(f'操作失败：{exc}', gid, 'text-danger')

        return NOOP

    # ---------------------------------------------- 从同花顺导入（全量覆盖）
    # 刻意**不塞进上面那个大回调**：那个回调已经有 15 个 Output，再加两个
    # 弹窗输出就要在它每一个 `return NOOP` 分支上补 no_update。
    # 这里用 `allow_duplicate=True` 单独接同样的几个 Output —— Dash 3 允许
    # 重复 Output 只要显式声明，两个回调不会同时被同一个 Input 触发。
    @app.callback(Output('wl-ths-modal', 'is_open'),
                  Output('wl-ths-body', 'children'),
                  Input('wl-ths', 'n_clicks'),
                  Input('wl-ths-no', 'n_clicks'),
                  prevent_initial_call=True)
    def _ths_ask(_open, _close):
        if callback_context.triggered_id != 'wl-ths':
            return False, no_update
        try:
            rep = ths_sync.preview()
        except Exception as exc:  # noqa: BLE001
            return True, html.Div(f'读不了同花顺目录：{exc}',
                                  className='text-danger')
        if not rep['groups']:
            return True, html.Div([
                html.Div('同花顺自选分组一个都没读到。', className='text-danger'),
                html.Div(f"目录：{rep['dir']}", className='small text-muted'),
                html.Div('要有 <用户名>\\stockblock.ini 这个文件；'
                         '路径可以用环境变量 FT_THS_DIR 改。',
                         className='small text-muted'),
            ])
        skipped = sum(rep['not_stock'].values())
        return True, html.Div([
            html.Div([html.Strong('这会先备份、再把这里的自选整体替换掉。')],
                     className='mb-2'),
            html.Ul([
                html.Li(f"删掉现有 {rep['old_groups']} 组 / "
                        f"{rep['old_stocks']} 只（成员级联删除）"),
                html.Li(f"照同花顺重建 {rep['group_count']} 组 / "
                        f"{rep['stock_count']} 只"),
                html.Li("备份写到 var\\backup\\watchlist_*.json，"
                        "要回退照它重插即可"),
                html.Li("成本与关注日期取自 SelfStockInfo.json"
                        f"（{len(rep['quote'])} 条）"),
                html.Li(f"跳过 {skipped} 条非个股（板块指数/ETF/外盘）"
                        + (f"、{len(rep['unknown'])} 条不在 ref.security 里"
                           if rep['unknown'] else '')),
                html.Li(['★ 重点关注按组名接续；同花顺里没有同名组的会丢 ★：',
                         html.Span('、'.join(rep['old_focus']) or '无',
                                   className='text-warning')]),
            ], className='small mb-1'),
            html.Div(f"来源：{rep['dir']}（用户 {rep['user']}）",
                     className='small text-muted'),
        ])

    @app.callback(Output('wl-gwrap', 'children', allow_duplicate=True),
                  Output('wl-mwrap', 'children', allow_duplicate=True),
                  Output('wl-msg', 'children', allow_duplicate=True),
                  Output('wl-cur', 'children', allow_duplicate=True),
                  Output('wl-tgroup', 'options', allow_duplicate=True),
                  Output('wl-gid', 'data', allow_duplicate=True),
                  Output('wl-ths-modal', 'is_open', allow_duplicate=True),
                  Input('wl-ths-yes', 'n_clicks'),
                  prevent_initial_call=True)
    def _ths_import(_n):
        try:
            rep = ths_sync.import_watchlist()
        except Exception as exc:  # noqa: BLE001
            return (no_update, no_update,
                    html.Span(f'导入失败：{exc}', className='text-danger'),
                    no_update, no_update, no_update, False)
        if rep.get('error'):
            return (no_update, no_update,
                    html.Span(rep['error'], className='text-danger'),
                    no_update, no_update, no_update, False)
        gid = _first_group()
        lost = ('；★ 丢失：' + '、'.join(rep['focus_lost'])
                if rep['focus_lost'] else '')
        msg = (f"已按同花顺覆盖：{rep['groups_written']} 组 / "
               f"{rep['stocks_written']} 只（原 {rep['old_groups']} 组 / "
               f"{rep['old_stocks']} 只已备份到 {rep['backup']}）{lost}")
        return (groups_table(gid), members_table(gid),
                html.Span(msg, className='text-success'),
                _cur_head(gid), group_options(), gid, False)
