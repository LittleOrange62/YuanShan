# -*- coding: utf-8 -*-
"""五大指数 + 两市 量能快照：每天两针入库（noon＝12:10 午间复盘 / close＝18:18 每日复盘）。

用户 11-05：「这个脚本最好写进数据库里……每天需要有两次入库：一次是中午复盘，一次是晚上复盘」，
外加推送核心要义（手机小屏）：同比只留百分比、不堆绝对亿值、给**位置**（加权分位）和**判断**。

口径（DDL 62 的列注释是权威，这里只留算法语义）：
  · amount_yuan：noon＝今早累计（腾讯分钟 11:30）；close＝全天（本地 `.day` 尾行是当天就用
    `.day`，否则腾讯分钟 15:00；北证 50 无分钟源 ⇒ 只有 `.day`）。取不到 ⇒ NULL（≠0），
    进 `missing` 点名 —— 宁缺毋假，不拿上一场顶。
  · yoy_pct：对前一交易日的**同一针**（noon＝前日 11:30 累计；close＝前日全天），百分数。
  · pctile250：当前值在近 250 个全天里的**加权分位**（权重按距尾行天数指数衰减、半衰期 60 天，
    近端权重更大——6 月那种峰值随时间淡出，位置反映的是"相对最近的常态"）。0~1，存 3 位小数。
  · trend_5d_pct：近 5 个全天 末值/初值 − 1（百分数）。
  · label：位置(低 <30 / 中 30~70 / 高 >70) × 趋势(±5%) 九宫：
    低位反转｜低位徘徊｜低位下行｜中位上行｜中位徘徊｜中位下行｜高位放量｜高位徘徊｜高位反转
    —— 阈值规则贴标签，**不是预测**。
  · asof_date：分位窗口用到的最后一个完整全天（`.day` 可能还没落到当天；noon 时最迟是前一
    交易日，close 时可以是当天）——界面上"位置48%"是哪一个交易日的口径，看这一列，不猜。

取数接缝（测试从这里打桩，不出网）：`_fetch_minutes(code)` / `_read_day(name, n)`。
本模块**不连库、不打印**：`compute()` 只算，`write_snap()` 才经 `store` 落库（返回行数）。
"""
from __future__ import annotations

import datetime as dt
import json
import urllib.request
from pathlib import Path
from typing import Callable

from app.config import VIPDOC_DIR
from app.intraday import store, volume

#: 推送名 / 腾讯码：分钟累计码（day/query）/ 实时报价码（qt.gtimg.cn）/ 本地 .day 名。
#: 北证 50 无分钟源（分钟码 None），全天额用实时报价 p[37]（万元）兜底。
IDX = (
    ('上证', 'sh000001', 'sh000001', '上证指数'),
    ('深证', 'sz399001', 'sz399001', '深证成指'),
    ('创业板', 'sz399006', 'sz399006', '创业板指'),
    ('科创', 'sh000688', 'sh000688', '科创50'),
    ('北证', None, 'bj899050', '北证50'),
)
TOT = '两市'

#: 加权分位半衰期（天）与默认参数 —— 配置键名见 broadcast_config.json（11-05 复盘后定参：
#: 窗 250、半衰期 60、位置档 30/70、趋势带 ±5%；复盘锚点是 2026-06-22 两市 3.74 万亿峰值
#: 一路缩到 10-22 的 1.53 万亿、近 5 日回升到 2.04 万亿的那段形态）。
DEFAULTS = {'window': 250, 'half_life': 60.0, 'bands': (30.0, 70.0), 'trend_band': 0.05}
SPEC = 'v1'
_TIMEOUT_S = 15


def _cfg() -> dict:
    try:
        root = Path(__file__).resolve().parents[3]
        raw = json.loads((root / 'app' / 'intraday' / 'broadcast_config.json').read_text(encoding='utf-8'))
    except Exception:  # noqa: BLE001  配置读不到就用定版默认，不崩
        raw = {}
    out = dict(DEFAULTS)
    out['window'] = int(raw.get('vol_pos_window', out['window']))
    out['half_life'] = float(raw.get('vol_pos_half_life', out['half_life']))
    out['bands'] = tuple(float(x) for x in raw.get('vol_pos_bands', out['bands']))
    out['trend_band'] = float(raw.get('vol_trend_band', out['trend_band']))
    return out


# ---------------------------------------------------------------- 取数接缝（测试打桩口）

def _fetch_minutes(code: str) -> dict[str, dict[str, float]]:
    """腾讯分钟累计成交额（近 5 个交易日 + 当天）；失败由调用方按指数单独容错。"""
    return volume.tx_minutes(code)


def _read_day(name: str, n: int) -> list[tuple[int, float]]:
    """本地 `.day` 尾 n 行 `[(YYYYMMDD, 全天成交额元)]`（旧→新）。北证 50 走 bj 目录。"""
    market, code = _day_code(name)
    return volume.day_amounts(VIPDOC_DIR / market / 'lday' / f'{code}.day', n)


def _fetch_quote_day(code: str) -> float | None:
    """腾讯实时指数报价 p[37]（万元）→ 元。收盘后调用＝全天额；盘中调用＝截至当时。

    北证 50 无分钟源、本地 .day 又常比 18:18 晚落地，这一格就靠它（11-05 实测 p[37]
    与本地日线同量级：北证 11-05 147,475 万 ≈ .day 145,0xx 万，差异＝时点差）。
    """
    req = urllib.request.Request(f'https://qt.gtimg.cn/q={code}',
                                 headers={'User-Agent': 'Mozilla/5.0'})
    with urllib.request.urlopen(req, timeout=_TIMEOUT_S) as r:
        txt = r.read().decode('gbk', 'replace')
    p = txt.split('~')
    if len(p) > 37 and p[37]:
        try:
            return float(p[37]) * 1e4
        except ValueError:
            return None
    return None


def _day_code(name: str) -> tuple[str, str]:
    if name in volume.DAY_FILES:
        return volume.DAY_FILES[name]
    return {'创业板指': ('sz', 'sz399006'), '科创50': ('sh', 'sh000688'), '北证50': ('bj', 'bj899050')}[name]


# ---------------------------------------------------------------- 算法（纯函数，先测后用）

def weighted_pctile(v: float, series: list[float], half_life: float = 60.0) -> float | None:
    """当前值 v 压在序列（旧→新）多少比例的权重之下（0~1）。

    权重 `w_i = 0.5 ** (距尾行天数 / half_life)`：越近越重。序列空 ⇒ None。
    """
    if not series:
        return None
    num = den = 0.0
    for i, x in enumerate(series):
        w = 0.5 ** ((len(series) - 1 - i) / half_life)
        den += w
        if x <= v:
            num += w
    return num / den if den else None


def trend_5d(series: list[float]) -> float | None:
    """近 5 个全天 末值/初值 − 1（小数）；不足 5 个 ⇒ None。"""
    if len(series) < 5:
        return None
    a, b = series[-5], series[-1]
    if not a or a <= 0:
        return None
    return b / a - 1


def judge(pctile: float | None, tr: float | None, *,
          bands: tuple[float, float] = (30.0, 70.0), trend_band: float = 0.05) -> str | None:
    """位置 × 趋势 九宫。pctile 是 0~1；任一格缺数 ⇒ None（宁可空，不硬凑标签）。"""
    if pctile is None or tr is None:
        return None
    pos = '低' if pctile * 100 < bands[0] else ('中' if pctile * 100 <= bands[1] else '高')
    if tr >= trend_band:
        mv = '升'
    elif tr <= -trend_band:
        mv = '降'
    else:
        mv = '平'
    grid = {
        ('低', '升'): '低位反转', ('低', '平'): '低位徘徊', ('低', '降'): '低位下行',
        ('中', '升'): '中位上行', ('中', '平'): '中位徘徊', ('中', '降'): '中位下行',
        ('高', '升'): '高位放量', ('高', '平'): '高位徘徊', ('高', '降'): '高位反转',
    }
    return grid[(pos, mv)]


def pattern_5d(series: list[float]) -> str | None:
    """近 5 个全天的逐日升/降计数 → 「3升1降」/「4连升」/「3连降」；不足 5 个 ⇒ None。"""
    if len(series) < 5:
        return None
    up = sum(1 for i in range(len(series) - 4, len(series)) if series[i] > series[i - 1])
    dn = 4 - up
    if up == 4:
        return '4连升'
    if dn == 4:
        return '4连降'
    return f'{up}升{dn}降'


# ---------------------------------------------------------------- 组装

def _morning(minutes: dict[str, float] | None) -> float | None:
    """今早额＝当日 11:30 累计（12:10 推送时上午已收盘）。"""
    return volume._morning(minutes)


def _full(minutes: dict[str, float] | None) -> float | None:
    """全天额＝当日 15:00 累计。"""
    if not minutes:
        return None
    if "1500" in minutes:
        return minutes["1500"]
    vals = [v for k, v in minutes.items() if k <= "1500"]
    return max(vals, default=None)


def _series_until(rows: list[tuple[int, float]], last: int, window: int) -> list[tuple[int, float]]:
    """`.day` 行里取 `≤ last` 的尾 window 行（旧→新）。"""
    rows = [r for r in rows if r[0] <= last]
    return rows[-window:]


def compute(kind: str, now: dt.datetime, *,
            note: Callable[[str, dict], None] | None = None) -> dict:
    """算一针的六行读数（上证/深证/创业板/科创/北证/两市），**不写库**。

    → `{'kind', 'asof': {series名: 最后全天|None}, 'src', 'idx': {名: {amount, yoy,
       pctile, trend, label, missing}}, 'tot': {...}}`。调用方拿去排版（`render_lines`）
    和落库（`write_snap`）。
    """
    if kind not in ('noon', 'close'):
        raise ValueError(f'kind 只能是 noon/close，收到 {kind!r}')
    cfg = _cfg()
    win = int(cfg['window'])

    def log(what: str, **kw) -> None:
        if note:
            note(what, kw)

    today = int(f'{now:%Y%m%d}')
    src_parts: list[str] = []

    tx: dict[str, dict[str, dict[str, float]]] = {}
    for name, code, _qc, _dn in IDX:
        if not code:
            continue
        try:
            tx[code] = _fetch_minutes(code)
        except Exception as exc:  # noqa: BLE001  一只挂了不影响其余
            log(f'vol_snap_tx_fail_{kind}', code=code, err=f'{type(exc).__name__}: {exc}'[:150])
    if tx:
        src_parts.append('腾讯分钟')

    # 前一交易日：分钟源日期里排除当天的最新一天（各源一致时取它；缺就用 .day 的尾）
    past: list[int] = []
    for code in tx:
        ds = sorted((d for d in tx[code] if int(d) < today), reverse=True)
        if ds:
            past = [int(d) for d in ds][:5]
            break
    # .day 系列（供分位/趋势/全天额兜底）—— 尾行是否可用看 asof 上限
    asof_max = today if kind == 'close' else (past[0] if past else None)
    days: dict[str, list[tuple[int, float]]] = {}
    for name, _mc, _qc, dn in IDX:
        try:
            days[dn] = _read_day(dn, win + 2)
        except Exception as exc:  # noqa: BLE001
            log(f'vol_snap_day_fail_{kind}', name=name, err=f'{type(exc).__name__}'[:80])
            days[dn] = []
    if any(days.values()):
        src_parts.append('.day')
    if asof_max is None:
        for dn in days:
            if days[dn]:
                asof_max = days[dn][-1][0]
                break

    out: dict = {'kind': kind, 'asof': {}, 'src': '+'.join(dict.fromkeys(src_parts)) or '无',
                 'idx': {}, 'tot': {}}

    def one(name: str, amount: float | None, yoy: float | None,
            series: list[float] | None, missing: dict) -> dict:
        p = weighted_pctile(series[-1], series, cfg['half_life']) if series and len(series) >= 2 else None
        tr = trend_5d(series) if series else None
        lab = judge(p, tr, bands=tuple(cfg['bands']), trend_band=cfg['trend_band'])
        return {'amount': amount, 'yoy': yoy, 'pctile': p, 'trend': tr, 'label': lab,
                'missing': missing}

    rows: dict[str, dict] = {}
    for name, code, qcode, dn in IDX:
        missing: dict = {}
        amount = yoy = None
        if code:
            m = tx.get(code) or {}
            prev_key = str(past[0]) if past else None
            if kind == 'noon':
                amount = _morning(m.get(today_s(now)))
                prev = _morning(m.get(prev_key))
            else:
                amount = _full(m.get(today_s(now)))
                prev = _full(m.get(prev_key))
            if amount is None and kind == 'close':
                # .day 尾行是当天 ⇒ 当天值用本地；**前日也换本地**（同源对比，
                # 避免腾讯/本地两源的口径差混进同比）
                dd = _series_until(days.get(dn) or [], today, win + 2)
                if dd and dd[-1][0] == today:
                    amount = dd[-1][1]
                    prev = dd[-2][1] if len(dd) >= 2 else None
            if amount is None:
                # 兜底：腾讯实时报价 p[37]（收盘后＝全天额；12:10 时＝上午累计）
                try:
                    q = _fetch_quote_day(qcode)
                except Exception as exc:  # noqa: BLE001
                    q = None
                    log(f'vol_snap_quote_fail_{kind}', name=name, err=type(exc).__name__)
                if q is not None:
                    amount = q
                    if prev is None and kind == 'close':
                        # 前一日全天用 .day（同源）；noon 的"前日同针"是 11:30 累计，
                        # .day 给的是全天额，口径对不上 —— 宁可 yoy 缺数，不做假对比
                        ddq = _series_until(days.get(dn) or [], today - 1, win + 2)
                        prev = ddq[-1][1] if ddq else None
            if amount is None:
                missing['amount'] = '分钟源、本地日线与实时报价都缺' if kind == 'close' else '分钟源缺（无当日数据）'
            if amount is not None and (prev is None or prev <= 0):
                yoy = None
                missing.setdefault('yoy', '前一交易日同针缺')
            else:
                yoy = amount / prev - 1 if prev else None
        else:
            # 北证 50：无分钟源（早间那一格永远缺数，明标；缺腿的合计不做假）
            if kind == 'close':
                dd = _series_until(days.get(dn) or [], today, win + 2)
                if dd and dd[-1][0] == today:
                    amount = dd[-1][1]
                    prev = dd[-2][1] if len(dd) >= 2 else None
                elif dd:
                    try:
                        amount = _fetch_quote_day(qcode)
                    except Exception as exc:  # noqa: BLE001
                        amount = None
                        log('vol_snap_quote_fail_close', name=name, err=type(exc).__name__)
                    prev = dd[-1][1] if dd else None   # 前一日全天用 .day（同源最近）
                if amount is None:
                    missing['amount'] = '北证50 .day 未落当天且实时报价取不到'
                else:
                    yoy = amount / prev - 1 if prev else None
                    if yoy is None:
                        missing['yoy'] = '前一日全天缺'
            else:
                missing['amount'] = '北证50无分钟源（早间无读数）'
        # 分位序列：`.day` 里 ≤ asof_max 的尾 win 行（北证也走这条，北证缺 .day ⇒ 位置缺数）
        dd = _series_until(days.get(dn) or [], asof_max or 0, win)
        series = [a for _d, a in dd] if len(dd) >= 5 else None
        if series is None:
            missing.setdefault('pctile', '.day 不足 5 行或未到 asof')
            missing.setdefault('trend', '同上')
            missing.setdefault('label', '同上')
        out['asof'][name] = dd[-1][0] if dd else None
        rows[name] = one(name, amount, yoy, series, missing)

    # 两市合计＝上证+深证 逐日相加（同源 .day，日期天然对齐）
    sh = {d: a for d, a in days.get('上证指数') or []}
    sz = {d: a for d, a in days.get('深证成指') or []}
    common = sorted(set(sh) & set(sz))
    common = [d for d in common if d <= (asof_max or 0)][-win:]
    tot_series = [sh[d] + sz[d] for d in common] if len(common) >= 5 else None
    out['_tot_tail'] = tot_series[-5:] if tot_series else None
    tot_missing: dict = {}
    tot_amount = tot_yoy = None
    if kind == 'noon':
        # 两市早间合计不算（北证无分钟源，缺腿的合计是假数）：位置/趋势/判断仍然在
        tot_missing['amount'] = '两市早间合计不算（北证无分钟源），单指见各指数行'
        tot_missing['yoy'] = '同上'
    elif past:
        # 与单指数同一条优先级：腾讯分钟 15:00 为主，`.day` 已落当天才作兜底
        prev_key = str(past[0])
        a = _full((tx.get('sh000001') or {}).get(today_s(now)))
        b = _full((tx.get('sz399001') or {}).get(today_s(now)))
        pa = _full((tx.get('sh000001') or {}).get(prev_key))
        pb = _full((tx.get('sz399001') or {}).get(prev_key))
        if a is not None and b is not None:
            tot_amount = a + b
            if pa is not None and pb is not None:
                tot_yoy = tot_amount / (pa + pb) - 1
            else:
                tot_missing['yoy'] = '前一日全天缺'
        else:
            dd2 = _series_until(sorted(((d, sh[d] + sz[d]) for d in common), key=lambda x: x[0]),
                                today, win + 2)
            if dd2 and dd2[-1][0] == today:
                tot_amount = dd2[-1][1]
                tot_yoy = tot_amount / dd2[-2][1] - 1 if len(dd2) >= 2 else None
                if tot_yoy is None:
                    tot_missing['yoy'] = '前一日全天缺'
            else:
                tot_missing['amount'] = '沪+深全天都缺（分钟源15:00缺且 .day 未落当天）'
    elif kind == 'close':
        tot_missing['amount'] = '前一交易日判不出（分钟源全挂），全天合计缺'
    rows[TOT] = one(TOT, tot_amount, tot_yoy, tot_series, tot_missing)
    out['asof'][TOT] = common[-1] if common else None
    out['idx'].update(rows)
    out['tot'] = rows[TOT]
    return out


def today_s(now: dt.datetime) -> str:
    return f'{now:%Y%m%d}'


def render_lines(data: dict, *, tpl: dict | None = None) -> list[str]:
    """精简两行（推送核心要义：百分比、位置、判断，不堆绝对亿值）。

    · 同比行：`量能 同比：上证+58% 深证+58% 创业板+61% 科创+70% 北证缺`
    · 位置行：`量能 位置48%(至11-04)·近5日3升1降+31%·中位上行`
    全缺 ⇒ `量能 缺数（主源不可用）`。
    """
    t = dict(_tpl_default())
    if tpl:
        t.update(tpl)
    items = []
    for name, _mc, _qc, _dn in IDX:
        r = data['idx'].get(name) or {}
        if r.get('yoy') is None:
            items.append(t['vol_yoy_item_missing'].format(name=name))
        else:
            items.append(t['vol_yoy_item'].format(name=name, pct=f"{r['yoy']:+.0%}"))
    lines = [t['vol_yoy_line'].format(items=' '.join(items))]
    tot = data['tot'] or {}
    pct = tot.get('pctile')
    asof = data.get('asof', {}).get(TOT)
    if pct is None:
        lines.append(t['vol_pos_missing'].format(why='; '.join(tot.get('missing', {}).values())[:40] or '缺数'))
    else:
        tr = tot.get('trend')
        lines.append(t['vol_pos_line'].format(
            pct=f'{pct * 100:.0f}',
            asof=f'{asof // 100 % 100:02d}-{asof % 100:02d}' if asof else '--',
            pat=pattern_5d(_tot_series(data)) or '--',
            delta=f'{tr:+.0%}' if tr is not None else '缺',
            label=tot.get('label') or '缺',
        ))
    if all('缺' in ln for ln in lines):
        lines = [t['vol_all_missing']]
    return lines


def _tot_series(data: dict) -> list[float] | None:
    """render 要用近 5 日序列画「升/降」—— compute 把合计尾 5 行塞在 `data['_tot_tail']`。"""
    return data.get('_tot_tail')


def _tpl_default() -> dict:
    """templates.json 的默认版（与库里文案同文；文件丢了也能出一版能看的）。"""
    return {
        'vol_yoy_line': '量能 同比：{items}',
        'vol_yoy_item': '{name}{pct}',
        'vol_yoy_item_missing': '{name}缺',
        'vol_pos_line': '量能 位置{pct}%(至{asof})·近5日{pat}{delta}·{label}',
        'vol_pos_missing': '量能 位置缺数（{why}）',
        'vol_all_missing': '量能 缺数（主源不可用）',
    }


# ---------------------------------------------------------------- 落库

def write_snap(kind: str, now: dt.datetime, *, data: dict | None = None,
               note: Callable[[str, dict], None] | None = None) -> int:
    """把一针的六行进 `analytics.index_volume_snap`（幂等 upsert）。返回行数。

    t05 传它已经算好的 `data`（不重取一遍）；t09 不传 ⇒ 这里自己 `compute()`。
    """
    d = data if data is not None else compute(kind, now, note=note)
    rows = []
    day = now.date()
    for name, _mc, _qc, _dn in IDX:
        r = d['idx'][name]
        rows.append(_row(day, kind, name, r, d.get('asof', {}).get(name)))
    r = d['tot']
    rows.append(_row(day, kind, TOT, r, d.get('asof', {}).get(TOT)))
    n = store.upsert_index_volume_snap(rows)
    if note:
        note(f'vol_snap_wrote_{kind}', rows=n)
    return n


def _row(day: dt.date, kind: str, name: str, r: dict, asof: int | None) -> tuple:
    return (
        day, kind, name, SPEC,
        dt.date(asof // 10000, asof // 100 % 100, asof % 100) if asof else None,
        r.get('amount'),
        (r['yoy'] * 100) if r.get('yoy') is not None else None,
        round(r['pctile'], 3) if r.get('pctile') is not None else None,
        (r['trend'] * 100) if r.get('trend') is not None else None,
        r.get('label'),
        None,          # source：写侧不猜 —— 由调用方在 note 里留痕，行内留 None
        json.dumps(r.get('missing') or {}, ensure_ascii=False),
    )
