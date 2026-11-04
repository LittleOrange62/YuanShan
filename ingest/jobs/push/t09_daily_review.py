# -*- coding: utf-8 -*-
"""D7 · 每日复盘（18:18 盘后那一栏）—— **骨架**：内容你填，入库/发送/留痕/补发这一层已经接好。

这一路按用户 11-03 定的分层写：「判断与取数属于数据加工环节，是取数落地的；跟推送到飞书的那些行为分开」
所以本文件里只有 **一个函数是要你写的**（`collect()`），其余都是已经跑通的那套壳：

    collect(now)      ← 你要改的：算出今天要说的事（只读库里已落地的东西）
    render(data, now) ← 排版（措辞在 `app/intraday/templates.json` 的 `daily_review_*`，别在代码里拼句子）
    正文先落 `analytics.intraday_broadcast`（`task='daily_review'`）→ 再发渠道
    --resend          ← 读今天那条留痕的正文再发一次，**不重新取数**（重跑内容一字不差）

宁缺毋假：`collect()` 还没写、或返回空 ⇒ **一条都不发**，只记一行 `daily_review_empty`；
这条默认就是关着的（系统配置里那一行的开关打开才注册任务），免得每天往群里发一句空话。

    python -m ingest.jobs.push.t09_daily_review --dry-run          # 现在跑一轮：只算只排版，不写账不发送
    python -m ingest.jobs.push.t09_daily_review                    # 排版 + 落留痕（不发）
    python -m ingest.jobs.push.t09_daily_review --send             # 真发（渠道读系统配置那一格）
    python -m ingest.jobs.push.t09_daily_review --send --resend    # 补发今天已落库的那一条正文
    python -m ingest.jobs.push.t09_daily_review --force            # 跳过交易日判断（周末手工试）

调度：Windows 任务计划程序每天 18:18 起这一炉（时点在系统配置「盘中播报」那一屏改，
或 `python -m app.intraday.tasks --set daily_review_times=18:18 --apply`）。
18:18 在盘后，所以这里**不判交易时段**，只判交易日（非交易日静默退，不发"今天没复盘"）。
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]      # push/ → jobs/ → ingest/ → 仓库根（挪目录时这一格最容易数错）
sys.path.insert(0, str(ROOT))
try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')      # type: ignore[attr-defined]
except Exception:  # noqa: BLE001
    pass

from app.db import engine                                           # noqa: E402
from app.intraday import store, tasks                               # noqa: E402
from ingest.jobs.push import t01_zt_watch as zt_watch                                    # noqa: E402  交易日判据共用一份

TPL_FILE = ROOT / 'app' / 'intraday' / 'templates.json'
TASK = 'daily_review'
DRY_RUN = False


def _log(now, what, **kw):
    if DRY_RUN:
        return 0
    try:
        store.write_broadcast(now, task=TASK, what=what, **kw)
    except Exception:  # noqa: BLE001  留痕写不进去不该把今天这一栏带走
        pass


def tpl() -> dict:
    """读文案模板。读不出来就**抛** —— 用一套编出来的措辞去播，等于把口径改掉。"""
    return json.loads(TPL_FILE.read_text(encoding='utf-8'))


# ───────────────────────────────────────────────────────────────── 你要改的就这一个函数

def collect(now: dt.datetime) -> dict:
    """算出"今天这一栏要说什么"，返回 `{'sections': ['第一段', '第二段', …]}`（可以再加 `title`）。

    写的时候请守住这四条（这一层已经替你守好的部分，别绕过去）：
    · **只读库里已落地的东西**（`analytics.*`／`market_data.*`／`reference_data.*`，用 `app.db.engine`
      的 `query/query_one/scalar(..., role='reader')`）。要新取数就另写一炉入库，别在这里边取边发 ——
      那会让"补发一次"发出与 18:18 那条不同的内容。
    · 缺的那格说"缺数"，**不写 0、不拿昨天顶**（`0` 与"没有"在这套系统里是两句话）。
    · 段落里别用 markdown 星号，屏上与群里都是纯文本。
    · 不打分、不预测、不给买卖建议（项目红线，免责声明也按这句走）。
    """
    return {}                     # ← 还没写：返回空 ⇒ 今天不发，只记 `daily_review_empty`


def render(data: dict, now: dt.datetime) -> str | None:
    """排版。`collect()` 没给内容就返回 None（调用方据此**不发**，不许发一条空复盘）。"""
    sections = [str(s).strip() for s in (data or {}).get('sections') or [] if str(s).strip()]
    if not sections:
        return None
    t = tpl()
    stamp = f'{now:%H:%M:%S}'
    lines = [t['daily_review_header'].format(t=stamp, title=(data.get('title') or '每日复盘'))]
    lines.extend(sections)
    lines.append(t['daily_review_footer'].format(t=stamp))
    return '\n'.join(lines)


# ───────────────────────────────────────────────────────────────── 以下是不用你动的壳

def today_body(day: dt.date) -> str | None:
    """今天已经落库的那一条正文（`--resend` 用它：只读库，不重新取数）。"""
    row = engine.query_one(
        "SELECT body FROM analytics.intraday_broadcast"
        " WHERE trade_date = %s AND task = %s AND what = 'daily_review' AND body IS NOT NULL"
        ' ORDER BY at_ts DESC LIMIT 1', (day, TASK), role='reader')
    return (row or {}).get('body')


def one_round(*, dry_run: bool, send: bool, force: bool, channel: str | None,
              resend: bool) -> int:
    global DRY_RUN
    DRY_RUN = dry_run
    now = dt.datetime.now()
    day = now.date()
    if not force and not resend:
        if not zt_watch.is_trading_day(now):
            print(f'[{now:%H:%M:%S}] 非交易日 —— 不发每日复盘')
            return 0
    if resend:
        body = today_body(day)
        if not body:
            print(f'[{now:%H:%M:%S}] 今天库里没有已落的每日复盘正文 —— 没有东西可补发（不重算冒充）')
            _log(now, 'daily_review_no_body', ok=False, err='analytics.intraday_broadcast 里没有今天的 daily_review 行')
            return 1
        print(f'[{now:%H:%M:%S}] 补发今天已落库的那一条（不重新取数）\n{body}')
        if not dry_run:
            _send(now, body, tasks.send_channels(channel), counts={'resend': True})
        return 0
    data = collect(now)
    text = render(data, now)
    if text is None:
        print(f'[{now:%H:%M:%S}] `collect()` 没给出内容 —— 一条都不发'
              f'（现在这是预期的：这一栏的正文还没写上）')
        _log(now, 'daily_review_empty', ok=False, err='collect() 返回空内容，未发送')
        return 1
    print(text)
    counts = {'sections': len([s for s in (data.get('sections') or []) if str(s).strip()])}
    if dry_run:
        print('（dry-run：未写账、未发送）')
        return 0
    _log(now, 'daily_review', body=text, counts=counts)
    if send:
        _send(now, text, tasks.send_channels(channel), counts=counts)
    return 0


def _send(now, text, keys, *, counts):
    from duanxian import notify
    res = notify.send_to(keys, '每日复盘', text)
    ok = bool(res.get('ok'))
    store.write_broadcast(now, task=TASK, what='sent', body=text, ok=ok, delivered=ok,
                          channel_key=','.join(keys), counts=counts,
                          err=None if ok else str(res.get('detail'))[:300])
    verdict = '已确认' if ok else f'未确认：{str(res.get("detail"))[:160]}（判据是渠道业务码，不是 HTTP 200）'
    print(f'  发送 → {",".join(keys)}：{verdict}')


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description='D7 · 每日复盘（18:18，正文由 collect() 提供）')
    ap.add_argument('--dry-run', action='store_true', help='只算只排版，不写账不发送')
    ap.add_argument('--send', action='store_true', help='发渠道（不给就只落留痕）')
    ap.add_argument('--channel', default=None,
                    help='逗号分隔的渠道串；不给就读系统配置「盘中播报」那一格')
    ap.add_argument('--force', action='store_true', help='跳过交易日判断（手工试）')
    ap.add_argument('--resend', action='store_true', help='补发今天已落库的那一条（不重新取数）')
    a = ap.parse_args(argv)
    return one_round(dry_run=a.dry_run, send=a.send, force=a.force,
                     channel=a.channel, resend=a.resend)


if __name__ == '__main__':
    raise SystemExit(main())
