@echo off
REM ============================================================
REM 每日通达信同步一键脚本：下载 -> 入库 -> 对账 -> 校验 -> 关通达信
REM
REM 2026-09-25 重定位：所有步骤改在 **YuanShan 项目内** 跑（原来 cd 到参考仓库那边、
REM 日志写那边，那个仓库随时会被删，指向它就是定时炸弹）。能力全部有本地副本：
REM 作业入口 `ingest\jobs\*.py`（`-m` 方式跑），拟人操作 `app\tdx_client\`。
REM
REM 前提：本机能开通达信、有交互式桌面。日志写 %CD%（cd 之后就是项目根）下的 logs\。
REM 用法：双击，或命令行 daily_sync.cmd [YYYY-MM-DD]
REM
REM ⚠️ 这条链会真的点击您桌面上的通达信窗口。不想被点就别双击，改用
REM    `python -m ingest.jobs.e1_fill_all`（只补已下载好的文件，不动 GUI）。
REM ============================================================
setlocal
set ROOT=%~dp0..\..\..
cd /d "%ROOT%"
set DATE_ARG=
if not "%~1"=="" set DATE_ARG=--date %~1

echo [1/6] 只读体检（客户端在不在、账号登没登、vipdoc 新不新）...
python -m app.tdx_client.run_agent --check

echo [2/6] 盘后下载（日线+1/5分钟线，约 10~30 分钟）...
python -m ingest.jobs.p0_tdx_predownload %DATE_ARG%
if errorlevel 1 (
  echo 下载未全部到位，看上面输出。分钟线没下来可单独重跑那两步：
  echo   python -m app.tdx_client.run_agent --only s5_dialog --only s7_minute
)

echo [3/6] 复核 vipdoc 是否真的落盘（判据只认数据日期，不认状态栏）...
python -m ingest.jobs.p0_tdx_predownload --check

echo [4/6] 日线/板块/分钟线入库...
python -m ingest.jobs.c1_day
python -m ingest.jobs.c3_sector
python -m ingest.jobs.c16_minute %DATE_ARG%

echo [5/6] 回算与对账（涨跌停档位、换手率、逐字段核对）...
python -m ingest.jobs.b6_reconcile --check-only

echo [6/6] 最终校验 + 把客户端恢复到干净状态...
python -m app.tdx_client.run_agent --check
python -m app.tdx_client.reset

echo.
echo 完成。下面这些不是每天都跑，需要时手动（T 类要求通达信客户端已登录，且单连接）：
echo   python -m ingest.jobs.c17_tdx_sector
echo   python -m ingest.jobs.c18_tdx_membership
echo   python -m ingest.jobs.c19_tdx_dividend
echo   python -m ingest.jobs.c20_tdx_profile
echo   python -m ingest.jobs.c13_ths_block         （同花顺板块成分）
echo   python -m ingest.jobs.c14_ths_watchlist     （同花顺自选；YuanShan 侧另有
echo                                                scripts\sync_watchlist.py 的守卫版，以那版为准）
endlocal
