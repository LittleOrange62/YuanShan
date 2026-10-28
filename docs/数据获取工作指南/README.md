# 数据获取工作指南

> 对象：本项目 `D:\finance\YuanShan` 的 financetool 库（PostgreSQL 17 @ 127.0.0.1:5432，数据目录 `D:\finance\TataPG`）。
> 本文档里的相对路径均以项目根 `D:\finance\YuanShan` 为基准；旧项目 FinanceTool 已并入本项目，
> 其说明书在本目录 `../参考/FinanceTool/`，旧脚本在 `ingest\legacy\` 与 `legacy\脚本\`。
> 全部结论均来自 2026-09-21～09-23 的实测（全库体检、联网抽样核对、全库改名、3 日数据追平），不是纸面设计。

| 文档 | 回答什么问题 |
|---|---|
| [01-通达信启动与登录](01-通达信启动与登录.md) | 客户端自动化：启动、登录、弹窗看门狗（2026-08 旧篇，仍然有效） |
| [02-盘后数据下载与入库](02-盘后数据下载与入库.md) | p0 下载细节与入库实操（2026-08 旧篇） |
| [03-故障排查](03-故障排查.md) | GUI 自动化故障树（2026-08 旧篇） |
| [01-数据源分层与获取方案](01-数据源分层与获取方案.md) | 每类数据从哪来？本地文件能不能拿到？实时怎么办？ |
| [02-每日流程runbook](02-每日流程runbook.md) | 每天盘后按什么顺序跑？跑坏了怎么判断？ |
| [03-数据质量与体检](03-数据质量与体检.md) | 库里数据可信吗？已知问题清单与复核方法 |
| [04-全库改名记录](04-全库改名记录.md) | 2026-09-22 md/fct/ref/ops→全称改名的完整映射与踩坑 |
| [05-外部数据源地图](05-外部数据源地图.md) | a-stock-data/akshare/baostock/mootdx/东财/tushare/AlphaFeed/腾讯——已接入 vs 备胎 |
| [06-跨项目数据源对照](06-跨项目数据源对照.md) | Vibe-Trading / TradingAgents-astock / quantdash / YuanShan 各自怎么取数；哪些思路值得抄、哪些代码因许可不能搬；跨源口径陷阱清单 |
| [07-YuanShan本地数据源接线](07-YuanShan本地数据源接线.md) | YuanShan 读了 financetool 库的哪四路（宽度/龙虎榜机构/板块周期/技术指标）、接线位置、三条必知的坑、怎么验 |
| [08-FinanceTool×YuanShan去重矩阵](08-FinanceTool×YuanShan去重矩阵.md) | 20 项能力逐条判"该合/该跳/该改"；⚠️ 本行原先写的「写库动作全部收口 FinanceTool」**已被后来的裁定取代**：本项目 `D:\finance\YuanShan` 才是这套库唯一的写入方（旧项目只是参考件，见《YuanShan 是唯一落点》那条裁定与本目录 07）；含派工给 `code：数据获取` 的 12 条工单（W1 授权 SQL 坏了是新表前置，10-18 现查那句仍成立） |
| [10-盘中播报与事件流入库](10-盘中播报与事件流入库.md) | 外部 `intraday_zt` 那套盘中播报（事件差／竞价／概念热度／午间复盘）整体迁入的**库表台账**：五张新表的粒度、唯一写入方、DDL 编号与现查行数；三处口径修正（缺数不再拿 `lbc or 1` 顶、`sector_daily.pct_change` 是小数要 ×100、`or None` 不许吞真实的 0）；顺手修的**跌停池 `fbt:asc` 整池读 0** 那条活缺陷；执行形态为什么必须是"单次短跑"（引外部 10-23 常驻静默死事故）；**双发风险与切换时序**、以及"事件流要不要上界面"卡在 `vr/astock.py` 那条「零标的」红线上待裁 |

（01/02/03 各两套：编号前缀的是 8 月"通达信实操"系列，另一组是 9 月"数据与库"系列。）

`scripts/` 目录 —— ⚠️ **这个 `scripts/` 是本文档目录下的 `docs/数据获取工作指南/scripts/`，不是项目根的 `scripts/`**（10-18 现查：根 `scripts/` 里根本没有下面这几个文件，那里放的是启动器 `manage.py`、`stop.py`、`jobs.py`、`doctor/`、`ops/`、`setup/`、`start/` 这些运行工具）。照本行原先的写法在项目根跑 `psql -f scripts/integrity.sql` 只会得到 "文件不存在"。

- `integrity.sql` —— 全库体检（新名版，**10-18 现数 20 项** `select 'X 名称', count(*)`；原先写的 21 项是 09-23 的快照）。头部那行用法里的 DSN 写的是占位符 `<FT_DSN_READER>`，**只读账号的连接串在 `.env` 里，别抄进文档、别打印**；且它硬写 `stock_daily_2026` 这一张分区表，换年份要改 SQL
- `coverage.sql` —— 表/列中文注释覆盖率检查
- `check_freshness.py` —— 一屏看各行情/计算表最新日期
- `backfill_derived.py` —— 缺日派生列（pre_close/涨跌停/换手）回算 + 计算链重跑
- `register_orphans_tnf.py` —— 从通达信 .tnf 补登记主档缺票
- `history/rename_db.py` / `history/rename_code.py` —— 全库改名的迁移器（一次性工具，已不再运行）；`mapping.json` 新旧名映射在 **`scripts/` 自己这一层**（不在 `history/` 里，10-18 现查）
- 另有两份**旧名版副本**在 `legacy/schema_analysis/{integrity,coverage}.sql` —— 那是改名之前的，**别拿它们对现在的库**
