# -*- coding: utf-8 -*-
"""
把全站说明灌进 `config.help_note`。

**默认只补不覆盖**：已经在库里的 note_key 一律跳过 —— 页面上手改过的说明不能被
一次重跑冲掉。要用脚本里的版本强制刷新，加 `--force`。

用法：

    python scripts/ops/seed_help_notes.py            # 只补缺的
    python scripts/ops/seed_help_notes.py --force    # 用脚本里的版本覆盖
    python scripts/ops/seed_help_notes.py --dry-run  # 只看会写什么

内容分四类：

- `snippet` 公共片段：操作分口径、板块热度口径这种到处要引用的定义，
  被 `includes` 引用，改一处所有说明跟着变
- `page`    整页说明：这一页回答什么问题、数据怎么串起来的
- `card`    卡片说明：配置卡用 `card_ref` 当 target，手写卡用稳定别名
  （`stock.kline` 这种），指标块用 `页面.stat.标签`
- 迁移      `ui.CARD_HELP` 里的 34 条存量 + `config.chart.options.note`，
  按段落拆成 content / caveat 两段（⚠️ 开头的段落归 caveat）
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.db import engine  # noqa: E402
from app.web import ui  # noqa: E402

# ---------------------------------------------------------------- 公共片段

SNIPPETS = [
    dict(note_key='snip.oper_score', scope='snippet',
         formula='**操作分** = 0.40 × X 个股质量 + 0.60 × Y 环境共振，'
                 '取值 −100 ~ +100 —— 是排序用的相对分，**不是涨幅预测**。'
                 'Y 占 60% 是有意的：同样质量的票，板块在风口上和在退潮里结果完全不同。'),
    dict(note_key='snip.sector_heat', scope='snippet',
         formula='**板块热度** = 0.30 × 板块涨幅 + 0.30 × 上涨广度 + '
                 '0.25 × 动量 + 0.15 × 成交，四项各自映射到 −100 ~ +100，'
                 '再按 `ref.sector_member` 取成员均值落到板块。'
                 '广度低而涨幅高 = 少数票拉起来的。'),
    dict(note_key='snip.no_scheduler', scope='snippet',
         caveat='**本机没有调度进程**：`config.job.cron_expr` 只是计划，没人读它，'
                '也没注册 Windows 计划任务。要更新数据只能在 `/jobs` 或 '
                '`/datasource` 上点，或者命令行跑脚本。'),
    dict(note_key='snip.tdx_first', scope='snippet',
         content='日线与板块的主源是**通达信 vipdoc 本地文件**，网络源只做兜底'
                 '且必须显式打开 —— 两边口径不完全可比，悄悄换源会让同一列数字'
                 '前后不是一个东西。'),
    dict(note_key='snip.score_gap', scope='snippet',
         caveat='`fct.score` 目前只回算了最近一段（`sector_heat` 从 2026-07-29 起），'
                '再往前是空的 —— 曲线左端断掉是**缺数据**，不是分数为 0。'),
]

# ---------------------------------------------------------------- 整页说明

PAGES = [
    dict(note_key='page.home', scope='page', target='/', title='复盘首页',
         purpose='盘后第一屏，一天只用点一个地方：先看市场温度 → 再看板块 → '
                 '再看资金 → 最后确认数据跑没跑。'
                 '2026-08-29 并进了 `/review`「每日复盘」，'
                 '08-30 又并进了 `/market`「大盘与资金」。',
         content='上面 6 个指标块是**当日横截面**；中段是分布、情绪、板块与四象限；'
                 '16~19 号槽是**资金面**（指数走势 / ETF 净申赎 / 两融 / 龙虎榜）；'
                 '最后一张是今日任务。**顶部可以选交易日**，指标块和卡片都跟着那天走'
                 '（`k_health` 例外，它讲的是库的当前状态）。',
         source_tables=['md.stock_daily', 'fct.score', 'fct.indicator_value',
                        'md.index_daily', 'md.margin_detail', 'md.lhb_detail'],
         script='scripts/ops/e1_fill_all.py（一键追平，10 步）',
         derive='每张卡一个独立回调：骨架先返回、卡片各自异步加载，'
                '所以慢卡不会拖住整页。',
         includes=['snip.oper_score']),
    dict(note_key='page.sector', scope='page', target='/sector',
         title='板块热度',
         purpose='回答"今天谁在领涨、这股热度是在积累还是在退潮"。'
                 '个股再好，踩在退潮的板块里也走不动，所以板块要单独一页。',
         content='五张卡的分工：**热度榜**看量级 → **热度趋势**看这几天的方向 → '
                 '**四分项拆解**看它凭什么热 → **板块轮动**看跨日的横向比较 → '
                 '**板块明细**是带趋势列的全表。\n\n'
                 '**排序依据**：`ORDER BY 当日热度 DESC`（`fct.sector_heat_daily.heat`），'
                 '**不是按涨幅排**。只取 `行业` + `概念`（默认剔除风格板块，'
                 '「昨日涨停」这类天生涨幅巨大会占满整张图），剔掉名字是纯数字的板块。'
                 '当天还没跑评分时整表**回退按涨幅排**，趋势列显示「（当日无评分）」。',
         derive='四段链路：① `md.sector_daily` ← 通达信 vipdoc 板块日线'
                '（719,825 行，含涨跌幅/成交额/上涨家数/成员数）→ '
                '② `ref.sector_member` 板块成员名单 → '
                '③ `scripts/compute/d2_scores.py` 给**每只票**算 `sector_heat` 分 → '
                '④ `scripts/compute/d3_sector_trend.py` 按成员取均值落到 '
                '`fct.sector_heat_daily`，并算各窗口最小二乘斜率进 `fct.sector_trend`。',
         source_tables=['md.sector_daily', 'ref.sector_member',
                        'fct.sector_heat_daily', 'fct.sector_trend'],
         script='scripts/compute/d3_sector_trend.py',
         includes=['snip.sector_heat', 'snip.tdx_first'],
         caveat='有行情的 653 个板块里只有 440 个有成员名单（缺成员就算不出热度、'
                '进不了榜）。缺口几乎全在**行业**：231 个行业板块只有 18 个有成员 ——'
                '通达信的行业板块有行情没成分股，2026-08-30 起用同花顺本地 '
                '`industry.ini` 按中文名精确对齐补了 18 个，其余 72 个名字对不上，'
                '往 `ref.sector_alias` 人工插一行下次就自动认（概念 268/268、'
                '风格 154/154 已满覆盖）。'),
    dict(note_key='page.stock', scope='page', target='/stock',
         title='个股详情',
         purpose='一只票的全景：K 线、评分归因、评分与因子走势、所属板块、龙虎榜。',
         content='**全市场任意一只票都能看，不限自选**。搜索支持代码 / 名称 / '
                 '拼音首字母 / 中间几个字。',
         source_tables=['md.stock_daily', 'fct.score', 'fct.score_detail',
                        'fct.indicator_value', 'ref.sector_member', 'md.lhb_detail'],
         includes=['snip.oper_score']),
    dict(note_key='page.screen', scope='page', target='/screen', title='筛选器',
         purpose='把"我想找什么样的票"变成一组可存可复用的条件。',
         content='条件类型登记在 `config.screen_rule`（5 种语义），方案存在 '
                 '`config.screen_preset`。参数控件按 `param_schema` 自动生成 —— '
                 '加一种条件是往注册表插一行，不是改代码。',
         source_tables=['config.screen_rule', 'config.screen_preset'],
         script='app/compute/screener.py'),
    dict(note_key='page.watchlist', scope='page', target='/watchlist',
         title='自选股组分析',
         purpose='按**组**做横截面与趋势分析：明天先看哪个组。'
                 '维护（建组 / 加票 / 移组）只是顺手的入口。',
         content='上半截是维护台（左组右票，点任意一格换组），'
                 '下半截三张卡：组热度 → 组趋势 → 自选池明细。\n\n'
                 '**「从同花顺导入」**：读同花顺安装目录的 `stockblock.ini`，把那边的'
                 '分组与成员**整体替换**这里的分组（同花顺自己就是这么组织的，'
                 '增量合并会留下一堆已删掉的旧组）。点了先弹确认，弹窗里写清'
                 '将删几组、将建几组、备份文件路径、跳过多少非个股条目、'
                 '★重点关注会不会丢。覆盖前自动备份到 `var/backup/watchlist_*.json`。',
         source_tables=['config.watchlist_group', 'config.watchlist_stock',
                        'fct.score', 'md.stock_daily'],
         caveat='导入是**全量覆盖且不可逆**（只有那个 json 备份能还原）：'
                '手工在这里建的组、同花顺上没有的组，导入后就没了。'
                '★ 按**组名**接续 —— 组名在同花顺那边改过，星标就会落在旧名字上'
                '而报告里如实列出丢失项。所以它刻意**没有**做成 `/jobs` 上的作业，'
                '只能从这个带确认的按钮走。',
         includes=['snip.oper_score']),
    dict(note_key='page.scores', scope='page', target='/scores',
         title='全市场个股分析',
         purpose='全市场逐票的评分与因子，**不限自选**：四象限选票 + 双高清单 + '
                 '个股明细 + 生效绑定。',
         content='上半截按象限选票（Q1 双正是"好票在风口"），'
                 '下半截是双高清单、操作分排行和当前生效的因子权重。',
         source_tables=['fct.score', 'fct.score_detail', 'md.stock_daily'],
         script='scripts/compute/d2_scores.py',
         includes=['snip.oper_score']),
    dict(note_key='page.blocks', scope='page', target='/blocks',
         title='积木管理',
         purpose='这套系统是注册表驱动的：页面、卡片、因子、作业都是**数据**。'
                 '这一页就是改那些数据的地方，改完刷新生效，不用动代码。',
         content='① 页面搭建：往 `config.page_layout` 里加卡片、排顺序；'
                 '其余每个 tab 对应一张注册表的原始行。'
                 '有效权重那张卡显示归一化后的实际权重，改动历史可回滚。',
         source_tables=['config.page_layout', 'config.chart', 'config.binding',
                        'config.capability', 'ops.config_history']),
    dict(note_key='page.jobs', scope='page', target='/jobs',
         title='任务与调度',
         purpose='跑数据、看跑批结果。日常只用最上面那个「一键追平」。',
         content='作业清单来自 `config.job`，执行历史是 `ops.task`。'
                 '「无执行器」的作业按钮是灰的 —— 缺对应脚本，不是坏了。',
         source_tables=['config.job', 'ops.task'],
         script='app/orchestrator/runner.py 的 RUNNERS 白名单',
         includes=['snip.no_scheduler']),
    dict(note_key='page.data', scope='page', target='/data',
         title='数据加工',
         purpose='关于数据的两个问题在同一页回答：**到齐了吗**、**到的对吗**。'
                 '2026-08-30 把原来的 `/quality`「数据质量」并了进来。',
         content='**① 数据现状**：同步批次 → 当前表行数 → 每日覆盖度 → 迁移前基线。'
                 '覆盖度那张卡是查"某天是不是漏采了"的第一站。\n\n'
                 '**② 数据质量**：审计缺口 + 四个指标块 + 最近校验结果 + 规则登记 + '
                 '字段口径字典。**打开这一页就会自动把落后的审计补上**：'
                 '按当前北京时间算出最近一个已收盘交易日，缺几天就逐天各跑一次'
                 '`e2_quality_audit.py`（异步，页面不阻塞）。字段单位以 '
                 '`config.field_spec` 为唯一来源 —— 库里的列注释、文档里的字段字典、'
                 '落库前的校验三处都由它生成，所以不会互相漂移。',
         source_tables=['ops.sync_batch', 'ops.migration_baseline',
                        'pg_stat_user_tables', 'config.quality_rule',
                        'ops.quality_result', 'config.field_spec'],
         caveat='旧系统的事故全是"静默的数据错误"而不是缺功能，'
                '所以这一页的意义是让口径问题**可见**。'
                '★2026-08-30 之前这里的「最近一次」是**迁移对账**的时间戳'
                '（24 条规则一次都没按交易日跑过），看板一片绿而实际什么都没查 ——'
                '所以现在「最近一次」旁边必须有"审计到哪个交易日"，'
                '并且有一张专门的「审计缺口」卡盯着它别再落后。'),
    dict(note_key='page.datasource', scope='page', target='/datasource',
         title='数据获取',
         purpose='两件事：① 每个外部源通不通、还剩多少配额；② 该先用谁。',
         content='**① 监控与采集**：健康灯 + 配额余量 + 探测历史 + 手动采集。'
                 '「无权限」和「接口有 bug」显示为灰色而不是红色 —— 它们不是故障，'
                 '重试一万次也没用，要做的是换源或买权限。红色只留给本该通而现在不通的。\n\n'
                 '**② 数据源管理**：层级阶梯（P0 本地文件 → 主力层 → 基座层 → '
                 '辅助层）加一张可编辑表，层级 / 职责 / 费用 / 定位 / 顺序 / 启用 / '
                 '配额 / 备注 就地改就落库，不用改代码。\n\n'
                 'P0 层有两个源，**互补不重叠**：通达信 vipdoc 管日线与分钟线，'
                 '同花顺本地 ini（`ths_local_block` / `_industry` / `_watchlist`）'
                 '管板块成分股与自选分组 —— 后两样通达信给不全。',
         source_tables=['config.datasource', 'ops.datasource_probe'],
         caveat='**层级是口径，health 是状态，两者刻意不混在一列里。**'
                'ts_* 现在 `no_permission`、em_push2 现在 `down`，层级照旧按'
                '"它本该担什么角色"填 —— 否则一次网络抖动就会把主力源降级成'
                '辅助源，下次没人知道原本该先用谁。',
         includes=['snip.no_scheduler']),
    dict(note_key='page.sql', scope='page', target='/sql',
         title='SQL 查询台',
         purpose='临时问一句库里的数据，不用为它专门做一张卡。',
         content='**只读**：走 reader 角色，只能 SELECT。'
                 '写操作要走注册表或页面上的维护入口。',
         # 查询台能查整个库，写不出一张"数据来源表"。这里列的是最常查的四张，
         # 加上执行入口本身 —— 至少让人知道这一页的权限边界在哪段代码里。
         source_tables=['md.stock_daily', 'fct.score', 'ref.security',
                        'ops.task'],
         script='app/web/pages/sql_console.py'),
    dict(note_key='page.admin', scope='page', target='/admin',
         title='系统管理',
         purpose='账号、菜单权限、系统设置。',
         content='口令用 argon2 存哈希，库里没有明文，本页也不回显任何口令。',
         source_tables=['config.app_user', 'config.menu', 'config.setting']),
]

# ---------------------------------------------------------------- 手写卡

CARDS = [
    # ---- 没有 card_ref 的配置卡 ----
    # `task_list` / `markdown` 这两类卡在 `config.page_layout` 里 card_ref 是 NULL，
    # 所以 target 只能用「页面.槽位」。槽位号会随布局调整变，改了布局要跟着改
    # 这里的 target —— 缺口清单和 `tests/test_help_note.py` 会立刻报出来。
    dict(note_key='card.home.20', scope='card', target='home.20',
         purpose='今天这一批数据是谁跑的、成没成 —— 首页最后一张卡，'
                 '看完市场再确认"上面这些数字是新的"。',
         content='最近 10 条任务，带状态徽章、能力键和交易日。'
                 '要重跑或看报错去 `/jobs`。',
         source_query='q_task_recent', source_tables=['ops.task'],
         includes=['snip.no_scheduler']),
    dict(note_key='card.jobs.1', scope='card', target='jobs.1',
         purpose='同一张任务列表，这里放长一点（20 条），用来回看前几天。',
         content='下面「执行历史」那张表是同一批数据的明细版（带耗时、行数、'
                 '原始报错）；这张只给一眼看状态。',
         source_query='q_task_recent', source_tables=['ops.task'],
         includes=['snip.no_scheduler']),
    dict(note_key='card.screen.1', scope='card', target='screen.1',
         purpose='说明这一页的两层结构，免得把"加条件类型"和"存方案"当成一回事。',
         content='**条件类型**登记在 `config.screen_rule`（5 种语义，属于改注册表）；'
                 '**方案**存在 `config.screen_preset`（属于存自己的组合）。'
                 '下面两张表就是这两层的当前内容。',
         source_tables=['config.screen_rule', 'config.screen_preset'],
         script='app/compute/screener.py'),
    # ---- /stock ----
    dict(note_key='card.stock.kline', scope='card', target='stock.kline',
         purpose='看这只票自己的形态：位置在哪、这波走了几天、量能配不配。',
         content='上图 K 线、下图成交量。**量柱按当天涨跌染色** —— '
                 '红量绿量分开才看得出是"放量上涨"还是"放量下跌"。',
         source_query='q_stock_kline', source_tables=['md.stock_daily'],
         formula='默认回看 120 个交易日；价格是**未复权收盘**，'
                 '除权日附近的跳空是真的价格断层，不是数据错。'),
    dict(note_key='card.stock.parts', scope='card', target='stock.parts',
         purpose='回答"这只票的分是怎么来的"：每个功能点的分值 × 权重。',
         content='按功能点分组显示，小标题是注册表里的中文名（如「个股质量(X轴)」）。',
         source_query='q_score_parts',
         source_tables=['fct.score_detail', 'config.v_effective_binding'],
         script='scripts/compute/d2_scores.py',
         derive='分项由 `d2_scores.py` 按 `config.binding` 调各 `app/compute/builtin/*.py` '
                '算出来，权重是**归一化后的** `eff_weight`（关掉一个子项，'
                '剩下的自动补齐到 1）。',
         includes=['snip.oper_score'],
         caveat='空表＝这只票当天没跑评分（新股 / 停牌 / 那天没跑批），'
                '不是"这只票不支持"。'),
    dict(note_key='card.stock.score_line', scope='card',
         target='stock.score_line',
         purpose='卡片上的操作分是一个时点的数，看不出"刚转强还是已经在高位横了'
                 '半个月"。三条线画在一起还能看出分歧。',
         content='操作分、X 个股质量、Y 环境共振三条线，近 120 个交易日。'
                 'X 在涨而 Y 在跌 = 个股自己的事，不是环境带的。',
         source_query='q_score_history', source_tables=['fct.score'],
         formula='y 轴**固定 −100 ~ +100** 并画 0 线 —— 纵轴自适应的话，'
                 '两只票的图并排看会得出完全错误的强弱印象。',
         includes=['snip.oper_score']),
    dict(note_key='card.stock.ind_line', scope='card', target='stock.ind_line',
         purpose='看因子本身的走势，而不是被合成之后的分。',
         content='10 日日均涨跌 / 10 日日均振幅 / 明日振幅预测，近 120 个交易日。',
         source_query='q_indicator_history',
         source_tables=['fct.indicator_value', 'config.indicator'],
         script='scripts/compute/d1_indicators.py',
         derive='竖表分组画线，不在 SQL 里横过来 —— 指标是注册表里的可配项，'
                '横过来等于把列名写死在代码里。',
         caveat='空表＝还没跑 `d1_indicators`。'),
    dict(note_key='card.stock.sectors', scope='card', target='stock.sectors',
         purpose='这只票挂在哪些板块上 —— 判断它是不是在风口上的第一手材料。',
         content='按板块类型分组，同组内按当日涨幅降序。',
         source_tables=['ref.sector_member', 'ref.sector_info', 'md.sector_daily'],
         formula='只取 `out_date IS NULL` 的在册关系；涨幅取 `md.sector_daily` '
                 '**最新一天**的值，不随页面上的交易日走。',
         caveat='一只票的概念标签常有几十个（实测最多 49 个概念），'
                '数量本身不说明什么 —— 看的是其中哪个当天在热。'),
    dict(note_key='card.stock.lhb', scope='card', target='stock.lhb',
         purpose='有没有被资金"点过名"：上榜原因、净买、占成交比。',
         content='近 30 条记录，按日期降序。',
         source_tables=['md.lhb_detail'],
         script='scripts/ingest/c6_lhb.py（akshare 转发东财）',
         formula='`净买万元` = 买入额 − 卖出额；`净买占比` 是净买占当日成交额的比例。',
         caveat='`md.lhb_detail` 现在只有 **10 个交易日**（2026-08-17 ~ 08-28），'
                '705 行 —— "近期没有记录"多半是库里没那么多历史，不是这只票没上榜。'),
    # ---- /scores ----
    dict(note_key='card.scores.result', scope='card', target='scores.result',
         purpose='这张表就是上面「四象限选票」按当前条件筛出来的票，一行一只。'
                 '**不是"我勾选过的票"，也不是系统推荐的买入清单** —— '
                 '换象限、改 X/Y 下限、改排序，这张表就跟着重算。',
         content='**默认给的就是"双高"**：Q1（X≥0 且 Y≥0）里再卡 X≥30 且 Y≥30，'
                 '按 X+Y 合计从高到低 —— 原来下半截那张「X>30 且 Y>30 的双高个股」'
                 '卡已经合并进来了（2026-09-01），同一个问题不摆两张表。'
                 '想看整个象限的全貌，把两个下限清空。'
                 '一页 80 行、80 行全部直接可见，更多的翻页。'
                 '每行末尾有站内深链「详情」，点进去看这只票的评分归因。'
                 '上面那行「Q1 命中 N 只（X≥30＋Y≥30）」是**加了门槛之后**的命中数，'
                 '所以它必然小于选票框里那四个按钮上的只数（按钮统计的是整个象限）；'
                 '带"已到条数上限"时说明被「条数上限」截断了，放大它才看得到全部。',
         source_query='q_quadrant_pick',
         source_tables=['fct.score', 'md.stock_daily'],
         includes=['snip.oper_score'],
         caveat='命中数受上面的「基准交易日」控制，跟这一页下半截那几张卡是同一天；'
                '但改输入框只重算这张表，下面的卡要刷新整页才跟着走。'
                '另外老卡的门槛是 `>30`、这里是 `>=30`，恰好 30.00 分的票会多几只。'),
    dict(note_key='card.q_quadrant_trend', scope='card',
         target='q_quadrant_trend',
         purpose='**《四象限趋势分（近 10 日 XY 走向）展示方案》**\n\n'
                 '一句话结论：当日 X/Y 只回答"今天在哪一格"，趋势要回答'
                 '"往哪走、走得稳不稳、是不是刚从差格子升上来"。所以这张表的主角'
                 '不是分数，而是**斜率 + 拟合度 + 象限迁移**三件事 —— '
                 '单看斜率会把"上下乱跳但恰好收高"的票排到最前。\n\n'
                 '同样 60 分的票，一个是从 20 分爬上来的、一个是从 90 分掉下来的，'
                 '在切片里长得一模一样。',
         content='#### 一、三个主角（谁都不能单看）\n\n'
                 '- `趋势分数`＝最小二乘斜率 × 窗口长度，也就是**整个窗口的累计'
                 '变化分**（正＝在变好）。**默认排序键**，口径与自选组页的'
                 '「趋势分数」一致。\n'
                 '- `拟合度R²`＝`corr(操作分, 天序)²`，趋势有多"直"。'
                 '`+8 分 / R²=0.9` 是一条斜线，`+8 分 / R²=0.1` 是一团噪声恰好'
                 '收在高位 —— 排行榜上挨着，能不能跟完全不是一回事。'
                 '**R² < 0.5 的高趋势分先当噪声**（页面上这一格会压灰）。\n'
                 '- `象限迁移`＝窗口第一天 → 今天（如 `Q4→Q1`）。`升格` 那一列的'
                 '`↑` 按**好坏次序 Q1 > Q3 > Q2 > Q4** 判，**不是 Q 编号大小** ——'
                 'Q3（板块热、个股暂时弱）好于 Q2（票好、没风口）。'
                 'Q4/Q3 升到 Q1 是最强信号之一。\n\n'
                 '#### 二、形态：判定有先后，顺序就是语义\n\n'
                 '`横盘`（|趋势分| < 1）→ `冲高回落`（距最高 ≤ −15）→ '
                 '`稳步上行`/`震荡上行`（R² ≥ 0.5 与否）→ `稳步下行`/`震荡下行`。\n\n'
                 '**冲高回落必须排在上行前面**：一只票 10 日里前 8 天一路上、'
                 '最后两天塌回去，最小二乘算出来的斜率仍然是正的、R² 还很高，'
                 '先判"上行"就会把它标成「稳步上行」，而它当下已经在回落。'
                 '这与量能阶段里"背离必须最先判"是同一类错误。\n\n'
                 '#### 三、"是不是已经回落了"配三列一起看\n\n'
                 '`窗口最高X+Y`（窗口内 X+Y 的最高值）、`最高那天`、'
                 '`距最高`（今天 − 最高，负得越多回落越深）。'
                 '**光给最大值会误导** —— 最大值出现在第 2 天还是今天，'
                 '含义完全相反，所以日期和差值必须一起给。\n\n'
                 '#### 四、其余列\n\n'
                 '`预测操作分`＝`a + b×(n+1)`（趋势线外推一天，与自选组页'
                 '`pred_oper` 同公式）；`今日操作分 / 今日X / 今日Y / X+Y合计` 是'
                 '当日切片；`连升天数`＝末尾连续上行的天数；`趋势天数`是窗口内'
                 '真有分的天数（元信息，用来判断上面那些数可不可信）。\n\n'
                 '`趋势图`是 `▁▂▃▅▇` 块字符拼的迷你走势（DataTable 画不了图，'
                 '块字符是零成本替代）。**只表示形状、不表示绝对高低** —— '
                 '每只票在自己的 min~max 之间归一化，两只票的 `▇` 不是同一个分数。\n\n'
                 '#### 五、怎么摆、怎么配色\n\n'
                 '一页 40 行、40 行一屏直接铺完，更多的翻页；默认趋势分从高到低，'
                 '想换排序点表头即可（不另做控件，象限筛选用这一页上半截的选票框）。'
                 '配色走 `config.page_layout.options.color`：趋势分/距最高/各项分'
                 '按正负染红绿、`升格` 的 `↑` 红字加粗、`R² < 0.5` 压灰 ——'
                 '"在变好还是在变坏、这数可不可信"要一眼看出来，读数字太慢。',
         source_query='q_quadrant_trend',
         source_tables=['fct.score', 'ref.security'],
         includes=['snip.oper_score'],
         derive='（六、数据基础与实现）'
                '`fct.score` 每行自带 `quadrant`，评分覆盖 2026-01-27 起共 142 个'
                '交易日、每日约 5,547 只 —— 10 日窗口约 5.5 万行，全在 PG 里聚合。\n\n'
                '窗口按**有评分的交易日**数取（不是日历日：长假一压，10 个自然日里'
                '可能只剩 5 个交易日，斜率量纲就变了）。斜率、截距、相关系数都用 PG '
                '自带的 `regr_slope` / `regr_intercept` / `corr`，不把序列拉回 '
                'Python；迷你走势用 `string_agg` 直接在 SQL 里拼。\n\n'
                '**性能是这张卡的主要工程量**（页面是服务端一次性渲染，这张卡慢多少'
                '页面就慢多少）：三表自连接取 X/Y/操作分 **2.0s** → 换成一遍扫 + '
                '`FILTER` 条件聚合仍 2.0s（瓶颈是 166,308 行宽行 GROUP BY 的外部'
                '归并，临时文件 9.6MB）→ 日期上下界改成**取自窗口本身** '
                '`min(seq)`/`max(seq)` **1.34s** → 序列只留 `oper_score`、X+Y 的'
                '最高值单独用 `sum(value) … HAVING count(*) = 2` 聚合（不再 '
                '`x JOIN y`）**≈1.0s** → 2026-08-31 **落库预算 0.45s**。'
                '`HAVING count(*) = 2` 不能省：只有 X 没有 Y '
                '的那天 `sum` 出来是"半个 X+Y"，会凭空拉低（或拉高）窗口最高值。\n\n'
                '**落库**（2026-08-31）：趋势是当天收盘后就定死的历史值，每次进页面'
                '重算一遍是纯浪费。现在斜率/截距/相关/窗口最高值/连升天数/迷你走势'
                '都由 `scripts/compute/d5_score_trend.py` 算进 `fct.score_trend`'
                '（作业 `calc_score_trend`，排在 `calc_scores` 之后；一键追平那条链'
                '也带它），页面只剩一次索引扫 + 当日切片的三次主键查。'
                '表里**存斜率（分/天）不存"累计变化分"**，与 `fct.sector_trend` 一个'
                '量纲，页面上的 `趋势分数` = 斜率 × 窗口是展示层换算；也**存 `r` 不'
                '存 `r²`**（`r` 的符号是信息，开方就丢了）。\n\n'
                '**库里没有那天时自动回退现算**，不会给空表 —— 而且落库和现算'
                '**共用同一份 SQL**（`app/compute/score_trend.py` 的 `CORE_SQL`），'
                '各写一份的话两边会慢慢漂开，页面上还看不出来。'
                '`tests/test_quadrant_trend.py` 里有一条逐行逐列比对两条路的断言。\n\n'
                '阈值写在 `app/web/queries.py` 的 `TREND_FLAT`（1.0）/ '
                '`R2_TIGHT`（0.5）/ `FADE_GAP`（−15），窗口与最少天数写在这张卡的'
                '`options.params`（`win` / `min_n`），改它不用动代码。'
                '排序列走 `QUADRANT_TREND_SORTS` 白名单 —— 列名是拼进 SQL 的。',
         caveat='窗口内至少 `min_n`＝8 天有分才出行 —— **新股和长停票会整片缺席**，'
                '3 天数据拟合出来的斜率没有意义。另外 `fct.score` 只回算了'
                '最近一段，越往前的日期能算的票越少。\n\n'
                '基准日走"最近一个**有评分**的交易日"并把请求日向下取整到那天：'
                '`latest_date()` 给的是日历上最新的已收盘交易日（实测 2026-08-31 '
                '15:50 给 08-31，而库里评分只到 08-28），拿它去 JOIN 一行都对不上，'
                '卡片会静默变空。所以这张表可能显示的是上一个交易日 —— '
                '看标题旁边的日期。\n\n'
                '⚠️ **换 `win` 会离开落库那条路**：`fct.score_trend` 里只有作业算过的'
                '窗口（默认 10）。把 `options.params.win` 改成 20 之后，页面仍然出'
                '正确结果，但每次都是现算（慢约 0.7 秒）—— 想要快，'
                '跑一次 `d5_score_trend.py --win 20 --recent N`。'),
    # ---- /screen ----
    dict(note_key='card.screen.cond', scope='card', target='screen.cond',
         purpose='把条件拼出来的地方。条件类型是**注册表里的数据**，不是代码分支。',
         content='勾上条件、填参数、点「运行筛选」。一条都不勾＝只看候选集本身。'
                 '调好的组合点「存为方案」，连条件、逻辑和候选集一起存下来。',
         source_tables=['config.screen_rule', 'config.screen_preset'],
         script='app/compute/screener.py',
         derive='每种条件在 `config.screen_rule.sql_template` 里写好 SQL 片段，'
                '参数按 `param_schema` 占位符替换后拼进 WHERE —— '
                '加一种条件是插一行注册表。'),
    dict(note_key='card.screen.result', scope='card', target='screen.result',
         purpose='筛选命中的票。',
         content='列里带操作分与象限，可以直接接着按分排；每行有「详情」深链。',
         source_tables=['fct.score', 'md.stock_daily'],
         includes=['snip.oper_score']),
    # ---- /jobs ----
    dict(note_key='card.jobs.fill', scope='card', target='jobs.fill',
         purpose='日常唯一要点的按钮：把数据补到最新已收盘交易日。',
         content='按固定顺序跑 10 步（日历 → 日线 → 板块 → 指数 → 因子 → 评分 → '
                 '板块热度趋势 …）。**目标交易日按点下去那一刻算**，不是按库里'
                 '最新那天算；面板上显示的是预告，真跑时会先同步一次日历再确认。',
         script='scripts/ops/e1_fill_all.py',
         source_tables=['ref.trade_calendar', 'ops.task'],
         includes=['snip.tdx_first'],
         caveat='vipdoc 还没覆盖目标日、又没勾「允许网络兜底」时，'
                '这一步会**按设计停下来**并在执行历史里说明，不会悄悄换源。'),
    dict(note_key='card.jobs.list', scope='card', target='jobs.list',
         purpose='所有已登记的作业，以及能不能点。',
         content='「触发」列里的 cron 只是**计划**。执行器白名单在 '
                 '`app/orchestrator/runner.py` 的 `RUNNERS` 里，'
                '绑定写在 `config.job.params.runner`。',
         source_tables=['config.job'],
         includes=['snip.no_scheduler']),
    dict(note_key='card.jobs.history', scope='card', target='jobs.history',
         purpose='刚才那一下到底跑成了没有 —— 状态、耗时、写了多少行、报什么错。',
         content='每 3 秒自动刷新，全部跑完自动停。',
         source_tables=['ops.task'],
         formula='状态：`queued` 排队 / `running` 在跑 / `succeeded` 成功 / '
                 '`failed` 失败（`message` 列是原始报错）。'),
    # ---- /data ----
    dict(note_key='card.data.batches', scope='card', target='data.batches',
         purpose='最近一次同步/迁移干了什么、写了多少行。',
         content='一行一个批次，含来源、目标表、行数、耗时。',
         source_query='q_recent_batches', source_tables=['ops.sync_batch']),
    dict(note_key='card.data.rows', scope='card', target='data.rows',
         purpose='库里现在每张表大概多少行 —— 判断"这张表是不是空的"最快的地方。',
         content='只列非空表，按行数降序取前 40。',
         source_tables=['pg_stat_user_tables', 'information_schema.tables'],
         caveat='`n_live_tup` 是**统计估算值**，不是 `count(*)`：'
                '刚跑完批还没 ANALYZE 时会偏，要精确数就自己去 `/sql` 数一遍。'),
    dict(note_key='card.data.coverage', scope='card', target='data.coverage',
         purpose='查"某天是不是漏采了"的第一站。',
         content='近 60 个交易日，每天的个股条数 / 有换手率的条数 / 有涨跌停标记的条数。'
                 '条数突降的日期要查是真停牌还是迁漏了。',
         source_query='q_coverage', source_tables=['md.stock_daily']),
    dict(note_key='card.data.baseline', scope='card', target='data.baseline',
         purpose='迁移动手**之前**抓的行数快照，是"迁完了没漏"的判据。',
         content='按旧库/旧表列出迁移前行数，与「当前表行数」对着看。',
         source_tables=['ops.migration_baseline']),
    # ---- /quality ----
    dict(note_key='card.quality.gap', scope='card', target='quality.gap',
         purpose='审计有没有跟上最新收盘 —— **落后就自动补**，一天一次地补。',
         content='四个数：当前北京时间、最近一个已收盘交易日、已审计到哪天、落后几天。'
                 '落后时打开这一页就会异步提交补齐（缺 2 天就跑 2 次，'
                 '各写自己那一天的结果），也可以点「立即补齐审计」。'
                 '跑的时候这块每 1.5 秒自己刷新，跑完停。',
         derive='目标日判定与 /jobs 的「一键追平」共用 '
                '`app/orchestrator/tradeday.py`：当前北京时间 → 收盘线 15:30'
                '（早于它退到昨天）→ `ref.trade_calendar` 里 `is_open` 且 ≤ 上限的'
                '最大那天。缺口 = 目标日往前、`ops.quality_result` 里没有的交易日。',
         source_tables=['ops.quality_result', 'ref.trade_calendar',
                        'md.stock_daily'],
         script='scripts/ops/e2_quality_audit.py',
         caveat='**在日历上是交易日、但库里还没有日线的天会被跳过并单独列出** ——'
                '"数据还没采"和"质量不合格"是两件事，混在一起会让人去修一个不存在的'
                '质量问题；那种情况先去 /jobs 追平数据。'
                '自动补齐只在**打开页面的第一次**触发，不会因为页面一直开着反复提交。'),
    dict(note_key='card.quality.recent', scope='card', target='quality.recent',

         purpose='最近的校验跑出什么结果 —— 未通过的才是要处理的。',
         content='一行一次校验：规则、对象、是否通过、实际值。',
         source_query='q_quality_recent', source_tables=['ops.quality_result'],
         formula='级别：`error` 阻断（该拦住落库）/ `warn` 提醒 / `info` 记录。'),
    dict(note_key='card.quality.rules', scope='card', target='quality.rules',
         purpose='都在校什么。规则是注册表里的数据，加一条不用改代码。',
         content='规则键、名称、作用对象、级别、是否启用。',
         source_tables=['config.quality_rule']),
    dict(note_key='card.quality.fields', scope='card', target='quality.fields',
         purpose='字段口径的**唯一定义处**：单位、类型、合理范围、计算公式、来源。',
         content='改这里的单位，库里的列注释与文档会一起变 —— 三处不会各说一套。',
         source_query='q_field_dict',
         source_tables=['ops.v_field_dict', 'config.field_spec']),
    # ---- /datasource ----
    dict(note_key='card.datasource.list', scope='card', target='datasource.list',
         purpose='每个外部源现在通不通。',
         content='健康状态六档：`ok` / `degraded` 降级 / `no_permission` 没权限 / '
                 '`broken` 接口自身有 bug / `down` 连不上 / `unknown` 还没探过。'
                 '后三种里的「无权限」「有 bug」是灰的，不是红的。',
         source_tables=['config.datasource'],
         caveat='密钥列只显示**环境变量名**，库里不存任何密钥本身。'),
    dict(note_key='card.datasource.gaps', scope='card', target='datasource.gaps',
         purpose='「已知缺口」那个数字缺的到底是什么 —— 逐条摆出来。',
         content='缺口 = **重试无用的那一类**：`no_permission` 付费档位不够'
                 '（如 Tushare 40203）或 `broken` 接口自身有 bug。'
                 '这和"网络抖了一下"是两件事：后者重试就好，前者重试一万次也没用，'
                 '要做的是换源或买权限。所以每行都写了「怎么消除」和'
                 '「现在实际走哪条路」。',
         source_tables=['config.datasource'],
         derive='取 health ∈ (no_permission, broken) 的启用源；'
                '「为什么算缺口」「怎么消除」按 health 与 auth_ref 推出来，'
                '不是逐个源写死的（见 app/datasource/registry.py 的 gaps()）。',
         caveat='`health` 和「当前探测报错」可能讲两件事：6 个 `ts_*` 的"无权限"'
                '是上次 40203 留下的结论，而现在报的是「FT_TUSHARE_TOKEN 未设置」'
                '—— **token 配上之前那条结论无法复验**，也不要指望'
                '"填个 token 就有权限了"。'),
    dict(note_key='card.datasource.collect', scope='card',

         target='datasource.collect',
         purpose='手动采集：`config.job` 里 `kind=ingest` 的那些作业。',
         content='点了立刻返回，子进程后台跑，表格每 3 秒自己刷新。'
                 '命令行等价物就是那一列显示的脚本路径。',
         source_tables=['config.job', 'ops.task'],
         includes=['snip.no_scheduler']),
    dict(note_key='card.datasource.quota', scope='card', target='datasource.quota',
         purpose='有配额的源还剩多少（Tushare 按积分限频、妙想按 token 计费）。',
         content='没有配额概念的源不显示余量条，而不是显示 0%。',
         source_tables=['config.datasource'],
         formula='余量 = `quota_limit − quota_used`，按 `quota_reset` '
                 '（daily / monthly）周期重置。'),
    dict(note_key='card.datasource.history', scope='card',
         target='datasource.history',
         purpose='"昨天还好今天坏了"和"一直是坏的"要区别对待 —— 前者是刚出的故障，'
                 '后者是已知缺口。只看当前状态区分不出来。',
         content='每次探测的时间、状态、延迟、拿到多少行。0 行也是信号。',
         source_tables=['ops.datasource_probe']),
    dict(note_key='card.datasource.ladder', scope='card',
         target='datasource.ladder',
         purpose='取数时**该先用谁** —— 四层，从上到下就是优先顺序。',
         content='`p0` **P0 本地**：两个源，互补不重叠 —— 通达信 vipdoc 管日线与'
                 '分钟线；同花顺本地 ini 管板块成分股与自选分组（通达信的 231 个'
                 '行业板块有行情没成分股，同花顺的 90 个行业有成分股没行情，'
                 '缝起来才完整）。都读本地文件，最快、无配额、不受接口变更影响。\n\n'
                 '`main` **主力层**：日常生产真正在跑的 —— AKShare（覆盖面最广）、'
                 '东财 push2（历史日K线质量最高）、妙想（自然语言查行情财务选股研报）。\n\n'
                 '`base` **基座层**：Baostock，历史底座与回测口径的参照，'
                 '匿名免注册，主力层对不上时拿它当第三方裁判。\n\n'
                 '`assist` **辅助层**：Tushare SDK（日线交叉验证）、'
                 'Tushare MCP（随手查，仅非复权日线、50 次/分钟）。缺了不影响跑批。',
         source_tables=['config.datasource'],
         caveat='层级是**口径**不是状态：某个源现在没权限或连不上，它该担的角色不变。'
                '灰掉的条目是 `is_active = false` 的已停用源。'),
    dict(note_key='card.datasource.manage', scope='card',
         target='datasource.manage',
         purpose='数据源的层级与定位就地改，改完立刻落 `config.datasource`。',
         content='可改：层级 / 职责 / 费用 / 定位 / 顺序 / 启用 / 配额上限 / '
                 '配额周期 / 备注。双击淡黄色格子改，改完这一格就写库，'
                 '没有「保存」按钮。写不进去会把表格拉回库里的真实值并在上方报原因。',
         source_tables=['config.datasource'],
         script='app/datasource/registry.py',
         caveat='**故意不给改的**：`health`（那是探测的结论，手改会掩盖真实状态）、'
                '`endpoint` / `probe_spec` / `auth_ref`（改了要连带改探测代码，'
                '属于改代码不是改配置）。可写列的白名单在 '
                '`app/datasource/registry.py` 的 `EDITABLE` —— 列名是唯一拼进 '
                'SQL 文本的东西，所以它不能来自页面。'
                '另外：重跑 `init_db.py` 会用 `05_seed.sql` 里的口径盖掉这里的'
                '手工修改，长期口径要改到那个文件里。'),
    # ---- /admin ----
    dict(note_key='card.admin.access', scope='card', target='admin.access',
         purpose='谁能打开这个网页 —— 本机之外的地址一律要在白名单里。',
         content='两个开关是**串联**的：`监听地址` 决定别的机器能不能连到这个端口'
                 '（127.0.0.1 = 谁都连不上，0.0.0.0 = 局域网可达），'
                 '`白名单` 决定连上之后放不放进来。所以只绑 127.0.0.1 时，'
                 '白名单里写什么都连不上 —— 那不是白名单失效。'
                 '白名单一行一条，支持单个 IP（`192.168.176.67`）与网段'
                 '（`192.168.176.0/24`）。',
         source_tables=['config.setting'],
         script='app/web/access.py',
         caveat='本机 loopback 永久放行，不受白名单影响（否则填错一个字就把自己锁在'
                '门外，而唯一的修复入口就是这个页面）；只认 TCP 来源地址，'
                '不看 `X-Forwarded-For`（那个头请求方能自己伪造）；'
                '白名单保存后即时生效，改监听地址要 '
                '`python scripts\\web_ctl.py restart`。'),
    dict(note_key='card.admin.users', scope='card', target='admin.users',

         purpose='账号增删启停。',
         content='角色两档：`user` / `admin`。菜单可以按角色隐藏（见右边那张卡）。',
         source_tables=['config.app_user'],
         caveat='口令用 argon2 存哈希，库里没有明文；本页不回显任何口令。'),
    dict(note_key='card.admin.menu_roles', scope='card', target='admin.menu_roles',
         purpose='哪些菜单只给管理员看。',
         content='`need_role` 空 = 所有登录用户可见。改这一列到「积木管理 → menu」。',
         source_tables=['config.menu']),
    dict(note_key='card.admin.settings', scope='card', target='admin.settings',
         purpose='系统设置的原始值 —— 页面上的窗口天数、阈值这类参数都在这里。',
         content='一行一个键值，按分类排。改完刷新页面生效。',
         source_tables=['config.setting']),
    # ---- /blocks ----
    dict(note_key='card.blocks.weights', scope='card', target='blocks.weights',
         purpose='看**实际生效**的权重，而不是登记时填的原始值。',
         content='关掉一个子项，剩下的会自动重新归一到 1 —— 这张卡显示归一后的结果。',
         source_tables=['config.v_effective_binding', 'config.binding'],
         formula='`eff_weight` = `raw_weight` ÷ 同一 cap 下所有启用项的权重和；'
                 '质量规则 `weight_sum` 会断言每个 cap 的 `eff_weight` 合计为 1。'),
    dict(note_key='card.blocks.history', scope='card', target='blocks.history',
         purpose='注册表改错了要能退回去。',
         content='每次改动记改了哪张表哪一行、改动前后的值，可以按记录回滚。',
         source_tables=['ops.config_history'],
         formula='回滚三种情形：`INSERT` 的改动前是空 → 回滚就是删掉那行；'
                 '`UPDATE` → 写回旧值；`DELETE` → 把旧行插回去。'),
    # ---- /sql ----
    dict(note_key='card.sql.samples', scope='card', target='sql.samples',
         purpose='给不熟悉库结构的人一个起点，照着改比从零写快。',
         content='点一条示例就填进上面的输入框。',
         source_tables=['md.stock_daily', 'fct.score'],
         caveat='查询台走 **reader 角色，只读**，写操作会直接报权限错误。'),
]

# ---------------------------------------------------------------- 指标块

STATS = [
    dict(note_key='card.jobs.stat.已登记作业', scope='card',
         target='jobs.stat.已登记作业',
         purpose='`config.job` 里一共登记了多少个作业。',
         source_tables=['config.job']),
    dict(note_key='card.jobs.stat.可运行', scope='card',
         target='jobs.stat.可运行',
         purpose='其中有几个真能点。',
         content='"可运行" = `config.job.params.runner` 绑定的执行器在 '
                 '`app/orchestrator/runner.py` 的 `RUNNERS` 白名单里。'
                 '差额那几个是**声明了但没实现**的作业，按钮是灰的。',
         source_tables=['config.job']),
    dict(note_key='card.jobs.stat.成功', scope='card',
         target='jobs.stat.成功',
         purpose='最新交易日跑成功了几个。',
         content='统计的是**最新有行情的交易日**，不是日历上的今天。',
         source_tables=['ops.task']),
    dict(note_key='card.jobs.stat.历史失败', scope='card',
         target='jobs.stat.历史失败',
         purpose='最近 200 条执行记录里有几条失败。',
         content='点开下面的执行历史看 `message` 列的原始报错。',
         source_tables=['ops.task']),
    dict(note_key='card.quality.stat.校验记录', scope='card',
         target='quality.stat.校验记录',
         purpose='`ops.quality_result` 累计跑了多少条校验。',
         source_tables=['ops.quality_result']),
    dict(note_key='card.quality.stat.未通过', scope='card',
         target='quality.stat.未通过',
         purpose='其中没通过的条数，括号里是 error 级的。',
         content='`error` 级是**该拦住落库**的问题，优先处理；`warn` 是提醒。',
         source_tables=['ops.quality_result']),
    dict(note_key='card.quality.stat.最近一次', scope='card',
         target='quality.stat.最近一次',
         purpose='最后一次校验是什么时候，**以及审计到了哪个交易日**。',
         content='只报时间会骗人：2026-08-30 之前这里显示的「2026-08-26 17:54」'
                 '其实是**迁移对账**的时间戳，那时 24 条规则一次都没按交易日跑过'
                 '（`ops.quality_result` 里只有 2 行、`trade_date` 全是 NULL）。'
                 '所以现在时间下面必须跟着"审计到 YYYY-MM-DD"，'
                 '它应该等于最近一个已收盘交易日。',
         source_tables=['ops.quality_result']),
    dict(note_key='card.quality.stat.口径字段数', scope='card',
         target='quality.stat.口径字段数',
         purpose='`config.field_spec` 里定义了多少个字段的单位与口径。',
         content='它是单位与口径的唯一定义处：列注释、文档字段字典、落库校验都由它生成。',
         source_tables=['config.field_spec']),
    dict(note_key='card.datasource.stat', scope='card', target='datasource.stat',
         purpose='数据源的总数与健康分布：通了几个、灰的几个（无权限/接口坏）、'
                 '真故障几个。',
         content='灰的那些不是故障，重试没用，要换源或买权限。',
         source_tables=['config.datasource']),
    dict(note_key='card.stock.stat', scope='card', target='stock.stat',
         purpose='这只票的当日快照：收盘与涨跌幅、操作分与象限、全市场排名。',
         content='排名是当天全市场按操作分的位次；象限是 X/Y 的四格分类'
                 '（Q1 双正 = 好票在风口）。',
         source_tables=['md.stock_daily', 'fct.score'],
         includes=['snip.oper_score']),
]

COLUMNS = ('note_key', 'scope', 'target', 'title', 'purpose', 'content',
           'source_query', 'source_tables', 'script', 'derive', 'formula',
           'caveat', 'includes', 'params', 'seq')

# `ui.CARD_HELP` 里那批文字讲的是"怎么看"，来源藏在代码里。迁移时按 key 补上，
# 否则库里的说明看不出数字打哪儿来（`tests/test_help_note.py` 会拦）。
# 只列那些 key 本身推不出来源的；`q_*` 就是命名查询名，chart 键能查
# `config.chart.data_query`，`k_*` 也是命名查询 —— 这三类在 `_migrated()` 里自动填。
MIGRATED_SOURCE = {
    'wl_groups': dict(
        source_tables=['config.watchlist_group', 'config.watchlist_stock', 'fct.score',
                       'md.stock_daily'],
        script='app/web/pages/watchlist.py'),
    'wl_members': dict(
        source_tables=['config.watchlist_stock', 'ref.security', 'md.stock_daily',
                       'fct.score', 'fct.indicator_value'],
        script='app/web/pages/watchlist.py'),
}


def _named_queries() -> set[str]:
    from app.web import queries
    return set(queries.names())


def _chart_queries() -> dict[str, str]:
    return {r['chart_key']: r['data_query'] for r in engine.query(
        'SELECT chart_key, data_query FROM config.chart WHERE data_query IS NOT NULL')}


def _migrated() -> list[dict]:
    """
    把代码里的存量说明拆成库里的行。

    拆法故意保守：**只按段落分 content / caveat**（⚠️ 开头的段落归 caveat），
    不去猜哪一句是"数据源"哪一句是"口径"。猜错了会把一段准确的说明改成错的，
    而这批文字是逐卡核过的。想拆得更细就在页面上手改 —— 库里的行才是正主。

    来源字段是**推出来的，不是猜出来的**：CARD_HELP 的 key 就是卡片的 card_ref，
    要么是命名查询名（`q_*` / `k_*`），要么是 `config.chart.chart_key`（那就取图的
    `data_query`）。推不出来的几个走 `MIGRATED_SOURCE`。
    """
    out = []
    named = _named_queries()
    charts = _chart_queries()
    for key, text in ui.CARD_HELP.items():
        paras = [p.strip() for p in text.split('\n\n') if p.strip()]
        body = [p for p in paras if not p.startswith('⚠️')]
        warn = [p for p in paras if p.startswith('⚠️')]
        target = {'wl_groups': 'watchlist.groups',
                  'wl_members': 'watchlist.members'}.get(key, key)
        out.append(dict(
            note_key=f'card.{target}', scope='card', target=target,
            content='\n\n'.join(body) or None,
            caveat='\n\n'.join(warn) or None,
            source_query=key if key in named else charts.get(key),
            seq=200,
            **MIGRATED_SOURCE.get(key, {})))
    for r in engine.query(
            "SELECT chart_key, options->>'note' AS note FROM config.chart "
            " WHERE options ? 'note'"):
        key = f'card.{r["chart_key"]}'
        if any(o['note_key'] == key for o in out):
            continue          # CARD_HELP 里已经有更细的一份，图上那句是旧的
        out.append(dict(note_key=key, scope='card', target=r['chart_key'],
                        content=r['note'],
                        source_query=charts.get(r['chart_key']), seq=200))
    return out


def rows() -> list[dict]:
    seen: dict[str, dict] = {}
    for r in [*SNIPPETS, *PAGES, *CARDS, *STATS, *_migrated()]:
        # 手写的在前、迁移的在后：同一个 target 两边都有时，手写的赢
        seen.setdefault(r['note_key'], r)
    return list(seen.values())


def upsert(rows_: list[dict], *, force: bool, dry: bool) -> tuple[int, int]:
    have = {r['note_key'] for r in engine.query(
        'SELECT note_key FROM config.help_note')}
    ins = upd = 0
    for r in rows_:
        exists = r['note_key'] in have
        if exists and not force:
            continue
        vals = [r.get(c) for c in COLUMNS]
        vals[COLUMNS.index('source_tables')] = r.get('source_tables') or []
        vals[COLUMNS.index('includes')] = r.get('includes') or []
        vals[COLUMNS.index('params')] = json.dumps(r.get('params') or {},
                                                   ensure_ascii=False)
        vals[COLUMNS.index('seq')] = r.get('seq') or 100
        if dry:
            print(('[upd] ' if exists else '[ins] ') + r['note_key'])
        else:
            cols = ', '.join(COLUMNS)
            ph = ', '.join(['%s'] * len(COLUMNS))
            sets = ', '.join(f'{c} = EXCLUDED.{c}' for c in COLUMNS[1:])
            engine.execute(
                f'INSERT INTO config.help_note ({cols}) VALUES ({ph}) '
                f'ON CONFLICT (note_key) DO UPDATE SET {sets}, '
                ' updated_at = now()', vals, role='app')
        upd += exists
        ins += not exists
    return ins, upd


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--force', action='store_true',
                    help='用脚本里的版本覆盖库里已有的说明（会冲掉手改）')
    # 改一条说明时要的是"只覆盖这一条" —— 整表 --force 会把前台改过的说明
    # 全冲掉，为了一条改动付那个代价不值。前缀匹配：--only card.datasource
    # 一次带走那一页的所有卡。
    ap.add_argument('--only', nargs='*', metavar='KEY前缀', default=None,
                    help='只处理 note_key 以这些前缀开头的条目')
    ap.add_argument('--dry-run', action='store_true')
    a = ap.parse_args()
    rs = rows()
    if a.only:
        rs = [r for r in rs if r['note_key'].startswith(tuple(a.only))]
        if not rs:
            print('没有匹配 --only 的条目：' + ' '.join(a.only))
            return 1
    ins, upd = upsert(rs, force=a.force, dry=a.dry_run)
    print(f'共 {len(rs)} 条：新增 {ins}，'
          + (f'覆盖 {upd}' if a.force else f'跳过已存在 {len(rs) - ins}'))
    if not a.dry_run:
        n = engine.scalar('SELECT count(*) FROM config.help_note')
        print(f'config.help_note 现有 {n} 条')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
