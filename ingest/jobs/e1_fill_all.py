# -*- coding: utf-8 -*-
"""
E1 · 一键追平：**以"现在"能拿到的最新已收盘交易日为上限，把这一天以及往前
缺的各层数据全部跑回本地 PG**。

这是 `/jobs`「一键追平到最新收盘日」按钮背后的那个脚本。以前要更新数据得按
顺序手点七八个采集口，中间哪一步该带什么日期、谁依赖谁全靠记 —— 记错的后果
不是报错而是"页面上有数但是旧的"，最难发现。

**目标交易日是按点击时刻算的，不是按库里最新那天算的。**
判定顺序（三步，缺一步就会算错）：

  1. 先同步 `reference_data.trade_calendar`。日历是从行情反推出来的，它**不知道明天开不
     开市**；不先接上，第 2 步就永远算出"日历里最后那一天"，于是判定永远是
     "已经最新"，整套追平再也不会去拿新数据（2026-08-27 实测踩过：日历停在
     08-25，目标就算成 08-25，而真正该拿的是 08-26）。
  2. 收盘线取 15:30（留清算时间）。现在早于 15:30 就把上限退到昨天 ——
     14:00 点这个按钮，"最新已收盘交易日"是上一个交易日，不是今天。
  3. 目标 = 日历里 `is_open` 且 ≤ 上限的最大那天。

**"往前"补到哪**：`market_data.stock_daily` 里缺的最早那个交易日；一天都不缺时按
`--back`（默认 5 个交易日）往前开一个窗口 —— 两融是 T+1 发布、龙虎榜和 ETF
份额都会补发，只打目标日那一天会永久漏掉那些迟到的数据。

**这里只编排，不自己抓数**：每一层都还是调原来那个采集脚本，源优先级、
口径、重试全在它们各自里面（日线主源永远是通达信 vipdoc，网络兜底要显式
`--allow-web`）。这个脚本自己不碰任何一张 md.* 表。

**第一步是通达信 GUI 盘后下载**（`ingest.jobs.p0_tdx_predownload`）——
日线主源是 vipdoc，文件不刷新后面全是空跑。它需要一个能操作鼠标的交互式桌面；
跑在没有桌面的计划任务里会直接判"会话自检不通过"退出，这时它是**非骨干**，
后面的 `daily` 一步照旧会用 vipdoc 硬判据决定是停下（退出码 2）还是继续。
不想让它动通达信就 `--skip tdxdl`。


**输出全程透传，不重定向**：长时任务把输出吞进文件就会被当成卡死。

**失败会自动跳过并在输出里归因**：某一步没成，这一行会打出「失败：原因」，
末尾摘要也会带归因（数据不全 / 网络故障 / 无权限 / 超时），不再只有光秃秃
一个步骤名；非骨干失败往下继续跑，骨干失败按下面退出码收尾。

退出码是有区别的，页面上看到 failed 要按这个读：

  0  全部步骤成功（或按 --skip 跳过）
  1  骨干失败：日线那一步没成，或者根本没拿到目标交易日
  2  vipdoc 还没覆盖目标日、又没开 --allow-web —— 停在这里是**设计如此**，
     不是坏了（见 c10_catchup 的说明）
  3  只有辅助源失败（两融/龙虎榜/资金流这些经常抖），骨干是好的

用法：
    python -m ingest.jobs.e1_fill_all                  # 一键追平（vipdoc 优先）
    python -m ingest.jobs.e1_fill_all --dry-run        # 只报目标日和每步要跑什么
    python -m ingest.jobs.e1_fill_all --back 10        # 往前多补几个交易日
    python -m ingest.jobs.e1_fill_all --allow-web      # 允许日线落到网络兜底
    python -m ingest.jobs.e1_fill_all --p0-instant     # 日线+快照入库后就地跑 d1
    python -m ingest.jobs.e1_fill_all --date 2026-08-28 --skip dividend,share
"""
from __future__ import annotations

import argparse
import datetime as dt
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding='utf-8')

from app.db import engine  # noqa: E402
from app.db.repo import security as sec_repo  # noqa: E402
from app.orchestrator import tradeday  # noqa: E402

# 收盘线、目标日判定、缺口/窗口计算都在 app/orchestrator/tradeday.py ——
# `/jobs` 页面要在按钮旁边先把目标日显示出来，判据抄两份就会出现"页面写 08-28、
# 脚本按 08-27 跑"这种两边都像对的错。
DEFAULT_BACK = 5           # 一天都不缺时，往前开几个交易日的窗口
MAX_SPAN = 30              # 因子/评分一次最多回算多少个交易日（实测 45 天≈18 分钟）

# 失败归因时保留子脚本输出的末尾多少行。真原因几乎总在最后那几行
# （c10 的「vipdoc 还没到 / 查不到」、c5 的「两所均为空」都是收尾打的）。
TAIL_LINES = 60

#: 两档重试（用户 2026-09-27 裁定）：**骨干步骤失败就地立刻重试**，
#: **非骨干步骤等整条链跑完再统一补一轮**。
#: 本机 push2/新浪是"按频率断连、冷却几秒恢复"，骨干就地等 8 秒比等下一轮便宜；
#: 而非骨干大多是网络源（龙虎榜/两融/资金流/分红/股本），就地重试会把日线主链
#: 拖在一条龙虎榜后面，所以放到最后。
CORE_RETRIES = 2
RETRY_BACKOFF = (8, 20)
DEFERRED_RETRY = True

#: 分钟线入库（`ingest.jobs.c16_minute`）的预算。**与获取段给同一件事的数一致**
#: （`daily_close_sync` 那一步实测"扫 19,121 个文件 = 91 秒"，给 1200 是十倍余量）。
MINUTE_INGEST_TIMEOUT_SEC = 1200

#: 每一步的**硬超时**（秒）。为什么必须有：这条链原先是 `Popen` + 一路读 stdout +
#: `proc.wait()`，**没有任何上限** —— 一步卡住（网络半连、GUI 对话框等不到按钮、
#: 大表扫描）就把整条链钉死，而宿主那一层的 5400 秒段超时只会掐掉**直接子进程**（本脚本），
#: 本脚本再起的 `d1/c1/p0/run_agent` 变成孤儿继续跑，表现就是"链断了但机器还在忙"。
#: ⚠️ 数值必须按实测给，别统一拍一个小数：2026-09-30 实跑 `d1_indicators --recent 5`
#: 正常就要 1002 秒，一个"统一 900 秒"的上限会**把正常跑完的那一步自己掐死**，
#: 掐完还写 0 行自称试过 —— 那比不超时更坏。
STEP_TIMEOUT_SEC = 1800
STEP_TIMEOUTS: dict[str, int] = {
    'indicators': 5400,      # 实测 1002s / 5 场；窗口拉到 10 场还要翻倍（MAX_SPAN=30 是上限）
    'scores': 3600,
    'sectortrend': 1800,
    'tdxdl': 2400 + STEP_TIMEOUT_SEC,   # p0 自己就带分钟线 7′41″ + 转档 + 每步重试
    'daily': STEP_TIMEOUT_SEC,          # c10 里可能顺手起一次 p0 下载
    'share': 1200,
    'dividend': 1800,
    # 个股财务多期：自己带 6 分钟预算（`--max-minutes 6`，实测 1.3 秒/只 ⇒ 约两百只）。
    # 480 秒是给"预算判据卡在一只正好慢的票上"留的余量，不是预期耗时。
    'financials': 480,
    # 首板形状：每天只重算最近 40 场（实测一片 12 个月的回算约 110 秒 ⇒ 40 场是十几秒量级）。
    # 900 秒是给"库里正好在重灌日线"那种最坏情况留的余量，不是预期耗时。
    'fbshape': 900,
    # 首板分析：实测 2026-10-21 CLI 跑 `--date 2026-10-21 --recent 3`（三场 116 行，含同花顺涨停揭秘池
    # 三次发单 + 落库后体检）9 秒。300 秒 = 三十倍余量，给"池子那张页慢/重传"与"日线正被 c1 重灌"那种最坏情况。
    'fbdaily': 300,
    # 启动前象限轨迹：实测 2026-10-17 回填 12 个月（2,263 行 / 324 个母体事件）约 25 秒，
    # 两条取数（母体名单 + 一条把窗口/名次/象限翻转一起算出来的聚合 SQL）都走 etl 角色。
    # 900 秒与 `fbshape` 同档：给"score 正在被 d2 重写 + 日线表正被重灌"那种最坏情况留的余量。
    'quadtrack': 900,
    # 爆量/急拉联合计数：与 `quadtrack` 同族（母体两档 + 每票往前 N 场的窗口扫描 + 一次 executemany）。
    # 900 秒同档余量：给「score/日线正被别的作业重写」那种最坏情况；实测回填 12 个月（五档母体）的
    # 数量级见《数据加工工作指南/10》§4.18（回填那一跑的数字都记在文档里，超时按实测核过）。
    'spikedays': 900,
    'quality': 600,
    'sentiment': 600,        # 一条 upsert + 一次 akshare 抓取（实测秒级；抓取失败也只是不写）
    'sectorflow': 900,       # akshare 即时榜一次 90 行，实测个位数秒；给足是因为源在抖时会重试
    # 板块对表：实测 2026-10-15 整跑 6 秒（东财两张清单各一发、em_get 自带 1 秒节流，
    # 再加 1,574 行 upsert）。300 秒是给"源在抖、走代理重试"那种最坏情况留的。
    'boardlink': 300,
    # 逐票量能倍数：实测单日 5,561 行 = 8.7 秒（含找上一根日线的窗口扫描），
    # 窗口 30 场一次扫完也远小于这个数 —— 900 秒是给"日线表被别的作业同时写满"那种最坏情况留的。
    'volratio': 900,
    'startupvol': 900,       # 实测 2026-10-07：每日档就是 12 个月批（996 行）23.2 秒，3 个月批 10.6 秒。
                             # 900 秒是给"stock_daily 正被重灌"那种最坏情况留的余量。
    # 埋伏票：实测 2026-10-13 单日（5,526 只满窗）65.5 秒 —— 三条取数（均线族 / 龙回头 / 周线）
    # 各自 15~35 秒，合成一条会被 reader 的 60 秒 statement_timeout 打断（当天实测 QueryCanceled）。
    # 420 秒 = 6.4 倍余量，给"日线表正在被 c1 重灌 + 别的会话在跑全量测试"那种最坏情况。
    'ambush': 420,
    # 选股汇总：五条 SELECT（各源一条，都带日期条件走索引）+ 几百到一千行 upsert，
    # 实测 2026-10-12 约 3 秒。300 秒是给"某个源正在被回算写满"那种最坏情况留的余量。
    'confluence': 300,
    # 盘中异动事件：实测 2026-10-13 单日约 18 秒（日线侧先筛出候选、只回查这批票的分钟线，
    # 每场约 10 万根 bar）。这一次量还是在 c16 正往 stock_minute 灌数的时候测的，
    # 平峰更快。300 秒 = 十几个倍数的余量，给"分钟表正被重灌"那种最坏情况。
    'spike': 300,
    # 交易体系榜：实测 2026-10-14 榜单部分单日 9 秒（一次查询取回全市场每票最近 30 根日线，
    # 判据在 Python 里逐票走 —— 不是每条判据一趟 SQL，所以不随票数线性变慢）。
    # 但这一步末尾还算**历史分布**（`analytics.system_entry_stats`）：250 场窗口 ≈ 135 万票·日
    # 按 bar_no 自连接取未来 20 根，实测 261 秒（与另一个查询并发抢 CPU 时测的；单跑 60 场 40 秒，
    # 外推 250 场约 170~200 秒）。这一步真正的上限是它，不是那 9 秒 ⇒ 900 秒。
    # 余量这么宽是因为它一天只算一次、又是判据落库的最后一环：超时被砍 = 页面那一块说明天没算。
    'sysentry': 900,
    # 趋势股形状榜：实测 2026-10-15 单日 18.1 秒（5,214 行，一条带窗口帧的 SQL + 一次 executemany）。
    # 420 秒 = 23 倍余量，给"日线表正被 c1 重灌 + 别的会话在跑全量测试"那种最坏情况。
    'trendstock': 420,
    # 节点分析：实测 2026-10-15 单日 8.2 秒（窗口内全市场日线判封板，只交封住的行）。
    # 300 秒同上，是余量不是预期。
    'boardnode': 300,
    # 异动提醒：实测 2026-10-15 单日约 14 秒（45 场窗口 × 5,200 只票的一整条窗口帧 SQL，
    # 母体筛到约 420 只再 executemany）。420 秒 = 30 倍余量，给"日线表正被 c1 重灌"那种最坏情况。
    'abnormalmove': 420,
}


def step_timeout(name: str) -> int:
    """这一步的超时。表里没有就用默认值 —— **但默认值不是"没有限制"**。"""
    return STEP_TIMEOUTS.get(name, STEP_TIMEOUT_SEC)

# 每一步：名字（--skip 用）、说明、脚本、这一步算不算骨干
#   骨干失败 → 退出码 1；辅助失败 → 退出码 3。分开是因为两融/龙虎榜/资金流
#   这些网络源经常抖，把它们和日线同等对待会让"追平失败"变成日常噪音，
#   真正的日线故障就淹没了。
STAGES = (
    # **第 0 步是通达信盘后下载**（P0 前置动作）：日线主源是 vipdoc，文件没刷新
    # 后面全都是空跑。以前这一步藏在 c10_catchup 里（vipdoc 落后时才顺手点一下、
    # 而且只下日线），提到这里是为了让"分钟线也一起下"和进度回显都看得见。
    # 非骨干：真正判"日线够不够"的是下面的 daily 那一步（它有 vipdoc 硬判据和
    # 按设计停下的退出码 2）；这一步失败也许只是分钟线没拿到。
    ('tdxdl',      '通达信盘后下载（日线 + 1/5 分钟线）',       False),
    ('daily',      '日线 / 指数 / 板块（通达信 vipdoc 优先）', True),
    # 收盘快照（市值/PE/PB/内外盘）。**必须排在 daily 之后**：它要拿
    # (Open,Max,Min,Now) 回 market_data.stock_daily 投票定交易日，日线没入库就一张表都
    # 认不出来。非骨干：数据来自客户端插件，插件没跑就没有新表，不该算追平失败。
    ('snapshot',   '收盘快照 market_data.stock_snapshot（市值/估值）',   False),

    # 情绪读数**紧跟日线**：它只读 market_data.stock_daily，不等因子也不等评分。
    # 排在这里而不是队尾，是为了让"评分那一段挂了"不至于连情绪读数一起丢 ——
    # 次日的「明日验证表」缺一天就永久缺一天的核验结论。
    # 非骨干：它算的是分析读数，缺一天不影响日线那条主链（失败走退出码 3，
    # 在摘要里单独列出来，不会被当成"追平失败"）。
    ('breadth',    '全市场情绪读数 analytics.market_breadth',        False),
    # 倍量震荡也只读日线。**排在情绪读数之后**只是为了先快后慢：它一天约 10 秒，
    # 是这条链里最慢的一步，放在前面会让人以为整条链卡住了。
    # 非骨干：同样是分析产出，缺一天不影响主链。
    # 首页「市场情绪 · 乐咕乐股口径」两档的逐日落点。**排在 breadth 之后**：
    # 回算口径读的就是上一步写的 `market_breadth`；而 `--live` 那一步（抓乐咕）也在这里做，
    # 因为那是**当天唯一一次**能拿到"收盘后的即时截面"的时机（报文里没有日期，隔天再抓就不可信）。
    # 非骨干 —— 缺一天只是折线断一格，不影响日线/评分主链。
    ('sentiment',  '市场情绪两档逐日 analytics.market_sentiment_daily',    False),
    ('voldbl',     '倍量震荡 analytics.volume_double',                  False),
    # 缩量收敛后首次突破形态信号。**排在 voldbl 之后**：两者同族（一个统计倍量次数、
    # 一个识别时序形态），失败归因放在一起好查。非骨干 —— 分析产出，缺一天不影响日线/评分主链。
    ('breakout',   '突破信号 analytics.volume_breakout_signal',          False),
    # 埋伏票（左侧机会）：突破信号的"另一栏"，同一屏左右两栏的两个阶段 —— 一个等确认、一个先埋伏。
    ('ambush',     '埋伏票 analytics.ambush_setup',                     False),
    # 逐票量能倍数（今日成交量 ÷ 上一根日线成交量）。也只读 `stock_daily`，排在倍量旁边：
    # 两者同族（一个给倍数、一个按倍数筛形态），失败归因放在一起好查。
    # 非骨干 —— 缺一天只是那一列断一格，不影响日线/评分主链。
    # ⚠️ 它**能**跟着窗口一起补（实测单日 5,561 行 8.7 秒，30 场一次窗口扫描也就十几秒），
    # 因为整段是一条 INSERT..SELECT；不要照 `voldbl` 那样钉死 --recent 1，否则补跑一次只补一天。
    ('volratio',   '逐票量能倍数 analytics.volume_ratio_daily',            False),
    # 启动前量能画像（4 板及以上 / 双创北证 4 日超四成）。**必须排在 volratio 之后**：
    # 它自己不除倍数，直接读上一步写的 `volume_ratio_daily` —— 两处各除一遍迟早漂移。
    # 非骨干 —— 分析产出，缺一天只是那一屏少几个样本，不影响日线/评分主链。
    ('startupvol', '启动前量能画像 analytics.startup_volume_profile',        False),
    # 异动筛查（盘中事件）：瞬间冲高与触及涨停的逐事件读数。判据入口在自己模块里，不另包壳。
    # **排在 startupvol 之后**只是把"读分钟线的那一族"放在一起 —— 它真正的依赖是
    # 获取段的 `c16_minute`（`stock_minute` 由 vipdoc 的 .lc1 落库）。
    # 非骨干，而且**缺分钟线不该把链判红**：那一天没有分钟线时，母体里「15 分钟内拉升 >=4 个点」
    # 那一刀判不了，它自己记 degraded 并在台账明写「分钟线没入库」，绝不说成「这些票没冲高」
    # （退出码 3 = 有场次降级，摘要里单独列，不算追平失败）。
    ('spike',      '盘中异动事件 analytics.intraday_spike',               False),
    # 交易体系榜（组合判据，用户 2026-10-14 口述）：近 20 场有涨停 × 向上跳空缺口三天不回补
    # × 缩量回调后一根阳线确认。只读 `stock_daily`（后复权价与涨停价），不依赖分钟线，
    # 所以排在 `spike` 之后只是把"形态/体系那一族"放在一起，失败归因集中。
    # 非骨干 —— 它是判据产出；但**这张表漏一天就是历史分布上少一天**，补要显式 --since。
    ('sysentry',   '交易体系榜 analytics.system_entry',                   False),

    # 趋势股形状榜（每天收盘严格站上当日 MA5 × 当天不涨停，另带 10/20 日新高与放量两口径）。
    # 只读 `stock_daily` + 股本分段表，不依赖分钟线，所以排在 sysentry 旁边把"读日线判形态"那一族放在一起。
    # 非骨干 —— 判据产出；但**这张表漏一天，/trend-stocks 那一天就是空的**，补要显式 --since。
    ('trendstock', '趋势股形状榜 analytics.trend_stock_daily',             False),
    # 节点分析（首板 × 大盘 × 前一日最高板梯队清空 的组合判据）。连板由本地日线数，不读涨停池。
    # 非骨干，理由同上：漏一天就是首板页与首页连板榜那一列断一格。
    ('boardnode',  '节点分析 analytics.board_node_daily',                  False),

    # 异动提醒（偏离值 / 异常波动 / 严重异常波动 / 明日涨停前瞻，用户 2026-10-15 口述三件事）。
    # 只读 `stock_daily` + `index_daily` 收盘价 + ST 档期/上市分段两张参照表，不依赖分钟线，
    # 所以排在 boardnode 旁边把"读日线判形态"那一族放在一起。
    # 非骨干 —— 判据产出；但**这张表漏一天，首页「异动提醒」那一天就是空的**，
    # 而且"明日涨停会不会踩到严重异动"只能看当天，补跑要显式 --since。
    ('abnormalmove', '异动提醒 analytics.abnormal_move_daily',              False),

    # qd 轨三步**排在这里**：紧跟日线与情绪读数、在因子/评分之前，理由与 `breadth` 同一句 ——
    # 评分那一段挂了不该连情绪周期一起丢。
    # 2026-09-28 实测到的事故形态：那天 15:59 的链在通达信一步死掉，人工补跑只跑了这条 20 步的链，
    # 于是日线、因子、评分、热度全好了，**首页「情绪周期」和「连板天梯」却仍停在 09-24** ——
    # 因为 `qd1_sentiment / qd2_ladder / qd3_sector` 此前**没有任何调度入口**，谁手跑一次才算一次；
    # 换句话说，链就算天天顺利，qd 轨也会天天落后。
    # ⚠️ 三条之间有先后：`qd_sentiment` 读 `qd_ladder` 写出来的天梯（拿不到就拒写并说
    #    "天梯未跑"），所以 ladder 必须排在 sentiment 前面，这个顺序不许调换。
    # 都是非骨干：缺一天不影响日线/评分那条主链。
    ('qd_ladder',   'qd 轨连板天梯族 qd.board_ladder / _member / _leader / board_height', False),
    ('qd_sentiment', 'qd 轨情绪周期 qd.sentiment_cycle（读上一步的天梯）',       False),
    ('qd_sector',   'qd 轨板块轮动与持续性 qd.sector_rotation / sector_persistence / sector_follow', False),


    ('etf',        'ETF 份额 → 净申赎',                        False),
    ('margin',     '两融',                                     False),
    ('lhb',        '龙虎榜',                                   False),
    ('fundflow',   '东财大盘资金流（一发给 120 场、带日期，可回补；但成段拒绝）', False),
    # 新浪个股主力资金流。**只有当天这一份即时截面、报文里没有日期**，所以漏一天就是
    # 永久少一天（与自选成员快照同一类）；非交易日它自己拒写（退出码 3），不会造假。
    ('fundflow_rank', '个股主力资金流排行 → fund_flow_rank（新浪，只给当天）', False),
    # 行业资金流（同花顺即时榜）逐日入库。**与 c21 同一条硬约束**：报文里没有日期字段，
    # 只有当天/次日凌晨采出来的那份榜才敢盖上归属日，所以归属超窗它自己拒写（退出码 2 =
    # 按设计不写，不算失败）。首页那块"2024 年以来累计"能不能算，全看这一步攒了几场。
    ('sectorflow', '行业资金流逐日 → market_data.sector_fund_flow_daily（同花顺，只给当天）', False),
    # 板块名 → 东财板块页（BK 码）的对表。屏幕上板块名能不能点开外链只看这张表，
    # 而东财的清单每天可能多个板块、也可能改名，所以要跟着链一起刷。
    # 排在 sectorflow 之后没有依赖关系，只是两块屏幕读的是同一批名字，放在一起好核对。
    ('boardlink', '板块名对东财板块页 → reference_data.em_board_link（两发请求，秒级）', False),
    ('dividend',   '分红 + 回算复权因子',                      False),
    ('share',      '股本（市值口径）',                         False),
    # 个股财务多期（同花顺，累计档 + 单季档）。**默认母体只有本机名单**（自选 ⊃ 持仓）：
    # 全市场一遍实测约 100 分钟（2026-10-16：1,750 只 37 分钟），不能天天跑；
    # 全库回刷是显式的 `--all`。名单外的票靠读侧那句"这份财务多久没重采"说话，不拿旧值冒充当期。
    # rc：0 全成 / 3 有成有败（逐只点名）/ 1 全败（源那条腿断了）
    #     / 4 自限预算用完（还剩几只照报）/ 2 本机名单是空的 —— 4 与 2 不算故障，见下面那条软分支。
    # 个股财务多期（同花顺，累计档＋单季档）。母体分**两档各拿一份预算**（`--refresh-gap 60`）：
    # 先补"表里一行都没有的 + 名单外 60 天没刷的"，剩下的刷"名单里最旧的" ⇒ 缺口当天吃掉、
    # 名单约 6 天轮一遍、名单外约 60 天轮一遍（10-16 就是那 11 只整只写失败没人接住的教训）。
    # 全市场一遍约 100 分钟（实测 1,750 只 37 分钟），仍然只在显式 `--all` 时跑。
    # rc：0 全成且没缺口／3 有成有败**或还缺几只**（逐只点名，也落 operations.ingest_batch）
    #     ／1 全败（源那条腿断了）／4 自限预算用完（还剩几只照报）／2 本机名单是空的
    #     —— 4 与 2 不算故障，见下面那条软分支；3 会被失败单步重试阶梯按点重跑。
    ('financials', '个股财务多期 → reference_data.stock_financial_period（同花顺，本机名单 + 缺口优先回补）', False),
    # 首板形状预示（10-17）：母体＝每一个连板段的第一天，段长与"次日开盘进场后的收益"都是**结果**。
    # 与 `startupvol` 的分工：那一屏的母体是"回头看 ≥4 板"（带未来函数），这一屏是当天可构造的事件。
    # 非骨干；每天只重算最近 40 场（把还没走完的段补成走完，r10/超额也随之补齐），历史靠显式回算。
    ('fbshape', '首板形状预示 analytics.first_board_shape（每个连板段首日 → 走到几板 / 次日进场）', False),
    # 首板分析（10-21，首页那块「首板分析」的读数）：**必须排在 `fbshape` 之后** —— 它的母体就是
    # fb-2 当天写出来的那批首板（`launch_pct/turnover/pre5/dbl_days/...` 逐字从母体搬），排前面会拿到昨天那份母体。
    # 非骨干 —— 判据产出，漏一天只是首页那一块那天是空的，不影响日线/评分主链。
    ('fbdaily', '首板分析 analytics.first_board_daily + 首页涨停母体 analytics.limit_up_pool_daily'
                '（同一场整池只拉一次，两张表一起落）', False),
    # 启动前 N 场四维象限轨迹（10-17）：母体＝`startup_volume_profile`（sv-2）那四类启动事件，
    # 读数＝`analytics.score` 里那 N 场的分数 / 名次 / 象限。
    # **必须排在 `startupvol` 与 `fbshape` 之后**：它读 sv-2 的母体名单，还从 fb-2 带 `outcome_len`
    # （走到几板）过来 —— 排前面就会拿到昨天那份母体、结果列也永远补不齐。
    # 非骨干；每天把最近 12 个月的母体重算一遍（不是逐日）：它是「母体 + 每票往前 N 场」的截面轨迹，
    # 逐日重算等于把同一批样本数 N 遍（同 `startupvol` 那一条理由）。
    ('quadtrack', '启动前象限轨迹 analytics.startup_quadrant_track（前 N 场的分数/名次/象限怎么挪的）', False),
    # 爆量/急拉 × 量增/量缩（10-18，用户追加那一问）：日线代理联合计数，母体＝anyfirst（fb-2 全部首板）
    # ＋ sv-2 四类。**必须排在 `quadtrack`/`fbshape` 之后**：它同样读 fb-2 当天补完的母体与结果列
    # （outcome_len/seg_complete/r5/ex5 都从 fb-2 join），排前面拿到的是昨天那份母体、结果列也永远补不齐。
    # 非骨干；每天把最近 12 个月的母体重算一遍（与 `quadtrack` 同一条理由：截面轨迹不逐日重算）。
    ('spikedays', '爆量急拉联合计数 analytics.startup_spike_days（倍量/急拉落在量增还是量缩，日线代理）', False),
    ('indicators', '基础因子 analytics.indicator',                   True),
    ('scores',     '评分 analytics.score',                           True),
    # 必须排在 scores 之后：板块趋势是从 analytics.score[sector_heat] 聚出来的，
    # 评分没算完先跑这一步只会把昨天的结果再写一遍。
    ('sectortrend', '板块热度趋势 analytics.sector_trend',           True),
    # 启动点必须**紧跟在 sectortrend 之后**：它读热度表，热度没写今天就等于白算。
    # 非骨干：它是分析产出，缺一天不影响日线/评分那条主链，把它算成骨干会让
    # "追平失败"变成日常噪音。
    ('ignition',   '趋势启动点 analytics.ignition',                  False),
    # 个股趋势分也必须排在 scores 之后（读 analytics.score 的三个 capability_key）。非骨干：
    # 漏跑只是让 /scores 那张卡回退成现算（慢 0.7s），不会变成空表 ——
    # 见 app/web/queries.py 的 _trend_is_stored。
    ('scoretrend', '个股趋势分 analytics.score_trend',               False),
    # 板块趋势档位与低吸候选。排在 ignition 之后：它读 `sector_heat_daily` +
    # `sector_trend`，也读 `ignition` 同批原料，早跑只会重算昨天。
    # 非骨干 —— 判据产出缺一天不影响日线/评分主链（`--scope group` 那条要等
    # 自选成员快照攒够窗口，现在会明写 skipped 返回 0，不把链子拖红）。
    ('signal',     '板块趋势形成度与低吸信号 analytics.sector_trend_signal', False),
    # 资金轮动趋势（份额口径的连续天数 / 近 10·20·30 场累计）。排在 signal 之后：
    # 两者都只读 `sector_daily`，放在一起失败归因更集中。非骨干 —— 分析产出，
    # 缺一天不影响日线/评分主链。主力净额那条 basis 现在会明写 skipped（fund_flow_rank 0 行）。
    ('flowtrend',  '资金轮动趋势 analytics.sector_flow_trend',          False),
    # 分配者视角四块读数（位置分 / 拥挤度温度 / 再分配阶段 / 位置×想象力象限）。
    # 排在 flowtrend 之后：两者都只读 `sector_daily` 与板块成分，放在一起失败归因更集中，
    # 而且它还要读 `dividend`/`stock_snapshot`/`margin_detail`（都在更前面就补好了）。
    # 非骨干 —— 分析产出，缺一天不影响日线/评分主链；但**这张表只有落库那天才有历史**，
    # 漏跑一天就是历史曲线上的一个洞，补要显式 `--since`。
    ('allocator',  '分配者视角 analytics.sector_allocator',               False),

    # 选股汇总（交叉命中）：**排在所有被汇总的步之后**（voldbl/breakout/ambush/sysentry/lhb/scores/allocator），
    # 否则它数到的是昨天那份名单，页面上「命中 2 榜」就成了拿旧数据拼新数据。
    # 各榜选法只住 `app/compute/confluence.py` 的 SOURCES 一处。非骨干 —— 它不产生判据，
    # 只把各链当天选出的票并到一起；缺一天就是那一天的「谁被多个判据同时点到」查不回来。
    ('confluence', '选股汇总 analytics.confluence_daily（交叉命中）',          False),

    # 追平的最后一步是**体检**：数据补完不体检，页面上的「最近一次」就还停在
    # 上一次，看板一片绿而实际没人查过这几天。非骨干 —— 有规则没通过不代表
    # 追平失败（退出码 2/3 在下面单独识别）。
    ('quality',    '质量审计 operations.quality_result',              False),
)

#: 骨干步骤名。失败就地重试；非骨干失败等整条链跑完再补一轮。
CORE = {name for name, _, core in STAGES if core}



def _retryable(name: str, rc: int) -> bool:
    """有些退出码是"按设计停下"，重试一百次也不会变好，别浪费一次 8 分钟下载。

    - `daily` 的 2 = vipdoc 没覆盖目标日且没给 `--allow-web` → 要人去下载，不是重跑；
    - `tdxdl` 的 2/3 = 日线到位、只有分钟线没拿到（或它自己判定停下）→ 该单独重跑
      下载那一段（`run_agent --only s5_dialog --only s7_minute`），不是重跑整步；
    - 其余非零（网络抖、超时、上游 5xx）都算可重试。
    """
    if name == 'daily' and rc == 2:
        return False
    if name == 'tdxdl' and rc in (2, 3):
        return False
    return rc != 0


def ingest_minute_bars(target: dt.date, why: str) -> int:
    """把 vipdoc 的 1/5 分钟线补进库（`c16_minute`，幂等：已入库的文件当场跳过）。

    为什么加工链要自己再点一次入库：**分钟线的"下载"在加工段第一步（`tdxdl`），
    而它的"入库"在获取段（c16）** —— 两段各自跑，顺序只保证"获取段整体在前"。
    2026-10-15 实测到差三小时的形状：获取段那次下载卡在"打开盘后数据下载"对话框
    （四级定位全灭，D 级要输入桌面 ⇒ 无人在场就必失败），c16 扫了个空；
    链尾补跑下载成功时文件已经 **18:52** 才落盘，c16 早在 17:44 跑完了 ⇒
    当天分钟线当天进不了库，`spike` 只能记「224 条，**224 条无分钟读数**」。

    这一步很轻：全部已入库时实测 6.5 秒（扫 18,950 个文件直接跳过），
    所以两种入口都补 —— 手动只跑 e1（根本不经过获取段）时也照样收口。
    """
    rc, tail = run_script('ingest.jobs.c16_minute', '--date', target.isoformat(),
                          timeout=MINUTE_INGEST_TIMEOUT_SEC)
    if rc == 0:
        print('  → 分钟线入库已补跑（%s）：`c16_minute --date %s`'
              % (why, target), flush=True)
    else:
        # 不许静默。下游 `spike` 会说"速度那一刀判不了"，但那句必须能和
        # "入库没做成"一起读，否则"判不了"会被成"这些票今天没冲高"。
        print('  ⚠️ 分钟线入库没做成（退出码 %s，%s）：输出末尾 %s'
              % (rc, why, ' / '.join((tail or '').splitlines()[-2:])), flush=True)
    return rc


# ------------------------------------------------------------------ 目标日

def sync_calendar() -> tuple[dt.date | None, dt.date | None, str]:
    """
    同步交易日历，返回 `(同步前最后一天, 同步后最后一天, 说明)`。

    走新浪（`ak.tool_trade_date_hist_sina`）：一次拿全年，不依赖 Tushare token。
    **这一步失败不致命**：日历没接上只是让目标日退回按星期几估算，会偏保守，
    比直接停下来有用。
    """
    before = engine.scalar('SELECT max(calendar_date) FROM reference_data.trade_calendar')
    try:
        import akshare as ak
        df = ak.tool_trade_date_hist_sina()
    except Exception as exc:                                     # noqa: BLE001
        return before, before, '同步失败（%s），沿用库里现有日历' % str(exc)[:80]
    dates = []
    for v in df['trade_date'].tolist():
        d = v if isinstance(v, dt.date) else dt.date.fromisoformat(str(v)[:10])
        dates.append(d)
    n = sec_repo.upsert_calendar(dates)
    after = engine.scalar('SELECT max(calendar_date) FROM reference_data.trade_calendar')
    return before, after, '写入/更新 %s 行' % n


def run_script(mod: str, *extra: str,
               timeout: int = STEP_TIMEOUT_SEC) -> tuple[int, str]:
    """
    以 `python -u -m <模块>` 起一个采集/计算作业，返回 `(退出码, 输出末尾)`。

    **为什么是 `-m` 而不是 `ROOT / 路径`**：这些作业都在 `ingest/jobs/` 下、按包内
    导入写，本项目从来没有 `scripts/{ops,ingest,compute}/` 那套目录 —— 旧写法把链上
    每一步都写成了 `can't open file`，"一键追平"于是变成一条只看得到进度、一行库都不写的
    空链（2026-09-25 定位；步数会长，所以这里不写死它是几步）。`-u` 保留：子进程的
    stdout 不块缓冲，长任务才不会被
    看得像卡死。

    输出照旧**实时透传**（一行一行打到本进程 stdout，不吞进文件 —— 长时任务
    被重定向后看起来就是卡死），同时在内存里留住末尾 `TAIL_LINES` 行，
    **失败归因全靠它**：以前只记退出码，某步 fail 了在摘要里就一个步骤名，
    看的人不知道是数据不全、网络抖还是没权限。现在拿到末尾，归因就有据可依了。

    命令同样打到 stdout：命令行里跑是实时进度条，被 `/jobs` 触发时由
    orchestrator 统一收走当执行摘要。
    """
    cmd = [sys.executable, '-u', '-m', mod, *extra]
    print('  $ %s' % ' '.join(cmd[1:]), flush=True)
    t0 = time.time()
    tail: list[str] = []
    # 子进程接管成管道后，Python 默认块缓冲，长任务进度不再实时透传——正好踩中
    # "输出吞掉=看起来卡死"。和 orchestrator.run_job 同一套：接管管道 +
    # PYTHONUNBUFFERED/UTF8，让它逐行吐，既实时又能留着归因用。env 是
    # 当前环境副本，`.env` 里的 DSN 和密钥靠继承才在。
    child_env = {**os.environ, 'PYTHONUNBUFFERED': '1',
                 'PYTHONIOENCODING': 'utf-8', 'PYTHONUTF8': '1'}
    proc = subprocess.Popen(          # noqa: S603
        cmd, cwd=str(ROOT), env=child_env, encoding='utf-8', errors='replace',
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    assert proc.stdout is not None
    # 看门狗而不是 `proc.wait(timeout=)`：读 stdout 的循环本身会阻塞，
    # 一步"**一行都不吐**地卡住"（GUI 等按钮、网络半连）用 wait(timeout) 根本到不了那一层。
    # 计时器到点直接按进程树收，收完 stdout 自然 EOF，下面的循环就跟着结束。
    timed_out = {'hit': False}

    def _reap():
        from duanxian.proc import kill_tree
        timed_out['hit'] = True
        note = kill_tree(proc.pid)
        print('\n  **%s 超过 %d 分钟未完成 → 已收掉（%s）**'
              % (mod, timeout // 60, note), flush=True)
        print('  这一步按失败处理（不要拿半截产出往下算）', flush=True)

    guard = threading.Timer(timeout, _reap)
    guard.daemon = True
    guard.start()
    try:
        for raw in proc.stdout:
            line = (raw.rstrip('\n') if isinstance(raw, str) else
                    raw.rstrip(b'\n').decode('utf-8', 'replace'))
            print(line, flush=True)
            if line.strip():
                tail.append(line)
                if len(tail) > TAIL_LINES:
                    del tail[:len(tail) - TAIL_LINES]
        rc = proc.wait()
    finally:
        guard.cancel()
    if timed_out['hit']:
        rc = 124                      # 与宿主的读法一致：124 = 被自己掐的表，不是"按设计跳过"
        tail.append('这一步超时：超过 %d 分钟未完成，已被看门狗收掉' % (timeout // 60))
    print('  ← 退出码 %s，用时 %.0fs' % (rc, time.time() - t0), flush=True)
    return rc, '\n'.join(tail)


def classify_failure(name: str, rc: int, tail: str) -> str:
    """
    把一次失败的子脚本归因成一句人话。判据是退出码 + 输出末尾的关键字。

    **从上往下命中即停**，顺序有讲究：`daily` 的 2 是"按设计停下"（vipdoc
    还没到目标日、又没开 --allow-web），它不是故障，必须最先挡住，不能让它
    掉进下面的"数据不全"——那会诱导人去怀疑数据源坏了。
    """
    if name == 'daily' and rc == 2:
        return 'vipdoc 还没覆盖目标日且未开 --allow-web，按设计停步（非故障）'
    text = (tail or '')
    low = text.lower()
    if any(k in low for k in ('timeout', 'timed out', '超时')):
        return '超时'
    if any(k in low for k in ('40203', '401', '403', 'no permission',
                              '没有接口访问权限', '无权限', '积分')):
        return '接口无权限 / 额度不足'
    if any(k in low for k in ('connection', 'remotedisconnected', '连接',
                              'network', '网络', 'econnreset', 'tlserror')):
        return '网络故障（连不上上游）'
    if any(k in low for k in ('为空', '空表', '0 行', '未发布', '非交易日',
                              '没有待补', '查不到')):
        return '数据不全（源未发布 / 空表 / 缺日期）'
    return f'脚本异常（退出码 {rc}）'


def ledger_path() -> str:
    """加工段**每一步的结果台账**（JSON）。为什么要有它：原先 28 步的成败只存在于
    跑的时候那段 stdout 里，跑完就没人看得见 —— 界面上既说不出"哪一步昨天没成"，
    也没有"就补救这一步"的入口。10-08 用户点名要这两件事。"""
    from duanxian import paths
    return paths.data_path('jobs', 'last_chain_steps.json')


def read_ledger() -> dict:
    import json
    try:
        with open(ledger_path(), encoding='utf-8') as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def write_ledger(data: dict) -> None:
    from duanxian.util import atomic_write_json
    atomic_write_json(ledger_path(), data)      # 台账只是观测，写坏不许带崩作业


def verify(target: dt.date) -> list[str]:
    """
    验收：每张表最新到哪天、目标日有多少行。返回"没追上目标日"的表名。

    只报 max(trade_date) 是不够的 —— 行数是 0 而日期在，页面上照样是空的。
    """
    # 表名是**实测核过的**，不是照作业名猜的：`market_data.margin_daily` / `market_data.lhb_daily` /
    # `market_data.fund_flow` / `analytics.indicator` 都不存在（2026-08-29 第一版就写错了这四个，
    # 验收段全打成"查不到"）。真名见 app/db/sql/02_schema.sql。
    checks = (
        ('market_data.stock_daily',      '个股日线'),
        ('market_data.index_daily',      '指数日线'),
        ('market_data.sector_daily',     '板块日线'),
        ('market_data.etf_share',        'ETF 份额'),
        ('market_data.margin_detail',    '两融明细'),
        ('market_data.dragon_tiger_detail',       '龙虎榜'),
        ('market_data.market_fund_flow', '大盘资金流'),
        ('analytics.indicator_value', '基础因子'),
        ('analytics.score',           '评分'),
    )
    lag = []
    for tbl, cn in checks:
        try:
            mx = engine.scalar(f'SELECT max(trade_date) FROM {tbl}')
            n = engine.scalar(
                f'SELECT count(*) FROM {tbl} WHERE trade_date = %s', (target,))
        except Exception as exc:                                 # noqa: BLE001
            print('  %-16s %-10s 查不到：%s' % (tbl, cn, str(exc)[:60]))
            lag.append(cn)
            continue
        ok = (mx == target) and (n or 0) > 0
        print('  %s %-16s %-10s 最新 %s   目标日 %s 行'
              % ('OK  ' if ok else 'LAG ', tbl, cn, mx, f'{n or 0:,}'))
        if not ok:
            lag.append(cn)
    return lag


def main() -> int:
    ap = argparse.ArgumentParser(description='一键追平到最新已收盘交易日')
    ap.add_argument('--date', help='指定目标交易日 YYYY-MM-DD（默认按点击时刻算）')
    ap.add_argument('--back', type=int, default=DEFAULT_BACK,
                    help='一天都不缺时，辅助源往前回补几个交易日，默认 %d'
                         % DEFAULT_BACK)
    ap.add_argument('--allow-web', action='store_true',
                    help='允许日线落到网络兜底（新浪）。默认不允许，主源是通达信')
    ap.add_argument('--overwrite', action='store_true',
                    help='用 vipdoc 覆盖已有行（把网络兜底写的换回通达信口径）')
    ap.add_argument('--skip', action='append', default=[],
                    help='跳过哪些步骤，可重复给也可逗号分隔：'
                         + '、'.join(s for s, _, _ in STAGES))
    ap.add_argument('--only', default='',
                    help='只跑这几步（逗号分隔，如 --only fundflow）：失败的那一步单独补救用，'
                         '不重跑整条链。跳过与 --skip 同义，台账只更新被跑的那几步')
    ap.add_argument('--p0-instant', action='store_true',
                    help='日线+快照入库后就地跑基础因子(d1) P0 字段分值，不等网络源')
    ap.add_argument('--dry-run', action='store_true',
                    help='只报目标日和每步要跑什么，不写库')
    args = ap.parse_args()

    # 两种写法都吃：`--skip a,b` 与 `--skip a --skip b`。
    # 必须是 append：`/jobs` 触发时 orchestrator 对列表参数就是重复发
    # `--skip a --skip b`（runner._argv），单值写法下**只有最后一个生效**，
    # 于是页面上勾了跳过四步、实际只跳了一步。
    skip = {t.strip() for v in args.skip for t in v.split(',') if t.strip()}
    bad = skip - {s for s, _, _ in STAGES}
    if bad:
        print('--skip 里有不认识的步骤名：%s' % '、'.join(sorted(bad)))
        return 1

    # `--only`：只补救个别失败步骤用（`/system` 上那句"补跑这一步"）。实现上等价于
    # "跳过其余全部"，因此重试、非骨干延后补跑、超时、台账那套机制**完全同一条路**，
    # 不另开一套 —— 两套语义迟早漂移。
    only = {t.strip() for t in args.only.split(',') if t.strip()}
    bad_only = only - {s for s, _, _ in STAGES}
    if bad_only:
        print('--only 里有不认识的步骤名：%s' % '、'.join(sorted(bad_only)))
        return 1
    if only:
        skip |= {s for s, _, _ in STAGES if s not in only}
        print('  只跑：%s（其余 %d 步按 --only 跳过）'
              % ('、'.join(sorted(only)), len(STAGES) - len(only)))

    now = dt.datetime.now()
    print('=== 0. 目标交易日 ===')
    print('  点击时刻 %s（收盘线 %02d:%02d，早于它就退到上一个交易日）'
          % (now.strftime('%Y-%m-%d %H:%M:%S'), *tradeday.CLOSE_HHMM))

    if args.date:
        target, how = dt.date.fromisoformat(args.date), '命令行指定'
    else:
        if args.dry_run:
            # 同步日历是写库动作（upsert 整张 reference_data.trade_calendar）。
            # dry-run 里绝不能干：以前这里先写完日历、到底部才 print「dry-run 未写库」，
            # 那句是假话。现在只读库里现有的最大值，并明说它可能不是最新的。
            after = engine.scalar(
                'SELECT max(calendar_date) FROM reference_data.trade_calendar')
            before, note = after, 'dry-run 不同步日历（只读库里现有最大值，可能落后于真实日历）'
            print('  交易日历：%s → %s（%s）' % (before, after, note))
        else:
            before, after, note = sync_calendar()
            print('  交易日历：%s → %s（%s）' % (before, after, note))
        # 日历余量要**提前**出声，不能等 1 月第一个工作日才发现链安静地不跑。
        # 新浪那一路只排到当年年底，次年放假安排要等官方发布；这条链第 0 步每天都在
        # 重同步，所以这里说的不是"坏了"，而是"还能靠日历几天"。余量之内那段
        # 由 `duanxian/pg.py` 自己判：周末直接判休市，工作日拿行情时间戳自证开市。
        tail = engine.scalar('SELECT max(calendar_date) FROM reference_data.trade_calendar')
        if tail is not None:
            tail_day = tail if isinstance(tail, dt.date) else dt.date.fromisoformat(str(tail)[:10])
            left = (tail_day - now.date()).days
            if left < 60:
                print('  ⚠️ 日历只排到 %s（还剩 %d 天）：次年的排班一发布，'
                      '这里明天就会自己延长，不用手动补' % (tail_day, left))
        target, how = tradeday.target_date(now)
    if target is None:
        print('\n拿不到目标交易日：reference_data.trade_calendar 是空的，先跑一次日线同步。')
        return 1
    print('  目标交易日 %s（%s）' % (target, how))

    # 这两个数**不是一回事**，分开打：
    #   have  = market_data.stock_daily 真实的 max(trade_date)，"库里到底有到哪天"
    #   ready = reference_data.view_latest_trade_date，日历+收盘线算出来的"应该有到哪天"
    # 2026-08-31 踩过：这里原来只打 sec_repo.latest_trade_date() 却标成
    # "库里最新 market_data.stock_daily"，于是日线停在 08-28 的时候输出写着 08-31 ——
    # 看输出的人会以为数据已经到位，而页面上是上周五的数。
    have = engine.scalar('SELECT max(trade_date) FROM market_data.stock_daily')
    ready = sec_repo.latest_trade_date()
    miss = tradeday.missing_days(target)
    print('  库里最新 market_data.stock_daily = %s（日历判定应有到 %s）' % (have, ready))

    print('  缺的交易日 %d 天：%s'
          % (len(miss), '、'.join(str(d) for d in miss[-10:]) or '无'))

    start = min(miss) if miss else tradeday.back_window(target, args.back)
    span = tradeday.trade_days_between(start, target)
    if span > MAX_SPAN:
        # 因子/评分按 --recent 逐日重算，天数一多就是几十分钟一步。库落后太多时
        # 先把最近这一段追上（再点一次会继续往前），而不是一口气跑到看起来卡死。
        print('  窗口 %d 个交易日超过上限 %d，本次只算最近 %d 天的因子与评分'
              % (span, MAX_SPAN, MAX_SPAN))
        span = MAX_SPAN
    print('  这次的处理窗口 %s .. %s（%d 个交易日）' % (start, target, span))

    ymd = '%Y%m%d'
    plan = {
        # P0 前置：通达信 GUI 盘后下载。分步调用 + 心跳 + 卡死检测 + 失败清进程
        # 重来，全在那个脚本里（ingest.jobs.p0_tdx_predownload 的模块说明）。
        # 不传 --day-only：分钟线也在这里一次下掉，它是这条链上最慢的一段
        # （实测 7 分 41 秒），没人会单独去点第二遍。
        'tdxdl': ('ingest.jobs.p0_tdx_predownload',
                  ['--date', target.isoformat()]),
        'daily': ('ingest.jobs.c10_catchup',
                  ['--no-calendar']
                  + (['--allow-web'] if args.allow_web else [])
                  + (['--overwrite'] if args.overwrite else [])),
        # 收盘快照：不传日期。表名是采集日期不是交易日，脚本自己按数据回推、
        # 盘中采的表自己跳过 —— 传日期反而会把"哪天有效"交给这里拍脑袋。
        'snapshot': ('ingest.jobs.c15_snapshot', []),
        'etf': ('ingest.jobs.c4_etf_share',

                ['--start', start.strftime(ymd), '--end', target.strftime(ymd)]),
        'margin': ('ingest.jobs.c5_margin',
                   ['--start', start.strftime(ymd), '--end', target.strftime(ymd)]),
        'lhb': ('ingest.jobs.c6_lhb',
                ['--start', start.strftime(ymd), '--end', target.strftime(ymd)]),
        'fundflow': ('ingest.jobs.c11_fundflow', []),
        # 不加 --date：归属日一律由日历判（这个口没有历史查询能力，给了假日期只会
        # 把今天的截面冒充那天，见 c21 的 check_date_arg）。
        'fundflow_rank': ('ingest.jobs.c21_fundflow_sina', []),
        'dividend': ('ingest.jobs.c8_dividend', []),
        'share': ('ingest.jobs.c9_share', []),
        # 个股财务多期（同花顺，累计档＋单季档）。母体**分两档各拿一份预算**（`--refresh-gap 60`）：
        # 第一档补"表里一行都没有的 + 名单外 60 天没刷的"，第二档刷"名单里最旧的"。
        # 为什么要分档而不是排成一条队：实测本机名单 1,122 只 ≈ 24 分钟 > 每日 6 分钟预算 ——
        # 一条队"名单在前"就永远轮不到缺口（10-16 我第一版就是这么写的，跑起来才发现尾巴一行都没执行），
        # "缺口在前"又会让名单几天刷不到新披露。分档之后：缺口当天吃掉、名单约 6 天轮一遍、名单外约 60 天一轮。
        # 全市场一遍约 100 分钟（实测 1,750 只 37 分钟），不能天天跑；全库回刷仍是显式 `--all`。
        # 两档的速率算术写在 `c25_financials_ths.refresh_plan` 的 docstring 里（为什么是 60 天而不是 45 天）。
        # rc：0 全成且没缺口／3 有成有败**或还缺几只**（逐只点名 + 落 operations.ingest_batch）
        #     ／1 全败（源那条腿断了）／4 自限预算用完／2 本机名单是空的
        #     —— 4 与 2 不算故障，见下面的软分支；3 会被失败单步重试阶梯按点重跑。
        'financials': ('ingest.jobs.c25_financials_ths', ['--refresh-gap', '60', '--max-minutes', '6']),
        # 首板形状预示：只重算最近 40 个交易日（不是"只算今天"）—— 连板段要走过两三天才知道
        # 它最终是几板、次日进场后 10 场的收益也要等日线补齐；每天重刷这一小段，
        # `seg_complete`/`r10`/`ex10` 才会从"还在走/还没到"变成确定值。历史用显式 `--start/--end` 回算。
        'fbshape': ('app.compute.first_board_shape', ['--recent-sessions', '40']),
        # 首板分析：`--recent 3`（以当天结尾的三场）而不是 1 —— 涨停原因/首封时间/炸板次数是**现打源站那一页**
        # 拿回来的，那一天拉取失败（或源站那一行没给）时，只算今天会让那三场里的今天永远带着空串；
        # 连着补三场，第二天还能把昨天的补上（源站那一页实测留得到约一年前的场次，见写侧 docstring）。
        # 母体本身（fb-2 那批首板）与前一交易日读数是历史值，重算一遍只是幂等 upsert + 清那一场的残留行。
        'fbdaily': ('app.compute.first_board_daily',
                    ['--date', target.isoformat(), '--recent', '3']),
        # 启动前象限轨迹：一次算满 12 个月的母体（页面有 3/6/12 三档窗口月数，跑 3 个月会让 6/12 档
        # 变成"挂着 12 个月标签的 3 个月名单"，同 `startupvol` 那一条理由）。
        # ⚠️ 排在 `startupvol`/`fbshape` 之后：它读 sv-2 的母体名单，还要从 fb-2 带 outcome_len（走到几板）。
        # 窗口那一档用默认 30（与 sv-2/fb-2 同一把尺子）；10/20 两档要另跑 `--window 10/20` 回填，
        # 读侧发现那一档没算过会明说"这一档还没算"，不会把空表说成"没有轨迹"。
        'quadtrack': ('app.compute.startup_quadrant_track', ['--months', '12']),
        # 爆量/急拉联合计数（10-18）：一次算满 12 个月、五档母体全要（anyfirst 排第一——每日链不带
        # --cohort，写侧 cohort=None 就是两路都跑，anyfirst 不会烂掉）。窗口用默认 30；10/20 要另跑
        # `--window 10/20` 回填（主键不带 window_days，换窗重灌是覆盖同一批行，读侧会明说哪一种算过）。
        'spikedays': ('app.compute.startup_spike_days', ['--months', '12']),
        'indicators': ('ingest.jobs.d1_indicators',
                       ['--date', target.isoformat(), '--recent', str(span)]),
        # 情绪读数：一天约 1s，所以直接跟着窗口一起补，不像因子那样受 MAX_SPAN 折磨。
        # 跑完会自己体检最后一天，量级不对返回 2（在这里等于非骨干失败，摘要里可见）
        'breadth': ('ingest.jobs.d6_breadth',
                    ['--date', target.isoformat(), '--recent', str(span)]),
        # 倍量震荡：一天约 10 秒。**不跟着 span 一起回填**（--recent 固定 1）——
        # 窗口落后几天时逐日重算的收益很低，而 30 天 × 10 秒 = 5 分钟，
        # 会把"一键追平"拖成让人以为卡死的长任务。要补历史用 d7 自己跑 --recent。
        'voldbl': ('ingest.jobs.d7_vol_double',
                   ['--date', target.isoformat(), '--recent', '1']),
        # 缩量收敛后首次突破形态信号。跟 voldbl 一样逐日跑（单日 15–20 秒），
        # 补历史用 d8 自己跑 --recent，这里不跟 span。
        'breakout': ('ingest.jobs.d8_breakout_signal',
                     ['--date', target.isoformat(), '--recent', '1']),
        # 埋伏票（同一屏左栏，右侧是上面那条突破信号）。判据入口在自己模块里，不另包壳。
        # 逐日跑一天一算：三条取数各自都在扫最近 200 个日历日，`--recent` 拉大等于把同一段扫描重做。
        'ambush': ('app.compute.ambush_setup',
                   ['--date', target.isoformat(), '--recent', '1']),
        # 逐票量能倍数（用户 2026-10-07 点名要的新字段）。判据的入口就在它自己模块里
        # （`app/compute/volume_ratio.py` 的 `main()` 有 argparse），不再包一层同名壳。
        # 跟 span 一起补：整段是一条 INSERT..SELECT 的窗口扫描，实测单日 8.7 秒、
        # 近一年 241 场一次跑完约一分多钟 —— 与 `voldbl` 那种"一天 10 秒、逐日重算"不同，
        # 这里逐日调用反而更慢（每场都要为找上一根再扫一次，第一版实测单日 45.3 秒）。
        'volratio': ('app.compute.volume_ratio',
                     ['--date', target.isoformat(), '--recent', str(span)]),
        # 启动前量能画像：一次算满 12 个月的母体（页面有 3/6/12 三个档，跑 3 个月会让 6/12 档
        # 变成"挂着 12 个月标签的 3 个月名单"—— 那种错在屏幕上看不出来）。
        # 实测 2026-10-07：3 月批 10.6 秒 / 12 月批（996 行）23.2 秒，多花 13 秒换三档都真。
        # 不跟 span 逐日跑 —— 它是"母体 + 每票往前 30 根"的截面分析，逐日重算等于把同一批样本数 30 遍。
        'startupvol': ('app.compute.startup_volume',
                       ['--end', target.isoformat(), '--months', '12']),
        # 盘中异动事件：逐日算（实测单日 18 秒，其中回查这批候选票的分钟线约 10 万根）。
        # 不跟 span 一起补 —— 它的取数是"按天切"的，窗口拉大等于把同一批分钟线按天重扫，
        # 而且分钟线本身可能只补到了部分日子（跟 span 跑会把没分钟的那几天算成"没冲高"）。
        # 要补历史：python -m app.compute.intraday_spike --since <日期> --skip-done。
        'spike': ('app.compute.intraday_spike',
                  ['--date', target.isoformat(), '--recent', '1']),
        # 交易体系榜：逐日算（实测单日 9 秒）。判据入口在自己模块里，不另包壳。
        # 不跟 span 一起补：它的取数是"每票最近 30 根日线"整批拉回 Python 判，
        # 跟窗口跑等于把同一批日线按天重拉 30 遍；补历史用 --since + --skip-done 显式做。
        'sysentry': ('app.compute.system_entry',
                     ['--date', target.isoformat(), '--recent', '1']),

        # 趋势股形状榜：逐日算（实测单日 18.1 秒 / 5,214 行）。判据入口在自己模块里，不另包壳。
        # 不跟 span 一起补：它的窗口帧按"目标日 + 前 60 根"整段扫，跟窗口跑等于把同一段日线
        # 按天重扫 span 遍；补历史用 --since + --skip-done 显式做（模块自己分段，每段 20 场）。
        'trendstock': ('app.compute.trend_stock',
                       ['--date', target.isoformat(), '--recent', '1']),
        # 节点分析：逐日算（实测单日 8.2 秒）。同上，不跟 span 一起补。
        # ⚠️ 它读 `market_data.index_daily` 的 000001.SH —— 那一天指数没入库时整列是"判不了"，
        #    行照写、sh_row_missing 记 true，页面会说这句，不会读成"这批票都没命中"。
        'boardnode': ('app.compute.board_node',
                      ['--date', target.isoformat(), '--recent', '1']),

        # 异动提醒：逐日算（实测单日 14 秒）。判据入口在自己模块里，不另包壳。
        # 不跟 span 一起补：它的 SQL 一次拉 45 场窗口只算目标日那一场，跟窗口跑等于把同一段
        # 日线按天重扫 span 遍；补历史用 `--since <日期> --skip-done` 显式做。
        # ⚠️ 它**必须每天跟着链跑**：「明日涨停会不会踩到严重异动」只有当天算才有意义，
        #    昨天那一场的前瞻今天已经过期（今天的涨停价要用今天的收盘价算）。
        'abnormalmove': ('app.compute.abnormal_move',
                         ['--date', target.isoformat()]),

        # qd 轨三步（判据与坑都在各作业自己的 docstring 里，这里只给入口）。
        # 传 --recent：窗口落后几天时一起补上，否则补跑一次只算一天，
        # 而 qd.sentiment_cycle 的环比基期正是"上一场有没有这一行"。
        # ⚠️ ladder 在 sentiment 之前（STAGES 里同一条规矩），反了 sentiment 会整批拒写。
        'qd_ladder': ('ingest.jobs.qd2_ladder',
                      ['--date', target.isoformat(), '--recent', str(span)]),
        'qd_sentiment': ('ingest.jobs.qd1_sentiment',
                         ['--date', target.isoformat(), '--recent', str(span)]),
        'qd_sector': ('ingest.jobs.qd3_sector',
                      ['--date', target.isoformat(), '--recent', str(span)]),


        'scores': ('ingest.jobs.d2_scores',
                   ['--date', target.isoformat(), '--recent', str(span)]),
        'sectortrend': ('ingest.jobs.d3_sector_trend',
                        ['--date', target.isoformat(), '--recent', str(span)]),
        'ignition': ('ingest.jobs.d4_ignition',
                     ['--date', target.isoformat(), '--recent', str(span)]),
        'scoretrend': ('ingest.jobs.d5_score_trend',
                       ['--date', target.isoformat(), '--recent', str(span)]),

        # 判据的入口就在它自己模块里（`main()` 有 argparse），不再包一层同名壳。
        # 不传 --recent：它按 `--date` 那一天算，历史要补由 app.compute 自己 --backfill 跑。
        'signal': ('app.compute.sector_trend_signal',
                   ['--date', target.isoformat()]),

        # 资金轮动趋势（统计量，不是信号）。不传 `--basis` ⇒ 按参数行的 `basis` 列表跑，
        # 2026-10-12 起是两条：`share`（份额，剔全场共同放量）与 `turnover`（换手强度，剔板块盘子大小），
        # 判据版本各走各的（`sft-1` / `stn-1`）。主力净额那条 basis 因为 `fund_flow_rank` 是空表会
        # 明写 skipped、不写行。换手那条有自己的门：一行都没算出来 ⇒ 退出码 1，不陪 share 一起假绿。
        'flowtrend': ('app.compute.sector_flow_trend',
                      ['--date', target.isoformat()]),

        # 分配者视角。判据与权重只住在 `app/compute/allocator.py` 一处，这里只是调用点。
        'allocator': ('app.compute.allocator', ['--date', target.isoformat()]),

        # 选股汇总（交叉命中榜）：把各链当天选出的票并成一票一行。**排在所有源之后**
        # （倍量/突破/埋伏在 6~8 步、龙虎榜在 lhb、评分在 scores、分配者在 allocator 之后），
        # 否则数到的是昨天那份名单。汇总口径与各榜选法只住 `app/compute/confluence.py` 一处。
        'confluence': ('app.compute.confluence',
                       ['--date', target.isoformat(), '--recent', '1']),

        # 情绪两档逐日：先抓乐咕（有则覆盖回算值，legu 不会被 local 盖掉），
        # 再由 market_breadth 回算补位。两步是一条命令里的两次写，幂等。
        'sentiment': ('app.compute.sentiment_daily', ['--live']),

        # 行业资金流入库。归属日由交易日历判（脚本自己拒写不可信的日子），不传 --date：
        # 链跑到这里必然是当天或次日凌晨，正是唯一可归属的窗口。
        'sectorflow': ('ingest.jobs.c22_sector_flow', []),

        # 板块名 → 东财板块页（BK 码）。整表刷新，无参数：它对的名单来自库里现读的板块名，
        # 归属日在这里没有意义（对表不是逐日读数，新鲜度就是表里的 tried_at）。
        'boardlink': ('ingest.jobs.c24_em_board_link', []),

        # 不传 --date：质量审计自己算"还没审过的交易日"，缺 2 天就跑 2 次
        'quality': ('ingest.jobs.e2_quality_audit', []),
    }


    if args.dry_run:
        print('\n=== dry-run：要跑的步骤 ===')
        for i, (name, what, core) in enumerate(STAGES, 1):
            if name in skip:
                print('  %d. %-11s 跳过（--skip）' % (i, name))
                continue
            mod, extra = plan[name]
            # 打的是真正会执行的命令形态（-m），不是"看着像能跑"的路径
            print('  %d. %-11s %s%s\n       $ %s'
                  % (i, name, what, '（骨干）' if core else '',
                     ' '.join(['-m', mod, *extra])))
            # --p0-instant：标出 d1 会提前到 snapshot 之后就地跑，靠后步骤待吃。
            if name == 'snapshot' and args.p0_instant and 'indicators' not in skip:
                im, ie = plan['indicators']
                print('       ★ --p0-instant：snapshot 后就地跑 -m %s %s'
                      % (im, ' '.join(ie)))
            if name == 'indicators' and args.p0_instant:
                print('       ↳ 已被 --p0-instant 就地跑过，届时跳过重复')
        print('\nDONE（dry-run 未写库）')
        return 0

    done, failed, skipped, stopped = [], [], [], False
    steps: dict[str, dict] = {}                 # 每一步：第几次跑、退出码、耗时、归因
    t_start = dt.datetime.now()

    def _run(name: str, mod: str, extra, *, round_no: int = 1):
        t0 = time.time()
        rc, tail = run_script(mod, *extra, timeout=step_timeout(name))
        ent = steps.setdefault(name, {})
        ent.update(rc=rc, sec=round(time.time() - t0, 1), round_no=round_no,
                   module=mod, tail=(tail or '')[-240:])
        return rc, tail
    core_retried: dict[str, tuple[int, int]] = {}   # 骨干步骤：重试次数 → 最终退出码
    deferred_recovered: list[str] = []              # 非骨干：链跑完后补跑成功的
    p0_done: set[str] = set()        # --p0-instant 就地已跑过的步骤（靠后同名步骤跳过）
    fail_reason: dict[str, str] = {}     # 失败的步骤 → 归因（数据不全/网络/无权限…）
    by_design = False        # daily 退出码 2：按设计停下，不算"坏了"
    minute_catchup: list[str] = []   # 链上就地补跑过分钟线入库的位置（首轮 / 链尾补跑）
    for i, (name, what, core) in enumerate(STAGES, 1):
        if name in skip:
            skipped.append(name)
            print('\n=== %d/%d %s —— 跳过（--skip） ===' % (i, len(STAGES), what))
            continue
        # --p0-instant 在 snapshot 跑成之后就把 d1 就地算过一遍了（见下面），
        # 靠后的 indicators 步骤在这里吃掉，不再重复跑。
        if name in p0_done:
            done.append(name)
            print('\n=== %d/%d %s —— 已由 --p0-instant 就地跑过，跳过重复 ==='
                  % (i, len(STAGES), what))
            continue
        if stopped and name in ('indicators', 'scores', 'sectortrend'):
            skipped.append(name)
            print('\n=== %d/%d %s —— 跳过：日线没追上目标日，算了也是旧的 ==='
                  % (i, len(STAGES), what))
            continue
        print('\n=== %d/%d %s ===' % (i, len(STAGES), what), flush=True)
        mod, extra = plan[name]
        rc, tail = _run(name, mod, extra)
        # 骨干失败就地立刻重试（非骨干留到整条链跑完后统一补，见下面第二轮）
        tries = 0
        while rc != 0 and core and _retryable(name, rc) and tries < CORE_RETRIES:
            tries += 1
            wait = RETRY_BACKOFF[min(tries - 1, len(RETRY_BACKOFF) - 1)]
            print('  ↻ 骨干步骤失败（退出码 %s），%d 秒后就地第 %d 次重试：%s'
                  % (rc, wait, tries, what), flush=True)
            time.sleep(wait)
            rc, tail = _run(name, mod, extra, round_no=tries + 1)
        if tries:
            core_retried[name] = (tries, rc)
        if rc == 0:
            done.append(name)
            # 分钟线的下载在这里、入库在获取段，两段各自跑 ⇒ 文件可能是"这一段刚落地"的。
            # 补一次入库（幂等，全部已入库时实测 6.5 秒），否则下面的 `spike` 只会记 degraded。
            # 这一路每晚都会走，属于正常收口，不写进 verdict 那句（只有"下载晚到"那种
            # 真自愈才需要让它在台账上看得见，见下面链尾补跑那一处）。
            if name == 'tdxdl':
                ingest_minute_bars(target, '首轮下载刚把盘后数据下下来')
            # --p0-instant：日线+快照入库后就地跑基础因子(d1) P0 字段分值。
            # d1 只读 market_data.stock_daily（P0 数据），快照一落就能算，不必等两融/龙虎榜
            # 这些网络源 —— 评分在 P0 数据阶段就有字段分值可用。
            # 失败不回滚：靠后的 indicators 步骤照常再跑一遍就好。
            if (name == 'snapshot' and args.p0_instant
                    and 'indicators' not in skip and not stopped):
                print('\n    --p0-instant：就地跑基础因子 P0 字段分值 ====', flush=True)
                ind_mod, ind_extra = plan['indicators']
                rc2, _tail2 = run_script(ind_mod, *ind_extra,
                                        timeout=step_timeout('indicators'))
                if rc2 == 0:
                    p0_done.add('indicators')
                    print('    → 就地跑成，靠后 indicators 步骤将跳过')
                else:
                    print('    → 就地跑 d1 失败（退出码 %s）：靠后 indicators 步骤照跑'
                          % rc2)
            continue
        # e2_quality_audit 的 2/3 是"审完了，有规则没通过"，不是"这一步坏了"。
        # 混在一起会让整次追平显示成 PARTIAL，而真正该看的是质量看板。
        if name == 'quality' and rc in (2, 3):
            done.append(name)
            print('  → 审计跑完了，但有规则未通过（退出码 %d）：去 /data → ② 数据质量看'
                  % rc)
            continue
        # p0 前置下载的 3 是"日线到位、只有分钟线没拿到"。日线才是这条链的骨干，
        # 分钟线缺了不该把整次追平标成 PARTIAL —— 但要在输出里说清楚。
        if name == 'tdxdl' and rc == 3:
            done.append(name)
            steps[name]['soft'] = '分钟线没拿到（退出码 3），日线已到位 —— 不算故障'
            print('  → 日线已到位，分钟线没拿到（退出码 3）。单独重跑：'
                  'python -m app.tdx_client.run_agent --only s5_dialog '
                  '--only s7_minute')
            continue
        # c21/c22 这两个"只给即时截面、报文里没有日期"的口，非交易日会自己拒写（rc 2/3）。
        # 它们的宿主循环把 rc=3 记成"非交易日跳过（ok=True）"，链这边若算失败就是
        # 同一件事在两个入口读出两种结论（2026-10-07 休市实测：链尾报"3 步失败"，
        # 而面板上这两条是绿的）。拒写是对的，归属日不可证明时写进去才是错。
        if name in ('fundflow_rank', 'sectorflow') and rc in (2, 3):
            done.append(name)
            steps[name]['soft'] = '按口径拒写：归属日不可证明（非交易日抓的是上一场的数）—— 不算故障'
            print('  → 按口径拒写：今天不是交易日，即时截面证明不了它属于哪一场。'
                  '不是故障（要调试加 --force，但写出来的归属日不可信）')
            continue
        # 财务多期：2 = 这一跑没有要刷的票（缺口 0 且名单里都在门槛内，或名单本身为空），4 = 六分钟预算用完还剩几只。
        # 两者都不是"这一步坏了"：2 是库里都还新（真原因由那一步自己按三档点名，见 c25 `_why_nothing_to_refresh`），
        # 4 是明天接着刷（读侧按"多久没重采"说话）。
        # 但 3（有成有败或跑完还有缺口）与 1（全败，源那条腿断了）照旧算失败 —— 那才是需要人看的。
        if name == 'financials' and rc in (2, 4):
            done.append(name)
            steps[name]['soft'] = ('这一跑没有要刷的票（库里都还在门槛内，或本机名单为空）—— 不算故障；'
                                   '是哪一种看上面对那三档的点名' if rc == 2
                                   else '六分钟预算用完，剩下的明天接着刷 —— 不算故障')
            print('  → %s' % steps[name]['soft'])
            continue
        # 首板形状：2 = 这段区间里一条首板都没有（按设计不写，不是坏了）。它每天只重算最近 40 场，
        # 遇上长假那几场没有新首板是正常读数。1（一条都没写成）与 3（写了但体检不过）照旧算失败。
        if name == 'fbshape' and rc == 2:
            done.append(name)
            steps[name]['soft'] = '这一段区间里没有首板事件（按设计不写）—— 不算故障'
            print('  → %s' % steps[name]['soft'])
            continue
        # 首板分析：2 = 这一段母体一条首板都没有（按设计不写，不是坏了；同 `fbshape` 那一条理由，
        # 长假前后会有这种场次）。1（库里没有日线/取不到那一天）与 3（写了但体检不过）照旧算失败。
        if name == 'fbdaily' and rc == 2:
            done.append(name)
            steps[name]['soft'] = '这一段区间里没有首板事件（母体 fb-2 那些场次一条都没有）—— 按设计不写，不算故障'
            print('  → %s' % steps[name]['soft'])
            continue
        # 象限轨迹：2 = 这一跑的母体事件在可用读数（score 里 is_stale=false 那一批）之前，窗口一场都凑不出
        # （按设计不写，不是坏了）。1（一条都没写成）与 3（写了但体检不过）照旧算失败。
        if name == 'quadtrack' and rc == 2:
            done.append(name)
            steps[name]['soft'] = ('这一段没有能凑出窗口的母体事件（score 可用批次还没铺到那些启动日）'
                                   '—— 按设计不写，不算故障')
            print('  → %s' % steps[name]['soft'])
            continue
        # 爆量/急拉联合计数：2 = 这一段没有母体事件、或每个事件的启动日前一场日线都凑不出
        # （按设计不写，不是坏了；同 `fbshape` 那段写法）。1（一条都没写成）与 3（写了但体检不过）照旧算失败。
        if name == 'spikedays' and rc == 2:
            done.append(name)
            steps[name]['soft'] = ('这一段没有母体事件、或窗口一场日线都凑不出 —— 按设计不写，不算故障')
            print('  → %s' % steps[name]['soft'])
            continue
        # 剩下的真失败：归因成一句人话，让"这步没成"不再是光秃秃一个名字。
        reason = classify_failure(name, rc, tail)
        fail_reason[name] = reason
        failed.append(name)
        if core:
            stopped = True
        # c10 用退出码 2 表示"vipdoc 还没到目标日，按设计不走网络兜底"
        if name == 'daily' and rc == 2:
            by_design = True
            print('  → vipdoc 还没覆盖 %s，且没有 --allow-web：**按设计停在这一步**。'
                  % target)
            print('    先把通达信盘后数据下下来，或者显式加 --allow-web 走兜底。')
        else:
            print('  → 失败：%s' % reason)

    # ---- 非骨干步骤的第二轮：整条链跑完再补 ----
    # 用户 2026-09-27 裁定的两档重试：骨干就地立刻重试（上面的 while），非骨干等全链
    # 跑完统一补一轮。跑到这里通常已过了几分钟冷却，网络源更容易成，
    # 也不会把日线主链拖在一条龙虎榜后面。
    if DEFERRED_RETRY:
        again = [n for n in failed if n not in CORE]
        if again:
            print('\n=== 补跑非骨干步骤（整条链跑完后的第二轮）：%s ==='
                  % '、'.join(again), flush=True)
        for name in again:
            what = next(w for n, w, _ in STAGES if n == name)
            mod, extra = plan[name]
            print('\n--- %s ---' % what, flush=True)
            rc, tail = _run(name, mod, extra, round_no=2)
            if rc == 0:
                failed.remove(name)
                fail_reason.pop(name, None)
                done.append(name)
                deferred_recovered.append(name)
                print('  → 补跑成功', flush=True)
                # 2026-10-15 的形状：下载在链尾才成功（18:52 落盘），而 c16 入库早在获取段
                # 跑空了 ⇒ 当天分钟线当天进不了库。这里立刻补一次入库；`spike` 排在
                # `tdxdl` 之后（`again` 沿 `failed` 的链上顺序），所以下一轮重跑它时就有数了。
                if name == 'tdxdl':
                    rc_min = ingest_minute_bars(target, '链尾补跑才把盘后数据下下来')
                    minute_catchup.append('链尾' if rc_min == 0 else '链尾（入库没做成）')
            else:
                fail_reason[name] = classify_failure(name, rc, tail)
                print('  → 仍然失败：%s' % fail_reason[name], flush=True)
        # 数据补齐了，体检结论必须跟着更新 —— 否则页面上的"最近一次"还是补跑前那份，
        # 看着绿、查的却是旧数据（与 09-25 那条"看板一片绿而没人查过"同一个坑）。
        if deferred_recovered and 'quality' in done and 'quality' not in again:
            print('\n--- 补跑改动了数据，重跑质量审计 ---', flush=True)
            mod, extra = plan['quality']
            rc, tail = _run('quality', mod, extra, round_no=3)
            if rc not in (0, 2, 3):
                done.remove('quality')
                failed.append('quality')
                fail_reason['quality'] = classify_failure('quality', rc, tail)

    print('\n=== 验收（对照目标日 %s） ===' % target)
    lag = verify(target)

    is_core = {name: core for name, _, core in STAGES}
    core_failed = [n for n in failed if is_core[n]]
    # 失败原因跟着步骤走进摘要。只有名字时，"追平失败"跟"数据缺"分不清，
    # 下面的括号里直接写明每步为什么没成。
    part = ', '.join('%s（%s）' % (n, fail_reason[n]) for n in failed)
    line = ('追平 %s：%d 步成功' % (target, len(done))
            + ('，骨干重试后成功：%s' % '、'.join(
                '%s×%d' % (n, t) for n, (t, rc) in core_retried.items() if rc == 0)
               if any(rc == 0 for _t, rc in core_retried.values()) else '')
            + ('，非骨干补跑成功：%s' % '、'.join(deferred_recovered)
               if deferred_recovered else '')
            + ('，分钟线入库已就地补跑（%s）' % '、'.join(minute_catchup)
               if minute_catchup else '')
            + ('，%d 步失败：%s' % (len(failed), part) if failed else '')
            + ('，%d 步跳过' % len(skipped) if skipped else '')
            + (('；没追上目标日的：' + '、'.join(lag)) if lag else '；各表都到目标日'))
    print('\n%s %s' % ('DONE' if not failed else 'PARTIAL', line), flush=True)

    # 台账落盘：界面要能回答"昨天到底哪一步没成、为什么、跑了多久"，
    # 以及"这一步能不能单独补一次"（`--only`）。原先这些只在 stdout 里，跑完就没地方看。
    labels = {n: (w, core) for n, w, core in STAGES}
    order = {n: i for i, (n, _w, _c) in enumerate(STAGES)}
    rows = []
    for name, ent in steps.items():
        label, core = labels.get(name, (name, False))
        ok = ent.get('rc') == 0
        # 三态而不是两态：`refused`（按口径拒写 / 只要到了日线就算收工）既不是成功也不是故障。
        # 分不清这三态，重试阶梯就会天天去重跑一个"本来就该拒写"的步骤（用户 10-08 要的
        # 是"只重试之前失败的"，而 by-design 的拒写每次都会以同一个退出码回来）。
        rows.append({'name': name, 'label': label, 'core': core, 'rc': ent.get('rc'),
                     'seconds': ent.get('sec'), 'rounds': ent.get('round_no'),
                     'module': ent.get('module'),
                     'status': 'ok' if ok else ('refused' if ent.get('soft') else 'fail'),
                     'soft': ent.get('soft'),
                     'reason': None if ok else (ent.get('soft') or fail_reason.get(name)),
                     'tail': None if ok else (ent.get('tail') or '')[-180:]})
    rows.sort(key=lambda r: order.get(r['name'], 999))
    ledger = {
        'written_at': dt.datetime.now().astimezone().isoformat(timespec='seconds'),
        'target': target.isoformat(),
        'started_at': t_start.astimezone().isoformat(timespec='seconds'),
        # 「链次」锚点：一整遍链 = 一个新锚点；`--only` 的补跑（阶梯点的、人点的都算）
        # **继承上一份的锚点**。重试阶梯用它判"这是不是同一场的档期"——它不能拿 `written_at`
        # 当锚点，因为**每跑一档都会把 written_at 刷新一次**，那样每一档都会被当成新一场、
        # 同一档会被 60 秒一轮地反复重跑。
        'cycle': t_start.astimezone().isoformat(timespec='seconds'),
        'verdict': 'DONE' if not failed else 'PARTIAL', 'line': line,
        'lagging_tables': lag, 'ran_only': sorted(only) if only else None,
        'steps': rows,
    }
    if only:
        # `--only` 只跑了几步：**合并**进上一份台账，不是整份覆盖。
        # 10-08 的第一版直接把新 rows 写下去，于是"补跑 fundflow 成功"这一笔把
        # 同一天另外三步失败一起洗掉了 —— 面板上从"4 步没成"变成"0 步没成"，
        # 而库里其实还缺着。补跑的意义是让别的失败**继续看得见**。
        old = read_ledger()
        if old.get('target') == target.isoformat():
            keep = {s['name']: s for s in (old.get('steps') or [])}
            keep.update({s['name']: s for s in rows})
            merged = sorted(keep.values(), key=lambda r: order.get(r['name'], 999))
            ledger['steps'] = merged
            ledger['cycle'] = old.get('cycle') or old.get('started_at') or old.get('written_at')
            still = [r['name'] for r in merged if r.get('status') == 'fail']
            # 结论也要按合并后的整体说：本次那一句"1 步成功"只描述本次跑的这几步，
            # 拿它当"今天全好了"就是假绿。
            ledger['verdict'] = 'DONE' if not still else 'PARTIAL'
            ledger['merged'] = ('本次只跑了 %s；合并台账里仍有 %d 步没成：%s'
                                % ('、'.join(sorted(only)), len(still), '、'.join(still) or '无'))
    try:
        write_ledger(ledger)
    except Exception as exc:  # noqa: BLE001  台账写坏不影响这次追平本身
        print('  ⚠️ 步骤台账没写成：%s: %s' % (type(exc).__name__, str(exc)[:120]))

    if not failed:
        return 0
    if by_design:
        return 2
    return 1 if core_failed else 3


if __name__ == '__main__':
    raise SystemExit(main())
