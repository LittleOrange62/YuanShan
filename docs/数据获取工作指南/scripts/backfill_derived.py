# -*- coding: utf-8 -*-
"""缺日派生列回算 + 计算链重跑。c1 入库后没跑 c10.finish() 时用它补。

用法（在项目根下，或从任何目录给绝对路径都行 —— 本文件自己算 ROOT）：

    python docs\\数据获取工作指南\\scripts\\backfill_derived.py 2026-09-18 2026-09-21
    python docs\\数据获取工作指南\\scripts\\backfill_derived.py 2026-09-23 --chain
    python docs\\数据获取工作指南\\scripts\\backfill_derived.py 2026-09-23 --chain --dry-run

`--chain` 依次跑 d1→d2→d6→d3→d4→d5→d7，全部走 `python -m ingest.jobs.<名>`。
**顺序不是随便排的**：d2 评分读 d1 的指标，d4 点火读 d3 的板块热度，
d5 个股趋势读 d2 的评分 —— 跳着跑不会报错，只会算出旧的数。

`--dry-run` 只做两件事：把要跑的命令打出来，并让 d 系列自己也 dry-run。
派生列回算那三步**没有 dry-run 语义**（它们就是 UPDATE），所以 dry-run 时整段跳过 ——
跳过必须写在输出里，不能悄悄少跑一步还报成功。

⚠️ **回算涨跌停用的是"今天的 ST 名单"**。`quote.recompute_limits` 里档位取自
`reference_data.board_limit_ratio(board, is_st)`，而 `is_st` 是**当前快照**、没有生效区间
（可证的历史区间在 `reference_data.security_st_period`，但那条还没接进来）。
所以对 2026-08-27 之前的日期跑这个脚本，会拿今天的 ST 状态改写那一天的涨跌停价 ——
`limit_up/limit_down`、涨停家数、评分里的涨跌停相关分量会一起错，而且**不会报错**。
默认直接拒绝；确知自己在做什么，再显式加 `--allow-st-blind`。
"""
import datetime as dt
import subprocess
import sys
from pathlib import Path

def _root() -> Path:
    """向上找项目根，**不数层级**。

    原来写死 `parents[3]`，数值上恰好对（本文件上溯三级就是项目根），但它把
    "文件放在哪儿"写进了运行时：挪一层、或指南目录改名，就会静默指到上一层，
    sys.path 指错了而脚本自己一声不吭。

    判据用根上独有的 `product.json` + `app/`。**别用 `manage.py`** —— 它在
    `scripts/` 下面不在根上，第一版我就栽在这个想当然上，当场找不到根。
    """
    here = Path(__file__).resolve().parent
    for cand in [here, *here.parents]:
        if (cand / 'product.json').is_file() and (cand / 'app').is_dir():
            return cand
    raise SystemExit('找不到项目根（判据：同级有 product.json 与 app/），'
                     '请把本文件放在项目内的任意目录下')


ROOT = _root()

sys.path.insert(0, str(ROOT))
from app.db.repo import quote  # noqa: E402

# 涨跌停档位的可证起点：早于这一天，`is_st` 现值不等于当天的状态
ST_HORIZON = dt.date(2026, 8, 27)
# 顺序 = 依赖顺序，见模块 docstring
CHAIN = ('d1_indicators', 'd2_scores', 'd6_breadth', 'd3_sector_trend',
         'd4_ignition', 'd5_score_trend', 'd7_vol_double')


def one(d: dt.date) -> None:
    pre = quote.recompute_pre_close(d, role='admin')
    lim = quote.recompute_limits(d, role='admin', only_missing=True)
    turn = quote.recompute_turnover(d, only_missing=True, role='admin')
    print('%s  pre_close %s / 涨跌停 %s / 换手 %s' % (d, pre, lim, turn))


def main() -> int:
    flags = {a for a in sys.argv[1:] if a.startswith('--')}
    args = [a for a in sys.argv[1:] if not a.startswith('--')]
    chain = '--chain' in flags
    dry = '--dry-run' in flags
    dates = [dt.date.fromisoformat(a) for a in args]
    if not dates:
        print(__doc__)
        return 1

    rc_any = 0
    if dry:
        print('--dry-run：跳过派生列回算（那三步是 UPDATE，没有"只看不写"的语义）')
    else:
        blind = [d for d in dates if d < ST_HORIZON]
        if blind and '--allow-st-blind' not in flags:
            print('拒绝执行：%s 早于 %s，而涨跌停档位取的是**今天的** `security.is_st`'
                  '（无生效区间）。\n'
                  '跑下去会用今天的 ST 名单改写这些日期的 limit_up/limit_down，'
                  '涨停家数与评分一起错，且不报错。\n'
                  '确知要这么干就加 --allow-st-blind（并知道自己在覆盖什么）。'
                  % ('、'.join(str(d) for d in sorted(blind)), ST_HORIZON))
            return 2
        for d in dates:
            one(d)

    if chain:
        recent = str(len(dates) + 1)
        for s in CHAIN:
            cmd = [sys.executable, '-m', 'ingest.jobs.' + s, '--recent', recent]
            if dry:
                cmd.append('--dry-run')
                print('$', ' '.join(cmd[1:]))
                continue
            r = subprocess.run(cmd, cwd=str(ROOT))
            print(s, 'rc=', r.returncode)
            if r.returncode != 0:
                # 以前这里只打印 rc、最后统一 return 0 —— 七步里前五步全失败，
                # 调用方（计划任务/页面）看到的仍然是"成功"。
                print('  !! %s 失败，后面的步骤照跑（依赖它的会算出旧数），但整体退出码非零' % s)
                rc_any = 1
    return rc_any


if __name__ == '__main__':
    sys.exit(main())
