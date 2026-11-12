# -*- coding: utf-8 -*-
"""D8 · 明日展望（19:00 盘后那一栏）—— **骨架**：内容你填，入库/发送/留痕/补发这一层已经接好。

用户 11-04 的原话是「每日复盘有了，再增加个明日展望，定时19:00」—— ⑨ 那一栏的正文已经写上去了，
这一条照着它同一个形态给一个壳：同样按 11-03 定的分层写（判断与取数属于数据加工环节，先落库；
推送到飞书是另一炉）。所以本文件里只有 **一个函数是要你写的**（`collect()`），
其余都是已经跑通的那套壳：

    collect(now)      ← 取数在 `duanxian/tomorrow_outlook.py`（11-05 起）：那张榜现在既要发飞书
                          又要上屏，判据只准住一处，这一炉只是它的一个调用方
    render(data, now) ← 排版（措辞在 `app/intraday/templates.json` 的 `tomorrow_*`，别在代码里拼句子）
    正文先落 `analytics.intraday_broadcast`（`task='tomorrow'`）→ 再发渠道
    --resend          ← 读今天那条留痕的正文再发一次，**不重新取数**（重跑内容一字不差）

宁缺毋假：`collect()` 还没写、或返回空 ⇒ **一条都不发**，只记一行 `tomorrow_empty`；
这条默认就是关着的（「推送任务」那一屏第 ⑩ 行的开关打开才注册任务），免得每天往群里发一句空话。

    python -m ingest.jobs.push.t10_tomorrow_outlook --dry-run          # 现在跑一轮：只算只排版，不写账不发送
    python -m ingest.jobs.push.t10_tomorrow_outlook                    # 排版 + 落留痕（不发）
    python -m ingest.jobs.push.t10_tomorrow_outlook --send             # 真发（渠道读系统配置那一格）
    python -m ingest.jobs.push.t10_tomorrow_outlook --send --resend    # 补发今天已落库的那一条正文
    python -m ingest.jobs.push.t10_tomorrow_outlook --force            # 跳过交易日判断（周末手工试）

调度：Windows 任务计划程序每天 19:00 起这一炉（时点在「推送任务」那一屏改，
或 `python -m app.intraday.tasks --set tomorrow_times=19:00 --apply`）。
19:00 在盘后，所以这里**不判交易时段**，只判交易日（非交易日静默退，不发"明天看什么"）。
为什么是 19:00 而不是紧跟 18:18：收盘链（实测 16:06 落定）与 `fill_all`（17:56）跑完之后，
今天的日线、涨跌停池、连板天梯、体系榜才都齐了 —— 展望要读的就是这些。

库里现成能读的东西（表名本机现查过；这一栏只读它们，别在这儿边取边发）：
    analytics.indicator_value        每票每指标的落地值（**父分区表**，按 trade_date 自动路由；
                                      「多指标靠前」那一节读它：家族各项各自 top{t10_top_n}，数重复上榜）
    analytics.xy_rank_daily          全市场 X-Y 排名（rank_sum 越小越靠前，短名单的第三把排序尺）
    analytics.trend_stock_daily      趋势票管线（selected5/selected10、站上 5 日线天数、20 日新高）
    analytics.limit_up_pool_daily    今天涨停池逐票（几天几板、封单、开板次数、题材归因）
    analytics.board_node_daily       连板天梯（高度、各档家数 —— "梯队"那一格读它）
    analytics.market_breadth         涨跌家数与中位涨幅（与上一场比才读得出情绪）
    analytics.market_sentiment_daily 情绪周期档位与证据
    analytics.system_entry           每日体系榜（⑥那一炉的产物）；`intraday_sysentry` 是今天 14:39 那一针
    analytics.stock_character_daily  股性三腿分位与档位（strong/middle/weak/NULL；
                                      用户 11-04 定的口径：两榜只留 strong，NULL=判不了照留并点名）
    analytics.noon_review            ⑤午间那一栏的整行（上午全景，与下午对比用）
    analytics.intraday_concept_count 竞价/午间/热度各时点的概念计数
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
from ingest.jobs.push import t01_zt_watch as zt_watch               # noqa: E402  交易日判据共用一份

TPL_FILE = ROOT / 'app' / 'intraday' / 'templates.json'
CFG_FILE = ROOT / 'app' / 'intraday' / 'broadcast_config.json'
TASK = 'tomorrow'
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


# ───────────────────────────────────────────────────────────────── 取数（判据不住这里）

#: 两张榜与"今日最高标"那三条取数 2026-11-05 起搬到 `duanxian/tomorrow_outlook.py`：
#: 同一张榜现在有两个调用方（这一炉发飞书、那一屏上界面），判据写两份迟早变成
#: "屏上八只、群里七只"。这里留**别名**而不是包一层薄函数 —— 那样 `inspect.getsource`
#: 顺着别名拿到的是真 SQL，钉"股性过滤住在查询里"那条测试才还指着实际跑的那句话。
from duanxian import tomorrow_outlook as TO                  # noqa: E402  判据只住那一处

_family = TO.family
_hits_shortlist = TO.hits_shortlist
_trend_shortlist = TO.trend_shortlist
_top_boards = TO.top_boards


def collect(now: dt.datetime) -> dict:
    """算出"明天这一栏要说什么"，返回 `{'sections': [...]}`。

    三问三答（用户 11-04 原话的口径）：**指标中靠前的**（一）→ **重复出现次数多的**
    （近 3 场重复上榜，混在一的排序里）→ **趋势最正的**（二）→ 交集与盯法（三）。
    只读库里今天已落地的加工结果；缺的那格说"缺数"，不写 0、不拿昨天顶。

    股性过滤（用户 11-04 追加）：两榜在 SQL 层就只留 `stock_character_daily.char_level`
    ∈ `t10_char_keep`（默认 `strong`=股性强）的票，middle/weak 直接出局；
    NULL（上市不足 120 场判不了）照留，但行尾点名"股性判不了"。
    """
    day = now.date()
    T = _tpl()
    cfg = json.loads(CFG_FILE.read_text(encoding='utf-8'))
    sections: list[str] = []

    hits, ind_latest = _hits_shortlist(day, cfg)
    trends, tr_latest = _trend_shortlist(day, cfg)
    if hits is None and trends is None:
        return {}                     # 两节都缺场次：今天不发（宁缺毋假，只记 tomorrow_empty）

    fam_n = len(_family(cfg))
    top_n = int(cfg.get('t10_top_n', 100))
    win = int(cfg.get('t10_window_sessions', 3))
    min_hits = int(cfg.get('t10_min_hits', 4))

    if hits is None and trends:
        sections.append(T['tomorrow_s1'].format(fam=fam_n, top=top_n)
                        + '\n' + T['tomorrow_missing_ind'].format(d=ind_latest or '缺场次'))
    else:
        body = []
        if hits:
            for i, r in enumerate(hits, 1):
                line = T['tomorrow_hit_row'].format(
                    i=i, name=r['name'] or r['security_code'],
                    board=r['board'] or '—', hits=r['hits_today'], fam=fam_n, top=top_n,
                    days=r['days_in_top'], win=win,
                    inds='、'.join(r['inds']) or '缺数')
                if 'char_level' in r and r['char_level'] is None:
                    line += T['tomorrow_char_na']     # 股性判不了（新股不足 120 场/档位价缺）——照留，但点名
                zt = r.get('zt')
                if zt:
                    one = (int(zt['consec'] or 0) <= 1)
                    key = ('tomorrow_hit_zt1' if one else 'tomorrow_hit_zt')
                    if not zt['reason']:
                        key += '_noreason'
                    line += T[key].format(consec=zt['consec'], reason=zt['reason'] or '')
                body.append(line)
        else:
            body.append(T['tomorrow_hits_none'].format(min=min_hits))
        sections.append(T['tomorrow_s1'].format(fam=fam_n, top=top_n) + '\n' + '\n'.join(body))

    min_streak = int(cfg.get('t10_trend_streak_min', 5))
    if trends is None:
        sections.append(T['tomorrow_s2']
                        + '\n' + T['tomorrow_missing_trend'].format(d=tr_latest or '缺场次'))
    else:
        body = []
        if trends:
            for i, r in enumerate(trends, 1):
                line = T['tomorrow_trend_row'].format(
                    i=i, name=r['name'] or r['security_code'],
                    board=r['board'] or '—', streak=r['ma5_streak'])
                if 'char_level' in r and r['char_level'] is None:
                    line += T['tomorrow_char_na']
                if r['nh_20']:
                    line += T['tomorrow_trend_nh']
                if r['pos250'] is not None:
                    line += T['tomorrow_trend_pos'].format(pos=float(r['pos250']))
                if r['sealed']:
                    line += T['tomorrow_trend_sealed']
                if r['pct_change'] is not None:
                    line += T['tomorrow_trend_pct'].format(pct=float(r['pct_change']))
                else:
                    line += T['tomorrow_trend_pct_missing']
                body.append(line)
        else:
            body.append(T['tomorrow_trend_none'].format(min=min_streak))
        sections.append(T['tomorrow_s2'] + '\n' + '\n'.join(body))

    # 三、明日观察清单：两榜交集 + 最高标（与每日复盘同一把尺）+ 什么算坏了
    watch: list[str] = []
    if hits is not None:
        both = None if trends is None else \
            {r['security_code'] for r in hits} & {r['security_code'] for r in trends}
        if both is not None:
            if both:
                names = '、'.join((next(r['name'] for r in hits + trends
                                        if r['security_code'] == c) or c)
                                  for c in sorted(both))
                watch.append(T['tomorrow_overlap'].format(names=names))
            else:
                watch.append(T['tomorrow_overlap_none'])
    all_codes: set[str] = set()
    if hits is not None:
        all_codes |= {r['security_code'] for r in hits}
    if trends is not None:
        all_codes |= {r['security_code'] for r in trends}
    cnt = len(all_codes)
    top = _top_boards(day)
    if cnt and top:
        top_names = '、'.join(f"{r['name'] or '?'}" for r in top)
        watch.append(T['tomorrow_watch'].format(
            text=f"上面 {cnt} 只的明日竞价 + 最高标 {top_names}"
                 f"（{int(top[0]['consec'])} 板）：开盘承接不弱则信号延续，"
                 f"竞价大面/炸板则当天作废（观察口径，非预测）"))
    bad_parts = []
    if trends is not None:
        bad_parts.append('趋势榜收盘跌破 5 日线')
    if hits is not None:
        bad_parts.append('指标榜前排次日大幅低开无承接')
    if bad_parts:
        watch.append(T['tomorrow_bad'].format(text='、或'.join(bad_parts) + ' = 对应信号失效'))
    if watch:
        sections.append(T['tomorrow_s3'] + '\n' + '\n'.join(watch))
    return {'title': '明日展望', 'sections': sections}


def render(data: dict, now: dt.datetime) -> str | None:
    """排版。`collect()` 没给内容就返回 None（调用方据此**不发**，不许发一条空展望）。"""
    sections = [str(s).strip() for s in (data or {}).get('sections') or [] if str(s).strip()]
    if not sections:
        return None
    t = _tpl()
    stamp = f'{now:%H:%M:%S}'
    lines = [t['tomorrow_header'].format(t=stamp, title=(data.get('title') or '明日展望'))]
    lines.extend(sections)
    lines.append(t['tomorrow_footer'].format(t=stamp))
    return '\n'.join(lines)


# ───────────────────────────────────────────────────────────────── 以下是不用你动的壳

def today_body(day: dt.date) -> str | None:
    """今天已经落库的那一条正文（`--resend` 用它：只读库，不重新取数）。"""
    row = engine.query_one(
        "SELECT body FROM analytics.intraday_broadcast"
        " WHERE trade_date = %s AND task = %s AND what = 'tomorrow' AND body IS NOT NULL"
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
            print(f'[{now:%H:%M:%S}] 非交易日 —— 不发明日展望')
            return 0
    if resend:
        body = today_body(day)
        if not body:
            print(f'[{now:%H:%M:%S}] 今天库里没有已落的明日展望正文 —— 没有东西可补发（不重算冒充）')
            _log(now, 'tomorrow_no_body', ok=False,
                 err='analytics.intraday_broadcast 里没有今天的 tomorrow 行')
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
        _log(now, 'tomorrow_empty', ok=False, err='collect() 返回空内容，未发送')
        return 1
    print(text)
    counts = {'sections': len([s for s in (data.get('sections') or []) if str(s).strip()])}
    if dry_run:
        print('（dry-run：未写账、未发送）')
        return 0
    _log(now, 'tomorrow', body=text, counts=counts)
    if send:
        _send(now, text, tasks.send_channels(channel), counts=counts)
    return 0


def _send(now, text, keys, *, counts):
    from app.intraday.cards import send_broadcast_with_card
    res = send_broadcast_with_card(keys, '明日展望', text)
    ok = bool(res.get('ok'))
    store.write_broadcast(now, task=TASK, what='sent', body=text, ok=ok, delivered=ok,
                          channel_key=','.join(keys), counts=counts,
                          err=None if ok else str(res.get('detail'))[:300])
    verdict = '已确认' if ok else f'未确认：{str(res.get("detail"))[:160]}（判据是渠道业务码，不是 HTTP 200）'
    print(f'  发送 → {",".join(keys)}：{verdict}')


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description='D8 · 明日展望（19:00，正文由 collect() 提供）')
    ap.add_argument('--dry-run', action='store_true', help='只算只排版，不写账不发送')
    ap.add_argument('--send', action='store_true', help='发渠道（不给就只落留痕）')
    ap.add_argument('--channel', default=None,
                    help='逗号分隔的渠道串；不给就读「推送任务」那一屏的渠道格')
    ap.add_argument('--force', action='store_true', help='跳过交易日判断（手工试）')
    ap.add_argument('--resend', action='store_true', help='补发今天已落库的那一条（不重新取数）')
    a = ap.parse_args(argv)
    return one_round(dry_run=a.dry_run, send=a.send, force=a.force,
                     channel=a.channel, resend=a.resend)


if __name__ == '__main__':
    raise SystemExit(main())
