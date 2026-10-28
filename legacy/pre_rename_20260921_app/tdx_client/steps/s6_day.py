# -*- coding: utf-8 -*-
"""
s6 · 第一遍下载：沪深京日线页勾「日线和实时行情数据」→ 开始下载。

2026-08-31 实测跑通，全程 10 秒：状态栏依次给出
`正在下载当天行情数据..` → `正在转档盘后数据...` → `下载完毕.`，
之后 `vipdoc/*/lday/*.day` 的 mtime 变成当前时间、`sh600519.day`
末条日期变成 20260831（收盘 1299.52，与 TQ 接口取到的完全一致）。

**这一步的三个要害：**

1. **控件按 ctrl_id 找，不按文字。** 「盘后数据下载」是标准 `#32770`，
   `日线和实时行情数据` = 1424、`开始下载` = 1、状态栏 = 1224，见
   `app/tdx_client/dl_dialog.py` 的 `CTRL`。按文字找的老做法在这个对话框上
   有个陷阱：**五个 tab 的控件同时挂在控件树里**，`1分钟线数据` 有两个
   （1425 是沪深京、1428 是扩展市场），按文字只能拿到第一个。

2. **复选框是 toggle，不能无脑点。** 通达信记住了上次的勾选状态，
   已经勾上时再点一次就变成取消，然后"开始下载"下了个空，
   窗口照样正常关闭，看不出任何异常。走 `win32.ensure_checked`。

3. **完成判据先读状态栏，再核文件。**

       (a) 状态栏出现「下载完毕」—— 通达信自己认为这一遍跑完了
       (b) **多市场多代码抽样，`.day` 尾部日期 == 目标交易日**

   实测出现过 (a) 成立而 (b) 不成立 —— 状态说完了但数据还是旧的。
   所以 (a) 只用来结束等待（顺带回显进度），(b) 才是 verify 的判据。
"""

from __future__ import annotations

from app.tdx_client import dl_dialog
from app.tdx_client.machine import RunContext, Step, StepResult

# 下载耐心上限。实测单日日线 10s；给到 30 分钟覆盖"很久没开机要补多天"。
# **到点就失败留现场，不是悄悄往下走。**
DOWNLOAD_TIMEOUT = 1800


class DayDownloadStep(Step):
    key = 's6_day'
    title = '下载个股日线（第一遍）'
    retry = 2
    verify_timeout = 300
    verify_interval = 10.0

    def precheck(self, ctx: RunContext) -> tuple[bool, str]:
        """
        个股日线已经到位就不重复下载。

        这条 precheck 很值钱：一天里多跑几次 agent（比如先跑一次日线、
        晚点再补分钟线），不该每次都重下一遍全市场日线。
        """
        v = ctx.readiness.day_ready(ctx.target)      # type: ignore[attr-defined]
        if v.ok:
            return True, f'个股日线已到 {ctx.target}（{v.note}），无需下载'
        return False, ''

    def act(self, ctx: RunContext) -> StepResult:
        pid = ctx.bag.get('pid')
        d = dl_dialog.find(pid)
        if d is None:
            return StepResult.fail('「盘后数据下载」对话框不在了，需要重跑 s5_dialog')

        ok, why = dl_dialog.switch_tab(d.hwnd, 'day', log=ctx.log)
        ctx.log(f'  切到沪深京日线页：{why}')
        if not ok:
            return StepResult.ask_human(f'切不到日线页：{why}')

        ok, why = dl_dialog.set_boxes(d.hwnd, {'day': True}, log=ctx.log)
        if not ok:
            return StepResult.fail(f'勾不上日线：{why}')

        ok, why = dl_dialog.start(d.hwnd)
        ctx.log(f'  {why}')
        if not ok:
            return StepResult.fail(why)

        # **等待放在 act 里而不是 verify 里**：状态栏每变一次就回显一行，
        # 长任务没有回显会被当成卡死。verify 只做数据判据，不再等。
        ok, why = dl_dialog.wait_done(d.hwnd, timeout=DOWNLOAD_TIMEOUT,
                                      log=ctx.log)
        if not ok:
            return StepResult.fail(f'日线下载没跑完：{why}')
        ctx.bag['s6_status'] = why
        return StepResult.done(f'通达信报告完成：{why}')

    def verify(self, ctx: RunContext) -> tuple[bool, str]:
        """
        判据是**文件里的日期**，不是状态栏 —— 见模块说明第 3 条。

        留 300s 是给"状态栏说完了但转档还在写盘"留的余量，
        不是用来等下载的（下载已经在 act 里等完）。
        """
        def check():
            v = ctx.readiness.day_ready(ctx.target)   # type: ignore[attr-defined]
            if v.ok:
                return True, v.note
            return False, (f'{v.note}；**状态栏报过 '
                           f'{ctx.bag.get("s6_status", "?")} 但数据仍是旧的** —— '
                           '通达信侧下载失败了，重试或检查网络/磁盘')

        ok, why = self.wait_until(check)
        if ok:
            ctx.bag['s6_done'] = True
        return ok, why
