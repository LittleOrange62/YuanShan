# -*- coding: utf-8 -*-
"""
质量审计 —— 把 `config.quality_rule` 的规则按交易日跑一遍，写 `ops.quality_result`。

**默认行为就是"追平"**：不给参数时自己算"当前北京时间往前推的最近一个已收盘
交易日"，再看 `ops.quality_result` 已经审计到哪天，缺几天就**一天一天各跑一次**
（缺 08-27 / 08-28 就跑两次，各写自己那一天的结果）。这样页面上的「最近一次」
永远等于"最新已收盘交易日体检过了"，而不是某次迁移的时间戳。

判定链与「一键追平」共用 `app/orchestrator/tradeday.py`，不抄第二份：
收盘线 15:30 → `ref.trade_calendar` 里 `is_open` 且 ≤ 上限的最大那天。

退出码（页面上看到 failed 按这个读）：

  0  跑完了，全部规则通过（或没有缺口可补）
  1  参数/环境问题：目标交易日算不出来、指定的日期不是交易日
  2  有 **blocking** 规则未通过 —— 下游加工不该在这天的数据上继续
  3  只有非阻断规则未通过（warning 之类），骨干是好的

用法：
    python scripts\\ops\\e2_quality_audit.py                  # 缺几天补几天
    python scripts\\ops\\e2_quality_audit.py --dry-run        # 只报缺口与判据，不写库
    python scripts\\ops\\e2_quality_audit.py --date 2026-08-28
    python scripts\\ops\\e2_quality_audit.py --recent 5       # 最近 5 个交易日重审
    python scripts\\ops\\e2_quality_audit.py --back 30        # 把回补上限放宽到 30 天
    python scripts\\ops\\e2_quality_audit.py --rule daily_row_count --date 2026-08-28
"""
from __future__ import annotations

import argparse
import datetime as dt
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.db import engine  # noqa: E402
from app.orchestrator import tradeday  # noqa: E402
from app.quality import audit  # noqa: E402


def say(msg: str = '') -> None:
    """输出实时透传，不重定向 —— 长时任务把输出吞掉就会被当成卡死。"""
    print(msg, flush=True)


def recent_days(n: int, target: dt.date) -> list[dt.date]:
    rows = engine.query(
        'SELECT cal_date FROM ref.trade_calendar '
        ' WHERE is_open AND cal_date <= %s ORDER BY cal_date DESC LIMIT %s',
        (target, max(int(n), 1)))
    return sorted(r['cal_date'] for r in rows)


def report(rep: dict) -> None:
    say('  规则 %d 条：通过 %d、未通过 %d（其中阻断 %d、规则本身出错 %d）'
        % (rep['total'], rep['total'] - rep['failed'], rep['failed'],
           rep['blocking'], rep['errors']))


def main() -> int:
    ap = argparse.ArgumentParser(description='按交易日跑质量审计，缺几天补几天')
    ap.add_argument('--date', help='只审这一天 YYYY-MM-DD')
    ap.add_argument('--recent', type=int,
                    help='重审最近 N 个交易日（无论审过没有）')
    ap.add_argument('--back', type=int, default=audit.DEFAULT_BACK,
                    help='自动补齐时最多往前追几个交易日，默认 %d'
                         % audit.DEFAULT_BACK)
    ap.add_argument('--rule', action='append', metavar='RULE_KEY',
                    help='只跑这些规则（可重复），默认跑全部启用的')
    ap.add_argument('--dry-run', action='store_true', help='只报，不写库')
    a = ap.parse_args()

    now = tradeday.now_bj()
    target, why = tradeday.target_date(now)
    say('=== 0. 时间与目标 ===')
    say('  北京时间 %s（收盘线 %02d:%02d，早于它就退到上一个交易日）'
        % (now.strftime('%Y-%m-%d %H:%M:%S'), *tradeday.CLOSE_HHMM))
    say('  最近一个已收盘交易日：%s ← %s' % (target, why))
    say('  已审计到：%s' % (audit.audited_max() or '从来没审计过'))
    if target is None:
        say('  目标交易日算不出来（日历为空？先跑一键追平同步日历）')
        return 1

    if a.date:
        days = [dt.date.fromisoformat(a.date)]
        if not engine.scalar('SELECT is_open FROM ref.trade_calendar '
                             ' WHERE cal_date = %s', (days[0],)):
            say('  %s 不是交易日（或日历里没有这一天）' % days[0])
            return 1
    elif a.recent:
        days = recent_days(a.recent, target)
    else:
        g = audit.gap(now, back=a.back)
        say('=== 1. 缺口 ===')
        if g['no_data']:
            # "还没采到数据"和"质量不合格"是两件事，混在一起会让人去修一个
            # 不存在的质量问题。这里单列出来，并且不去审它们。
            say('  跳过 %d 天（这些天还没有日线，先去 /jobs 追平）：%s'
                % (len(g['no_data']), '、'.join(str(d) for d in g['no_data'])))
        if not g['runnable']:
            say('  没有需要补的交易日 —— 已经审计到最新已收盘交易日。')
            return 0
        say('  缺 %d 天，逐天各跑一次：%s'
            % (len(g['runnable']), '、'.join(str(d) for d in g['runnable'])))
        days = g['runnable']

    say('=== 2. 执行 ===')
    total_failed = total_blocking = 0
    for d in days:
        if not audit.has_data(d):
            say('  === %s 跳过：这一天还没有日线 ===' % d)
            continue
        say('  === 审计 %s%s ===' % (d, '（dry-run，不写库）' if a.dry_run else ''))
        rep = audit.audit_day(d, only=a.rule, save=not a.dry_run, echo=say)
        report(rep)
        total_failed += rep['failed']
        total_blocking += rep['blocking']

    say('=== 3. 结论 ===')
    say('  审计 %d 天，未通过合计 %d 条（阻断 %d 条）'
        % (len(days), total_failed, total_blocking))
    say('  最近一次审计时间已更新到 %s'
        % (engine.scalar('SELECT max(run_at) FROM ops.quality_result') or '—'))
    say('DONE')
    if total_blocking:
        return 2
    return 3 if total_failed else 0


if __name__ == '__main__':
    raise SystemExit(main())
