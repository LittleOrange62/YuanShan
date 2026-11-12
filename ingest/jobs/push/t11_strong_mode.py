# -*- coding: utf-8 -*-
"""D9 · 最强模式（19:19 盘后那一栏）—— 用户 2026-11-10 点名「加个推送 11，每天 19:19 分准时跑。
计算最强模式，你可以预设一些，然后分析方式和方法参考上面昨天的算法，格式也按照上面的进行飞书推送。
并且列出最近 5 天最强模式的变化和趋势」。

分工与 ⑨⑩ 一模一样（11-03 定的两层）：**判据只住 `duanxian/strong_mode.py` 一份**，
这一炉只做「读那份判据的读数 → 按用户给的三段式排版 → 落留痕 → 发渠道」：

    collect(now)      → `SM.collect()`：六档预设模式的命中/占比/常态基准/强度，三条证据，最近 5 场趋势
    render(data, now) → 排版（措辞全在 `app/intraday/templates.json` 的 `strong_mode_*`，代码里不拼句子）
    正文先落 `analytics.intraday_broadcast`（`task='strong_mode'`），逐档读数另落
    `analytics.strong_mode_daily`（事后问得出「那天说的高位反复，第二天走得怎么样」）→ 再发渠道
    --resend          → 读今天那条留痕的正文再发一次，**不重新取数**（重跑内容一字不差）

格式照用户给的那条样例：`最强模式:` 一行 ＋ `证据1|`／`证据2|`／`证据3|` 三段，
后面接「最近 5 场最强模式的变化」逐场一行 ＋ 一句趋势（哪档在扩、哪档在收）。

宁缺毋假三条：
  * 库里没有今天的涨停池场次 ⇒ `SM.collect()` 交 `missing='no_pool_rows'` ⇒ **一条都不发**，
    只记 `strong_mode_empty`，并在屏上能读出的位置点名「库里最新只到 X」；
  * 某只票的 250 日分位判不了 ⇒ 它不进「高位反复」那一档，行末点名「另有 N 只判不了」，不当 0 分位；
  * 配置文件读不到 ⇒ 走代码默认阈值并打一行声明（不悄悄换尺）。

    python -m ingest.jobs.push.t11_strong_mode --dry-run          # 现在跑一轮：只算只排版，不写账不发送
    python -m ingest.jobs.push.t11_strong_mode                    # 排版 + 落留痕与逐档表（不发）
    python -m ingest.jobs.push.t11_strong_mode --send             # 真发（渠道读系统配置那一格）
    python -m ingest.jobs.push.t11_strong_mode --send --resend    # 补发今天已落库的那一条正文
    python -m ingest.jobs.push.t11_strong_mode --backfill 30      # 只补逐档读数的历史，不发东西
    python -m ingest.jobs.push.t11_strong_mode --force            # 跳过交易日判断（周末手工试）

调度：Windows 任务计划程序每天 19:19 起这一炉（时点在「推送任务」那一屏第 ⑪ 行改，
或 `python -m app.intraday.tasks --set strong_mode_times=19:19 --apply`）。
19:19 排在 ⑩（19:00）之后：那一炉先把名单发完，这一炉读的是同一批已落地的加工结果，
两者互不等对方的数，也就不会因为谁先跑而读出不同的东西。
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]      # push/ → jobs/ → ingest/ → 仓库根
sys.path.insert(0, str(ROOT))
try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')      # type: ignore[attr-defined]
except Exception:  # noqa: BLE001
    pass

from app.db import engine                                        # noqa: E402
from app.intraday import store, tasks                            # noqa: E402
from duanxian import strong_mode as SM                           # noqa: E402  判据只住那一处
from ingest.jobs.push import t01_zt_watch as zt_watch             # noqa: E402  交易日判据共用一份

TPL_FILE = ROOT / 'app' / 'intraday' / 'templates.json'
TASK = 'strong_mode'
DRY_RUN = False


def _log(now, what, **kw):
    if DRY_RUN:
        return 0
    try:
        store.write_broadcast(now, task=TASK, what=what, **kw)
    except Exception:  # noqa: BLE001  留痕写不进去不该把今天这一栏带走
        pass


def _tpl() -> dict:
    """读文案模板。读不出来就**抛** —— 用一套编出来的措辞去播，等于把口径改掉。"""
    return json.loads(TPL_FILE.read_text(encoding='utf-8'))


def _d(day) -> str:
    """`2026-11-09` → `11-09`（那一栏每行都用短日子，正文一行一场比赛看得过来）。"""
    return str(day)[5:]


# ───────────────────────────────────────────────────────────────── 排版（判据不在这里）

def collect(now: dt.datetime) -> dict:
    """算出「今天这一栏要说什么」。缺场次 ⇒ 交空 dict（调用方据此**不发**）。"""
    res = SM.collect(now.date())
    if res.get('missing'):
        print(f"[{now:%H:%M:%S}] {res['missing']}：库里最新只到 {res.get('latest')} —— 不发这一栏")
        return {}
    return res

def render(data: dict, now: dt.datetime) -> str | None:
    """按用户给的三段式排版。`data` 是空读数就返回 None（**不许发一条空模式判断**）。"""
    if not data or data.get('missing'):
        return None
    T = _tpl()
    stamp = f'{now:%H:%M:%S}'
    ev = data['evidence']
    n_sealed = data['n_sealed']
    p = data['params']
    lines = [T['strong_mode_header'].format(title='最强模式', t=stamp)]

    # 一、最强模式：复合时两个键一起报名字，强度逐档一行（数字各说各的，不合成一句含糊话）
    if data['winners']:
        lines.append(T['strong_mode_winner'].format(label=data['winner_label']))
    else:
        best = max([m for m in data['modes'] if m['lift_pp'] is not None],
                   key=lambda m: m['lift_pp'], default=None)
        lines.append(T['strong_mode_no_mode'].format(
            n_sealed=n_sealed, label=(best or {}).get('mode_label') or '—',
            lift=(best or {}).get('lift_pp'), min_n=p['sm_min_n'])
            if best else T['strong_mode_no_base'].format(base=p['sm_baseline_sessions']))
    for w in data['winners']:
        if w['lift_pp'] is None:
            # 基准没凑到 ⇒ 减不出"比平时强几个点"。这种档在名次那一层就进不了候选，
            # 这里留一格是防有人以后改了门槛把它塞进来时，正文念出"None 个百分点"。
            continue
        flat = w['lift_pp'] <= 0
        lines.append(T['strong_mode_winner_row_flat' if flat else 'strong_mode_winner_row'].format(
            name=w['mode_label'], n_sealed=n_sealed, n=w['n_stocks'], share=w['share_pct'],
            base=w['base_sessions'], base_share=w['base_share_pct'], lift=w['lift_pp']))

    # 二、证据1：窗口涨停天数 TOP N 里几只不是单波连板（母体含今天没封住的那些，与样例同一读法）
    if not ev['top']:
        lines.append(T['strong_mode_e1_none'].format(win=data['window']))
    else:
        # 一句里两把措辞：有反复的报「N 次涨停仅 M 连板」，全是纯连板的报「N 次涨停＝M 连板」，
        # 后者硬套前者会变成「6 次涨停仅 6 连板」——那说的是同一件事，读起来像数错了。
        picked = ev['not_single'] if ev['not_single_n'] else ev['top']
        row_key = 'strong_mode_e1_row' if ev['not_single_n'] else 'strong_mode_e1_row_single'
        lines.append(T['strong_mode_e1' if ev['not_single_n'] else 'strong_mode_e1_all'].format(
            win=data['window'], top=len(ev['top']), k=ev['not_single_n'],
            rows='、'.join(T[row_key].format(
                name=r['name'], zt=r['zt_days'], mx=r['max_consec'])
                for r in picked[:p['sm_evidence_n']])))

    # 三、证据2：今日断过板又接上的轮 ＋ 断板后今日再首板的那几只
    broken = '、'.join(T['strong_mode_e2_row'].format(name=r['name'], label=r['label'])
                       for r in ev['broken'])
    reseal = '、'.join(T['strong_mode_e2_reseal_row'].format(
        name=r['name'], mx=r['max_consec']) for r in ev['reseal'])
    if broken and reseal:
        line = T['strong_mode_e2'].format(broken=broken, reseal=reseal)
    elif broken:
        line = T['strong_mode_e2_broken_only'].format(broken=broken)
    elif reseal:
        line = T['strong_mode_e2_reseal_only'].format(reseal=reseal)
    else:
        line = T['strong_mode_e2_none'].format(n_sealed=n_sealed)
    lines.append(line)

    # 四、证据3：真·一波流还剩几只
    if not ev['single']:
        lines.append(T['strong_mode_e3_none'].format(min=p['sm_wave_min']))
    else:
        cells = []
        for r in ev['single'][:p['sm_evidence_n']]:
            key = ('strong_mode_e3_row' if r['reason'] and r['float_cap_yi'] is not None else
                   'strong_mode_e3_row_noreason' if r['float_cap_yi'] is not None else
                   'strong_mode_e3_row_nocap' if r['reason'] else 'strong_mode_e3_row_bare')
            cells.append(T[key].format(name=r['name'], consec=r['consec'],
                                       reason=r['reason'] or '', cap=r['float_cap_yi']))
        lines.append(T['strong_mode_e3'].format(
            min=p['sm_wave_min'], n=ev['single_n'], rows='、'.join(cells),
            case=T['strong_mode_e3_case'] if ev['is_case'] else ''))

    # 五、最近 5 场逐场一行 ＋ 一句趋势
    tr = data['trend']
    lines.append(T['strong_mode_trend_title'].format(k=tr['span']))
    for r in tr['sessions']:
        if not r['n_sealed']:
            lines.append(T['strong_mode_trend_row_empty'].format(d=_d(r['day'])))
        elif r.get('winner_label'):
            lines.append(T['strong_mode_trend_row'].format(
                d=_d(r['day']), label=r['winner_label'], n=r['n_sealed'], h=r['max_consec']))
        else:
            lines.append(T['strong_mode_trend_row_no_mode'].format(
                d=_d(r['day']), n=r['n_sealed'], h=r['max_consec']))
    notes = tr['notes']
    parts = [T['strong_mode_trend_expand'].format(
        label=x['mode_label'], a=x['from_share'], b=x['to_share'], pp=x['delta_pp'])
        for x in notes['expanding']] + [T['strong_mode_trend_shrink'].format(
        label=x['mode_label'], a=x['from_share'], b=x['to_share'], pp=x['delta_pp'])
        for x in notes['shrinking']]
    if not (notes.get('height') or notes.get('sealed')):
        lines.append(T['strong_mode_trend_short'])
    elif parts:
        lines.append(T['strong_mode_trend_note'].format(
            fd=_d(notes['first_day']), ld=_d(notes['last_day']), parts='、'.join(parts),
            hf=notes['height']['from'], ht=notes['height']['to'],
            sf=notes['sealed']['from'], st=notes['sealed']['to']))
    else:
        lines.append(T['strong_mode_trend_flat'].format(
            fd=_d(notes['first_day']), ld=_d(notes['last_day']), step=p['sm_tie_pp'],
            hf=notes['height']['from'], ht=notes['height']['to'],
            sf=notes['sealed']['from'], st=notes['sealed']['to']))

    # 六、判不了的那几格要自己点名（空着不写就是把"没读到"念成"没有"）
    und = data['undecided']
    declares = []
    if und.get('no_mode_n'):
        declares.append(T['strong_mode_undecided'].format(n=und['no_mode_n']))
    if und.get('pos_missing_n'):
        declares.append(T['strong_mode_pos_missing'].format(n=und['pos_missing_n']))
    lack = sum(m.get('order_lack_n') or 0 for m in data['modes'])
    if lack:
        declares.append(T['strong_mode_order_lack'].format(n=lack))
    if data.get('params_unread'):
        declares.append(T['strong_mode_params_unread'].format(why=data['params_unread']))
    lines.extend(declares)
    lines.append(T['strong_mode_footer'].format(
        spec=SM.POOL_SPEC, win=data['window'], base=p['sm_baseline_sessions'], t=stamp))
    return '\n'.join(lines)


# ───────────────────────────────────────────────────────────────── 以下是不用改的壳

def today_body(day: dt.date) -> str | None:
    """今天已经落库的那一条正文（`--resend` 用它：只读库，不重新取数）。"""
    row = engine.query_one(
        "SELECT body FROM analytics.intraday_broadcast"
        " WHERE trade_date = %s AND task = %s AND what = 'strong_mode' AND body IS NOT NULL"
        ' ORDER BY at_ts DESC LIMIT 1', (day, TASK), role='reader')
    return (row or {}).get('body')


def one_round(*, dry_run: bool, send: bool, force: bool, channel: str | None,
              resend: bool, backfill: int | None, day: dt.date | None = None) -> int:
    global DRY_RUN
    DRY_RUN = dry_run
    now = dt.datetime.now()
    #: `--date` 给的是「复盘那一场」：读数以那天为今天、留痕也落在那天，并跳过交易日闸
    #: （人既然点了日子，就不该再被"今天不是交易日"挡回去）。正文里那个钟点是**现在**的钟点，
    #: 不是那场的 19:19 —— 补发/重排的历史正文都带这一格，读的人分得清哪条是当时发的。
    if day:
        now = dt.datetime.combine(day, now.time())
    day = now.date()
    if backfill is not None:
        print(f'[{now:%H:%M:%S}] 补历史逐档读数（最近 {backfill} 场，不发任何东西）')
        got = SM.backfill(sessions=backfill)
        print(f"  落了 {got['written_days']} 场：{got['first']} → {got['last']}")
        return 0
    if not force and not resend:
        if not zt_watch.is_trading_day(now):
            print(f'[{now:%H:%M:%S}] 非交易日 —— 不发最强模式')
            return 0
    if resend:
        body = today_body(day)
        if not body:
            print(f'[{now:%H:%M:%S}] 今天库里没有已落的最强模式正文 —— 没有东西可补发（不重算冒充）')
            _log(now, 'strong_mode_no_body', ok=False,
                 err='analytics.intraday_broadcast 里没有今天的 strong_mode 行')
            return 1
        print(f'[{now:%H:%M:%S}] 补发今天已落库的那一条（不重新取数）\n{body}')
        if not dry_run:
            _send(now, body, tasks.send_channels(channel), counts={'resend': True})
        return 0
    data = collect(now)
    text = render(data, now)
    if text is None:
        print(f'[{now:%H:%M:%S}] 读数是空的（缺场次或库里没有今天的涨停池）—— 一条都不发')
        _log(now, 'strong_mode_empty', ok=False,
             err='涨停池里没有今天这一场（或整场没有一只封住），未发送')
        return 1
    print(text)
    counts = {'n_sealed': data['n_sealed'],
              'winners': [w['mode_key'] for w in data['winners']],
              'winner_label': data['winner_label'],
              'trend_sessions': data['trend']['span']}
    if dry_run:
        print('\n（dry-run：未写留痕、未落逐档表、未发送）')
        return 0
    _log(now, 'strong_mode', body=text, counts=counts)
    try:
        counts['tally_rows'] = SM.write(data)
    except Exception as exc:  # noqa: BLE001  表写不进去要说，但不该带走已经落好的正文
        counts['tally_error'] = f'{type(exc).__name__}: {exc}'[:200]
        print(f'  ⚠️ 逐档读数没落进 {SM.TABLE}：{counts["tally_error"]}')
    if send:
        _send(now, text, tasks.send_channels(channel), counts=counts)
    return 0


def _send(now, text, keys, *, counts):
    from app.intraday.cards import send_broadcast_with_card
    res = send_broadcast_with_card(keys, '最强模式', text)
    ok = bool(res.get('ok'))
    store.write_broadcast(now, task=TASK, what='sent', body=text, ok=ok, delivered=ok,
                          channel_key=','.join(keys), counts=counts,
                          err=None if ok else str(res.get('detail'))[:300])
    verdict = '已确认' if ok else f'未确认：{str(res.get("detail"))[:160]}（判据是渠道业务码，不是 HTTP 200）'
    print(f'  发送 → {",".join(keys)}：{verdict}')


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description='D9 · 最强模式（19:19，三段式证据 + 最近 5 场趋势）')
    ap.add_argument('--dry-run', action='store_true', help='只算只排版，不写账不发送')
    ap.add_argument('--send', action='store_true', help='发渠道（不给就只落留痕与逐档表）')
    ap.add_argument('--channel', default=None,
                    help='逗号分隔的渠道串；不给就读「推送任务」那一屏的渠道格')
    ap.add_argument('--force', action='store_true', help='跳过交易日判断（手工试）')
    ap.add_argument('--resend', action='store_true', help='补发今天已落库的那一条（不重新取数）')
    ap.add_argument('--backfill', type=int, default=None, metavar='N',
                    help='只把最近 N 场的逐档读数落进表里（不发任何东西），如 --backfill 30')
    ap.add_argument('--date', default=None, metavar='YYYY-MM-DD',
                    help='复盘某一场：读数以那天为今天、留痕落在那天（手工重排/补账用）')
    a = ap.parse_args(argv)
    when = dt.date.fromisoformat(a.date) if a.date else None
    return one_round(dry_run=a.dry_run, send=a.send, force=a.force,
                     channel=a.channel, resend=a.resend, backfill=a.backfill, day=when)


if __name__ == '__main__':
    raise SystemExit(main())
