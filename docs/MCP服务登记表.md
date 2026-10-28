# MCP 服务登记表 · 工具 ↔ 来源端点 ↔ 屏幕 的链接台账

> **这份文档只做一件事：把「MCP 上暴露的每一个工具」与「它背后真正在跑的那个端点、那个函数、那张表、屏幕上哪一屏在展示同一份数」逐条钉在一起。**
> 它不是功能说明（逐屏功能点在 `docs/功能点说明/`，每个数怎么算在 `docs/数据加工工作指南/`）。
> **改 MCP 工具＝改这份表**：工具增删、端点搬家、参数改名、表换名，都必须同步到这里，否则 `tests/test_mcp_registry_sync.py` 会红。

## 目录

- [一、服务形态与三个入口](#一服务形态与三个入口)
- [二、权限边界与失败口径](#二权限边界与失败口径)
- [三、A 层：全市场榜与数据加工产出（24 个专用工具）](#三a层全市场榜与数据加工产出24-个专用工具)
- [四、B 层：个股明细（8 个专用工具）](#四b层个股明细8-个专用工具)
- [五、C 层：只进目录、靠兜底工具取](#五c层只进目录靠兜底工具取)
- [六、通用层 3 个工具](#六通用层-3-个工具)
- [七、全量端点索引（现扫，不抄条数）](#七全量端点索引现扫不抄条数)
- [八、代码引用清单：改哪一处会牵动哪几处](#八代码引用清单改哪一处会牵动哪几处)
- [九、界面侧：AI 探索 · MCP 服务](#九界面侧ai-探索--mcp-服务)
- [十、与既有两套 AI 取数的关系](#十与既有两套-ai-取数的关系)
- [十一、持续更新：新增一屏要做的固定动作](#十一持续更新新增一屏要做的固定动作)
- [十二、校验与实跑命令](#十二校验与实跑命令)

---

## 一、服务形态与三个入口

同一个登记表（`mcp_service/capabilities.py`）同时喂三处，避免"三份台账各说各话"：

| 入口 | 形态 | 谁在用 | 文件 |
|---|---|---|---|
| MCP stdio | `python -m mcp_service` | 本机 Claude Code / Qoder / codex 等，一条 JSON 自己拉起进程 | `mcp_service/server.py` |
| MCP streamable HTTP | `python -m mcp_service --transport streamable-http --port 8920` | 局域网机器、别的电脑、常驻服务 | 同上 |
| 界面 tab | `GET /api/mcp/registry` 等三个端点 | 「AI 探索 · MCP 服务」这一片 | `vr/mcpboard.py`（处理函数）＋ `vr/app.py` 里的路由 |

**取数一律走本机 HTTP，不重新实现任何算法**：MCP 工具内部是 `GET http://127.0.0.1:8910/api/...`，与浏览器点那一屏读的是同一个函数、同一份返回。好处是"智能体拿到的数"与"屏幕上看到的数"**结构上不可能不一致**；代价是**后端必须在跑**——所以状态端点与 tab 都显式体检这一条，取不到就报错，不返回空表。

- 基址：环境变量 `YUANSHAN_MCP_BASE`（默认 `http://127.0.0.1:8910`）
- **工作目录**：`-m mcp_service` 靠工作目录找包，而多数宿主不给设 cwd ⇒ 界面那段配置里同时带 `cwd` 与
  `PYTHONPATH=项目根`（10-22 补：少了 `PYTHONPATH` 时粘过去报 `ModuleNotFoundError: mcp_service`，看着像"服务没装"）。
  实测姿势＝在项目根之外的目录（`%TEMP%`）原样起一次，35 个工具照样列得出
- 鉴权：后端设了 `VR_API_KEY` 时，MCP 侧从同名环境变量读，拼 `Authorization: Bearer <key>`
- 超时：`YUANSHAN_MCP_TIMEOUT`（默认 60 秒；慢榜如象限/覆盖体检单次可到 30 秒）
- HTTP 模式的监听与端口：`YUANSHAN_MCP_HTTP_HOST`（默认 `127.0.0.1`）＋ `YUANSHAN_MCP_HTTP_PORT`（默认 `8920`，
  界面自检那一项就按这个端口探"有没有人听"，没起时如实报红叉，不影响 stdio 那一条）

---

## 二、权限边界与失败口径

1. **纯只读**：只注册 `@app.get` / `@app.head` 的端点；写类端点（journal/add、modes/delete、notify/preview、data-process 重跑触发…）**不进登记表、不进索引、兜底工具也拒绝**。判据见 `mcp_service/capabilities.py` 的 `endpoint_index()`——它按装饰器方法筛，不是靠名字猜。
2. **不触发计算、不起跑批、不发起出网请求**。`refresh=true` 这一类参数在 MCP 侧一律强制为 `false`（登记表里 `refresh_forbidden=True` 的条目），因为那会抢 8910 的互斥、把正在跑的全量测试窗口污染掉。
3. **失败不许兜成空值**：后端没在跑、端点 404、SQL 报错 → MCP 工具**抛异常**（智能体侧看到 `isError`），绝不返回 `[]` 或 `{}`。空值会被下游写成一句合理的假读数，这是本项目踩过的坑。
4. **返回附带新鲜度回执**：每条结果里加 `_meta`（`source_path`、`as_of`＝本次实际读到的数据日、`fetched_at`、`degraded`）。`as_of` 来自 `/api/coverage` 与返回体自身的 `date` 字段，判"数据跟没跟上"以 `market_data.stock_daily` 的 max 为基准现查，不在文档里抄死日期。

---

## 三、A 层：全市场榜与数据加工产出（24 个专用工具）

一屏一工具。工具名＝`capability_id`＝登记表里的 `id`。列里的"主读表"是**取数模块实际 SQL 里出现的表**（`grep` 实核），不是猜的；「现算」表示该模块不落新表、只读已有表现场聚合。

| 工具 / capability_id | 标签 · 屏幕落点 | 来源端点 → 处理函数 | 取数模块 · 主读表 | 参数（默认值） |
|---|---|---|---|---|
| `quadrant_board` | 四维象限 · `/quadrant` | `/api/quant/quadrant` → `vr/app.py:564` | `duanxian/quant.py`：`analytics.score`、`analytics.score_trend` | `date=''`、`min_x`、`min_y`、`limit=200(1..1000)` |
| `rank_stability` | 名次稳定榜 · `/quadrant` | `/api/quant/rank-stability` → `vr/app.py:620` | `duanxian/quant.py`：`analytics.score` | `days=7(2..20)`、`limit=50(5..200)`、`date=''` |
| `rank_momentum` | 名次稳步前进榜 · `/quadrant` | `/api/quant/rank-momentum` → `vr/app.py:643` | `duanxian/quant.py`：`analytics.score` | `metric='xy'`、`limit=80(5..200)`、`date=''` |
| `momentum_follow` | 前进榜前 N 名今日与近 5 日表现 · `/quadrant` | `/api/quant/momentum-follow` → `vr/app.py:667` | `duanxian/momentum_follow.py`：`analytics.score`、`analytics.volume_double`、`analytics.volume_breakout_signal`、`analytics.ambush_setup`、`analytics.trend_stock_daily`、`analytics.abnormal_move_daily`、`market_data.stock_daily` | `metric='xy'`、`date=''`、`limit=80(5..200)` |
| `volume_double` | 倍量形态 · `/volume-double` | `/api/quant/volume-double` → `vr/app.py:745` | `analytics.volume_double`、`analytics.volume_ratio_daily` | `date=''`、`limit=150(1..800)`、`min_energy=0(0..40)`、`quadrants=''`、`step_gap_min=None(-40..40)` |
| `energy_track` | 加权分进榜后逐日涨跌幅 · `/volume-double` | `/api/quant/energy-track` → `vr/app.py:794` | `duanxian/quant.py`：`analytics.score`、`market_data.stock_daily`（后复权） | `date=''`、`top_n=None(10..500，默认取阈值表 120)` |
| `startup_volume` | 启动前量能分布 · `/volume-double` | `/api/quant/startup-volume` → `vr/app.py:1039` | `analytics.startup_volume_profile` | `cohort='board4'`、`months=3(1..12)`、`limit=200(1..1200)` |
| `first_board_shape` | 首板形状预示 · `/volume-double` | `/api/quant/first-board-shape` → `vr/app.py:1064` | `duanxian/first_board_view.py`：`analytics.first_board_shape`、`analytics.volume_ratio_daily` | `months=60(1..60)`、`bucket='dbl_days'`、`buckets=4(2..10)`、`metric='ex5'`、`board=''`、`complete_only=true`、`exclude_locked=true`、`min_bars=20(1..30)` |
| `startup_quadrant_track` | 启动前 30 场象限轨迹 · `/volume-double` | `/api/quant/startup-quadrant-track` → `vr/app.py:1096` | `duanxian/quadrant_track_view.py`：`analytics.startup_quadrant_track`、`analytics.startup_volume_profile`、`analytics.first_board_shape`、`analytics.score` | `cohort='board4'`、`months=12(1..12)`、`window=30`、`key='all'`、`limit=200(1..600)` |
| `startup_spike_days` | 启动前爆量与突然拉高 · `/volume-double` | `/api/quant/startup-spike-days` → `vr/app.py:1126` | `duanxian/spike_days_view.py`：`analytics.startup_spike_days`、`analytics.startup_volume_profile`、`analytics.first_board_shape`、`analytics.volume_ratio_daily` | `cohort='anyfirst'`、`months=12(1..12)`、`window=30`、`jump_pct=0.07(0.03..0.15)`、`limit=200(1..600)` |
| `volume_breakout` | 突破信号 · `/volume-breakout` | `/api/quant/volume-breakout` → `vr/app.py:1018` | `analytics.volume_breakout_signal` | `date=''`、`limit=150(1..800)`、`signal_type=''` |
| `ambush_setup` | 埋伏票 · `/volume-breakout` | `/api/quant/ambush` → `vr/app.py:872` | `analytics.ambush_setup` | `date=''`、`limit=150(1..800)`、`types=''`、`only_strict=false`、`min_weekly_ups=0(0..10)` |
| `confluence_hits` | 选股汇总（各榜交叉命中）· `/confluence` | `/api/quant/confluence` → `vr/app.py:894` | `duanxian/confluence_view.py`：`analytics.confluence_daily`、`analytics.score` | `date=''`、`min_hits=1(1..6)`、`sources=''`、`limit=300(1..1000)` |
| `system_entry` | 交易体系榜 · `/confluence` | `/api/quant/system-entry` → `vr/app.py:952` | `duanxian/system_entry_view.py`：`analytics.system_entry`、`analytics.system_entry_stats` | `date=''`、`only_entry=true`、`min_gap=0(0..30)`、`limit=300(1..1000)`、`days=250(10..600)` |
| `system_entry_cohort` | 体系票近六个月名单 · `/confluence` | `/api/quant/system-entry-cohort` → `vr/app.py:978` | `duanxian/system_entry_cohort.py`：`analytics.system_entry`、`analytics.confluence_daily`、`analytics.score`、`market_data.stock_daily` | `months=6(1..24)` |
| `intraday_spike` | 异动筛查（盘中冲高/触板）· `/spike` | `/api/quant/intraday-spike` → `vr/app.py:926` | `duanxian/spike_view.py`：`analytics.intraday_spike`、`market_data.stock_daily` | `date=''`、`tags=''`、`min_hi=0(0..30)`、`limit=400(1..1000)`、`days=43(2..250)` |
| `trend_stocks` | 趋势股 · `/trend-stocks` | `/api/quant/trend-stocks` → `vr/app.py:1353` | `duanxian/trend_view.py`：`analytics.trend_stock_daily`、`market_data.sector_daily` | `date=''`、`lookback=5`、`limit=300(1..1000)` |
| `sector_heat` | 板块热度与点火 · `/sector-heat` | `/api/quant/sector-heat` → `vr/app.py:1476` | `analytics.sector_heat_daily`、`analytics.ignition` | `date=''`、`type=''`、`stage=''`、`sort='heat'`、`limit=200(1..1000)`、`stats='day'` |
| `sector_trend` | 板块趋势（位置×阶段）· `/sector-heat` | `/api/quant/sector-trend` → `vr/app.py:1531` | `analytics.sector_trend`、`analytics.sector_trend_signal`、`config.setting` | `date=''`、`regime=''`、`type=''`、`scope='sector'`、`spec=''`、`sort='dip'`、`limit=200(1..1000)` |
| `sector_allocator` | 分配者视角 · `/allocator` | `/api/quant/allocator` → `vr/app.py:1510` | `duanxian/allocator_view.py`：`analytics.sector_allocator`、`market_data.index_daily` | `date=''`、`limit=300(20..1000)` |
| `abnormal_move` | 异动提醒 · 首页（短线情绪下面） | `/api/market/abnormal-move` → `vr/app.py:1330` | `duanxian/abnormal_view.py`：`analytics.abnormal_move_daily` | `date=''`、`recent=10`、`hot_limit=20(1..100)`、`limit=60(1..200)` |
| `first_board_daily` | 首板分析 · 首页（换手率 TOP20 上面） | `/api/quant/first-board-daily` → `vr/app.py:1582` | `duanxian/first_board_daily.py`：`analytics.first_board_daily`、`analytics.first_board_shape`、`analytics.volume_double` | `date=''`、`limit=80(10..240)`、`shapes=''` |
| `risk_signal` | 风险提示（宽度×阿姆斯）· 首页（盘面数据那一屏内部，异动提醒上面） | `/api/quant/risk-signal` → `vr/app.py:1555` | `duanxian/risk_signal.py`：只读 `analytics.market_breadth` | `months='3'`、`group='day'`、`date=''` |
| `market_emotion` | 短线情绪 · 首页 | `/api/market/emotion` → `vr/app.py:292` | `duanxian/limit_up_board.py`：`analytics.limit_up_pool_daily`（母体，2026-10-26 起）、`analytics.market_sentiment_daily`、`analytics.board_node_daily`；`vr/market.py` 只做接法 | 无参数 |

> ⚠️ `refresh` 参数在 A 层条目上一律存在，但 **MCP 侧强制 `false`**（见第二节第 2 条），所以参数列里不写它。

## 四、B 层：个股明细（8 个专用工具）

| 工具 / capability_id | 标签 · 屏幕落点 | 来源端点 → 处理函数 | 取数模块 · 主读表 | 参数（默认值） |
|---|---|---|---|---|
| `stock_scores` | 趋势评分八分量 · `/stock-review` | `/api/quant/scores` → `vr/app.py:584` | `analytics.score`、`analytics.score_detail` | `code`（必填，带市场后缀全码）、`date=''` |
| `stock_score_trend` | 评分窗口斜率/连续上行 · `/stock-review` | `/api/quant/score-trend` → `vr/app.py:598` | `analytics.score`、`analytics.score_trend` | `code`、`window=10(2..120)`、`sessions=60(1..500)` |
| `stock_indicators` | 35 项技术指标读数 · `/stock-review` | `/api/stock/indicators` → `vr/app.py:412` | `analytics.indicator_value` | `code`、`date=''` |
| `stock_dossier` | 个股量化档案（八块拼一页）· `/stock-review` | `/api/quant/stock-dossier` → `vr/app.py:999` | `duanxian/stock_dossier.py`：`analytics.score(+_detail/_trend)`、`volume_double`、`volume_breakout_signal`、`ambush_setup`、`confluence_daily`、`sector_heat_daily`、`startup_volume_profile` | `code`、`date=''` |
| `stock_facts` | 个股信息主档 · `/stock-review` | `/api/stock/facts` → `vr/app.py:1153` | `duanxian/stock_facts.py`：`reference_data.security/stock_profile/sector_member`、`market_data.stock_daily/stock_snapshot`、`analytics.stock_brief` | `code` |
| `stock_fundamentals` | 财务多期与外推 · `/stock-review` | `/api/stock/fundamentals` → `vr/app.py:1249` | `duanxian/stock_fundamentals.py`：`reference_data.stock_financial_period` | `code` |
| `stock_price_volume_trend` | 量价走势 日/周/月 · `/stock-review` | `/api/stock/price-volume-trend` → `vr/app.py:1290` | `market_data.stock_daily`（本机日线聚合，前复权形状） | `code`、`days=60(6..250)`、`weeks=60(6..160)`、`months=48(6..64)` |
| `security_search` | 找一只票（代码/名称/全拼/首字母）· 全站搜索框 | `/api/quant/security-search` → `vr/app.py:723` | `duanxian/security_search.py`：`reference_data.security` | `q`、`limit=20(1..100)`、`kinds=''` |

## 五、C 层：只进目录、靠兜底工具取

这些条目**不占工具名**（35 个工具已经够多，全塞进去模型会挑花），但 `list_capabilities()` 会连参数 schema 一起返回，`capability_data()` 按 `capability_id` 或端点路径就能取到。

| capability_id | 标签 · 屏幕落点 | 来源端点 → 处理函数 | 主读表 / 备注 |
|---|---|---|---|
| `stock_metrics_ledger` | 全指标总账 · `/stock-review` | `/api/stock/metrics-ledger` → `vr/app.py:1312` | `analytics.indicator_value`、`score`、`volume_ratio_daily`、`market_sentiment_daily` |
| `stock_volume_similarity` | 近 30 根量价形状相近样本 · `/stock-review` | `/api/stock/volume-similarity` → `vr/app.py:1269` | `analytics.startup_volume_profile`、`volume_ratio_daily`；首约 6 秒 |
| `confluence_history` | 单票交叉命中历史 · `/confluence` | `/api/quant/confluence-history` → `vr/app.py:914` | `duanxian/confluence_view.py` |
| `sector_members` | 板块成分股明细 · `aiPresets` 二级展开 | `/api/quant/sector-members` → `vr/app.py:1375` | `reference_data.sector_member` |
| `new_high` | 自选股历史新高 · `/grouped` | `/api/quant/new-high` → `vr/app.py:698` | `reference_data.security`、`market_data.stock_daily` |
| `market_overview` | 盘面总览 · `/sector-heat`、首页 | `/api/market/overview` → `vr/app.py:260` | `market_data.sector_fund_flow_daily` |
| `market_turnover_top` | 换手率 TOP20 · 首页 | `/api/market/turnover-top` → `vr/app.py:346` | `analytics.ignition` |
| `market_first_board` | 昨日首板梯队 · 首页 | `/api/market/first-board` → `vr/app.py:332` | `vr/firstboard.py`：`analytics.board_node_daily` |
| `market_sector_flow` | 板块资金流累计 · `/sector-heat` | `/api/market/sector-flow` → `vr/app.py:269` | `duanxian/sector_flow.py`：`analytics.sector_flow_trend`、`market_data.fund_flow_rank/sector_fund_flow_daily` |
| `market_sector_cycle` | 板块周期 · 首页（原量化对照） | `/api/market/sector-cycle` → `vr/app.py:355` | `analytics.sector_flow_trend`、`analytics.ignition` |
| `market_sector_rotation` | 板块轮动 | `/api/market/sector-rotation` → `vr/app.py:388` | `analytics.indicator_value`、`analytics.sector_flow_trend` |
| `qd_sentiment_cycle` | 情绪周期（quantdash 口径）· `/qd-compare` | `/api/qd/sentiment-cycle` → `vr/app.py:446` | `qd.sentiment_cycle` |
| `qd_board_ladder` | 连板梯队（quantdash 口径）· `/qd-compare` | `/api/qd/board-ladder` → `vr/app.py:464` | `qd.board_ladder(+_member)` |
| `qd_sector_cycle` | 题材周期（quantdash 口径）· `/qd-compare` | `/api/qd/sector-cycle` → `vr/app.py:481` | `qd.sector_rotation` |
| `qd_sector_follow` | 前三甲轨迹与相似板块（qd 口径轨）· 首页、`/qd-compare` | `/api/qd/sector-follow` → `vr/app.py:498` | `qd.sector_follow`；`lag=0..3`（判据与落库在 `app/compute/qd_sector_follow.py`，链步 `qd_sector`，读侧只读行） |
| `qd_distribution` | 指标分布对照 · `/qd-compare` | `/api/qd/distribution` → `vr/app.py:497` | `days=0(0..5000)` |
| `qd_news` | 资讯条目 · `/intel` | `/api/qd/news` → `vr/app.py:515` | `qd.news_item` |
| `screen_rules` | 筛选条件登记 · `/screen` | `/api/screen/rules` → `vr/app.py:1677` | `config.screen_rule` |
| `screen_run` | 筛选器执行 · `/screen` | `/api/screen` → `vr/app.py:1693` | `duanxian/screen_view.py`：`analytics.indicator_value`、`market_data.stock_daily`；`rule`/`conds`/`logic`/`universe`/`date`/`limit` |
| `watchlist_groups` | 自选分组 · `/grouped`、`/my-stocks` | `/api/watchlist/groups` → `vr/app.py:1615` | `config.watchlist_group` |
| `watchlist_stocks` | 组内自选明细 · `/grouped` | `/api/watchlist/stocks` → `vr/app.py:1626` | `config.watchlist_stock`、`analytics.score` |
| `watchlist_group_analytics` | 分组整体分析 · `/grouped` 顶部 | `/api/watchlist/group-analytics` → `vr/app.py:1644` | `analytics.score`（现场聚合，慢） |
| `data_coverage` | 覆盖体检 · `/coverage` | `/api/coverage` → `vr/app.py:537` | `duanxian/coverage.py` 登记的逐表覆盖日 |
| `data_quality` | 质量闸 · `/quality`、`/data-process` | `/api/quality` → `vr/app.py:1662` | `config.quality_rule` |
| `datasource_status` | 数据源健康 · `/datasource` | `/api/datasource` → `vr/app.py:2732` | 30 个源的可用状态 |
| `jobs_snapshot` | 任务与调度 · `/jobs` | `/api/jobs` → `vr/app.py:2076` | `operations.*`、`vr/jobsboard.py` |
| `jobs_chain_steps` | 加工段逐步结果 · `/system` | `/api/jobs/chain-steps` → `vr/app.py:2338` | 只读，不触发补跑 |
| `volume_energy_config` | 倍量加权分档 · `/system` | `/api/config/volume-energy` → `vr/app.py:822` | 只读；**写端点 `PUT` 不进登记表** |
| `quote_links_config` | 行情外链地址 · `/system` | `/api/config/quote-links` → `vr/app.py:2236` | 只读 |
| `journal_list` | 交易日志流水 · `/journal` | `/api/journal/list` → `server.py:976` | `ys.*`；`limit=200`、`offset=0` |
| `journal_stats` | 交易日志统计 · `/journal` | `/api/journal/stats` → `server.py:988` | 只读 |
| `positions` | 重点关注（持仓）· `/my-stocks` | `/api/positions` → `server.py:1060` | 只读 |
| `risk_report` | 风险监控报告 · `/my-stocks` 一带 | `/api/risk/report` → `server.py:1241` | 只读；同族 `at-risk`/`attribution`/`excursion`/`inbox`/`rules` 走索引 |
| `review_latest` | 复盘报告 · `/agent/review` | `/api/review/latest` → `server.py:697` | 文件侧产出；`date` 可选 |
| `backtest_stats` | 历史统计 · `/backtest` | `/api/backtest` → `server.py:1447` | `days=60` |
| `weekly_heat` | 近 5 天热度 · `/heat` | `/api/weekly` → `server.py:755` | 只读 |
| `live_emotion` | 盘中实时情绪 | `/api/market/live-emotion` → `server.py:634` | `duanxian/live_emotion.py` |
| `intraday_path` | 盘中走势 · 复盘材料 | `/api/intraday/path` → `server.py:3926` | `date` 可选 |
| `verification_menu` | 盘中核验清单 · 复盘材料 | `/api/verification/menu` → `server.py:926` | `date` 可选 |
| `review_notes` | 复盘笔记 · `/journal` tab | `/api/review-notes` → `vr/app.py:2822` | `ys.review_note`（⚠️ 端到端还没真跑过，别当已验证能力宣传） |
| `reminders` | 提醒 · `/journal` tab | `/api/reminders` → `vr/app.py:2846` | `ys.reminder` |

## 六、通用层 3 个工具

| 工具 | 作用 | 实现 |
|---|---|---|
| `list_capabilities(layer='all')` | 返回全部可读条目目录：`capability_id`、标签、屏幕落点、来源端点、参数 schema（名/类型/默认/上下界）、`layer`（A/B/C/index）。`layer='A'`/'B'/'C'/'index' 可单选 | `mcp_service/capabilities.py` |
| `capability_data(capability_id, params)` | **兜底取数**：A/B/C 层的 `capability_id`，或直接给端点路径（`/api/...`）都能取；`params` 是对象。拒绝非只读路径、拒绝 `refresh=true` | `mcp_service/client.py` |
| `data_freshness()` | 一次说清"读到的是哪一场的数"：`stock_daily` 的 max 交易日、十张 `analytics.*` 产出表各自的 max、与基准日是否齐平。判产出没跟上时先调它 | `duanxian/coverage.py` ＋ `/api/coverage` |

## 七、全量端点索引（现扫，不抄条数）

`endpoint_index()` 用 `ast` 静态解析 `server.py` 与 `vr/app.py` 的 `@*.get/head` 装饰器，只收 `/api/` 开头的那些
（SPA 兜底 `/{full_path:path}` 因此天然在外；用静态解析而不是导入 app，是因为导入会把常驻线程与调度器一起拉起来）。
索引层条目没有中文标签、没有主读表，但只要它没被排除、路径是可直接打的那种（不含 `{rid}` 这类路径参数），
`capability_data` 就能按路径取到——这样"新加一屏忘了登记"最多是目录里少一句解释，而不是**取不到数**。
带路径参数的那一类会被兜底工具当场拒绝并说明原因，不猜 id。

- 条数**一律现查**（`list_capabilities(layer='index')` 或 `GET /api/mcp/registry`），本文档不抄总数。
- 解析器实现：`mcp_service/capabilities.py::endpoint_index`；验收它的单测：`tests/test_mcp_registry_sync.py`。
- 排除项写死在 `capabilities.py::EXCLUDE_INDEX`（键＝路径，值＝理由，单测逐条钉住这两样），现四条：
  `/api/cli/available` 会探测本机 CLI 子进程；`/api/tdx/status` 与 `/api/tdx/watch` 会枚举／操作通达信桌面窗口；
  `/api/wxpush` 读的是本机微信桌面通道的状态。带路径参数（`{rid}` 那一类）的端点不进兜底——
  `capability_data` 会直接说明原因，而不是猜一个 id 填进去。⚠️ 新增排除项必须同时在这里说明理由。

## 八、代码引用清单：改哪一处会牵动哪几处

| 位置 | 是什么 | 谁读它 |
|---|---|---|
| `mcp_service/capabilities.py` | **登记表唯一事实来源**：A/B/C 层条目 + `endpoint_index()` | MCP 服务、`/api/mcp/registry`、同步单测 |
| `mcp_service/client.py` | 只读 HTTP 取数、`_meta` 新鲜度回执、错误如实抛出 | 全部工具 |
| `mcp_service/server.py` | FastMCP 装配：A/B 层注册成工具、通用层 3 个工具、双模 transport | `python -m mcp_service`、`vr/mcpboard.py` 的试调用 |
| `mcp_service/__main__.py` | CLI 参数：`--transport`、`--port`、`--host` | 启动命令 |
| `vr/mcpboard.py` | `registry_payload()` / `status_payload()` / `try_invoke()` | `vr/app.py` 三条路由 |
| `vr/app.py`（`/api/mcp/*` 三条） | 只读 registry、status；`POST /api/mcp/try` 按白名单试调用 | 前端 tab |
| `frontend/src/components/settings/McpServicePanel.tsx` | tab 内容：对照表、两段可复制 JSON、连接自检、试调用 | `Settings.tsx` |
| `frontend/src/pages/Settings.tsx` | `TABS` 三条（`chat` / `access` / `mcp`）、tab 解析与面板分支 | 路由 `/settings` |
| `frontend/src/pages/System.tsx`（`CAPABILITIES`） | 屏幕侧能力清单：端点 → 屏。**A/B 层每个工具的路径都必须同时出现在这里**，否则"给智能体的数"与"屏幕上的数"迟早分家 | 系统配置自检那一屏 |
| `frontend/test/mcpServiceTab.test.ts` | 前端侧：tab 已注册、面板取的是 `/mcp/registry`＋`/mcp/status`＋`/api/mcp/try`、条数没被抄死、自检不过不显示 ✓ | `node --test` |
| `tests/test_mcp_registry_sync.py` | **本文档 ↔ 登记表 ↔ 端点真实存在** 三方逐条对齐 | `pytest` |

## 九、界面侧：AI 探索 · MCP 服务

「AI 探索」是**一页多 tab**（`/settings?tab=`），不新增路由、不新增导航条目——同一路由挂两条导航项会被 `frontend/test/modules.test.ts` 判红，`?tab=` 是本项目的既有做法（交易日志那一屏同理）。

- `TABS` 现三条：`chat` AI 对话、`access` 接入设置、**`mcp` MCP 服务**。
- 面板内容四块，**全部现读接口，不写死任何条数**：
  1. **这份服务是什么**：一句话口径（把屏幕上那些榜和产出，按登记表原样交给外部智能体）＋权限边界三条（只读／不触发计算／失败不兜空）。
  2. **工具对照表**：`capability_id` · 标签 · 屏幕落点 · 来源端点 · 层 · 参数个数，可点列头排序，点一行展开参数与主读表。
  3. **接入配置**：stdio 与 HTTP 两段 JSON，一键复制；带 `YUANSHAN_MCP_BASE`／`VR_API_KEY` 说明。
  4. **连接自检**：后端 8910 是否可达、登记表条数、A/B/C/索引各层数量、`mcp` SDK 版本、HTTP 模式端口是否在听；**任一项取不到就显示"未取到＋原因"，不显示 ✓**。
  5. **试调用**：选一条 A/B 层工具＋填参数 → `POST /api/mcp/try` → 原样返回 JSON（只读、白名单、带耗时）。
- 交付口径（用户 10-19 定的那几条）：已登记行要能看明细、主按钮不静默 disabled（拦下来就列"还差哪几样"）、描述性长段默认收起而控件留在外面、屏幕上不出现 markdown 星号。

## 十、与既有两套 AI 取数的关系

| 已有机制 | 位置 | 与 MCP 服务的关系 |
|---|---|---|
| 复盘 Agent 的 MCP 服务 | `review_agent/mcp_server.py`（stdio，6 个工具，服务名 `astock_public_review`） | **保持独立、不合并**。它喂的是"某一场冻结复盘包"（证据卡、不许出网），生命周期由 `review_agent/runtime.py` 托管；本服务喂"当前实时的加工产出"。两者工具名不重叠，宿主里并存时按服务名区分 |
| 页面问答的前端取数 | `frontend/src/lib/aiPresets.ts` + `lib/aiContext.ts` | 同一批端点的**浏览器侧**用法（预设题跑之前现场抓 `/api`，拼成上下文）。**登记表与 `AiPreset.sources` 是两份台账**：新增一屏只登记一处会被另一处忘掉，所以 `tests/test_mcp_registry_sync.py` 里有一条把 `sources` 里的路径逐个对回登记表 |
| 系统配置自检清单 | `frontend/src/pages/System.tsx::CAPABILITIES` | 见第八节：A/B 层必须成对出现，单测把关 |

## 十一、持续更新：新增一屏要做的固定动作

1. 端点先存在且是 `@app.get`（返回 `{"data": …}` 信封），否则不进 MCP。
2. 在 `mcp_service/capabilities.py` 加一条：`id`（发布后不改）、`layer`、`label`（屏幕上那几个字，逐字一致）、`where`（路由或首页位置）、`path`、`params`、`tables`、`refresh_forbidden`。
3. 同步本文件的对应表格（第三节 / 第四节 / 第五节）——**逐字段照抄登记表，不要凭记忆重写**。
4. `frontend/src/pages/System.tsx` 的 `CAPABILITIES` 加一行同路径。
5. 跑 `tests/test_mcp_registry_sync.py`（它会红在本文件的表格与登记表不一致处）。
6. 若是 A/B 层：`python -m mcp_service` 后用客户端**真的调一次**这条工具，确认返回非空且 `_meta.as_of` 是当前基准日，再算登记完成。

## 十二、校验与实跑命令

```bash
# 1) 登记表 ↔ 本文档 ↔ 真实端点 三方对齐
.venv/Scripts/python.exe -m pytest tests/test_mcp_registry_sync.py -q

# 2) 服务起得来、工具有几条（stdio 客户端实测）
.venv/Scripts/python.exe -m mcp_service --check-tools

# 3) HTTP 模式（局域网/别的电脑）
.venv/Scripts/python.exe -m mcp_service --transport streamable-http --port 8920

# 4) 界面侧三端点（本机默认不设密钥）
curl -s http://127.0.0.1:8910/api/mcp/registry > /tmp/mcp_reg.json
curl -s http://127.0.0.1:8910/api/mcp/status  > /tmp/mcp_status.json

# 5) 前端
cd frontend && npm run build && node --test test/
```

⚠️ 本文件里**不写**索引层条数、数据日期这类会自己翻转的数；要条数就跑第 12 节那些命令现取。三、四、五节表格的**行数与内容**由 `tests/test_mcp_registry_sync.py` 逐条与登记表对齐（多一条、少一条、字段漂移都会红），所以小节标题里的 24 / 8 不是靠人记得改的数字。日期类同理：判基准日以 `market_data.stock_daily` 的 max 现查。
