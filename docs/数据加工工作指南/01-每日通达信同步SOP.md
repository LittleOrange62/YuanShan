# 01 · 每日通达信同步 SOP

目标交易日 = 15:30 收盘线之后、交易日历里最近的开市日（`readiness.expected_trade_date()`）。
所有命令在本项目根目录 `D:\finance\YuanShan` 下执行。

## 完整链路（四步）

```bat
cd /d D:\finance\YuanShan

:: 1) 只读体检：报当前 vipdoc 到哪一天、库里有没有当日数据（不动界面，随时可跑）
python -m app.tdx_client.run_agent --check

:: 2) 盘后下载：GUI 自动化操作通达信，把 日线 + 1/5 分钟线 下到 vipdoc（约 10~30 分钟）
python -m ingest.jobs.p0_tdx_predownload
::    只要日线：--day-only    只补分钟线：python -m app.tdx_client.run_agent --only s5_dialog --only s7_minute
::    反复失败想从头来：python -m app.tdx_client.reset 后重跑，或加 --attempts 3

:: 3) 复核文件真的到目标日（readiness 只看文件尾部日期）
python -m ingest.jobs.p0_tdx_predownload --check

:: 4) 入库 + 对账
python -m ingest.jobs.c1_day         :: 个股/指数/ETF 日线 → market_data.stock_daily
python -m ingest.jobs.c3_sector      :: 板块日线 → market_data.sector_daily
python -m ingest.jobs.c16_minute     :: 1/5 分钟线 → market_data.stock_minute
python -m ingest.jobs.b6_reconcile --check-only   :: 对账，"对账通过"才算干净

:: 收尾：确认 + 关通达信
python -m app.tdx_client.run_agent --check
python -m app.tdx_client.reset
```

- 采集/计算作业都在本项目的 `ingest\jobs\` 下，**统一用 `-m` 方式跑**（`python -m ingest.jobs.<名字>`），
  不再是 `scripts\ingest\xx.py` 那种文件路径 —— 那种目录在本项目里已经没有了。

## 一键版

- `daily_sync.cmd`（本目录 `脚本/`）——把上面串起来。
- 项目里 `python -m ingest.jobs.e1_fill_all` 是更全的"一键追平"（第 0 步就是盘后下载，后面还带份额/两融/龙虎榜/计算等）。只要日线+分钟线用上面的 SOP 即可。

## 判据：什么才算"当日同步好了"

`run_agent --check` 五项全绿：
- 指数自动同步 / 个股日线 / `.lc1` / `.lc5`：vipdoc 尾部日期 == 目标交易日
- PG 入库：`market_data.stock_daily` 当日 **> 4000 行**（全市场约 5000+）

分钟线的完成判据**到 vipdoc 文件为止即可**；要进库再跑 `c16_minute.py`（10-15 晚起加工链在盘后下载跑成后会**自己补点一次**这一步，人工不必记着跑；它的验收只数本次区间，不数全表 —— 见《数据加工工作指南/05》第 34、35 条）。

`--date` 那一天只解那一天（`app/ingest/tdx_binary.minute_window`：先读尾条日期，整文件早于窗口的
直接跳过；再用 `date_idx` 二分定位窗口左端）。**别退回"全解一遍再按日期丢"** —— 那是
O(整个 vipdoc)：实测 `--date 2026-09-23` 全量解析 400 秒跑不完，改成窗口定位后
冷文件缓存 127 秒、热缓存 **37 秒**，入库行数一模一样（2,266,647 根 1 分 + 453,399 根 5 分）。
等价性是钉住的：`tests/test_minute_window.py`（与 `parse_minute` 逐行相等 + 畸形文件必须交回 `None`），
真 vipdoc 上另有 2,880 次窗口比对、380 万参考行、零差异（`.local/c16_equiv.py`）。

**验收看只数，不看日期**：`select dt::date, period, count(distinct security_code) from market_data.stock_minute group by 1,2`
（一天应接近 9,527 只；实测 09-23 曾只有北交所 351 只，日期看着"到了"、行数也不算空）。
被中断后重跑不用从头再来：**当日已齐 ≥200/40 根的代码，其文件整只跳过**（`landed_codes`）——
实测补完 09-23 再跑一次是 `already_in_files 18,888 / files 225 / 3.0 秒 / 合并 0 行`。
每 2,000 个文件打一行进度；`SIGTERM`/Ctrl-C 会把批次记成 `failed` 并返回 130（硬杀 `taskkill /F` 拦不住，
那种情况批次停在 `running`，由覆盖体检的**动态条数判据**兜住（2026-10-07 起：本场条数必须
≥ 前一天的条数 − 偏离值，日线类默认 30 条、分钟类 200 条，两个数在「系统配置 → 收盘链 →
条数完整性」改得动，实现见 `duanxian/completeness.py`）。

## Web 等价操作（不想敲命令时）

- `/datasource` 点「刷新 P0 本地」= 盘后下载；点「一键采集」= 整条链。
- `/jobs` 点「一键追平到最新收盘日」= `fill_all`。
- 注意：调度器默认关（`scheduler_enabled=false`），cron 表达式不生效，都得手动点/手动跑。
- ✅（历史坑，已修）页面上这两条按钮曾经常坏：`app/orchestrator/runner.py` 的 `RUNNERS` 曾把作业指向
  `scripts/ingest/*.py`、`scripts/ops/*.py` 这些本项目里**已不存在**的路径（真身在 `ingest\jobs\`），
  点了会找不到文件。命令行按本页 SOP 的 `-m` 跑法不受影响。详见 [README](README.md) 的同一处警告。

## 实测耗时（2026-09-22 全市场）

| 步骤 | 耗时 |
|---|---|
| 盘后下载（日线+两周期分钟线，9591 只） | 约 50 分钟（含一轮失败重来） |
| c1 日线入库（828 万根解析） | 约 3 分钟 |
| c16 分钟线入库（271 万行） | 约 33 分钟（瓶颈：每只 lc1 存数月历史，整文件读完再按日过滤） |
