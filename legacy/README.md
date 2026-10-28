# `legacy/` 与 `ingest/legacy/` 是什么

用户裁定终态只有一个成品项目，参考项目会被删除，所以「全部拿过来放在本地」这件事分两个落点：

| 位置 | 含义 | 谁能跑它 |
|---|---|---|
| `ingest/legacy/` | **被本项目运行时指向**的旧脚本（只有这一小撮真的还是依赖） | 只有 `01_数据同步/tdx_data.py`，且只被 `app/tdx_client/steps/s9_ingest.py` 的 `--legacy` 分支调用 —— **该分支建议删除，见下** |
| `legacy/` | 抄本与一次性工具的**归档**，运行时零引用 | 谁也别指望双击它能用；要用先照下面的判据体检 |

`manage.py doctor` 第 9 项（外部路径引用）把这两个目录连同 `docs/参考/`、`.local/` 一起当归档区豁免 ——
里面满是指向别处的路径才是对的。所以**不要**因为"扫描器没报红"就以为这里的东西能用。

## 清点（2026-09-25 从旧工作区 `脚本\` 搬入，共 374 文件 / 1.7 MB）

| 目录 | 文件 | 其中 py | 状态判据 |
|---|---|---|---|
| `ingest/legacy/01_数据同步` | 23 | 23 | **15 个**把 `D:\finance\data\tq_*.db` 写死在代码里，**那个目录本机已不存在** → 跑了就是在别处新建第二数据仓 |
| `ingest/legacy/02_通达信操作` | 44 | 24（另有 20 个 `.ps1/.bat`） | **唯一还活着的一批**：GUI 拟人操作（`open_tdx.ps1`、`login_tdx.py`、`force_focus_tdx.py`、`trigger_download.ps1`、`uia_dump*.ps1`、`close_tdx_popup.py`…）。不写库，只点窗口 |
| `ingest/legacy/03_数据校验` | 37 | 37 | **25 个**同样写死旧 SQLite 路径；这批是当年一次性探针，读完就打印、无副作用，但目标文件已不在 |
| `ingest/legacy/_不可跑` | 4 | 4 | 目录名即声明：已隔离的一次性重勘脚本 |
| `legacy/脚本/04_数据清洗` | 5 | 5 | ⚠️ **5 个全部含 `DELETE`/`DROP`**（如 `delete_0814.py:14` 直删 `daily_kline` 某天）。别跑 |
| `legacy/脚本/05_辅助工具` | 27 | 27 | 一次性 probe（`probe_blockmap/day/hq_cache/schema`、`query_hx.py`、`add_bom.py`） |
| `legacy/脚本/06_临时脚本` | 14 | 14 | 一次性 fix/tmp 件，文件名即 `temp_*`/`tmp_*` |
| `legacy/脚本/07_因子跑分` | 5 | 5 | 旧因子跑批，跑法已被本项目 `compute/` 取代 |
| `legacy/脚本/ceo` | 205 | 192 | **另一个 agent 的工作区**，读 `/app/working/workspaces/data_in/data/stocks_master.db`（容器内路径，本机根本没有）；`score/`、`temp/` 下各有一份 `gen_yuanshan_report.py.RETIRED` —— 原处就已标了退役。整体判死，留作考古 |
| `legacy/脚本/data_from` | 13 | 13 | 融资融券/北向的早期取数（`fetch_margin*.py`），口径已被本项目 `ingest/jobs/c5_margin.py`、`c11_fundflow.py` 取代 |
| `legacy/脚本/_整理清单.json` | 1 | — | 2026-08-26 那次"散落 py 归类"的 move 台账，可反查某个文件原来躺在 `D:\finance` 根下哪里 |
| `legacy/日志/tq_fetch_log.txt` | 1 | — | 历史日志，只有 09 月之前的一段 |
| `legacy/日志/FinanceTool_logs/` | 17 | — | 旧项目 `logs/` 全量（1 MB）。**其中 `daily_sync.log` 是 `FinanceTool-DailyCloseSync` 在 09-24/09-25 两次真实执行、以及"c18/c19/c20 写库 OK + taskkill 强杀通达信"这个结论的唯一凭证** —— 旧目录一删就没处查了，所以搬进来 |
| `legacy/pre_rename_20260921_{app,scripts,tests}` | — | — | 09-21 全库改名**之前**的代码快照（表名/库名还是旧的），恢复验证时才对得上旧转储 |
| `legacy/schema_analysis/` | — | — | 改名作业的映射表与 SQL 推导过程（`mapping.json`、`detail.txt`、`rename_code.py`）。已完成，不再运行 |

## 一条待办的清除动作

`app/tdx_client/steps/s9_ingest.py:32` 的 `LEGACY_SYNC` 指 `ingest/legacy/01_数据同步/tdx_data.py`，
`run_agent.py:157` 的 `--legacy` 开关触发它。这个脚本会 `os.makedirs('D:/finance/data')`
在**项目外重新长出一个 SQLite 库**。PG 是唯一存储是用户裁定，所以正确动作是**删掉这条分支**
（连带 `--legacy` 开关和 `app/config.py` 的 `LEGACY_DATA`），而不是给它修路径。
删之前先确认 `ingest/jobs/c1_day.py` 这条 PG 路线每天正常出数 —— 那是它唯一存在的理由（"顺手也同步一份旧库"）。

## 想复活某个文件时的判据（三条都过才算能用）

1. 全文 grep `D:\\\\finance\\\\data`、`tq_*.db`、`fin_data` —— 命中即死（旧库没了）。
2. `grep -n "^import \|^from "` 里出现 `tushare`/`akshare`/`mootdx` 且本项目 `requirements.txt` 没有它 —— 环境不保证。
   本机实测 mootdx 与北向那条路已经死了，见《数据获取工作指南/05-外部数据源地图》。
3. 它会写库或写文件吗？写的落在本项目内吗？落在 `D:\finance\` 根下就不许跑。
