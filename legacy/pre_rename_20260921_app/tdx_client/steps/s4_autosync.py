"""
s4 · 等登录后的自动同步。

用户原话："检测 TPyth 这样一个同步数据的进程，等待他下载完数据后关闭即可。"

**两条判据必须同时成立，缺一个都会误判：**

1. `TPyth*` 进程退出 —— 说明同步器自己认为干完了；
2. 指数日线（`sh999999`、`sz399001`）尾部日期 == 目标交易日 ——
   说明它真的把数据写下来了。

只看第 1 条的问题：TPyth 有可能因为网络失败而**提前退出**，
进程没了但数据还是旧的。只看第 2 条的问题：数据可能是上一次运行留下的，
这次同步还在跑，此刻读到的"新"是旧的新。两条一起看才闭合。

还有一个反向情况要区分开：**指数是自动同步的，个股不是。**
所以这一步通过之后个股日线仍然可能是旧的 —— 那恰好就是
"该去手动点盘后数据下载"的信号，也就是 s5~s6 存在的理由。
"""

from __future__ import annotations

from app.tdx_client import locator
from app.tdx_client.machine import RunContext, Step, StepResult

# TPyth 同步的耐心上限。实测正常几十秒到两三分钟；给到 10 分钟是
# 为了覆盖"很久没开机、要补好几天"的情况。到点了就报失败留现场，
# **不是**悄悄往下走。
SYNC_TIMEOUT = 600


class AutoSyncStep(Step):
    key = 's4_autosync'
    title = '等自动同步（TPyth + 指数日线）'
    retry = 1
    verify_timeout = SYNC_TIMEOUT
    verify_interval = 5.0

    def precheck(self, ctx: RunContext) -> tuple[bool, str]:
        running, who = locator.sync_running()
        v = ctx.readiness.index_ready(ctx.target)      # type: ignore[attr-defined]
        if not running and v.ok:
            return True, f'TPyth 已退出且指数已到 {ctx.target}'
        return False, ''

    def act(self, ctx: RunContext) -> StepResult:
        """
        这一步**不主动做任何事** —— 自动同步是通达信登录后自己触发的，
        我们只负责等和判。

        写成一个 act 什么都不干的 Step 而不是塞进 s2 的 verify 里，
        是为了能单独 `--only s4_autosync` 重跑："我怀疑同步没完成"
        是个独立的问题，不该逼着重新登录一次。
        """
        running, who = locator.sync_running()
        if running:
            return StepResult.done(f'同步进程在跑：{who}，等它自己结束')
        return StepResult.done('没看到同步进程，直接看数据判定')

    def verify(self, ctx: RunContext) -> tuple[bool, str]:
        def check():
            running, who = locator.sync_running()
            if running:
                return False, f'{who} 还在跑'
            v = ctx.readiness.index_ready(ctx.target)   # type: ignore[attr-defined]
            if not v.ok:
                dates = [str(p.last_date) for p in v.probes]
                return False, (f'TPyth 已退出但指数还停在 {dates}，'
                               f'目标 {ctx.target}')
            return True, f'TPyth 已退出且指数到位（目标 {ctx.target}）'

        ok, why = self.wait_until(check)
        if ok:
            # 顺手报一下个股的现状：这决定后面 s6 是真要下载还是可以跳过。
            dv = ctx.readiness.day_ready(ctx.target)    # type: ignore[attr-defined]
            ctx.bag['day_ready_before'] = dv.ok
            ctx.log(f'  个股日线现状：{"已到位" if dv.ok else "仍落后"} —— '
                    + ('s6 可跳过' if dv.ok else '需要手动触发盘后下载'))
        return ok, why
