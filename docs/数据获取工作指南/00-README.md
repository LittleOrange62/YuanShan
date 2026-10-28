# 通达信数据同步 · 工作指南

> **换模型 / 换智能体后，从这里开始读。**
> 本目录记录 D:\finance 通达信数据同步的完整操作流程。每一步都给出可复制的命令、
> 判断成功的具体标志、以及失败时的表现。**不要凭记忆操作，照文档走。**
>
> **本项目 `D:\finance\YuanShan` 是唯一成品。本文出现的相对路径全部相对项目根**，
> 跑之前先 `cd /d D:\finance\YuanShan`。旧项目 FinanceTool 的说明书在本目录
> `../参考/FinanceTool/`，旧脚本在 `ingest\legacy\` 与 `legacy\脚本\`，都是本项目内的归档件。

## 目录

| 文档 | 内容 |
|---|---|
| [01-通达信启动与登录.md](01-通达信启动与登录.md) | 关闭 → 重启 → 登录 → 关弹窗 → 等指数同步 |
| [02-盘后数据下载与入库.md](02-盘后数据下载与入库.md) | 盘后数据下载（日线 / 分钟线）→ 就绪判定 → 入库 |
| [03-故障排查.md](03-故障排查.md) | 已知失败模式与对策 |

## 关键路径

| 用途 | 路径 |
|---|---|
| 通达信程序 | `D:\new_tdx64\TdxW.exe` |
| 通达信数据（vipdoc） | `D:\new_tdx64\vipdoc\{sh,sz,bj}\lday\*.day` |
| 分钟线 | 1 分 `...\vipdoc\{sh,sz,bj}\minline\*.lc1`；**5 分在 `fzline\` 不是 `minline\`**（10-18 实测 `vipdoc\sh\fzline\*.lc5`；《加工/03-本地数据地图》一直是写对的） |
| 旧 SQLite 数据目录 | `D:\finance\data`（主库 `tq_daily_2026.db`） |
| 数据权威源 PostgreSQL 项目 | 本项目根 `D:\finance\YuanShan`（库 `financetool`） |
| 操作脚本（归档，可直接跑） | `ingest\legacy\02_通达信操作\` |
| 同步脚本（归档，可直接跑） | `ingest\legacy\01_数据同步\` |
| 校验脚本（归档，可直接跑） | `ingest\legacy\03_数据校验\` |

## 账号

- 账号：`18969969696`
- 口令：见 `ingest\legacy\02_通达信操作\open_tdx.ps1` 的 `param($Pass)` 默认值
- **注意**：口令是 `Chengzi.888`（**中间有一个点**）。历史笔记里出现过 `Chengzi888`（无点）的写法，那是错的，用它登录会失败。

## 三条硬规矩

1. **不用固定 sleep 判断"完成"**。所有等待都必须有可验证的判据（进程消失 / 窗口出现 / 文件里的日期变成目标交易日）。写 `sleep 600` 等于赌运气。
2. **数据权威源是 PostgreSQL**（本项目根 `D:\finance\YuanShan`，库 `financetool`），不是 `D:\finance\data`。旧 SQLite 里 `limit_up/limit_down` 是一刀切 `pre_close×1.1/0.9`（创业板 20%、北交所 30%、ST 5% 全错），`vol` 存的是「手」不是「股」。详见 [03-故障排查.md](03-故障排查.md#旧库口径已知有偏)。
3. **UI 自动化必须在你登录的桌面会话里跑**。以服务身份 / 计划任务"不管用户是否登录都运行"启动的进程处在 Session 0，**完全看不到通达信窗口**，所有窗口枚举都会返回空。

## 最快路径（数据已经下载好，只要入库）

```powershell
cd /d D:\finance\YuanShan

# 入 PostgreSQL（权威源，口径正确）
python -m ingest.jobs.c1_day           # 日线
python -m ingest.jobs.c3_sector        # 板块日线 + 家数

# 入旧 SQLite（兼容旧脚本，可选；归档件在本项目内，可直接跑）
cd ingest\legacy\01_数据同步
python tdx_data.py sync --all
```

## 完整路径

按 [01](01-通达信启动与登录.md) → [02](02-盘后数据下载与入库.md) 顺序走。
