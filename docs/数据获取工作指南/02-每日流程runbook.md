# 02 · 每日流程 runbook（盘后）

前提：Windows 交互式桌面（GUI 自动化要抢焦点），通达信可登录。所有命令在本项目根 `D:\finance\YuanShan` 下执行
（c/e/p/b 链是 `ingest/jobs/` 下的模块，跑法统一 `python -m ingest.jobs.<名>`）；
含中文/✓✗ 输出的脚本建议统一加 `PYTHONIOENCODING=utf-8`，含中文的 SQL 用文件 + `PGCLIENTENCODING=UTF8 psql -f`（GBK 控制台会炸，实测）。

## 标准顺序（一键版）
```
python -m ingest.jobs.e1_fill_all        # 第0步就是 tdxdl 下载，之后 c 链 → d 链 → 审计
```
## 手工分步（一键失败时定位用）
1. **下载**：`python -m ingest.jobs.p0_tdx_predownload`（GUI 自动化点"盘后数据下载"，日线+1/5分钟，约 25~50 分钟；内置 验证-重试-重启，`--attempts`）。
   - prepare 阶段 s3/s4 静默轮询 600s 卡死被杀 = **正常**，链路照常往下走；
   - 第 1 轮 s5 失败（主窗口未最大化）等第 2 轮 kill_all 重启后基本能过；
   - 下载期间别在前台窗口抢焦点；
   - 它**自己带总预算**（默认 1800s，`--budget-sec` 可改）：到点带着出口码回来，
     而不是被上级的 `kill_tree` 收掉留下一堆不知道算不算的状态。
     "下载到一半没成功"分 A 卡死／B 还在推进／C 要人来三种，动作和上限都不一样 —— 见
     [03-故障排查.md](03-故障排查.md) 的「下载到一半没成功是三种病」。
2. **入库**：`c1_day.py` → `c3_sector.py` → `c16_minute.py`；网络源：`c5_margin.py --date`、`c6_lhb.py --date`、`c4_etf_share.py --date`（c11 只取当日）、`c15_snapshot.py`（需 TQ）。
   - ⚠️ **凡"需 TQ"的一步，前提是通达信客户端正在跑且已登录**：TQ 是 `127.0.0.1:17709` 的本地 RPC，
     客户端关着就**直接不可用**（2026-10-06 17:30 实测，`available()` 探不到端口，c12/c13/c15/c20 一次全空，
     报的是"取不到"而不是"0 条"）。它不会因为重试而变好 —— 要么先跑 `p0`（它的 prepare 段会把客户端起起来并登录），
     要么手工开客户端。vipdoc 那一圈（日线主源）读文件，不受这条影响。
3. **派生列回算（最容易漏的一步！）**：c1 之后必须跑 `c10_catchup.finish()` 等价物，否则 `pre_close/limit_up/limit_down/turnover_rate_percent` 整列为空，指标评分全部静默算错。漏了用：
   ```
   python "docs\数据获取工作指南\scripts\backfill_derived.py" 2026-09-23 --chain
   ```
   **判据**：`integrity.sql` 的 A10 项（近 7 日每日 limit_up 空值数应 ≈ 当日新股数，不是全量）。
4. **计算链**：d1→d2→d6→d3→d4→d5→d7（`--recent 3` 幂等 upsert）。
5. **审计与对账**：`PYTHONIOENCODING=utf-8 python -m ingest.jobs.e2_quality_audit`；`python -m ingest.jobs.b6_reconcile --check-only`。
6. **收尾**：`python -m app.tdx_client.reset` 关闭通达信。

## 2026-09-23 实操记录（当晚全流程）
- `Get_DayEnd.py` 在 `py_strategy.cfg` 里 **AutoRun=1**：客户端启动即自动跑收盘采集。当晚 19:07 拉起后 ~19:13 客户端死过一次（采集半途、无 WER dump）；19:41 用户重启客户端后**同一僵尸进程自愈续采**，20:05 写出完整 `day_end_20260923`（5576 行/5576 票）。教训：看到刷屏的"初始化错误"先别杀进程——端口恢复后它能自己续；判活看 `get_dayend.log` 是否还有新行 + `sqlite_master` 表行数。
- 快照节奏：SLEEP_TIME 0.02→0.08（每秒 ~550 次 RPC 是当晚客户端没挺住的最大嫌疑）；熔断 5 次连接级失败即退。
- 当晚整链用 `python -m ingest.jobs.e1_fill_all` 一键跑（tdxdl→daily→snapshot→breadth→voldbl→etf/margin/lhb/fundflow/dividend/share→indicators→scores→sectortrend→ignition→审计），日志 `logs/e1_20260923_night.log`，非骨干步骤失败不拦路、摘要里单列。

## 完成判据
```
python "docs\数据获取工作指南\scripts\check_freshness.py"
```
体检那几张表除 snapshot（等 TQ 那次运行）外全部 = 最近交易日（⚠️ 别背"18 张"这个数：现读 `scripts/manage.py::FRESHNESS_TABLES` 是 **5 张红绿灯表 + 1 张附录**（分钟线刻意不进红绿灯），`completeness` 那份清单另算 —— 两个数是两件事）；`test_ignition` 类"当天无 ignite"是常态（平均 0.7 个/日）。

## 调度器化（免手工）
- `/schedule` 页把 `scheduler_enabled` 打开、`dry_run` 关闭；cron 已登记：盘后 c/d 链 + 同花顺侧 20:20 板块 / 20:30 席位与档案 / 20:40 自选镜像（覆盖前自动备份）。
- 调度器只决定入队，执行在 worker；判定流水看 `operations.schedule_log`，漏采看 `schedule_state.missed_count`。
- 机器当天没开机 → 用 `c10_catchup.py --date` 从 vipdoc 直接补，不用重新下载。

### 自选组成员每日快照（`reference_data.watchlist_member_snapshot`）
这张表存在的唯一理由：把"自选组趋势"从**以今日名单回算**（未来函数）里区分出来。
成员历史**只能当天存、事后补不出来**，所以它比别的表都不容许"今天没跑"。

- **唯一写入方**：`duanxian/watchlist_sync.py:write_snapshot()`，跟着 20:40 那轮自选同步跑。
  名单取自 `stockblock.ini`（源），`group_id` 只按组名从 `config.watchlist_group` 查；
  库里还没有的新组**跳过并报名**（`groups_skipped`），绝不编 id —— 编出来的 id 第二天会撞真 id，历史永久错位。
- **快照走独立事务，覆盖失败也要落**。2026-09-26 实测：`config.*` 被 `config_history` 触发器权限卡住时，
  旧写法（与覆盖同事务）把当天名单一起回滚了，而那几天 `config` 已落后源 +8/-3
  （ini 1288 成员行 vs 库 1283，ini mtime 停在 09-24 10:42）。代价是"快照与库可以不一致"，
  那是故意的：**快照记源，库记镜像**。
- **`app/compute/sector_trend_signal.py` 每天顺手写成员快照**（10-18 读码现证：`snapshot_members()` 在 `:515` 的每日路径里被调，注释原文「**不能等有人单独去点 `--snapshot-only`**：自选组的实盘序列要求连续 21 场，漏一天就少一天，而漏的那些天事后补不回（补出来的只是"今日名单"，那是未来函数）」）。本行原先写"不再写这张表（与数据加工对齐）"——那与代码正相反，也与本屏另一句"唯一写入方"自相矛盾；**唯一写入方这件事不变**，只是那"一步"就是本步自己，不是别人。dry-run 仍不许有写副作用。
  它读的是 `config.watchlist_* JOIN` = 镜像；镜像被卡住的那些天它会天天落同一份旧名单，
  只有 `captured_at` 在动 —— 看着攒够 21 场，实际零信息。
- 空组不产行（join 不到成员）：45 组里 4 个空组当日无行是正常的，别据此判断"组被删了"。
- 读侧算组序列请按 **`group_name`** 聚合，别按 `group_id`：删组重建同名会换 id。
- `ys.watchlist_member_snapshot` 是已作废的影子表（0 行，从未落过真数据），随 `ys` 一起待删。

#### 已回填的历史（2026-09-26，`python -m ingest.jobs.c22_watchlist_snapshot_backfill`）
源是本项目的 `var/backup/watchlist_*.json`（旧项目每次全量导出的副本）。落库 4 天 / 4689 行：
`09-02(1095) 09-14(1114) 09-16(1221) 09-22(1259)`；`captured_at` = 备份自己的 `exported_at`，
所以**回填的行与每日增量的行一眼分得开**（每日增量的 `captured_at` 就是当天 20:40 那轮）。

- `08-30` 那份**没写**：日历 `is_open=false`（非交易日不落归属日）。
- `0922_224527.json`（122 字节、`groups: []`）**判为"没有数据"而不是"当天没人"**，弃掉并在输出里报出来。
- **备份里的 `group_id` 不可移植**：与库里的 id 一个都对不上，且同一个组在备份之间自己就在漂
  （`犇犇熊` 249→544→588→632，库里现在是 705）。照搬会把一个组在几天里当几个组，
  而那些小号（2、3、5）还会撞上库里另一个不相干组的 id、把两条历史并成一条。
  所以回填**只按组名认组**、id 取库里现在的；库里已无此名（当年有、后来删/改名）的组**整组跳过并报名**：
  09-02 跳 9 组、09-14 跳 8 组、09-16 跳 1 组、09-22 跳 0 组。
- ⚠️ 这 4 天**不等间距、有洞**，只能用来验口径与量级，**不能当连续 21 场窗口**。
- 可重跑的抽查：`python -m ingest.jobs.c22_watchlist_snapshot_backfill --verify 犇犇熊`
  （逐日比"备份里的成员集合"与"库里的成员集合"，不一致就退出码 1；换组名传参即可）。
