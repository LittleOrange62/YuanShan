"""
s3 · 清弹窗。

登录之后必然弹 `通达信信息`（公告），有时还叠着升级提示和资讯窗。
用户描述的"关闭弹窗（看下有没有'通达信信息'窗口有的话就关闭）"就是这一步。

**关键点是别关错。** 具体规则和"哪些不能关"写在 `watchdog.py` 里，
这一步只负责跑几轮清理并把结果报出来。

看门狗线程在 `run_agent.py` 里全程开着，这一步存在的意义是
**在进入下载环节之前做一次确定性的清理** —— 后台线程是"迟早会清掉"，
而 s5 需要"现在就没有弹窗挡着"。
"""

from __future__ import annotations

from app.tdx_client import watchdog
from app.tdx_client.machine import RunContext, Step, StepResult

ROUNDS = 3


class PopupStep(Step):
    key = 's3_popup'
    title = '清理登录后弹窗'
    retry = 1
    verify_timeout = 10
    verify_interval = 1.0
    # 弹窗没清干净不该拦住整条链路：真挡住了后面的步骤自己会失败并留现场。
    fatal = False

    def act(self, ctx: RunContext) -> StepResult:
        pid = ctx.bag.get('pid')
        events, alerts = watchdog.sweep_once(pid=pid, rounds=ROUNDS)
        for e in events:
            ctx.log(f'  {e}')
        if alerts:
            lines = '\n'.join(f'  {a}' for a in alerts)
            return StepResult.ask_human(
                f'有需要人工确认的弹窗（关掉也解决不了问题）：\n{lines}')
        closed = sum(1 for e in events if e.action == 'close')
        unknown = [e for e in events if e.action == 'unknown']
        msg = f'清了 {ROUNDS} 轮，关掉 {closed} 个'
        if unknown:
            msg += (f'；另有 {len(unknown)} 个没有匹配规则的窗口**已放过不关**'
                    '（需要处理请往 config.popup_rule 加规则）')
        return StepResult.done(msg, closed=closed, unknown=len(unknown))

    def verify(self, ctx: RunContext) -> tuple[bool, str]:
        """
        判据：**主窗口还在，且没有剩下 close 类规则能匹配上的窗口**。

        "主窗口还在"这条不是多余的 —— 早期脚本盲关一切，
        把主窗口自己关掉过。verify 里加这一条，那种错误当场就会暴露。
        """
        pid = ctx.bag.get('pid')
        if ctx.refresh_main() is None:
            return False, '**主窗口不见了** —— 清弹窗时误关了主窗口'
        rules, _src = watchdog.load_rules()
        wd = watchdog.PopupWatchdog(pid=pid, rules=rules)
        leftover = [e for e in wd.sweep() if e.action in ('close', 'alert')]
        if leftover:
            return False, f'还剩 {len(leftover)} 个待处理弹窗：' + \
                '、'.join(repr(e.title) for e in leftover)
        return True, '当前没有待处理的弹窗'
