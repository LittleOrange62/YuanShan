# -*- coding: utf-8 -*-
"""
s7 · 第二遍下载：切到「沪深京分钟线」页，勾「1分钟线数据」+「5分钟线数据」
→ 开始下载。

2026-08-31 实测跑通，全程 7 分 41 秒：

    正在转档盘后数据...
    正在下载1分钟线数据..[20260831-20260831] 18/9542 … 9471/9542   （4.5 min）
    正在下载5分钟线数据..[20260831-20260831] 15/9542 … 9345/9542   （3.2 min）
    下载完毕.

之后 `vipdoc/*/minline/*.lc1` 与 `*/fzline/*.lc5` 的 mtime 全部刷新。

**比 s6 多两个坑：**

1. **切 tab 只能用鼠标点标签。** `TCM_SETCURSEL` 会让 tab 控件的选中项和
   对话框内部页面状态不一致 —— 实测 `TCM_GETCURSEL` 返回 1、标签也高亮了，
   但 `IsWindowVisible` 显示内容区还是日线页，**紧接着通达信整个进程崩了**。
   判据用 `IsWindowVisible(1425)`，不是 `TCM_GETCURSEL`。详见
   `app/tdx_client/dl_dialog.py` 模块说明第 4 条。

2. **`1分钟线数据` 这个文字在对话框里有两份。** 1425 是沪深京、1428 是扩展
   市场行情，五个 tab 的控件同时挂在控件树上。按文字找会拿到第一个 ——
   可能勾到扩展市场那一页去，下载的东西完全不是想要的。所以只按 ctrl_id 找。

**日线那个框不用取消。** 实测第二遍开头只有一句「正在转档盘后数据」就直接进
1 分钟线了，没有重下日线；为了取消它还要切回日线页，多两次点击反而更脆。
"""

from __future__ import annotations

from app.tdx_client import dl_dialog
from app.tdx_client.machine import RunContext, Step, StepResult

# 分钟线全市场比日线重得多：实测 9,542 只 × 两个周期 = 7 分 41 秒。
# 一小时封顶，覆盖"补多天"和网络慢的情况。
DOWNLOAD_TIMEOUT = 3600


class MinuteDownloadStep(Step):
    key = 's7_minute'
    title = '下载 1/5 分钟线（第二遍）'
    retry = 2
    verify_timeout = 600
    verify_interval = 20.0
    # 分钟线失败不该拦住日线入库 —— 日常复盘主要吃日线。
    fatal = False

    def precheck(self, ctx: RunContext) -> tuple[bool, str]:
        v1 = ctx.readiness.minute_ready(ctx.target, period=1)   # type: ignore[attr-defined]
        v5 = ctx.readiness.minute_ready(ctx.target, period=5)   # type: ignore[attr-defined]
        if v1.ok and v5.ok:
            return True, f'1/5 分钟线都已到 {ctx.target}，无需下载'
        return False, ''

    def act(self, ctx: RunContext) -> StepResult:
        pid = ctx.bag.get('pid')
        d = dl_dialog.find(pid)
        if d is None:
            return StepResult.fail('「盘后数据下载」对话框不在了，需要重跑 s5_dialog')

        ok, why = dl_dialog.switch_tab(d.hwnd, 'min1', log=ctx.log)
        ctx.log(f'  切到沪深京分钟线页：{why}')
        if not ok:
            return StepResult.ask_human(f'切不到分钟线页：{why}')

        ok, why = dl_dialog.set_boxes(d.hwnd, {'min1': True, 'min5': True},
                                      log=ctx.log)
        if not ok:
            return StepResult.fail(f'分钟线勾不上：{why}')

        ok, why2 = dl_dialog.start(d.hwnd)
        ctx.log(f'  {why2}（已勾：{why}）')
        if not ok:
            return StepResult.fail(why2)

        # 进度就在状态栏里（`5850/9542`），每变一次回显一行 —— 这一步要跑
        # 七八分钟，没有回显会被当成卡死。
        ok, why2 = dl_dialog.wait_done(d.hwnd, timeout=DOWNLOAD_TIMEOUT,
                                       log=ctx.log)
        if not ok:
            return StepResult.fail(f'分钟线下载没跑完：{why2}')
        ctx.bag['s7_status'] = why2
        return StepResult.done(f'通达信报告完成：{why2}')

    def verify(self, ctx: RunContext) -> tuple[bool, str]:
        """判据是 `.lc1`/`.lc5` 里的日期，不是状态栏。"""
        def check():
            v1 = ctx.readiness.minute_ready(ctx.target, period=1)   # type: ignore[attr-defined]
            v5 = ctx.readiness.minute_ready(ctx.target, period=5)   # type: ignore[attr-defined]
            if v1.ok and v5.ok:
                return True, f'1 分钟线：{v1.note}；5 分钟线：{v5.note}'
            return False, (f'1 分钟线：{"ok" if v1.ok else v1.note}；'
                           f'5 分钟线：{"ok" if v5.ok else v5.note}；'
                           f'状态栏报过 {ctx.bag.get("s7_status", "?")}')

        ok, why = self.wait_until(check)
        if ok:
            ctx.bag['s7_done'] = True
        return ok, why
