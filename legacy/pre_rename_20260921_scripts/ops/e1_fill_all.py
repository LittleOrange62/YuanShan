# -*- coding: utf-8 -*-
"""
E1 · 一键追平：**以"现在"能拿到的最新已收盘交易日为上限，把这一天以及往前
缺的各层数据全部跑回本地 PG**。

这是 `/jobs`「一键追平到最新收盘日」按钮背后的那个脚本。以前要更新数据得按
顺序手点七八个采集口，中间哪一步该带什么日期、谁依赖谁全靠记 —— 记错的后果
不是报错而是"页面上有数但是旧的"，最难发现。

**目标交易日是按点击时刻算的，不是按库里最新那天算的。**
判定顺序（三步，缺一步就会算错）：

  1. 先同步 `ref.trade_calendar`。日历是从行情反推出来的，它**不知道明天开不
     开市**；不先接上，第 2 步就永远算出"日历里最后那一天"，于是判定永远是
     "已经最新"，整套追平再也不会去拿新数据（2026-08-27 实测踩过：日历停在
     08-25，目标就算成 08-25，而真正该拿的是 08-26）。
  2. 收盘线取 15:30（留清算时间）。现在早于 15:30 就把上限退到昨天 ——
     14:00 点这个按钮，"最新已收盘交易日"是上一个交易日，不是今天。
  3. 目标 = 日历里 `is_open` 且 ≤ 上限的最大那天。

**"往前"补到哪**：`md.stock_daily` 里缺的最早那个交易日；一天都不缺时按
`--back`（默认 5 个交易日）往前开一个窗口 —— 两融是 T+1 发布、龙虎榜和 ETF
份额都会补发，只打目标日那一天会永久漏掉那些迟到的数据。

**这里只编排，不自己抓数**：每一层都还是调原来那个采集脚本，源优先级、
口径、重试全在它们各自里面（日线主源永远是通达信 vipdoc，网络兜底要显式
`--allow-web`）。这个脚本自己不碰任何一张 md.* 表。

**第一步是通达信 GUI 盘后下载**（`scripts/ops/p0_tdx_predownload.py`）——
日线主源是 vipdoc，文件不刷新后面全是空跑。它需要一个能操作鼠标的交互式桌面；
跑在没有桌面的计划任务里会直接判"会话自检不通过"退出，这时它是**非骨干**，
后面的 `daily` 一步照旧会用 vipdoc 硬判据决定是停下（退出码 2）还是继续。
不想让它动通达信就 `--skip tdxdl`。


**输出全程透传，不重定向**：长时任务把输出吞进文件就会被当成卡死。

**失败会自动跳过并在输出里归因**：某一步没成，这一行会打出「失败：原因」，
末尾摘要也会带归因（数据不全 / 网络故障 / 无权限 / 超时），不再只有光秃秃
一个步骤名；非骨干失败往下继续跑，骨干失败按下面退出码收尾。

退出码是有区别的，页面上看到 failed 要按这个读：

  0  全部步骤成功（或按 --skip 跳过）
  1  骨干失败：日线那一步没成，或者根本没拿到目标交易日
  2  vipdoc 还没覆盖目标日、又没开 --allow-web —— 停在这里是**设计如此**，
     不是坏了（见 c10_catchup 的说明）
  3  只有辅助源失败（两融/龙虎榜/资金流这些经常抖），骨干是好的

用法：
    python scripts\\ops\\e1_fill_all.py                  # 一键追平（vipdoc 优先）
    python scripts\\ops\\e1_fill_all.py --dry-run        # 只报目标日和每步要跑什么
    python scripts\\ops\\e1_fill_all.py --back 10        # 往前多补几个交易日
    python scripts\\ops\\e1_fill_all.py --allow-web      # 允许日线落到网络兜底
    python scripts\\ops\\e1_fill_all.py --p0-instant     # 日线+快照入库后就地跑 d1
    python scripts\\ops\\e1_fill_all.py --date 2026-08-28 --skip dividend,share
"""
from __future__ import annotations

import argparse
import datetime as dt
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding='utf-8')

from app.db import engine  # noqa: E402
from app.db.repo import security as sec_repo  # noqa: E402
from app.orchestrator import tradeday  # noqa: E402

# 收盘线、目标日判定、缺口/窗口计算都在 app/orchestrator/tradeday.py ——
# `/jobs` 页面要在按钮旁边先把目标日显示出来，判据抄两份就会出现"页面写 08-28、
# 脚本按 08-27 跑"这种两边都像对的错。
DEFAULT_BACK = 5           # 一天都不缺时，往前开几个交易日的窗口
MAX_SPAN = 30              # 因子/评分一次最多回算多少个交易日（实测 45 天≈18 分钟）

# 失败归因时保留子脚本输出的末尾多少行。真原因几乎总在最后那几行
# （c10 的「vipdoc 还没到 / 查不到」、c5 的「两所均为空」都是收尾打的）。
TAIL_LINES = 60

# 每一步：名字（--skip 用）、说明、脚本、这一步算不算骨干
#   骨干失败 → 退出码 1；辅助失败 → 退出码 3。分开是因为两融/龙虎榜/资金流
#   这些网络源经常抖，把它们和日线同等对待会让"追平失败"变成日常噪音，
#   真正的日线故障就淹没了。
STAGES = (
    # **第 0 步是通达信盘后下载**（P0 前置动作）：日线主源是 vipdoc，文件没刷新
    # 后面全都是空跑。以前这一步藏在 c10_catchup 里（vipdoc 落后时才顺手点一下、
    # 而且只下日线），提到这里是为了让"分钟线也一起下"和进度回显都看得见。
    # 非骨干：真正判"日线够不够"的是下面的 daily 那一步（它有 vipdoc 硬判据和
    # 按设计停下的退出码 2）；这一步失败也许只是分钟线没拿到。
    ('tdxdl',      '通达信盘后下载（日线 + 1/5 分钟线）',       False),
    ('daily',      '日线 / 指数 / 板块（通达信 vipdoc 优先）', True),
    # 收盘快照（市值/PE/PB/内外盘）。**必须排在 daily 之后**：它要拿
    # (Open,Max,Min,Now) 回 md.stock_daily 投票定交易日，日线没入库就一张表都
    # 认不出来。非骨干：数据来自客户端插件，插件没跑就没有新表，不该算追平失败。
    ('snapshot',   '收盘快照 md.stock_snapshot（市值/估值）',   False),

    # 情绪读数**紧跟日线**：它只读 md.stock_daily，不等因子也不等评分。
    # 排在这里而不是队尾，是为了让"评分那一段挂了"不至于连情绪读数一起丢 ——
    # 次日的「明日验证表」缺一天就永久缺一天的核验结论。
    # 非骨干：它算的是分析读数，缺一天不影响日线那条主链（失败走退出码 3，
    # 在摘要里单独列出来，不会被当成"追平失败"）。
    ('breadth',    '全市场情绪读数 fct.market_breadth',        False),
    # 倍量震荡也只读日线。**排在情绪读数之后**只是为了先快后慢：它一天约 10 秒，
    # 是这条链里最慢的一步，放在前面会让人以为整条链卡住了。
    # 非骨干：同样是分析产出，缺一天不影响主链。
    ('voldbl',     '倍量震荡 fct.vol_double',                  False),


    ('etf',        'ETF 份额 → 净申赎',                        False),
    ('margin',     '两融',                                     False),
    ('lhb',        '龙虎榜',                                   False),
    ('fundflow',   '东财资金流（只有最新交易日）',             False),
    ('dividend',   '分红 + 回算复权因子',                      False),
    ('share',      '股本（市值口径）',                         False),
    ('indicators', '基础因子 fct.indicator',                   True),
    ('scores',     '评分 fct.score',                           True),
    # 必须排在 scores 之后：板块趋势是从 fct.score[sector_heat] 聚出来的，
    # 评分没算完先跑这一步只会把昨天的结果再写一遍。
    ('sectortrend', '板块热度趋势 fct.sector_trend',           True),
    # 启动点必须**紧跟在 sectortrend 之后**：它读热度表，热度没写今天就等于白算。
    # 非骨干：它是分析产出，缺一天不影响日线/评分那条主链，把它算成骨干会让
    # "追平失败"变成日常噪音。
    ('ignition',   '趋势启动点 fct.ignition',                  False),
    # 个股趋势分也必须排在 scores 之后（读 fct.score 的三个 cap_key）。非骨干：
    # 漏跑只是让 /scores 那张卡回退成现算（慢 0.7s），不会变成空表 ——
    # 见 app/web/queries.py 的 _trend_is_stored。
    ('scoretrend', '个股趋势分 fct.score_trend',               False),

    # 追平的最后一步是**体检**：数据补完不体检，页面上的「最近一次」就还停在
    # 上一次，看板一片绿而实际没人查过这几天。非骨干 —— 有规则没通过不代表
    # 追平失败（退出码 2/3 在下面单独识别）。
    ('quality',    '质量审计 ops.quality_result',              False),
)



# ------------------------------------------------------------------ 目标日

def sync_calendar() -> tuple[dt.date | None, dt.date | None, str]:
    """
    同步交易日历，返回 `(同步前最后一天, 同步后最后一天, 说明)`。

    走新浪（`ak.tool_trade_date_hist_sina`）：一次拿全年，不依赖 Tushare token。
    **这一步失败不致命**：日历没接上只是让目标日退回按星期几估算，会偏保守，
    比直接停下来有用。
    """
    before = engine.scalar('SELECT max(cal_date) FROM ref.trade_calendar')
    try:
        import akshare as ak
        df = ak.tool_trade_date_hist_sina()
    except Exception as exc:                                     # noqa: BLE001
        return before, before, '同步失败（%s），沿用库里现有日历' % str(exc)[:80]
    dates = []
    for v in df['trade_date'].tolist():
        d = v if isinstance(v, dt.date) else dt.date.fromisoformat(str(v)[:10])
        dates.append(d)
    n = sec_repo.upsert_calendar(dates)
    after = engine.scalar('SELECT max(cal_date) FROM ref.trade_calendar')
    return before, after, '写入/更新 %s 行' % n


def run_script(rel: str, *extra: str) -> tuple[int, str]:
    """
    调一个采集/计算脚本，返回 `(退出码, 输出末尾)`。

    输出照旧**实时透传**（一行一行打到本进程 stdout，不吞进文件 —— 长时任务
    被重定向后看起来就是卡死），同时在内存里留住末尾 `TAIL_LINES` 行，
    **失败归因全靠它**：以前只记退出码，某步 fail 了在摘要里就一个步骤名，
    看的人不知道是数据不全、网络抖还是没权限。现在拿到末尾，归因就有据可依了。

    命令同样打到 stdout：命令行里跑是实时进度条，被 `/jobs` 触发时由
    orchestrator 统一收走当执行摘要。
    """
    cmd = [sys.executable, str(ROOT / rel), *extra]
    print('  $ %s' % ' '.join(cmd[1:]), flush=True)
    t0 = time.time()
    tail: list[str] = []
    # 子进程接管成管道后，Python 默认块缓冲，长任务进度不再实时透传——正好踩中
    # "输出吞掉=看起来卡死"。和 orchestrator.run_job 同一套：接管管道 +
    # PYTHONUNBUFFERED/UTF8，让它逐行吐，既实时又能留着归因用。env 是
    # 当前环境副本，`.env` 里的 DSN 和密钥靠继承才在。
    child_env = {**os.environ, 'PYTHONUNBUFFERED': '1',
                 'PYTHONIOENCODING': 'utf-8', 'PYTHONUTF8': '1'}
    proc = subprocess.Popen(          # noqa: S603
        cmd, cwd=str(ROOT), env=child_env, encoding='utf-8', errors='replace',
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    assert proc.stdout is not None
    for raw in proc.stdout:
        line = (raw.rstrip('\n') if isinstance(raw, str) else
                raw.rstrip(b'\n').decode('utf-8', 'replace'))
        print(line, flush=True)
        if line.strip():
            tail.append(line)
            if len(tail) > TAIL_LINES:
                del tail[:len(tail) - TAIL_LINES]
    rc = proc.wait()
    print('  ← 退出码 %s，用时 %.0fs' % (rc, time.time() - t0), flush=True)
    return rc, '\n'.join(tail)


def classify_failure(name: str, rc: int, tail: str) -> str:
    """
    把一次失败的子脚本归因成一句人话。判据是退出码 + 输出末尾的关键字。

    **从上往下命中即停**，顺序有讲究：`daily` 的 2 是"按设计停下"（vipdoc
    还没到目标日、又没开 --allow-web），它不是故障，必须最先挡住，不能让它
    掉进下面的"数据不全"——那会诱导人去怀疑数据源坏了。
    """
    if name == 'daily' and rc == 2:
        return 'vipdoc 还没覆盖目标日且未开 --allow-web，按设计停步（非故障）'
    text = (tail or '')
    low = text.lower()
    if any(k in low for k in ('timeout', 'timed out', '超时')):
        return '超时'
    if any(k in low for k in ('40203', '401', '403', 'no permission',
                              '没有接口访问权限', '无权限', '积分')):
        return '接口无权限 / 额度不足'
    if any(k in low for k in ('connection', 'remotedisconnected', '连接',
                              'network', '网络', 'econnreset', 'tlserror')):
        return '网络故障（连不上上游）'
    if any(k in low for k in ('为空', '空表', '0 行', '未发布', '非交易日',
                              '没有待补', '查不到')):
        return '数据不全（源未发布 / 空表 / 缺日期）'
    return f'脚本异常（退出码 {rc}）'


def verify(target: dt.date) -> list[str]:
    """
    验收：每张表最新到哪天、目标日有多少行。返回"没追上目标日"的表名。

    只报 max(trade_date) 是不够的 —— 行数是 0 而日期在，页面上照样是空的。
    """
    # 表名是**实测核过的**，不是照作业名猜的：`md.margin_daily` / `md.lhb_daily` /
    # `md.fund_flow` / `fct.indicator` 都不存在（2026-08-29 第一版就写错了这四个，
    # 验收段全打成"查不到"）。真名见 app/db/sql/02_schema.sql。
    checks = (
        ('md.stock_daily',      '个股日线'),
        ('md.index_daily',      '指数日线'),
        ('md.sector_daily',     '板块日线'),
        ('md.etf_share',        'ETF 份额'),
        ('md.margin_detail',    '两融明细'),
        ('md.lhb_detail',       '龙虎榜'),
        ('md.market_fund_flow', '大盘资金流'),
        ('fct.indicator_value', '基础因子'),
        ('fct.score',           '评分'),
    )
    lag = []
    for tbl, cn in checks:
        try:
            mx = engine.scalar(f'SELECT max(trade_date) FROM {tbl}')
            n = engine.scalar(
                f'SELECT count(*) FROM {tbl} WHERE trade_date = %s', (target,))
        except Exception as exc:                                 # noqa: BLE001
            print('  %-16s %-10s 查不到：%s' % (tbl, cn, str(exc)[:60]))
            lag.append(cn)
            continue
        ok = (mx == target) and (n or 0) > 0
        print('  %s %-16s %-10s 最新 %s   目标日 %s 行'
              % ('OK  ' if ok else 'LAG ', tbl, cn, mx, f'{n or 0:,}'))
        if not ok:
            lag.append(cn)
    return lag


def main() -> int:
    ap = argparse.ArgumentParser(description='一键追平到最新已收盘交易日')
    ap.add_argument('--date', help='指定目标交易日 YYYY-MM-DD（默认按点击时刻算）')
    ap.add_argument('--back', type=int, default=DEFAULT_BACK,
                    help='一天都不缺时，辅助源往前回补几个交易日，默认 %d'
                         % DEFAULT_BACK)
    ap.add_argument('--allow-web', action='store_true',
                    help='允许日线落到网络兜底（新浪）。默认不允许，主源是通达信')
    ap.add_argument('--overwrite', action='store_true',
                    help='用 vipdoc 覆盖已有行（把网络兜底写的换回通达信口径）')
    ap.add_argument('--skip', default='',
                    help='跳过哪些步骤，逗号分隔：' + '、'.join(s for s, _, _ in STAGES))
    ap.add_argument('--p0-instant', action='store_true',
                    help='日线+快照入库后就地跑基础因子(d1) P0 字段分值，不等网络源')
    ap.add_argument('--dry-run', action='store_true',
                    help='只报目标日和每步要跑什么，不写库')
    args = ap.parse_args()

    skip = {s.strip() for s in args.skip.split(',') if s.strip()}
    bad = skip - {s for s, _, _ in STAGES}
    if bad:
        print('--skip 里有不认识的步骤名：%s' % '、'.join(sorted(bad)))
        return 1

    now = dt.datetime.now()
    print('=== 0. 目标交易日 ===')
    print('  点击时刻 %s（收盘线 %02d:%02d，早于它就退到上一个交易日）'
          % (now.strftime('%Y-%m-%d %H:%M:%S'), *tradeday.CLOSE_HHMM))

    if args.date:
        target, how = dt.date.fromisoformat(args.date), '命令行指定'
    else:
        before, after, note = sync_calendar()
        print('  交易日历：%s → %s（%s）' % (before, after, note))
        target, how = tradeday.target_date(now)
    if target is None:
        print('\n拿不到目标交易日：ref.trade_calendar 是空的，先跑一次日线同步。')
        return 1
    print('  目标交易日 %s（%s）' % (target, how))

    # 这两个数**不是一回事**，分开打：
    #   have  = md.stock_daily 真实的 max(trade_date)，"库里到底有到哪天"
    #   ready = ref.v_latest_trade_date，日历+收盘线算出来的"应该有到哪天"
    # 2026-08-31 踩过：这里原来只打 sec_repo.latest_trade_date() 却标成
    # "库里最新 md.stock_daily"，于是日线停在 08-28 的时候输出写着 08-31 ——
    # 看输出的人会以为数据已经到位，而页面上是上周五的数。
    have = engine.scalar('SELECT max(trade_date) FROM md.stock_daily')
    ready = sec_repo.latest_trade_date()
    miss = tradeday.missing_days(target)
    print('  库里最新 md.stock_daily = %s（日历判定应有到 %s）' % (have, ready))

    print('  缺的交易日 %d 天：%s'
          % (len(miss), '、'.join(str(d) for d in miss[-10:]) or '无'))

    start = min(miss) if miss else tradeday.back_window(target, args.back)
    span = tradeday.trade_days_between(start, target)
    if span > MAX_SPAN:
        # 因子/评分按 --recent 逐日重算，天数一多就是几十分钟一步。库落后太多时
        # 先把最近这一段追上（再点一次会继续往前），而不是一口气跑到看起来卡死。
        print('  窗口 %d 个交易日超过上限 %d，本次只算最近 %d 天的因子与评分'
              % (span, MAX_SPAN, MAX_SPAN))
        span = MAX_SPAN
    print('  这次的处理窗口 %s .. %s（%d 个交易日）' % (start, target, span))

    ymd = '%Y%m%d'
    plan = {
        # P0 前置：通达信 GUI 盘后下载。分步调用 + 心跳 + 卡死检测 + 失败清进程
        # 重来，全在那个脚本里（scripts/ops/p0_tdx_predownload.py 的模块说明）。
        # 不传 --day-only：分钟线也在这里一次下掉，它是这条链上最慢的一段
        # （实测 7 分 41 秒），没人会单独去点第二遍。
        'tdxdl': ('scripts/ops/p0_tdx_predownload.py',
                  ['--date', target.isoformat()]),
        'daily': ('scripts/ingest/c10_catchup.py',
                  ['--no-calendar']
                  + (['--allow-web'] if args.allow_web else [])
                  + (['--overwrite'] if args.overwrite else [])),
        # 收盘快照：不传日期。表名是采集日期不是交易日，脚本自己按数据回推、
        # 盘中采的表自己跳过 —— 传日期反而会把"哪天有效"交给这里拍脑袋。
        'snapshot': ('scripts/ingest/c15_snapshot.py', []),
        'etf': ('scripts/ingest/c4_etf_share.py',

                ['--start', start.strftime(ymd), '--end', target.strftime(ymd)]),
        'margin': ('scripts/ingest/c5_margin.py',
                   ['--start', start.strftime(ymd), '--end', target.strftime(ymd)]),
        'lhb': ('scripts/ingest/c6_lhb.py',
                ['--start', start.strftime(ymd), '--end', target.strftime(ymd)]),
        'fundflow': ('scripts/ingest/c11_fundflow.py', []),
        'dividend': ('scripts/ingest/c8_dividend.py', []),
        'share': ('scripts/ingest/c9_share.py', []),
        'indicators': ('scripts/compute/d1_indicators.py',
                       ['--date', target.isoformat(), '--recent', str(span)]),
        # 情绪读数：一天约 1s，所以直接跟着窗口一起补，不像因子那样受 MAX_SPAN 折磨。
        # 跑完会自己体检最后一天，量级不对返回 2（在这里等于非骨干失败，摘要里可见）
        'breadth': ('scripts/compute/d6_breadth.py',
                    ['--date', target.isoformat(), '--recent', str(span)]),
        # 倍量震荡：一天约 10 秒。**不跟着 span 一起回填**（--recent 固定 1）——
        # 窗口落后几天时逐日重算的收益很低，而 30 天 × 10 秒 = 5 分钟，
        # 会把"一键追平"拖成让人以为卡死的长任务。要补历史用 d7 自己跑 --recent。
        'voldbl': ('scripts/compute/d7_vol_double.py',
                   ['--date', target.isoformat(), '--recent', '1']),


        'scores': ('scripts/compute/d2_scores.py',
                   ['--date', target.isoformat(), '--recent', str(span)]),
        'sectortrend': ('scripts/compute/d3_sector_trend.py',
                        ['--date', target.isoformat(), '--recent', str(span)]),
        'ignition': ('scripts/compute/d4_ignition.py',
                     ['--date', target.isoformat(), '--recent', str(span)]),
        'scoretrend': ('scripts/compute/d5_score_trend.py',
                       ['--date', target.isoformat(), '--recent', str(span)]),

        # 不传 --date：质量审计自己算"还没审过的交易日"，缺 2 天就跑 2 次
        'quality': ('scripts/ops/e2_quality_audit.py', []),
    }


    if args.dry_run:
        print('\n=== dry-run：要跑的步骤 ===')
        for i, (name, what, core) in enumerate(STAGES, 1):
            if name in skip:
                print('  %d. %-11s 跳过（--skip）' % (i, name))
                continue
            rel, extra = plan[name]
            print('  %d. %-11s %s%s\n       $ %s %s'
                  % (i, name, what, '（骨干）' if core else '',
                     rel, ' '.join(extra)))
            # --p0-instant：标出 d1 会提前到 snapshot 之后就地跑，靠后步骤待吃。
            if name == 'snapshot' and args.p0_instant and 'indicators' not in skip:
                ir, ie = plan['indicators']
                print('       ★ --p0-instant：snapshot 后就地跑 %s %s'
                      % (ir, ' '.join(ie)))
            if name == 'indicators' and args.p0_instant:
                print('       ↳ 已被 --p0-instant 就地跑过，届时跳过重复')
        print('\nDONE（dry-run 未写库）')
        return 0

    done, failed, skipped, stopped = [], [], [], False
    p0_done: set[str] = set()        # --p0-instant 就地已跑过的步骤（靠后同名步骤跳过）
    fail_reason: dict[str, str] = {}     # 失败的步骤 → 归因（数据不全/网络/无权限…）
    by_design = False        # daily 退出码 2：按设计停下，不算"坏了"
    for i, (name, what, core) in enumerate(STAGES, 1):
        if name in skip:
            skipped.append(name)
            print('\n=== %d/%d %s —— 跳过（--skip） ===' % (i, len(STAGES), what))
            continue
        # --p0-instant 在 snapshot 跑成之后就把 d1 就地算过一遍了（见下面），
        # 靠后的 indicators 步骤在这里吃掉，不再重复跑。
        if name in p0_done:
            done.append(name)
            print('\n=== %d/%d %s —— 已由 --p0-instant 就地跑过，跳过重复 ==='
                  % (i, len(STAGES), what))
            continue
        if stopped and name in ('indicators', 'scores', 'sectortrend'):
            skipped.append(name)
            print('\n=== %d/%d %s —— 跳过：日线没追上目标日，算了也是旧的 ==='
                  % (i, len(STAGES), what))
            continue
        print('\n=== %d/%d %s ===' % (i, len(STAGES), what), flush=True)
        rel, extra = plan[name]
        rc, tail = run_script(rel, *extra)
        if rc == 0:
            done.append(name)
            # --p0-instant：日线+快照入库后就地跑基础因子(d1) P0 字段分值。
            # d1 只读 md.stock_daily（P0 数据），快照一落就能算，不必等两融/龙虎榜
            # 这些网络源 —— 评分在 P0 数据阶段就有字段分值可用。
            # 失败不回滚：靠后的 indicators 步骤照常再跑一遍就好。
            if (name == 'snapshot' and args.p0_instant
                    and 'indicators' not in skip and not stopped):
                print('\n    --p0-instant：就地跑基础因子 P0 字段分值 ====', flush=True)
                ind_rel, ind_extra = plan['indicators']
                rc2, _tail2 = run_script(ind_rel, *ind_extra)
                if rc2 == 0:
                    p0_done.add('indicators')
                    print('    → 就地跑成，靠后 indicators 步骤将跳过')
                else:
                    print('    → 就地跑 d1 失败（退出码 %s）：靠后 indicators 步骤照跑'
                          % rc2)
            continue
        # e2_quality_audit 的 2/3 是"审完了，有规则没通过"，不是"这一步坏了"。
        # 混在一起会让整次追平显示成 PARTIAL，而真正该看的是质量看板。
        if name == 'quality' and rc in (2, 3):
            done.append(name)
            print('  → 审计跑完了，但有规则未通过（退出码 %d）：去 /data → ② 数据质量看'
                  % rc)
            continue
        # p0 前置下载的 3 是"日线到位、只有分钟线没拿到"。日线才是这条链的骨干，
        # 分钟线缺了不该把整次追平标成 PARTIAL —— 但要在输出里说清楚。
        if name == 'tdxdl' and rc == 3:
            done.append(name)
            print('  → 日线已到位，分钟线没拿到（退出码 3）。单独重跑：'
                  'python -m app.tdx_client.run_agent --only s5_dialog '
                  '--only s7_minute')
            continue
        # 剩下的真失败：归因成一句人话，让"这步没成"不再是光秃秃一个名字。
        reason = classify_failure(name, rc, tail)
        fail_reason[name] = reason
        failed.append(name)
        if core:
            stopped = True
        # c10 用退出码 2 表示"vipdoc 还没到目标日，按设计不走网络兜底"
        if name == 'daily' and rc == 2:
            by_design = True
            print('  → vipdoc 还没覆盖 %s，且没有 --allow-web：**按设计停在这一步**。'
                  % target)
            print('    先把通达信盘后数据下下来，或者显式加 --allow-web 走兜底。')
        else:
            print('  → 失败：%s' % reason)

    print('\n=== 验收（对照目标日 %s） ===' % target)
    lag = verify(target)

    is_core = {name: core for name, _, core in STAGES}
    core_failed = [n for n in failed if is_core[n]]
    # 失败原因跟着步骤走进摘要。只有名字时，"追平失败"跟"数据缺"分不清，
    # 下面的括号里直接写明每步为什么没成。
    part = ', '.join('%s（%s）' % (n, fail_reason[n]) for n in failed)
    line = ('追平 %s：%d 步成功' % (target, len(done))
            + ('，%d 步失败：%s' % (len(failed), part) if failed else '')
            + ('，%d 步跳过' % len(skipped) if skipped else '')
            + (('；没追上目标日的：' + '、'.join(lag)) if lag else '；各表都到目标日'))
    print('\n%s %s' % ('DONE' if not failed else 'PARTIAL', line), flush=True)

    if not failed:
        return 0
    if by_design:
        return 2
    return 1 if core_failed else 3


if __name__ == '__main__':
    raise SystemExit(main())
