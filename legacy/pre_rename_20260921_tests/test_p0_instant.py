# -*- coding: utf-8 -*-
"""`--p0-instant`：日线+快照入库后就地跑 d1，靠后 indicators 步骤跳过。

用 dry-run（--dry-run 不写库）验证编排的输出，不真跑子脚本：
真跑要动通达信/网络，测试环境里又慢又抖，dry-run 已经把要跑的顺序、
`p0-instant` 的提前插入和靠后步骤的吃跳过都在输出里写出来了。
"""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _dry_run(*extra) -> str:
    out = subprocess.run(
        [sys.executable, str(ROOT / 'scripts/ops/e1_fill_all.py'),
         '--date', '2026-09-16', '--dry-run', *extra],
        capture_output=True, text=True, encoding='utf-8', errors='replace')
    return out.stdout, out.returncode


def test_p0_instant_marks_early_d1_and_skips_later():
    stdout, rc = _dry_run('--p0-instant')
    assert rc == 0
    # snapshot 后面就该标出"就地跑 d1"
    assert '★ --p0-instant：snapshot 后就地跑' in stdout
    assert 'd1_indicators.py' in stdout
    # 靠后的 indicators 步骤要标明"届时跳过重复"
    assert '已被 --p0-instant 就地跑过，届时跳过重复' in stdout


def test_no_p0_instant_by_default():
    stdout, rc = _dry_run()
    assert rc == 0
    assert '★ --p0-instant' not in stdout