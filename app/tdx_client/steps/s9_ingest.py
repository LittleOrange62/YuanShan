"""
s9 · 解析入库。

顺序固定，全部用 `python -m`（入口都在 `ingest/jobs/`）：

    1. `-m ingest.jobs.c1_day`                 vipdoc 个股/指数日线 → market_data.stock_daily
    2. `-m ingest.jobs.c3_sector`              vipdoc 板块日线      → market_data.sector_daily
    3. `-m ingest.jobs.b6_reconcile --check-only`                   对账

**PG 是唯一存储。** 旧 SQLite 仓（`D:\\finance\\data`）本机已彻底不存在，
那条"顺手同步一份到旧库"的 `--legacy` 路径已在 2026-09-25 删除：它会在项目外
`os.makedirs` 长出一个第二数据仓，与"PG 唯一存储"直接冲突；而且它的口径已知有偏
（`limit_up/down` 一律 `pre_close×1.1/0.9` 不分板、`vol` 存「手」、
`adjustment_factor` 恒 1.0），拿它当依据会算错涨跌停和换手。

**这一步不用 GUI**，纯命令行，所以它是整条链路里最不容易出问题的一环 ——
前面八步的全部意义就是把 vipdoc 里的数据变新，好让这一步有东西可读。
"""

from __future__ import annotations

import subprocess
import sys

from app.config import BASE_DIR
from app.tdx_client.machine import RunContext, Step, StepResult

# 单步超时。全市场日线解析实测几分钟量级。
STEP_TIMEOUT = 3600


def _run(module: str, args: list[str], *,
         timeout: int = STEP_TIMEOUT) -> tuple[int, str]:
    """
    以 `python -m <module>` 跑本项目里的一个作业入口，返回 `(退出码, 尾部输出)`。

    **为什么是 `-m` 而不是脚本路径**：这些入口在 `ingest/jobs/` 下、按包内相对导入
    写（`from app.config import ...`），直接给文件路径会 ModuleNotFoundError；
    而以前这里写的是 `scripts/ingest/c1_day.py` —— **本项目根本没有那个目录**，
    于是这一步从来没真正跑过，只是把 rc=-1 记进日志。

    参数用列表传，**不拼命令行字符串** —— 日期、路径里出现空格或引号时
    字符串拼接就是命令注入。
    """
    cmd = [sys.executable, '-m', module, *args]
    try:
        r = subprocess.run(cmd, cwd=str(BASE_DIR), capture_output=True, text=True,
                           encoding='utf-8', errors='replace', timeout=timeout)
    except subprocess.TimeoutExpired:
        return -1, f'超时（{timeout}s）'
    except OSError as e:
        return -1, str(e)
    out = (r.stdout or '') + (r.stderr or '')
    tail = '\n'.join(out.strip().splitlines()[-12:])
    return r.returncode, tail


class IngestStep(Step):
    key = 's9_ingest'
    title = '解析入库（PG）'
    retry = 1
    verify_timeout = 30
    verify_interval = 2.0

    def precheck(self, ctx: RunContext) -> tuple[bool, str]:
        v = ctx.readiness.db_ready(ctx.target)      # type: ignore[attr-defined]
        if v.ok:
            return True, f'PG 里已有 {ctx.target}（{v.note}）'
        return False, ''

    def act(self, ctx: RunContext) -> StepResult:
        # 入库前先确认 vipdoc 真的是新的 —— 否则这一步会"成功地"把旧数据
        # 又写一遍，然后 verify 通过（因为 upsert 幂等），整条链路显示成功
        # 而 PG 里其实没有目标交易日。
        dv = ctx.readiness.day_ready(ctx.target)    # type: ignore[attr-defined]
        if not dv.ok:
            return StepResult.fail(
                f'vipdoc 里的个股日线还没到 {ctx.target}（{dv.note}），'
                '入库没有意义 —— 先把 s6_day 跑通')

        results = []
        rc, tail = _run('ingest.jobs.c1_day', [])
        ctx.log(f'  c1_day → rc={rc}\n{_indent(tail)}')
        results.append(('c1_day', rc))
        if rc != 0:
            return StepResult.fail(f'c1_day 失败（rc={rc}）：\n{tail}')

        rc, tail = _run('ingest.jobs.c3_sector', [])
        ctx.log(f'  c3_sector → rc={rc}\n{_indent(tail)}')
        results.append(('c3_sector', rc))
        if rc != 0:
            # 板块失败不该让日线白跑 —— 日线已经进去了。
            ctx.log('  **板块日线入库失败**，个股日线已入库，继续对账')

        # 分钟线入库。此前这一步只跑 c1+c3+b6，**s6/s7 下回来的 .lc1/.lc5 从来没人
        # 摄取**（实测那段下载要 7 分 41 秒，没人会单独再点一遍），所以
        # market_data.stock_minute 一直停在旧数据上。c16 自身幂等（默认只补空洞、
        # 不覆盖），所以每天跟着骨干一起跑是安全的。
        rc, tail = _run('ingest.jobs.c16_minute', ['--date', ctx.target.isoformat()])
        ctx.log(f'  c16_minute → rc={rc}\n{_indent(tail)}')
        results.append(('c16_minute', rc))
        if rc != 0:
            # **非骨干**：报出来，但不把当日判死 —— 缺分钟线只让"首封时间/分钟级
            # 复算"这类读数用不了，日线口径不受影响（判据同 p0 对退出码 3 的处理）。
            ctx.log('  **分钟线入库未完成**（非骨干，不判当日失败；'
                    '受影响的只有 market_data.stock_minute 及其衍生读数）')

        rc, tail = _run('ingest.jobs.b6_reconcile', ['--check-only'])
        ctx.log(f'  b6_reconcile --check-only → rc={rc}\n{_indent(tail)}')
        results.append(('b6_reconcile', rc))

        # c16_minute 不进 bad：它失败只记在摘要里，不把这一步整体判负
        bad = [k for k, r in results if r != 0 and k != 'c16_minute']
        msg = '、'.join(f'{k}={r}' for k, r in results)
        if bad:
            return StepResult.done(f'{msg}（其中 {bad} 非零，见上面日志）')
        return StepResult.done(msg)

    def verify(self, ctx: RunContext) -> tuple[bool, str]:
        """判据：PG 里有目标交易日、且行数是全市场量级。"""
        def check():
            v = ctx.readiness.db_ready(ctx.target)   # type: ignore[attr-defined]
            return v.ok, v.note

        return self.wait_until(check, ctx=ctx)


def _indent(text: str) -> str:
    return '\n'.join('      ' + ln for ln in text.splitlines())
