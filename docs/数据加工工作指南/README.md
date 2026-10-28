# 通达信数据加工工作指南

> 汇总本项目里"把本地通达信数据同步/解析入库"的全部经验、脚本与口径
> （这套流程成型于旧项目 FinanceTool，其说明书留在本目录 [`../参考/FinanceTool/`](../参考/FinanceTool/) 供查出处；
> 代码与作业不再从那边跑，本项目是唯一落地点、也是这套 PG 库唯一的写入方）。
> 最近更新：2026-10-21（加工链 42 步（第 30 步 `fbdaily` 首板分析、第 18 步 `voldbl` 当晚换尺）、`c21~c25` 五支新脚本、`ssd-1`/`fb-2`/`fbd-1`/`risk` 三列等新判据版本都在这一版里对上）。

## 这是什么

一套围绕**本地通达信客户端**（`D:\new_tdx64`）→ **PostgreSQL 库 `financetool`** 的数据加工流程：
盘后把行情下载到本地、解析入库、校验、关客户端；以及用通达信自带的 **TQ 接口**取板块/复权/财务/档案等进阶数据。

## 阅读顺序

| 文档 | 内容 |
|---|---|
| [01-每日通达信同步SOP.md](01-每日通达信同步SOP.md) | 每天跑的那条链：下载→入库→校验→关通达信，含一键脚本 |
| [02-TQ接口与策略脚本.md](02-TQ接口与策略脚本.md) | TQ策略是什么、怎么写本地脚本、`tq_bridge` 怎么用、`tq_fetch` 工具 |
| [03-通达信本地数据地图.md](03-通达信本地数据地图.md) | 本地到底有哪些数据可解析、哪些走文件哪些必须走 TQ、哪些根本没有 |
| [04-数据库schema与命名.md](04-数据库schema与命名.md) | 真实 schema 名、关键表、字段口径（含 2026-09-22 全库改名） |
| [05-踩坑与经验清单.md](05-踩坑与经验清单.md) | 这一路踩过的坑与实证结论，避免重复踩 |
| [06-数据清洗与加工任务总览.md](06-数据清洗与加工任务总览.md) | **全项目采集/清洗/加工任务全景**：c1~c20 按数据源分类、24 条质量规则、d1~d7 计算、迁移与编排 |
| [07-定时任务.md](07-定时任务.md) | 每交易日 16:05 收盘后自动同步的 Windows 计划任务（含交互桌面硬约束） |
| [08-跨源口径契约.md](08-跨源口径契约.md) | **对数必读**：同一事实几套数怎么办、涨跌停/触板vs炸板/ST档位/**当日涨跌停价权威**/复权/未来函数/板块分类错标等口径坑、各通道存活状态 |
| [09-quantdash逻辑规格.md](09-quantdash逻辑规格.md) | **派活契约**：quantdash 五项功能（连板天梯/情绪周期/板块周期/信息采集/AI对接）的逐条阈值与公式、新 schema `qd` 建表清单、输入可行性矩阵、42 处禁止复现的伪造点 |
| [10-每个数怎么算：判据、指标与参数总表.md](10-每个数怎么算：判据、指标与参数总表.md) | **「这个数是怎么做出来的」唯一一处**（2026-10-14 用户点名后新增）：指标族 35 条公式与复权现状、表达式函数的缺数规矩、各榜判据阈值与 `spec_version`、每个端点参数的默认/上下限/界面实际值、系统配置旋钮与界面状态键、一个从分量一路推到屏幕的算例、以及「同一个数两把尺 / 两处实现」清单。逐屏《功能点说明》只指过来、不复述 |

## 脚本清单与落点

| 脚本 | 实际落点（本项目内） | 作用 | 路线 |
|---|---|---|---|
| `tq_fetch.py` | `脚本/` | 一键取某只票的行情/档案/板块/除权/财务（只打印不写库） | TQ |
| `c16_minute.py` | `ingest/jobs/` | 1/5 分钟线入库 `market_data.stock_minute` | 文件解析 vipdoc |
| `c17_tdx_sector.py` | `ingest/jobs/` | 通达信板块全集+分类入库 `reference_data.sector_info` | 文件解析 tdxzs*.cfg |
| `c18_tdx_membership.py` | `ingest/jobs/` | 板块成分入库 `reference_data.sector_member` | TQ |
| `c19_tdx_dividend.py` | `ingest/jobs/` | 除权除息入库 `reference_data.dividend` | TQ |
| `c20_tdx_profile.py` | `ingest/jobs/` | 个股档案入库 `reference_data.stock_profile` | TQ |
| `tdx_binary.py` | `app/ingest/`（零依赖副本另存 `ingest/`） | vipdoc `.day/.lc1/.lc5` 二进制解析器（被 c16 等复用） | 库 |
| `daily_sync.cmd` | `脚本/` | 每日同步一键批处理 | — |

> `ingest/jobs/` 下的都是**模块**，跑法是 `python -m ingest.jobs.c16_minute`，不是文件路径。

> ⚠️ **路径已变更（2026-09-24 用户裁定）**：本指南已从 `D:\finance\数据加工工作指南\` 搬进 YuanShan，
> 现在的唯一路径是 `D:\finance\YuanShan\docs\数据加工工作指南\`（2026-09-24 二次调整：`docs\工作指南\` 那一层已取消）。别再往旧路径写，那会新建出一份副本。
> 旧说法"权威副本在旧项目的 `scripts\ingest\` 下"**已作废** —— 旧项目退役、由本项目全吸收，
> 脚本的权威落点就是 `D:\finance\YuanShan\`（分阶段方案见 [`../吸收FinanceTool方案.md`](../吸收FinanceTool方案.md)）。
> ~~在搬迁完成前，旧项目里那份仍是唯一能跑的实现，改逻辑要先在那边验~~ —— 这句也作废：
> 能跑的实现已经全部搬进本项目，旧项目那份随时会被删，**不许**再把任何入口指过去。
> 旧脚本目录本身也已在本地：`01_数据同步`/`02_通达信操作`/`03_数据校验` → `ingest\legacy\`，
> `04_数据清洗`/`05_辅助工具`/`06_临时脚本`/`07_因子跑分`/`ceo`/`data_from` → `legacy\脚本\`（归档区，不作为跑法入口）。

## c17–c20 已登记成 /jobs 作业（2026-09-23）

迁移 `app/db/sql/11_jobs_tdx_enrich.sql` 注册了 4 个手动作业，`/jobs` 页面可点"立即触发"：

| job_key | 对应脚本 | 备注 |
|---|---|---|
| `sync_tdx_sector` | c17 | 文件解析，无需客户端 |
| `sync_tdx_membership` | c18 | 走 TQ，**需通达信已登录** |
| `sync_tdx_dividend` | c19 | 走 TQ，**需通达信已登录** |
| `sync_tdx_profile` | c20 | 走 TQ，**需通达信已登录** |

执行器在 `app/orchestrator/runner.py` 的 `RUNNERS`（`ingest_tdx_sector/membership/dividend/profile`）。
✅ **这条链已经好了**（原话是 09-25 的状态，留作历史）：`app/orchestrator/runner.py` 的 `RUNNERS` 每一行现在是 `'module': 'ingest.jobs.c1_day'` 这种**模块名 + `python -m`**（`runner.py:76` 起；`:60-61` 还留着注释点名旧病：本项目没有 `scripts/ingest|ops|compute/` 那套目录，写 `ROOT / 'scripts/...'` 的结果是 `/jobs` 上每个"立即触发"都 FileNotFoundError）。旧写法曾让页面按钮一按就失败：`RUNNERS` 每行的 `'script'` 曾写着 `scripts/ingest/cNN_*.py` / `scripts/ops/e1_fill_all.py`
这类**本项目里已不存在**的文件路径（真实位置是 `ingest/jobs/` 下的模块），而执行器是
`script = ROOT / spec['script']` → `argv = [sys.executable, str(script)]`（`runner.py:291-292`）—— 所以 `/jobs` 上点"立即触发"会直接找不到文件。
改 `runner.py` 之后要重启 web 才生效：本项目用 `关闭 YuanShan.cmd` → `启动 YuanShan.cmd`
（旧的 `scripts\web_ctl.py restart --web-only` 已归档到 `legacy\pre_rename_20260921_scripts\web_ctl.py`，**现役替身是两个脚本**：`scripts/stop.py`（预览用 `--dry-run`）与 `scripts/restart_8910.ps1`，两步之间要读 `.local/uvicorn.err.log` 的 `Application startup complete` 才算起来 —— 见《系统配置/01》。）

## 三条铁律（血泪）

1. **判据只认数据日期，不认状态栏/不认时间**——通达信说"下载完毕"不代表文件真的更新了。
2. **私有二进制一律走 TQ，不逆向**——板块成分/复权/档案这些，通达信插件自己也走 TQ 接口，硬啃 `.dat`/`gbbq` 易错。
3. **TQ 要客户端已登录，且单连接**——同一时刻只能跑一个 TQ 片段。
