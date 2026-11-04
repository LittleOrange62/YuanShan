# -*- coding: utf-8 -*-
"""M1 · 竞价榜微信镜像 —— 外部 `send/skills/wxpush/scripts/mirror_auction.py` 收编版（11-03）。

它原本干什么：09:26 起轮询观澜那份 `var/zt_events.jsonl`，等新出的 `auction` 行，
把同一串文字发到个人微信「2026double」；桌面预检不过就重试到 09:35。
**它为什么已经三天没发出东西**：10-26 起竞价榜改由本项目发（`YuanShan-AuctionReport`），
留痕进 PG 的 `analytics.intraday_broadcast`，那份 jsonl 从此不再长新行 ——
镜像脚本按自己的口径等一个不存在的数据源，到点报 `mirror_timeout` 退出，
除了它自己 var 里那行日志，整机没有任何一处说"微信这一份今天没到"。
（现查：它 10-20 上线以来 15 次启动、10 次超时、0 次送达。）

收编后的立场：**判据零复制**这一条不变，但改读真源 ——
只取本项目今天真发给飞书的那一段正文（`broadcast_ledger` 里 `what='sent'` 的 `body`），
原样交给 `duanxian/wxpush` 那条通道。本项目改模板/门槛，微信这份自动跟着；
两处各算一遍竞价榜才是事故源。

为什么单独一条任务、而不是在 09:25:30 那个作业里顺手发出去：
桌面自动化慢且随时会被预检挡回来（RDP 一断就没有输入桌面），把它接在飞书发送之后
等于让飞书那条等它、还共享它的失败面。分开之后各自记账，谁没到一目了然。

跑法：
    python -m ingest.jobs.push.t08_wx_mirror --dry-run          # 只算分块与预检，一个键都不按
    python -m ingest.jobs.push.t08_wx_mirror                     # 真镜像（默认等正文等到 --until）
    python -m ingest.jobs.push.t08_wx_mirror --force             # 跳过交易日判断
退出码：0 已发（或今天已镜像过 / dry-run）· 3 到点没等到今天的竞价正文 · 2 被拒发 · 4 桌面预检没过
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]      # push/ → jobs/ → ingest/ → 仓库根（挪目录时这一格最容易数错）
sys.path.insert(0, str(ROOT))
try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')      # type: ignore[attr-defined]
except Exception:  # noqa: BLE001
    pass

from app.db import engine                                           # noqa: E402
from app.intraday import store, tasks                               # noqa: E402

#: 同一天只镜像一次（重跑、调度器补跑都不会发第二份到群里）
STATE_FILE = ROOT / 'var' / 'wx_mirror_state.json'
SOURCE_TASK = 'auction'          # 镜像的是哪一路的正文
MIRROR_TASK = 'mirror'           # 留痕里这一路自己的名字


def _log(now, what, **kw):
    try:
        store.write_broadcast(now, task=MIRROR_TASK, what=what, **kw)
    except Exception:  # noqa: BLE001  留痕写不进不该把镜像带崩
        pass


def today_body(day: dt.date) -> str | None:
    """今天真发给飞书的那段竞价正文。没有 ⇒ None（还没跑到 / 那天没发）。

    只认 `what='sent'`：`digest`/`auction` 那些行是过程记录，正文可能是没发出去的那一版。
    """
    row = engine.query_one(
        "SELECT body FROM analytics.intraday_broadcast"
        " WHERE trade_date = %s AND task = %s AND what = 'sent' AND ok IS TRUE AND body IS NOT NULL"
        " ORDER BY at_ts DESC LIMIT 1", (day, SOURCE_TASK), role='reader')
    return (row or {}).get('body') or None


def chunk(body: str, cap: int) -> list[str]:
    """按**行**切成不超过 cap 的几块（微信那条通道的「正文长度上限」）。

    为什么不整段发：客户端对超长是**静默截断**（截在哪它不说），而通道那一侧是拒发 ——
    两边都不肯替你猜，所以这里自己切，并在每块头上标 `(1/3)` 这种序号，
    让人一眼看出被切成几块（少一块就是少一块，不会读成"今天就这么长"）。
    """
    from duanxian import wxpush as WX
    cap = cap or WX.body_cap()
    lines = (body or '').split('\n')
    parts: list[str] = []
    cur: list[str] = []
    for ln in lines:
        trial = (cur + [ln])
        text = '\n'.join(x for x in trial)
        if cur and len(text) + 6 > cap:          # 留 6 字给后面补的「 (2/3)」
            parts.append('\n'.join(cur))
            cur = [ln]
        else:
            cur = trial
    if cur:
        parts.append('\n'.join(cur))
    if not parts:
        parts = [body or '']
    return [f'{p} ({i + 1}/{len(parts)})' if len(parts) > 1 else p
            for i, p in enumerate(parts)]


def already_mirrored(day: dt.date, digest: str) -> bool:
    try:
        st = json.loads(STATE_FILE.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return False
    return st.get('date') == f'{day:%Y-%m-%d}' and st.get('digest') == digest


def mark_mirrored(day: dt.date, digest: str, parts: int) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE_FILE.with_suffix('.json.tmp')
    tmp.write_text(json.dumps({'date': f'{day:%Y-%m-%d}', 'digest': digest,
                               'at': f'{dt.datetime.now():%Y-%m-%d %H:%M:%S}', 'parts': parts},
                              ensure_ascii=False), encoding='utf-8')
    tmp.replace(STATE_FILE)


def wait_for_body(day: dt.date, *, until: str, interval_s: int, now_fn, sleep_fn) -> str | None:
    """等到 `until` 为止；每 `interval_s` 秒回头看一次今天的正文。"""
    deadline = dt.datetime.combine(day, dt.datetime.strptime(until, '%H:%M').time())
    while True:
        body = today_body(day)
        if body:
            return body
        if now_fn() >= deadline:
            return None
        sleep_fn(min(interval_s, max(1, int((deadline - now_fn()).total_seconds()))))


def one_round(*, dry_run: bool, force: bool, until: str, interval_s: int) -> int:
    now = dt.datetime.now()
    if not force and not zt_trade_day(now):
        print(f'[{now:%H:%M:%S}] 非交易日 —— 不镜像')
        return 0
    day = now.date()
    body = wait_for_body(day, until=until, interval_s=interval_s,
                        now_fn=dt.datetime.now, sleep_fn=time.sleep)
    if not body:
        print(f'[{dt.datetime.now():%H:%M:%S}] 等到 {until} 也没看到今天发给飞书的竞价正文'
              f' —— 这一路今天没到微信（退出码 3）')
        _log(dt.datetime.now(), 'mirror', err=f'等到 {until} 没有 what=sent 的竞价正文')
        _log(dt.datetime.now(), 'mirror_timeout', err=f'等到 {until}')
        return 3
    digest = hashlib.sha256(body.encode('utf-8')).hexdigest()[:16]
    parts = chunk(body, 0)
    if already_mirrored(day, digest) and not dry_run:
        print(f'今天这份已经镜像过（{digest}），不再发第二份')
        return 0
    if not dry_run:
        # dry-run 不往留痕表写"这一路跑过一针" —— 屏上那一格说的是真发过几次
        _log(now, 'mirror', counts={'parts': len(parts), 'chars': len(body), 'until': until})
    from duanxian import wxpush as WX

    sent, problems = 0, []
    for i, part in enumerate(parts):
        res = WX.send(to='', text=part, dry_run=dry_run, source='wx-mirror',
                      via=f'{"dry" if dry_run else "task"}:{now:%H:%M}')
        if dry_run:
            print(f'  第 {i + 1}/{len(parts)} 块 {len(part)} 字 → 预检'
                  f'{"过" if res.get("ok") else "不过：" + str(res.get("reason"))[:120]}')
            if not res.get('ok'):
                problems.append(str(res.get('reason') or '')[:160])
            continue
        if res.get('ok'):
            sent += 1
            if i + 1 < len(parts):
                # 通道自己有一道「离上一条真发送够不够久」的闸，两块之间不留间隔
                # 会被它挡成"预检不过"，那是自己卡自己
                time.sleep(max(5, WX.cooldown_seconds() + 2))
        else:
            problems.append(f'第 {i + 1} 块{res.get("status")}：'
                            f'{str(res.get("reason") or "")[:140]}')
            break
    if dry_run:
        print(f'（dry-run：{len(parts)} 块，没有按任何键）'
              + (f'，预检问题：{("；".join(problems))[:180]}' if problems else ''))
        return 0
    ok = sent == len(parts)
    err = '；'.join(problems)[:300] or None
    _log(dt.datetime.now(), 'sent' if sent else 'wx_warn', ok=ok, delivered=ok,
         channel_key='wx', err=err, body=None if ok else body,
         counts={'parts': len(parts), 'sent': sent})
    print(f'[{dt.datetime.now():%H:%M:%S}] 微信镜像：{sent}/{len(parts)} 块发出去'
          + (f'，问题：{err[:200]}' if err else ''))
    if ok:
        mark_mirrored(day, digest, len(parts))
        return 0
    return 2


def zt_trade_day(now: dt.datetime):
    """交易日判断复用事件差那一份读法（不在这里写第二套日历口径）。"""
    from ingest.jobs.push import t01_zt_watch as zt_watch
    return zt_watch.is_trading_day(now)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description='M1 · 竞价榜微信镜像（读本项目真发出去的那份正文）')
    ap.add_argument('--dry-run', action='store_true', help='只算分块与预检，一个键都不按')
    ap.add_argument('--force', action='store_true', help='跳过交易日判断')
    ap.add_argument('--until', default=None, help='等到这个时刻为止（默认读系统配置那一格）')
    ap.add_argument('--interval', type=int, default=None, help='回看间隔秒（默认 60）')
    a = ap.parse_args(argv)
    cfg, warns = tasks.load_cfg()
    for w in warns:
        print(f'⚠ {w}')
    until = a.until or str(cfg.get('mirror_until') or '09:35')
    interval = a.interval or int(cfg.get('mirror_interval_s') or 60)
    return one_round(dry_run=a.dry_run, force=a.force, until=until, interval_s=interval)


if __name__ == '__main__':
    raise SystemExit(main())
