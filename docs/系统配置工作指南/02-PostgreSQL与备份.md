# 02 · PostgreSQL 与备份

> 库是这台机器上唯一不可再生的资产。**先有备份，再谈搬迁。**

## 服务与连接事实（2026-09-24 实测；凭据一行 2026-09-25 复核）

| 项 | 值 |
|---|---|
| 形态 | **Windows 服务** `postgresql-x64-17`，`Automatic` / `Running`（不是手工 `pg_ctl` 起的，重启机器会自动起） |
| 版本 / 二进制 | PG 17 @ `D:\PostgreSQL\17\bin` |
| 数据目录 | `D:\finance\TataPG`（约 **9.7 GB**） |
| 监听 / 库 | `127.0.0.1:5432`，库 `financetool` |
| 角色 | `ft_reader`（只读）/ `ft_app` / `ft_etl`（写）/ `ft_admin`；超管口令在本项目根目录 `.env` 的 `FT_PG_SUPERPASS` |
| 读凭据 | `.env` 的 `PG_USER` / `PG_PASSWORD` = **`ft_reader`**，实测对 `config.setting` 的 `INSERT=false`（`compute/db.py`、`duanxian/pg.py` 读这两个键） |
| 写凭据 | `.env` 的 `PG_ETL_USER` / `PG_ETL_PASSWORD` = **`ft_etl`**（`compute/db.py`、`duanxian/watchlist_sync.py` 读），**本项目是这套库唯一的写入方** |
| 四角色 DSN | `.env` 里四条：`FT_DSN_READER` / `_APP` / `_ETL` / `_ADMIN`，由 `app/config.py` 读 —— `FT_` 前缀是历史包袱、不是依赖，改名要连 `app/config.py` 一起改 |

⚠️ **`pg_dump` 单库不含角色与口令**。换机恢复后 `ft_reader/ft_app/ft_etl/ft_admin` 会全部不存在 —— 所以 globals 必须单独导（`app/ops/backup.py` 的 monthly 档就干了这件事，别只 dump 库）。

## 备份策略（`app/ops/backup.py`，本项目内）

| 档 | 内容 | 保留 | 命令 |
|---|---|---|---|
| `daily` | `pg_dump -Fc` **只导四个 schema**：`config` / `analytics` / `operations` / `reference_data` | 30 天 | `python -m app.ops.backup daily` |
| `weekly` | `pg_dump -Fc` **全库**（含 `market_data`） | 8 周 | `… weekly` |
| `monthly` | `pg_basebackup` 整簇 + `pg_dumpall --globals-only` | 6 份 | `… monthly` |

分层依据是模块注释里的一个判断：**`md` 域（行情）可再生** —— 日线/分钟线的权威源是通达信 vipdoc，坏了重解析即可；不可再生的是配置表、自选股、评分结果、审计与血缘。所以小个子天天备，大的周周备。

**每档都带恢复验证**：`verify()` 把 dump 恢复到临时库、比对关键表行数、然后删掉那个临时库。作者的话写在文件头：**"没验证过的备份不算备份"**。结果记进 `operations.quality_result`，页面上能看到"最近一次恢复验证是什么时候、过没过"。
（临时库名 10-14 起带本次运行的印记 `financetool_verify_<pid>_<时分秒>` —— 原来是硬名 `financetool_verify`，
 一次没删干净就把后面每一轮都挡成"验不过"，那一晚 daily 连败 123 次就是这个形状。）

### 整簇物理备份：2026-10-19 13:00 第一次真的跑成

`pg_basebackup` 从这套备份代码立起来那天起 **0 次**（旧记录写着"11GB 的簇放得下，没有空间障碍，只是没人跑"）。
本轮实测：`backup.basebackup()` 产物 `monthly/base_20261019_130035/`，**6.24 GB / 25.3 分钟**，
落盘是 **tar 格式**（`base.tar.gz` + `pg_wal.tar.gz` + `backup_manifest`，不是平铺的 PGDATA 形状 ——
恢复时要按 tar 解开归位，别按"目录里应该有 `base/`、`PG_VERSION`"去判断它坏了）。

三条边界要说清：

- **它不跨盘拷**（用户 2026-10-19 裁定）：`mirror()` 只拷文件型产物，目录型交回 `skipped` 并写明原因，
  不假装做了。所以"整簇那份在第二块盘上"这句话**不成立**，成立的是"dump + globals 有第二份"。
- **25 分钟是压着夜间窗口跑的**：monthly 排在每月 1 号（`kind_for()`），那一晚 18:40 的档要跑
  basebackup + 全库 dump + globals，比 daily 慢一个量级 —— 别把 monthly 挂到启动路径上（同 weekly 那条理由）。
- **能生成 ≠ 能恢复**：这一档的"恢复验证"到现在为止**没有做过**（`verify()` 只验 dump）。
  要验得另起一个 PGDATA 解 tar、换一个端口起一次、比行数 —— 那是一个独立的活儿，别把"6.24 GB 落盘了"读成"整簇可恢复"。

### 跨盘那一份：现在由备份这一步自己写（2026-10-18 起）

`app/ops/backup.mirror()`：恢复验证**通过之后**才把这一档的**文件型产物**（daily/weekly 的 dump、monthly 的 globals）
复制到第二块盘 `FT_BACKUP_MIRROR_DIR`（默认那棵树在 C 盘），**复制完核对字节数**，
对不上就报"半截文件"，并把**这一轮整轮记成失败**（措辞点名是哪一半坏的：`跨盘副本没落地…这一轮只有一份档`）。

三条边界都写在函数 docstring 里，这里只说结论：

- **为什么 10-18 才补上**：09-24 那次跨盘复制是**手工一次性的**，之后没有任何代码在写它
  （`grep -rn finance_backup --include=*.py` 当时零命中）。到 10-18 实测，C 盘那棵树里还是 09-25 两份，
  而体检只看 D 盘那一棵树 —— "有一份在另一块盘上"这句话已经悄悄过期三周，**没人知道它过期了**，
  这正是"写着有其实没接"最省事的形态。
- **`FT_BACKUP_MIRROR_DIR` 置空串＝明确不做**：台账里写"没配"（`mirror_ok=None`），不算做成也不算失败。
- **目录型的 `pg_basebackup` 不跨盘复制**（整簇体量，会把夜间窗口吃掉）：交回 `skipped` 并写明原因，
  要做得单独排一档，这里不假装做了。

配套的判据改动一处：`manage.py doctor` 的 daily 阈值不再是一条裸的 36 小时文件年龄，
按 `backup_last_daily_due()` 的**日历**判（周六周日按设计背全库 weekly，那两天没有 daily 是正常的）。
10-18 实测到的假红就是这条裸阈值：`daily＝52 小时前` 挂在体检里，而当晚 18:54 的 weekly 是好的。

## 2026-09-24 实测状态：这套东西之前一次都没跑过

| 检查 | 结果 |
|---|---|
| `python -m app.ops.backup status` | **daily 0 份 / weekly 0 份 / monthly 0 份** |
| Windows 计划任务 `YuanShan-Backup-Daily/Weekly/Monthly` | **一个都没注册** —— `scripts/ops/setup_backup_task.ps1` 需要管理员 PowerShell，从没跑过 |
| 磁盘上唯一存量 | `D:\finance\TataPG_backup\pre_rename_financetool_20260921.dump`（605 MB，09-22 手工留的），**不在三档轮转管理里** |
| 存在的计划任务 | 只有旧项目那条收盘同步任务（State=Ready），当时读到 `LastTaskResult=267011`＝从未运行 |

⚠️ **最后那一行的结论已在 2026-09-25 被推翻，就地改正**：旧项目那条 Windows 计划任务
（任务名 `FinanceTool-DailyCloseSync`，入口 `scripts\ops\daily_close_sync.py`、cwd 在旧仓库、日志写旧仓库 `logs\`）
**真的跑过两次**：2026-09-24 16:05 与 2026-09-25 16:05（09-25 那次由 `LastRunTime` 与计划任务直接对上；
09-24 那次是同一份日志里同一时刻 16:05:01 的同一条链），两次都走完"一键追平 → c18 板块成分 / c19 除权 / c20 个股档案
三步 **PG 写库成功** → 校验 → 结尾 `taskkill /F /IM TdxW.exe` 强杀通达信"，`LastTaskResult=1`（非零是收尾那条）。
2026-09-25 读到的是 `LastRunTime=2026-09-25 16:05:05`、`NextRunTime=2026-09-28 16:05:05`，之后由用户
`Disable-ScheduledTask` 停用（`State=Disabled`，**别删，留着当证据**）。
含义：**这套库此前存在第二个写入进程**，且它写的是与本项目同名的生产 schema —— 现在裁定为本项目唯一写入方，
旧任务保持停用就是这条裁定的执行手段；把它重新启用＝恢复双写。详见 [数据加工/07](../数据加工工作指南/07-定时任务.md)。
本项目自己的对应任务（`YuanShan-DailyCloseSync`，入口 `python -m ingest.jobs.daily_close_sync`，
日志 `D:\finance\YuanShan\logs\daily_sync.log`）**尚未注册**。

结论：所谓"唯一真资产无备份"此前是字面成立的 —— **有备份代码、有备份策略，但没有任何一份在管的备份，也没有任何调度在跑它**。

本轮（2026-09-24 22:31）已手工补跑：`daily` → `daily_20260924_223141.dump` 225.24 MB，**恢复验证通过**；`weekly` 全库随后在跑。

## ✅ 2026-09-24 已落地的两件事

**1. 手工补齐了第一份可用备份**（P0 前置）：

| 档 | 文件 | 大小 | 恢复验证 |
|---|---|---|---|
| daily | `TataPG_backup/daily/daily_20260924_223141.dump` | 225.24 MB | **通过**（恢复到 `financetool_verify` 比行数后删库） |
| weekly | `TataPG_backup/weekly/weekly_20260924_223652.dump` | 608.29 MB | **没做** —— 见下面的缺口 |

用 `pg_restore -l` 点过 weekly 那份的内容：`market_data` 83 张分区表、`analytics` 31、`config` 24、`operations` 12、`reference_data` 10、**`qd` 9 张全在**，另含 `reference_data.security_st_period`（is_st 那张维度表已经建好了）。

**2. `manage.py doctor` 加了「数据库备份」项**（`scripts/manage.py` 的 `backup_freshness()`；引用体检项用名字不用序号，序号会随加项漂移）：

```
✓ 数据库备份：最近备份 daily＝0 小时前、weekly＝0 小时前（阈值 daily≤36h／weekly≤8天）   ← 10-06 当时的措辞
! 数据库备份：备份目录不存在：D:\...\no-backups-here（可设 VIBE_BACKUP_DIR）
```

10-19 之后的同一项长这样（daily 的过期点改成了按排程算，跨盘那一份也进来了）：

```
✓ 数据库备份：daily＝1 小时前、weekly＝15 小时前 —— 都在阈值内（daily 按排程应到 2026-10-16）；
  跨盘副本 daily＝有、weekly＝缺（…\weekly_20261018_184020.dump） —— 缺的是「程序学会自己拷之前」背的那几份，
  只陈述不判红；从现在起每背成一档就当场补一份，没拷成的那一轮自己记失败（见「采集作业」）
```

两个设计决定，动手改之前先看懂：

- **它是 advisory：红叉用 `!` 不用 `✗`，永不拦启动。** `checks_ok()` 把 advisory 行排除在启动闸、`auto` 的"要不要自动跑 setup"判定、`--json` 的 `ok` 与退出码之外。理由：启动器和备份没有因果关系，拦下来只会逼人绕过启动器直起 uvicorn —— 那才是真正会丢备份的用法。今天就是活例子：三档 0 份的时候如果拦启动，第一反应一定是"先起来再说"，然后照样直起 uvicorn。
- **必须排在「安装与源码一致」之前。** `setup()` 用 `checks[:-1]` 剔掉最后一项（那一刻指纹还没落盘）。**任何排在指纹后面新增的体检项都会被 setup 当成指纹项一起剔掉** —— 而 `setup()` 里那句 `all(...)` 一旦为假就抛错、永远不写 `setup.json`，等于把 setup 自己锁死。这条已用测试钉住：`tests/test_launcher.py::test_stale_backup_is_advisory_and_fingerprint_stays_last` 断言 `checks[-1]["name"] == "安装与源码一致"`。

备份目录默认 `D:\finance\TataPG_backup`，可用环境变量 `VIBE_BACKUP_DIR` 覆盖。

## ⚠️ 吸收时必须一起解决的三个缺口

1. **`do_verify = kind in ('daily', 'monthly')`（`app/ops/backup.py:409`，在 `run()` 里；10-18 现读，原先抄的 `:270` 现在是 rotate/清理那段）—— weekly 恰好是唯一不做恢复验证的档**，而它是唯一覆盖 `qd` 与 `market_data` 的那一份。也就是说"全库备份"从来没被证明恢复得出来。CLI 没有强制验证的开关（`--no-verify` 只能关不能开），本轮只能直接调 `app.ops.backup.verify(path)` 补验。吸收时应改成**三档都验**，至少给一个 `--verify` 逃生口。
2. **`CORE_SCHEMAS = ('config','analytics','operations','reference_data')` —— 没有 `qd`。** daily 靠"`md` 域可再生、vipdoc 能重解析"给它开了免检，但 `qd` 那 9 张表是按 quantdash 口径**重新实现**并回填的（1169 个交易日），vipdoc 里没有任何东西能重算它 —— 它属于 config 那一类，不属于 md 那一类。择一：`qd` 加进 `CORE_SCHEMAS` / weekly 改成天天跑 / 吸收时按新 schema 清单重划可再生边界。**别都不做。**
3. **`verify()` 的"验证"比它的说法弱得多**（2026-09-24 实读代码 + 实跑）：
   - `VERIFY_TABLES` 只有 **11 张，全部在 `config` 与 `reference_data`** —— `market_data`、`analytics`、`qd`、`operations` **一张都不查**。
   - docstring 写"比对关键表行数"，实现是 `SELECT count(*)` 后**只判 `> 0`**（`result['ok'] = not empty`）。**非空 ≠ 和源库一致。**
   - 后果：`ok=True` 实际只证明"608MB 的 dump 能被 `pg_restore -j4` 完整跑完不中断 + 11 张配置表非空"，**不证明 637 万行日线回来了**。
   - 吸收时改成：**恢复库与源库逐表比行数**，且覆盖 `market_data`/`analytics`/`qd`。本轮已另写一次性脚本按 13 张表实测比对（含 `qd.sentiment_cycle`、`stock_daily_2021..2026` 这类不可变历史表）。
   - 📛 顺带记我自己的教训：**第一次跑我把返回里的 `tables` 明细过滤掉了没留档**（`{k:v for k,v in r.items() if k != 'tables'}`），等于把唯一能证伪"通过"这两个字的数据扔了 —— 同日另一次是 `pytest | tail -8` 把 FAILED 清单截掉。**诊断输出先落盘再看，别边看边裁。**
   - **连带发现（数据布局问题，归 `code：数据加工`，不是备份问题）**：补跑逐表比数时发现 `analytics.indicator_value` 的 **14,254,680 行全部落在 `_2026` 一个分区**，`_2021`～`_2025` 与 `_default` 都是 0，而分区键是 `RANGE (trade_date)`。也就是**按年建了分区但边界配错了**，年度分区形同装饰、按日期查询拿不到分区裁剪的收益。它同时让"抽一张年度分区表来验恢复"这种抽查失效（两边都 0 也算通过）—— **验证要挑有数的表，别挑名字看起来对的。**

## ⚠️ 更正：计划任务不是"从未跑过"，它今天真跑了（2026-09-25）

本页与多处记忆里"唯一存在的计划任务 `FinanceTool-DailyCloseSync`，`LastTaskResult=267011`＝从未跑"是 **2026-09-24 的观测，已经过期**。2026-09-25 实测：

- **今天 16:05:05 它真跑了一次**：`c18/c19/c20`（板块成分 / 除权除息 / 个股档案）**写库成功**，结尾执行 `taskkill /F /IM TdxW.exe` 强杀通达信，**退出码 1**。已由总协调 `Disable-ScheduledTask` **停用（未删除，`Enable-ScheduledTask` 可复原）**。
- **教训：非零退出 ≠ 什么都没发生。** 这个任务是一串副作用的合集：写库、杀进程都在 `exit 1` 之前完成。只看退出码判"这次没生效"会得出完全相反的结论 —— 数据其实进去了，通达信其实被杀了。查这类任务必须看**它自己的日志**（`legacy/日志/`、`var/`），不能只看 `LastTaskResult`。
- **`YuanShan-Backup-Daily/Weekly/Monthly` 三档确实仍未注册**，这条结论不变。
- 顺带：注册这类任务的前置是"用户点头"，因为注册＝**让机器每天 16:05 自己去点桌面上的通达信并写库**。脚本改对 ≠ 可以注册。

## 备份现在能从 YuanShan 本项目跑完了（2026-09-25 实测）

`.env` 补齐 `FT_DSN_ADMIN` / `FT_PG_SUPERPASS` 等 19 个键之后，本项目那份 `app/ops/backup.py` 是真能跑的，不再依赖旧项目：

```
$ .venv/Scripts/python.exe -X utf8 -m app.ops.backup daily
=== 备份 daily ===
  dump        daily_20260925_201824.dump  225.24 MB
  恢复验证    通过
```

> 之前那条"`backup` 作业只能读文件、执行体在外部进程"的判断**在当时是对的**（`dsn_parts('admin')` 当场抛 `角色 'admin' 的 DSN 未配置`），键迁入后已不成立。`/api/jobs` 里这一项的 `available` 也跟着放开，并且现在可以真触发（`POST /api/jobs/backup/run`）。

## 不注册计划任务，怎么也能每天有一份新备份（2026-10-06 立）

用户红线：**「不要注册任何计划任务」**（要注册必须点头）。但备份这件事当时实测已经拖到
**daily＝201 小时前、weekly＝238 小时前**（09-25 01:13 / 09-26 19:33 那两份），而库里 11GB 是本机唯一真资产。
所以走**「启动即补」**这条不注册任何东西的路：

```
manage.py start() → 健康检查通过 → 打开浏览器之前 → backup_on_start(root)
```

| 要素 | 落点与做法 |
|---|---|
| 判据 | `scripts/manage.py::backup_ages()`（**唯一一份**读档入口，与体检「数据库备份」共用，两处各写就会"页面说不旧、启动器天天补"）。阈值两件事分开：**daily 按日历**判"最近一个该背它的那天有没有档"（`backup_last_daily_due()`，10-19 起；原来是裸的 36 小时文件年龄，周末必假红），weekly 仍是 ≤8 天的文件年龄；再加上**跨盘那一份在不在**（`backup_mirror_gap()`，同名同大小才算有） |
| 动作 | **只补 daily**：`python -X utf8 -m app.ops.backup daily`，`Popen` 后台、参数全是常量（不起 shell、没有可拼接输入） |
| 为什么 weekly 不挂启动路径 | weekly 是全库（实测这份 866MB、十几分钟）。把"打开网站"变成"等一个全库 dump"，用户就会绕过启动器直起 uvicorn —— **那才是真正会丢备份的用法**。weekly 过期时这一项只点名并给人工命令：`python -m app.ops.backup weekly` |
| 不叠第二份 | 最新 dump 在 `BACKUP_IN_PROGRESS_MIN=20` 分钟内被改过 → 不起新的、也不跑 `pg_restore` 验它（半截文件会被读成坏文件）。10-06 17:41 我的钩子与 17:42 一份手工备份**同时在长**，两份 `pg_dump` 抢同一块盘，谁都不会更快 —— 这条边界是那次实测逼出来的 |
| 不重复起 | `.local/backup_on_start.daily.lock` 30 分钟内不再起；**过期锁必须能重新拿起**（第一版用 `O_EXCL` 却不清旧锁，一次崩溃就把这条通道永久焊死，而它看起来还在工作） |
| 不带崩网站 | 起不来只回一句「想补跑但起不来（原因）」，异常一律吞在这一层 —— 备份和启动网站没有因果关系 |
| 关掉 | `VIBE_BACKUP_ON_START=0`（`start()` 里那行读数会明写"已关掉"） |
| 留痕 | `.local/backup_on_start.log`（每次补跑一行 header + 子进程全部输出）；体检「数据库备份」项照常报年龄 |

**今天真跑出来的一组**（10-06，从"六天没有可恢复副本"到都在阈值内）：

```
daily_20261006_173658.dump  364 MB   （17:39 完成）
daily_20261006_174135.dump  364 MB   （17:44 完成）
weekly_20261006_174205.dump 866 MB   （17:49 完成）
18:22 体检原文：✓ 数据库备份：daily＝1 小时前、weekly＝1 小时前 —— 都在阈值内
```

⚠️ 这条替代方案**买到的东西有边界，别当成已经解决**：

1. **只在有人开网站的那天生效**。连续不开机（长假、机器搬走）就没有新备份 —— 这正是计划任务能补而它补不了的那一段；
2. **跨卷副本 `C:\finance_backup\` 不会自动更新**（仍是人工，见上面 P0 表）；
3. ~~**weekly / monthly 仍需人工**~~ **10-12 深夜起不再需要人工**：备份有了第 11 条常驻循环 `pg-backup`
   （默认 18:40 一档，档由日期定：1 号 monthly／周六周日 weekly／其余 daily），
   判据与记账只住 `app/ops/backup.py::kind_for / run_once` 一处，命令行 `run-now backup` 走同一个执行体。
   **"只在有人开网站的那天生效"这条边界没变**（进程不开就没有循环）—— 那条还是要计划任务或开机自启；
4. `pg_dump` 子进程**本身没有超时**：起了不收，外部只能靠"20 分钟内没再被动过"去猜它完了还是死了。
   ⚠️ 新加的循环也**没有**给它加超时，但它补了一条更值前置的判据：**0 字节的产物不算背到**
   （`run()` 逐份用 `_dump_bytes` 量，空档直接把这一轮判失败并**跳过轮转**，不去删旧的那份）。
   这不是假想：`monthly/globals_20260924_231413.sql` 至今 0 字节，`_失败_weekly_20260926/` 里躺着四份 0 字节 weekly
   —— 只看退出码的话那些都会被记成"背到了"，等到真要恢复那天才发现手里是空文件。

**10-12 实测**（接循环那天，同一入口 `python -X utf8 scripts/jobs.py run-now backup`）：
`daily_20261012_222603.dump` 499.23 MB、恢复验证通过，`state.json` 里 `backup` 那一格同步刷新
（`kind/artifact/mb/verify_ok/rotated` 都在），体检「数据库备份」从 ✗148 小时 转 ✓1 小时。
`pg_dump` 全跑约 10 分钟，所以 18:40 那一档到点后有 120 分钟的补窗，当天背成一次就不再重跑。

要不要注册那条真调度（`YuanShan-Backup-*`，脚本 `scripts\ops\setup_backup_task.ps1` 已在），
**等用户点头**；点头之前本文件不出现任何"已注册"的表述。

## P0 收口进度（2026-09-24 起）

| 条件 | 状态 |
|---|---|
| 有一份逻辑上完整的库备份 | ✅ weekly 全库 `weekly_20260924_223652.dump`（608 MB，`qd`/`market_data` 全含） |
| **做过一次实际恢复验证** | ✅ daily 22:31 通过。**weekly 23:2x 另跑一次"恢复库 vs 源库逐表比行数"**：13 张表全部相等，其中 3,789,096 行是 `market_data.stock_daily_2021/2022/2023/2026` 的真实历史数据，`qd.sentiment_cycle` 1169、`qd.board_ladder` 8183 也对得上。⚠️ 13 行里 `analytics.indicator_value_2023` 是 **`0 == 0` 的假通过**（见 §三缺口 3 的连带发现），有效比对按 **12 张**计。日志：`TataPG_backup\weekly_fullcompare_20260924.log` |
| 角色与口令 | ✅ `monthly/globals_20260924_231413.sql`（`dump_globals()`，`pg_dumpall --globals-only`）—— 此前**从没导过**，意味着旧备份换机恢复后四个角色全不存在 |
| **跨卷副本** | ✅ 已复制到 `C:\finance_backup\TataPG\{daily,weekly,monthly}`，字节数与源一致（daily 236,183,144 / weekly 637,840,252） |
| 三档计划任务 | ❌ 仍未注册（要管理员 PowerShell） |
| basebackup（整簇兜底） | ❌ 未做，留作 monthly 原设计 |

### ⚠️ `globals_*.sql` 是敏感文件，不是普通备份

实测该文件含 **5 条 `CREATE ROLE ... PASSWORD '...'`**（`ft_admin`/`ft_app`/`ft_etl`/`ft_reader`/`postgres` 的口令散列）。因此：

- **不要**把它放进 `.local/logs`、分享包、截图、会话回报，或任何会被粘贴出去的地方；
- 跨卷副本落点 `C:\finance_backup\` 要按"等同于能登录数据库"来对待（NTFS 权限，不共享）；
- 诊断脚本只准报**计数**（`grep -c "PASSWORD '"`），不准报行内容 —— 本文档里的实测就是这么做的。

## 备份树的 NTFS 权限（2026-09-25 已收紧，含一次踩坏与还原）

`globals_*.sql` 含角色口令散列，`daily/weekly` dump 是整库数据 → 两棵备份树**必须**只有 `SYSTEM` + `BUILTIN\Administrators` 能读。现状（实测）：

```
D:\finance\TataPG_backup   SYSTEM:(F) (OI)(CI)(F) + Administrators:(F) (OI)(CI)(F)     全树残留宽授权 0 条
C:\finance_backup          同上                                                          全树残留宽授权 0 条
```

### ⚠️ 做法上有两个坑，我第一次两个都踩了

1. **`icacls /inheritance:r /grant:r "…:(OI)(CI)F" /T` 会把子文件的 ACE 清空却给不出有效授权** —— `(OI)(CI)` 是容器标志，落到**文件**上不产生可访问权限。结果：`globals.sql`、两个 `weekly.dump` 全部 `PermissionError`，**备份读不出来就等于没有备份**。
   正确顺序（本次实际用的）：**先给显式授权，再动 inheritance**，且对后代用不带容器标志的纯 `F`：

   ```bat
   icacls "D:\finance\TataPG_backup" /grant:r "BUILTIN\Administrators:(OI)(CI)F" "NT AUTHORITY\SYSTEM:(OI)(CI)F"
   icacls "D:\finance\TataPG_backup" /T /grant:r "BUILTIN\Administrators:F" "NT AUTHORITY\SYSTEM:F"
   ```

2. **`icacls /save` 的快照救不了这次**：`/restore` 报「处理 1 个文件时失败」（0 成功），**别把"我存了快照"当成能回滚**。真正回滚手段是上面那条 `/T /grant:r`（不碰 inheritance），几秒就能把访问加回来。

### 验收必须用恢复工具，不能用 python 读一下就完事

`python -c "open(...).read()"` 通过 ≠ 恢复路径通。**判据是 `pg_restore -l <dump>` 能列出 TOC**（实测 `TOC Entries: 3301`）—— 它走的是真正会用于恢复的那条读取路径。另外 Git Bash 会把 `/save`、`/T` 这类参数改写成 `C:/Git/save`，**必须先 `export MSYS2_ARG_CONV_EXCL="*" MSYS_NO_PATHCONV=1`**，否则 icacls 静默收到的是错误参数。

快照留档：`YuanShan/.local/acl_backup/*.acl`（内容只有 SID 与权限位，无敏感数据，属诊断件，放 `.local/` 正确）。

### 「可恢复性」怎么被 doctor 与页面共用（标记文件契约）

`manage.py` 的「数据库备份」项**实跑 `pg_restore -l`**（判据不是文件存在与 mtime），结论写进
`YuanShan/.local/backup_readcheck.json`：

```json
{ "D:\finance\TataPG_backup\daily\daily_20260925_011226.dump":
  { "stamp": {"mtime": 1790270022, "size": 236183538},
    "ok": true, "why": "", "checked_at": 1790314378.1 } }
```

同一 `(mtime,size)` 24 小时内复用结论 —— 全库 608MB 列一次 TOC 要几秒，`/api/jobs` 每次刷新都付不值；标记缺失时页面老实报「未验」。⚠️ 缓存失效判据是 mtime+size，**权限被改坏时两者不变**，所以结论最长 24 小时才自纠；刚动过 ACL 想立刻复测就删掉这个文件。

## 还欠的（P0 未完全收口）

- **三个备份计划任务仍未注册**：`scripts\ops\setup_backup_task.ps1`（Daily 22:30 / Weekly 周日 23:00 / Monthly 四周一次周六 23:30，
  任务名 `YuanShan-Backup-*`，日志 `logs\backup_{daily,weekly,monthly}.log`），**要管理员 PowerShell**，从没跑过。
  不注册就还是"代码在、没人跑"。
  **2026-10-06 更新**：daily 这一档已经不再依赖注册 —— 启动即补（见上面「不注册计划任务，怎么也能每天有一份新备份」）。
  **2026-10-18 更新**：跨卷副本也不再依赖人记得复制了 —— 备份那一步做完恢复验证后自己拷到第二块盘并核对字节数
  （见上面「跨盘那一份」），没拷成这一轮就记失败。三档的**调度**现在由常驻循环那条 `pg-backup` 负责，
  所以"没注册计划任务"不再等于"没人跑"；剩下的缺口只有一个：**机器没开机 / uvicorn 死了那天就没有新备份**，
  而这一条看护是 `YuanShan-KeepAlive8910` 那一档管的（它只拉服务、不跑数据）。
  剩下的仍是"没人开机就没有新备份"和 weekly/monthly 要人工。
  ⚠️ 旧版这里写的"实测 `Get-ScheduledTask` 里只有一条收盘同步任务、且 `LastTaskResult=267011`＝从未跑"**已作废**——
  那条任务实测跑过两次并写过库，见上面「2026-09-24 实测状态」的改正段。
- ~~**`ft_etl` 凭据还没进 `.env`**~~ ✅ **已到位（2026-09-25）**：`FT_DSN_READER/APP/ETL/ADMIN` 四条 DSN、`FT_PG_SUPERPASS`、
  `FT_TDX_ACCOUNT/PASSWORD/DIR`、`FT_BACKUP_DIR`、`FT_PG_BIN/DATA` 与三个 API token 等 19 个键都已迁入本项目根目录 `.env`，
  写通道另有 `PG_ETL_USER` / `PG_ETL_PASSWORD`（`compute/db.py` 用）。**剩下的纪律是"别打印"**：
  `.env` 不在任何 git 保护之下（本项目不是 git 仓库），口令进诊断输出/异常栈/`--json` dump 就是永久泄露。

## 动手前必读（用户已裁定：旧项目 FinanceTool 退役、能力全部吸收进本项目，本项目是这套库唯一的写入方）

- **动库结构前先手跑一次 daily + 确认 weekly 有份**。备份轮转是靠脚本跑的顺带清理，光有 dump 没有 `status` 里的记录＝没有可信备份。
- 旧项目那边**没有 `.venv`**，它的 `app/ops/backup.py` 是用**全局 Python 3.14** 跑的（那条旧计划任务的 Action 里写的也就是全局 3.14），
  `.ps1` 里"找不到 `.venv` 就退回 PATH 上的 python"正好是实际生效的分支。
  本项目统一走 `.venv\Scripts\python.exe`（`scripts\ops\*.ps1` 已改成优先 `.venv`、只有它不存在才退回 PATH），别再依赖全局环境。
- **控制台是 GBK**，任何 `-m app.ops.*` 都要带 `-X utf8`（或先设 `PYTHONIOENCODING=utf-8`）。
- 计划任务需要**管理员** PowerShell 才能注册；这一步不能省，否则又是"代码在、没人跑"。
- 恢复演练：`pg_restore -d financetool_verify --no-owner <dump>` 后比对行数。**别在真库上试恢复。**

相关：[01-启停与端口](01-启停与端口.md)、[数据获取/04-全库改名记录](../数据获取工作指南/04-全库改名记录.md)

## 恢复验证为什么会整夜记成"验不过"（2026-10-14 实测并修）

**现象**：每日备份常驻循环 18:40 那一档记成失败，屏幕上只有「恢复验证未通过」；
手动跑那一发（`scripts/jobs.py run-now backup`）给出的原文是
`建临时库失败：ERROR: database "financetool_verify" already exists`。
**dump 本身是好的**（`daily_20261014_203634.dump`，554,522,731 字节 = 528.83 MiB 已落盘），
坏的只是"证明它能恢复"这一步 —— 这类失败最阴的地方在于它长得像"备份坏了"。

**真因（两件事叠在一起）**：

1. `app/ops/backup.py::verify()` 的临时库名是**写死的** `financetool_verify`；
2. 它开头那句 `DROP DATABASE IF EXISTS` 的**返回值原先被丢掉不看** ——
   上一轮 `pg_restore -j4` 还有客户端进程连着这个名字时，DROP 必然失败
   （`database is being accessed by other users`），于是紧接着的 `CREATE DATABASE`
   撞同名报错，屏幕上只留下一句"恢复验证未通过"。
   当晚手动那一发（20:40）之所以撞上，是因为常驻循环自己正在跑一次验证（20:36 起，20:47:54 成功）——
   **两个写入者共用一个固定名**，与《数据加工/05》第 30 条那个中间表事故是同一个形状。

**修法（本轮已落地）**：

- 临时库名带本次运行的印记：`_verify_db()` → `financetool_verify_<pid>_<HHMMSS>`；
  名字只允许 `[A-Za-z0-9_]`（`_db_name_or_raise()`，那几句是 `psql -tAc` 拼字符串、不能绑参）。
- 删之前先 `pg_terminate_backend` 收掉还连着它的会话，**顺序不能反**。
- **DROP 失败不再静默**：直接返回一句「清掉旧临时库 … 没成，先不建新的」，把真实原因带出来。
- 名字唯一化之后没人再去清那个历史固定名库，所以 `verify()` 顺手清一次；清不掉就在结果里留
  `stale_verify_db` 一句（本档结论照算，但不许悄悄）。存在性用 `pg_database` 目录查，
  **不去解析错误消息里的英文子串** —— 这台机器的服务端消息是中文的。
- 收尾删不掉时留 `cleanup_error`（孤儿临时库会一直占磁盘，且会让别人的 DROP 开始失败）。
- 回归测试：`tests/test_backup_round.py` 里两条新的（临时库名每次不同 + 删不掉要说话、
  库名不许带出能拼进 SQL 的其它字符），全文件 **11 passed**（不打真库、不起真进程）。

**今晚的结果（台账现读 `~/.duanxian-agents/jobs/state.json`）**：

```
backup: last_status=ok  last_ok_at=2026-10-14 20:47:54  consecutive_failures=0
        kind=daily  artifact=…\daily_20261014_203634.dump  mb=528.83  verify_ok=true
```

即：**这一份 dump 的"可恢复性"是被真验过的**（`verify_ok=true` 意味着 pg_restore 进去、
11 张关键表行数都非 0）。此前那一百多次连败的根因（`china_today()` 返回 str 被当 date 用）
与今晚这一发的根因（固定临时库名）是两件不同的事，都已修。

**给下一次自己的提醒**：手动补跑备份之前，先看一眼 `/api/jobs` 的 `backup` 那一行是不是正在跑
（或在 `.local/` 留的日志里有没有在写的 dump 文件名）—— 与循环抢同一份资源，只会得到一句误导性的红。
