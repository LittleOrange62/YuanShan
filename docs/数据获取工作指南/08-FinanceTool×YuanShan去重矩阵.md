# 08 · FinanceTool 能力 × YuanShan 现状 去重矩阵（2026-09-24）

> **收口原则（用户 2026-09-24 定，⚠️ 已被 09-25 晚第四轮裁定推翻，本段为准）**：原文写的是「YuanShan 是集大成的**读库/复盘/展示**端，**所有写库动作归 FinanceTool**」——
> **这句话现在不能照着干活**。用户 2026-09-25 晚的原话：「写入这个问题以后，**PG 的这个库就只有你能写**。读的话可能会有别的人来读，但是写、更新和删除就只有你能做。」
> 于是本矩阵里那些以"那边才是写库方／YS 只是只读方"为唯一理由的「跳」**整体作废**：`ingest/jobs/` 与 `app/compute/` 就是本项目的写入侧，写库不越界。
> 由 `code：数据获取`（cwd 为本项目根 `D:\finance\YuanShan`）执行这件事没变。
> **仍然成立的那半**：本项目的 PG **读取**层确实只读 —— 10-18 现读 `duanxian/pg.py:91、:93` 全文件只有两条 `cur.execute()`（都在 `fetch_df`（`:83`）里），
> 无 INSERT / COPY / CREATE；只读账号与写账号（`.env` 的 `PG_ETL_USER`）是两条通道。**"读取层只读"≠"本项目不能写"**，别把这两件事混成一句。
>
> 每条判定都带实测证据。矩阵里 `FT` = 旧项目 FinanceTool（已并入本项目，其说明书在本目录 `../参考/FinanceTool/`），`YS` = `D:\finance\YuanShan`。
> 新鲜度数字均为 2026-09-24 上午直连 `financetool` 库查得（`ft_reader`）。

## 一、判定标记

| 标记 | 含义 |
|---|---|
| **跳** | 不搬。YS 是只读方，搬 ETL 就是两套抢一张表 |
| **合** | YS 改读 FT 现成表（纯 SELECT，写库侧不动） |
| **改-FT** | 缺口在 FT 写库侧，**派工给 `code：数据获取`**，YS 只能等 |
| **改-YS** | 缺口在 YS 读库/口径/呈现侧，本会话可做 |

## 二、矩阵

| # | FinanceTool 能力 | 实测状态（证据） | YuanShan 现状 | 判定 |
|---|---|---|---|---|
| 1 | 日线入库 `c1_day` → `market_data.stock_daily` | 637 万行，2021-08→**2026-09-23**，最新场 5556 票（`python -m ingest.jobs.c1_day`） | 6 个 fetcher 头部已插本地优先（`fetchers.py:307,436,496,541,573,609`） | **跳**（已合，别再动） |
| 2 | c1~c20 入库 / 派生列回算 / 24 条质量审计 / 自研调度器 | `app/orchestrator/schedule.py:139-195`，33 作业仅 5 个带 cron | **10-18 现查重报**：入库脚本已到 **c1~c25**（`ingest/jobs/`，10-16 加 `c25_*` 那批财务/回补），质量规则在 `config.quality_rule` 是 **30 条（23 error + 7 warning）**，`config.job` **36 行、6 行带 cron_expr**。"自研调度器"那条也已被否掉：现役宿主是常驻 uvicorn 的 12 条循环（`vr/jobsboard.py::LOOP_JOBS`），内置调度器仍 `scheduler_enabled=false` | **跳**（这条判定的**结论**仍然对，但支撑它的三个数都旧了；口径既定：不引第二套调度器） |
| 3 | **逐票涨停/炸板池表** | ~~`information_schema` 按 `zt\|limit\|pool\|seal` 检索零命中~~ **10-18 现查这条说法已不成立**：那把正则命中 4 张 —— `reference_data.limit_price_daily`（当日档位，122,339 行）+ `operations.limit_price_backup_20261007{,_b,_c}`（10-07 那次档位重刷的现场备份）。**但结论不变**：这几张都是**档位/备份表，不是池表**，库里**仍然没有**逐票涨停池/炸板池历史表；封单额仅在 `stock_snapshot.extra` 里，TQ 侧 `get_zdt_data` 未暴露（`app/datasource/tq_http.py:22` 返回 `-32601`） | **三条独立入口只本地化了一条**：`fetch_zt_pool`✅ / `pool_source.py:9` 仍直连 akshare / `vr/astock.py` 盯盘仍直连东财 `push2ex`（行号会漂，认符号）。YS 自算单次 **3.2s**（90 日回看 + Python 逐票算连板）—— 这是 09-24 的计时，**未重验** | **建表 + 三入口统一读它**（原「改-FT」已按 09-25 裁定改成本项目自己建，见工单 W3）。另：口径实测差（触板 37 vs 炸板 18）见《pool caliber》那条，建表时要一并定口径 |
| 4 | 全市场宽度 `analytics.market_breadth` | ~~判据单边 `close>=limit_up-0.01`（`app/compute/breadth.py:33,37`）~~ **已整改（2026-10-07）**：10-18 现读 `breadth.py:33-38` 从 `app/compute/limit_prices.py` import **`LIMIT_EPS = 0.005`（半分，全项目一份）**，注释原话写明「与契约里的双边 `abs(close - limit_up) <= eps` 在这一列上等价」；当年那 0.01 会把"差一分钱没封住"算成封住（09-30 给 57，通达信 `sh880006` 与严格双边都是 56） | 刻意**不读该表**、自己从 `stock_daily` 重算这条**仍在**（3.2s 那半句未重验） | **判据那一半已闭环**；剩「回补历史」那一半 → 之后 **合**（YS 去掉自算）。改一处两下游都受益 |
| 5 | `reference_data.security.is_st` 档位维度 | **212 只 is_st 行的 updated_at 全为 2026-08-27 11:16:37**（10-18 现查仍是这一批；同期非 ST 的 9,339 行已更新到 09-28）→ 快照过期**至今没刷**。09-22 被它带偏的 5 只：000056.SZ *ST皇庭 涨 7.00%、002082 涨 9.99%、002581 涨 7.80%、002650 涨 5.88%、600337 涨 6.60%，`limit_up` 列全按 5% 档推导 | 只读受害方。双边判据只兜得住"高价冒充涨停"，兜不住反方向（已摘帽票仍按 5% → 真涨停判成非涨停） | ~~待用户拍板~~ **主修已落地（2026-10-07）**：`reference_data.security_st_period`（10-18 现查 354 段，`valid_from/valid_to/is_st/method`）+ 档位按 `trade_date` 取（`app/compute/limit_prices.py` 唯一写入口，出处记在 `stock_daily.limit_basis`）⇒ **不再拿当前快照套历史**，所以这条对 `is_st` 列的依赖已被绕开。**还剩的**：`security.is_st` 本身仍是 08-27 那份，页面上任何"直接读 is_st"的筛法都还是过期口径 |
| 6 | 龙虎榜 + 机构净买 | `dragon_tiger_detail` 6876 / `dragon_tiger_institution` 4102，均到 **09-23**（`c6`） | 已合（`pg_sources.lhb()`，喂原恒 None 的 `inst_net`） | **跳** |
| 7 | 板块点火 `analytics.ignition` / `sector_trend` / `sector_heat_daily` | 到 09-23（688 板块/日；`sector_trend` 532 万行） | 已合（`sector_cycle()` + `GET /api/market/sector-cycle`） | **跳**；但见 #16 广度缺口 |
| 8 | 技术指标 `analytics.indicator_value` | 1425 万行，到 09-23（18.8 万行/日） | 已合（34 项 + `GET /api/stock/indicators`） | **跳** |
| 9 | 个股/板块资金流 `market_data.fund_flow_rank` | ~~**0 行**~~ **10-18 现查 89,020 行（2026-09-28 → 10-16）**，来源是新浪那条路（`ingest/jobs/c21_fundflow_sina.py`，链步 `fundflow_rank`＋常驻循环 `fundflow-sina`）。写下这条时的根因（`c11_fundflow.py` 的 `except Exception: continue` 把失败吞成"这档没数据"；且 `ak_fundflow_em` 健康＝`down`、备用 `ts_moneyflow`＝`no_permission`）**只解决了一半**：10-18 现读 `c11_fundflow.py:163-166` 那句现在会 `print('… 采不到：%s')` 再 continue —— **出声了，但仍不落 probe 记录**，所以事后还是查不出那天为什么少一路 | `individual_fund_flow()` / `sector_flow()` **不再恒 None**：10-18 现读 `duanxian/fetchers.py:540`（个股）与 `:607`（板块）注释即"本地 P0 优先：`fund_flow_rank` 到货即用；空表/盘中回退东财" ⇒ **W5 那一半（本地源接上）已完成** | **合**（本地优先已生效）＋ **仍欠两样**：① 新浪/同花顺**只给当天截面**，09-28 之前逐日补不回，这是数据现实不是待修 bug；② 采集失败要记 probe（现在只 print） |
| 10 | 大盘资金流 `market_data.market_fund_flow` | ~~**120 行，停在 2026-09-17**，最新日只有 1 行 → 断更~~ **10-18 现查 135 行、2026-03-27 → 10-16，逐日有数**（9/18、9/21、9/22、9/23、9/24 那几天各 1 行都在） | 未读（`c11` 那条 `--market-only` 路；**`--date` 至今没有**，`c11_fundflow.py:191-192` 只有 `--market-only`/`--dry-run`） | ~~W6 查断更~~ **已修**；剩的那半是"没有历史参数 ⇒ 断一天只能靠当天多跑" |
| 11 | 估值快照 `stock_snapshot`（PE/PB/市值/内外盘/量比） | 17.4 万行，07-22→**09-23**，5576 票/日，源 `tdx_snapshot_db`（health=ok） | `/api/valuation`、`/api/valuation/percentile` **全部在线** | **合**（直读快照）。⚠️ 历史仅约 **2 个月**，长周期估值分位 **FT 给不了**，必须标注窗口而非冒充 |
| 12 | 两融 `margin_detail` | 29.4 万行，最新 **09-22（落后一日）**，4106 票/日；`ak_margin_sum_szse`=broken、`ts_margin`/`ts_margin_detail`=no_permission | `/api/margin` 打东财 | **合** + **改-FT**（W8 补 09-23 与深市汇总） |
| 13 | 分钟线 `stock_minute` | ~~只有 2026-09-22 一天~~ **10-18 现查（按月分片数）**：2026-08 **32,387,802 行**、2026-09 **56,871,521**、2026-10 **19,076,346（到 10-16）**；**2024、2025、2026-01~07 仍为空**。**逐日是否一天不缺没复算**（那条 `count(distinct dt::date) group by month` 在只读账号的 60 秒 `statement_timeout` 下直接被 cancel —— 108M 行的全表聚合本来就不该走 reader 通道，要数请用覆盖体检那条预计算）。`c16_minute.py` 解析 `.lc1`（`minline\`）与 `.lc5`（**`fzline\`，不在 minline\**，10-18 实测） | 自建分钟库**已损坏**（旧 SQLite 系废弃）—— 这半句仍然对 | **W7 那一半已完成**（链步 `spike` 就在读它）；**仍欠**更早月份要重下 vipdoc，且本机有没有那些月的 `.lc1` 文件是另一回事 ⇒ **"分钟线类判据的可用起点＝2026-08"** 这条要写进任何用它的判据里，**绝不维护两套分钟库** |
| 14 | ETF/基金份额 `etf_share`、`fund_daily` | 25.9 万 / 165 万行，到 09-23 | 未读 | **合**（低优先，等有面板需求再动） |
| 15 | 复权因子 `adjustment_factor` | 后复权由 dividend 区间累乘（`quote.py:435-514`），qfq/hfq 查询期换算；**09-23 有 113 行因子 NULL** | 读 `stock_daily` 时用 | **改-FT**（补 NULL 因子）。复权口径**必须在 FT 单点**，YS 不许二次换算 |
| 16 | 板块成分与编号 `sector_member`(35万) / `sector_info`(3024) / `sector_alias`(TDX_1337/THS_1140/GN_/FG_/ZS_ 混装) | **230 个行业板块没有涨跌家数** → YS 板块周期广度分项算不出，`term_count≤4`，规则上永远不会判成点火。编号两套：FT 板块周期用 TDX_880(607)+THS_881(85)，YS 判"所属行业"用 TDX_881 | 已打 `synthetic` 标交出 `sector_type`，不裁 | **改-FT**（W10 补行业板块成分×日线出广度；补 alias 归一视图）→ 之后 **合**（YS 读别名归一） |
| 17 | **数据源健康登记** `config.datasource` + `operations.datasource_probe` | 30 源，健康分布实测 **ok 20 / no_permission 6 / down 2 / broken 1 / unknown 1**（down 的正是 `ak_concept_em`、`ak_fundflow_em`）。probe 表 251 行覆盖 29 个 key（08-27→09-23），但**最新一批 15 条全是同一个 `tdx_snapshot_db`**；且 `probe.py:323` STICKY 状态**永不再探**。两张表 `ft_reader` **实测可读**（`has_table_privilege=True`） | 完全没读 → 页面上看不见源已死 | **跳（重要修正）**：**不要"移植思路自建探测器"**（此前记忆/索引记的是移植，本矩阵推翻它）—— YS 只需一条 SELECT。YS 独有的在线源（akshare/东财/腾讯里 FT 未登记的那些）才需要一份**轻量自有登记**。探测覆盖不全归 FT，见 W11 |
| 18 | 指数 `index_daily` | 20.8 万行，172 指数/日，到 09-23 | 已合（`fetch_indices` 本地优先） | **跳** |
| 19 | 期货/期权/财务报表/北向 | ~~**库里都没有**~~ **一半变了（10-18 现查）**：**财务报表已入库** —— `reference_data.stock_financial_period` **627,296 行**（DDL `app/db/sql/41_stock_financial_period.sql`，采集 `ingest/jobs/c25_financials_ths.py`（同花顺），加工链第 **28** 步 `financials`，母体＝本机名单 + 缺口优先回补）。**仍然没有的那半**：期货/期权 —— `app/` 全树现在只对 `期货|期权|futures` 命中 1 处（`app/datasource/ths_local.py`，是措辞不是能力），期权一个字段都没有；北向 2024-08 上游断供这半句未重验 | 估值仍在 `stock_snapshot`；财务**不再走在线**（读层按票取本地那批期次） | **期货/期权：跳**（无可合对象，不是去重问题）；**财务报表：这一格原来的"跳"已作废** —— 它已经在本地了，后续判据要防的是**拿它当全市场覆盖**（母体只有本机名单那点票，不是全 A 财报库） |
| 20 | 对外读接口 | 明确**不做 HTTP/MCP 封装**（旧项目说明书 `../参考/FinanceTool/docs/07-多智能体直连规范.md:3-5` 自记"该方案已废弃"）。下游直连 `127.0.0.1:5432/financetool`，四角色 `ft_reader/ft_app/ft_etl/ft_admin`，或用本项目的 `app.db.engine` / `app.db.repo`（旧项目的 `pip install -e` + `ftdb` 共享包在本项目**尚无对应物，待补**：本项目无 `pyproject.toml`/`setup.py`，不可 `pip install -e`）。网页 18420 只绑本机+IP 白名单，不是数据接口 | 直连 + `ft_reader` | **跳**：维持只读直连，**别给 FT 加 API 层**（多一层就多一处口径漂移和一处鉴权面） |

## 三、派工给 `code：数据获取` 的写库工单（FT 侧，YS 一律不动）

按性价比排。**W1 是所有新表的前置**——不修就是"表建了下游读不到"。

| 工单 | 内容 | 证据 | 依赖 |
|---|---|---|---|
| **W1** | 修 `04_constraints.sql` 授权段：`:258` 仍给不存在的 `ref,fct,ops` 授权（10-18 现读：`GRANT USAGE ON SCHEMA market_data, ref, fct, config, ops TO ft_app, ft_etl, ft_reader;`，这三个 schema 在现库里查不到）；`:261,262,265,266,273` 写成 `operationsTO`、`:272` 写成 `configTO`（缺空格，整句成了给一个名叫 `operationsTO`/`configTO` 的角色授权）→ 重跑 init 必失败，新表的 SELECT 也给不下去。**这一条至今没修，所以它仍是所有新表的前置** | 10-18 现读原始字节（原先抄的 `:252` 与 `:255,256,259,266,267` 是 09-24 的快照，那份文件后来长过） | 无 |
| **W9** | **调度器决策**（本行写于 09-24，**结论部分已被现状取代**）：内置调度器仍然锁着 —— 10-18 现读 `config.setting`：`scheduler_enabled=false`、`scheduler_dry_run=true`、`tick_sec=15`、`max_parallel=1`、`catchup_days=5`、`stop_retry_after=3`；`operations.schedule_state` 仍是 16 行。**但"所以靠手工脚本撑"这句不成立了**：现役宿主是本进程 `server.py` 的常驻循环（10-18 现读 `vr/jobsboard.py::LOOP_JOBS`＝**12 条**，含收盘链 `daily-close`、失败单步重试 `step-retry`、备份 `pg-backup`、消息推送 `notify-push`），判"在不在跑"只认 `/api/jobs` 的 `scheduler.host_loops`。Windows 计划任务那条也翻案了：`FinanceTool-DailyCloseSync` 在 09-24/09-25 的 16:05 **各真跑过一次**（`LastTaskResult=267011`=从未跑 是当时的误读），09-25 被 `Disable-ScheduledTask` 停用（未删）。`operations.data_ready` **10-18 现查仍 0 行** ⇒ 那 4 个 `on_ready` 作业恒 wait（`mark_ready()` 无人调用）这一条**还是活的**，别当作已解决 | 10-18 现读库与 `jobsboard` | 待办只剩一件：`on_ready` 那 4 个作业要么给 `data_ready` 写入方，要么改成 `chain` |
| **W4** | `market_breadth` 涨停/跌停判据改双边 + 回补历史 | ~~`app/compute/breadth.py:33,37`~~ **判据那一半已闭环（2026-10-07）**：10-18 现读 `breadth.py:33-38` 从 `app/compute/limit_prices.py` import `LIMIT_EPS`（半分，全项目一份），并注明"哨兵归零后单边与双边在这一列上等价"。**回补历史那一半仍未复算**（09-22 存量 70/11 至今没重数，`integrity.sql` 的 A6/A7 只管 2026 分区、现跑各 0） | ~~W2~~ W2 已落地，所以这里只剩回补 |
| **W2** | ~~新增维度表 + 等用户拍板~~ **已落地（2026-10-07）**：`reference_data.security_st_period` 真实形状是 `(security_code, valid_from, valid_to, is_st, method, evidence, calculated_at)`（DDL `app/db/sql/12_qd_schema.sql:563-570`，10-18 现查 354 段、起点 2022-04-29），档位按 `trade_date` 取、写入口唯一 `app/compute/limit_prices.py`、出处记 `stock_daily.limit_basis`；`limit_price_daily` 122,339 行。**顺带那个反向 bug 也在这轮收的**：`app/compute/pattern.py` 头部现在直接写明"2026-10-07 之前 ST 档用当前 `is_st` 快照套历史是错的，现在权威定死"，并把哨兵做成可重跑命令 `python -m app.compute.limit_prices --check` | 本矩阵 #5 实测 | **已完成，别再当未修的洞查** |
| **W3** | 逐票涨停/炸板池表（`code/date/触板/首封时间/开板次数/最终状态/连板数`）。**前置：口径先定"触板 vs 封板后炸"**——YS 触板 37 vs 东财炸板池 18，炸板率被系统性抬高 64%；且封单额目前**无源**（`get_zdt_data` -32601），拿不到就别建空列 | #3 | 口径确认 |
| **W5** | ~~`fund_flow_rank` 0 行~~ **入库那一半已完成（10-18 现查 89,020 行，09-28→10-16，走新浪 `c21_fundflow_sina` + 常驻循环 `fundflow-sina`），欠的只剩记账那一半**：`c11_fundflow.py:163-166` 的 `except Exception` 现在会 `print('… 采不到：%s')` 再 continue —— 出声了，**但仍不写 probe**，所以事后仍查不出那天为什么少一路 | #9 | 可配 `FT_TUSHARE_TOKEN` 解锁 `ts_moneyflow`（仍未设） |
| **W6** | ~~`market_fund_flow` 断更排查（停 09-17，120 行）~~ **已修**：10-18 现查 135 行、到 10-16 逐日有数。**留着的真缺口**：`c11` 没有 `--date`（`:191-192` 只有 `--market-only`/`--dry-run`）⇒ 断档不能事后补 | #10 | — |
| **W8** | `margin_detail` 补 09-23 + 深市汇总（`ak_margin_sum_szse` broken） | #12 | token 可解锁 `ts_margin*` |
| **W7** | ~~`stock_minute` 补连续区间（现在只有 09-22 一天）~~ **已完成**：10-18 现查 2026-08（32,387,802 行）/ 09（56,871,521）/ 10（19,076,346，到 10-16），链步 `spike` 在读。**没做的**：更早月份（2024、2025、2026-01~07 仍空），要不要补取决于本机 vipdoc 有没有那些月的 `.lc1/.lc5` | #13 | — |
| **W10** | 230 个行业板块的涨跌家数（`sector_member` × `stock_daily` 回算）+ `sector_alias` 归一视图 | #16 | — |
| **W11** | `datasource_probe` 覆盖度：最新批次只探 1 个 key，且 `probe.py:323` STICKY 永不再探 → 死的源不会自己变活 | #17 | — |
| **W12** | `adjustment_factor` 09-23 有 113 行 NULL 补算 | #15 | — |

**顺带一条能一次解锁三处备用源的**：6 个 `no_permission` 源**全是同一个原因**——环境变量 `FT_TUSHARE_TOKEN` 未设置。配上就同时给 W5/W8 和龙虎榜第二源铺好退路。

## 四、YS 侧本会话可做的（不碰写库）

1. `preflight` 加一行 `SELECT health, last_err FROM config.datasource`（对 #17 的**跳**落地）
2. `pool_source.py:9`、`vr/astock.py:516` 两条未本地化入口：等 W3 出表后统一改读；**在此之前先把"这条数字出自哪个入口"打进日志**，别再靠人记住
3. `#9` 的资金流在页面上改成 `available=false + reason`
4. `#11` 估值直读 `stock_snapshot`，并**强制标窗口**（只有 2 个月，不许冒充长期分位）
5. 废弃 YS 自建分钟库，等 W7

## 五、还没定的

- **is_st 生效日期维度（W2）做不做** —— 待拍板第 3 项，本矩阵给了实测代价（09-22 五个假涨停 + 跌停数 11 vs 4）
- **调度器开不开（W9）** —— 决定"本地化"是真离线还是纸面离线
- **`FT_TUSHARE_TOKEN` 给不给** —— 影响 W5/W8 走不走第二源

相关：[06-跨项目数据源对照](06-跨项目数据源对照.md)、[07-YuanShan本地数据源接线](07-YuanShan本地数据源接线.md)、[数据加工 08-跨源口径契约](../数据加工工作指南/08-跨源口径契约.md)、`D:\finance\YuanShan\docs\功能集成分类.md`
