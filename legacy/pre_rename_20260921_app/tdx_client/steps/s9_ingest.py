"""
s9 · 解析入库。

顺序固定：

    1. `scripts/ingest/c1_day.py`      vipdoc 个股/指数日线 → md.stock_daily
    2. `scripts/ingest/c3_sector.py`   vipdoc 板块日线      → md.sector_daily
    3. `scripts/migrate/b6_reconcile.py --check-only`        对账

**权威口径是 PG，不是 `D:\\finance\\data`。** 旧库同步（`tdx_data.py`）
只作为可选副本，默认不跑：它的口径已知有偏（`limit_up/down` 一律
`pre_close×1.1/0.9`，不分板；`vol` 存的是「手」；`adj_factor` 恒为 1.0），
拿它当依据会算错涨跌停和换手。要跑就显式 `--legacy`。

**这一步不用 GUI**，纯命令行，所以它是整条链路里最不容易出问题的一环 ——
前面八步的全部意义就是把 vipdoc 里的数据变新，好让这一步有东西可读。
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from app.config import BASE_DIR, LEGACY_DATA
from app.tdx_client.machine import RunContext, Step, StepResult

# 旧库同步脚本。**路径以 01_数据同步 为准** ——
# `tdx_download_data.py` 里把它当成在 `脚本\` 根下，那是脚本重组之后
# 留下的错，见 D:\finance\工作指南\03-故障排查.md。
LEGACY_SYNC = Path(r'D:\finance\脚本\01_数据同步\tdx_data.py')

# 单步超时。全市场日线解析实测几分钟量级。
STEP_TIMEOUT = 3600


def _run(script: Path, args: list[str], *, cwd: Path,
         timeout: int = STEP_TIMEOUT) -> tuple[int, str]:
    """
    跑一个 Python 脚本，返回 `(退出码, 尾部输出)`。

    参数用列表传，**不拼命令行字符串** —— 日期、路径里出现空格或引号时
    字符串拼接就是命令注入。
    """
    cmd = [sys.executable, str(script), *args]
    try:
        r = subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True,
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
        rc, tail = _run(BASE_DIR / 'scripts' / 'ingest' / 'c1_day.py', [],
                        cwd=BASE_DIR)
        ctx.log(f'  c1_day.py → rc={rc}\n{_indent(tail)}')
        results.append(('c1_day', rc))
        if rc != 0:
            return StepResult.fail(f'c1_day.py 失败（rc={rc}）：\n{tail}')

        rc, tail = _run(BASE_DIR / 'scripts' / 'ingest' / 'c3_sector.py', [],
                        cwd=BASE_DIR)
        ctx.log(f'  c3_sector.py → rc={rc}\n{_indent(tail)}')
        results.append(('c3_sector', rc))
        if rc != 0:
            # 板块失败不该让日线白跑 —— 日线已经进去了。
            ctx.log('  **板块日线入库失败**，个股日线已入库，继续对账')

        rc, tail = _run(BASE_DIR / 'scripts' / 'migrate' / 'b6_reconcile.py',
                        ['--check-only'], cwd=BASE_DIR)
        ctx.log(f'  b6_reconcile.py --check-only → rc={rc}\n{_indent(tail)}')
        results.append(('b6_reconcile', rc))

        if ctx.bag.get('legacy'):
            results.append(('legacy_sync', self._legacy(ctx)))

        bad = [k for k, r in results if r != 0]
        msg = '、'.join(f'{k}={r}' for k, r in results)
        if bad:
            return StepResult.done(f'{msg}（其中 {bad} 非零，见上面日志）')
        return StepResult.done(msg)

    def _legacy(self, ctx: RunContext) -> int:
        """
        可选：同步一份到旧库 `D:\\finance\\data`。

        **只作副本，不作依据。** 口径偏差见模块 docstring。
        """
        if not LEGACY_SYNC.is_file():
            ctx.log(f'  跳过旧库同步：找不到 {LEGACY_SYNC}')
            return 0
        rc, tail = _run(LEGACY_SYNC, ['sync', '--all'], cwd=LEGACY_SYNC.parent)
        ctx.log(f'  旧库同步 → rc={rc}（目标 {LEGACY_DATA}）\n{_indent(tail)}')
        return rc

    def verify(self, ctx: RunContext) -> tuple[bool, str]:
        """判据：PG 里有目标交易日、且行数是全市场量级。"""
        def check():
            v = ctx.readiness.db_ready(ctx.target)   # type: ignore[attr-defined]
            return v.ok, v.note

        return self.wait_until(check)


def _indent(text: str) -> str:
    return '\n'.join('      ' + ln for ln in text.splitlines())
