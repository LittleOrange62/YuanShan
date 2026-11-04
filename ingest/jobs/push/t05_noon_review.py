# -*- coding: utf-8 -*-
"""N1 · 交易日 12:10 午间复盘 —— 外部 `intraday_zt/noon_review.py`（2026-10-30 定版）迁入。

三段正文（判读＝阈值规则贴标签，**不是预测、不含买卖建议**）：
  一、情绪温度：涨跌停/炸板率/连板梯队「X天Y板」/三大指数/几大指数量能/涨跌家数/昨日涨停今日溢价
  二、涨停方向聚集：概念归组（家数占比贴主线/辅线/点状，代表股取连板高·封单大前 3）
  三、下午观察点：把上午的数值嵌进观察条件里

跑法：
    python -m ingest.jobs.push.t05_noon_review --dry-run     # 取数+算+打印，不落库不发送
    python -m ingest.jobs.push.t05_noon_review               # 落 analytics.noon_review / intraday_concept_count
    python -m ingest.jobs.push.t05_noon_review --send        # 再加发送（渠道读系统配置「盘中播报」）

12:10 是午间休市：池子＝上午收盘终态；`analytics.market_breadth` 那时点还是昨日的数，
所以这一炉必须自己取池子（外部定版口径）。**宁缺毋假**：任何一源取不到就在正文里明标缺数，
不拿上一场的值顶、不把 0 当缺数（`up/down` 家数的 0 是真实读数）。
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]      # push/ → jobs/ → ingest/ → 仓库根（挪目录时这一格最容易数错）
sys.path.insert(0, str(ROOT))
try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')      # type: ignore[attr-defined]
except Exception:  # noqa: BLE001
    pass

from app.db import engine                                           # noqa: E402
from app.intraday import sources, store, tasks, volume               # noqa: E402
from app.intraday.pool_events import SPEC_VERSION, money_text, normalize   # noqa: E402
from ingest.jobs.push import t01_zt_watch as zt_watch                                    # noqa: E402  池子出口只有一份

CFG = json.loads((ROOT / 'app' / 'intraday' / 'broadcast_config.json').read_text(encoding='utf-8'))
TPL = json.loads((ROOT / 'app' / 'intraday' / 'templates.json').read_text(encoding='utf-8'))
UA = {"User-Agent": "Mozilla/5.0", "Referer": "https://quote.eastmoney.com/"}
#: 昨日涨停名单的判定容差：与本地日线口径同源（`close >= limit_up - 0.005`）
LIMIT_TOL = 0.005
#: 连板梯队回算窗口（外部定版：近 9 场，够判到 8 天 8 板）
LADDER_LOOKBACK = 9


def _get(url: str, enc: str = 'utf-8') -> str:
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=10) as r:
        return r.read().decode(enc, 'replace')


#: `--dry-run` 时置 True：干跑连失败留痕都不该写（11-03 就是被一次手动 dry-run 与几遍
#: 单元测试往当天的账上写了 23 行"取数失败"，界面与看护一起念这些不存在的事）。
DRY_RUN = False


def _log(now, what, **kw):
    """留痕写不进库不该把播报带崩。"""
    if DRY_RUN:
        return 0
    try:
        store.write_broadcast(now, task='noon', what=what, **kw)
    except Exception:  # noqa: BLE001
        pass


# ---------------------------------------------------------------- 行情
def market_stats() -> tuple[dict, int | None, int | None, str]:
    """三大指数涨跌幅 + 全市场涨跌家数。东财 ulist 一发拿齐，挂了退腾讯（只有指数）。

    ⚠️ 家数那一格**0 是真实读数**：`sum(...) or None` 会把"全场只数 0"吞成缺数，
    这是外部搬入时点名要改的三处之一（见《10-盘中播报与事件流入库》§二.3），别再写回去。
    """
    last_err = ''
    for attempt in (1, 2):
        try:
            u = ("https://push2.eastmoney.com/api/qt/ulist.np/get?fltt=2&invt=2"
                 "&fields=f2,f3,f12,f14,f104,f105,f106&secids=1.000001,0.399001,0.899050")
            diff = (json.loads(_get(u)).get("data") or {}).get("diff") or []
            idx = {it["f14"]: it["f3"] for it in diff if isinstance(it.get("f3"), (int, float))}
            ups = [it["f104"] for it in diff if isinstance(it.get("f104"), (int, float))]
            dns = [it["f105"] for it in diff if isinstance(it.get("f105"), (int, float))]
            if idx:
                return idx, (sum(ups) if ups else None), (sum(dns) if dns else None), "eastmoney_ulist"
        except Exception as exc:  # noqa: BLE001
            last_err = str(exc)[:150]
            _log(dt.datetime.now(), 'noon_em_ulist_fail', err=f'try{attempt} {last_err}')
            if attempt == 1:
                time.sleep(8)
    idx = {}
    try:
        txt = _get("https://qt.gtimg.cn/q=sh000001,sz399001,bj899050", 'gbk')
        for line in txt.split(";"):
            p = line.split("~")
            if len(p) > 32:
                idx[p[1]] = float(p[32])
        return idx, None, None, "tencent_qq(涨跌家数缺)"
    except Exception as exc:  # noqa: BLE001
        _log(dt.datetime.now(), 'noon_tx_fail', err=str(exc)[:150])
        return idx, None, None, f"两源皆挂({last_err[:60] or '无归因'})"


def tencent_quotes(codes6: list[str]) -> dict[str, tuple[str, float]]:
    """6 位代码 → {代码: (名称, 今日涨幅%)}，腾讯批量 60 只一发。"""
    out: dict[str, tuple[str, float]] = {}
    for i in range(0, len(codes6), 60):
        batch = codes6[i:i + 60]
        syms = [("bj" if c[0] in "489" else "sh" if c[0] == "6" else "sz") + c for c in batch]
        try:
            txt = _get("https://qt.gtimg.cn/q=" + ",".join(syms), 'gbk')
        except Exception as exc:  # noqa: BLE001  一发挂了不影响其余批次
            _log(dt.datetime.now(), 'noon_tx_batch_fail', err=str(exc)[:150])
            continue
        for line in txt.split(";"):
            p = line.split("~")
            if len(p) > 32 and p[2]:
                try:
                    out[p[2]] = (p[1], float(p[32]))
                except ValueError:
                    pass
    return out


# ---------------------------------------------------------------- PG 侧计算
def prev_limit_up() -> tuple[dt.date | None, list[str]]:
    """上一交易日涨停名单（收盘触及涨停价，容差见 `LIMIT_TOL`）。"""
    d = engine.scalar("select max(trade_date) from market_data.stock_daily where trade_date < current_date",
                      role='reader')
    if d is None:
        return None, []
    rows = engine.query(
        """select distinct left(security_code,6) c6 from market_data.stock_daily
            where trade_date = %s and limit_up is not null and close >= limit_up - %s""",
        (d, LIMIT_TOL), role='reader') or []
    return d, [r['c6'] for r in rows]


def premium_stats() -> dict:
    d, codes = prev_limit_up()
    if not codes:
        return {"date": d, "n": 0}
    q = tencent_quotes(codes)
    pcts = [q[c][1] for c in codes if c in q]
    if not pcts:
        return {"date": d, "n": len(codes), "quotes": 0}
    pos = sum(1 for x in pcts if x > 0)
    return {"date": d, "n": len(codes), "quotes": len(pcts), "pos": pos,
            "pos_rate": pos / len(pcts), "avg": sum(pcts) / len(pcts)}


def board_labels(zt_rows: list[dict]) -> dict[str, str]:
    """{代码: '3天3板' / '首板' / '2连板'（窗口有数据缺口时的退化写法）}。"""
    codes6 = [r['code'] for r in zt_rows]
    if not codes6:
        return {}
    past_dates = [r['trade_date'] for r in (engine.query(
        "select distinct trade_date from market_data.stock_daily"
        " where trade_date < current_date order by 1 desc limit %s",
        (LADDER_LOOKBACK,), role='reader') or [])]
    if not past_dates:
        return {r['code']: f"{r['board_count']}连板" for r in zt_rows}
    rows = engine.query(
        """select left(security_code,6) c6, trade_date,
                  (limit_up is not null and close >= limit_up - %s) is_zt
             from market_data.stock_daily
            where left(security_code,6) = any(%s::text[]) and trade_date >= %s and trade_date < current_date""",
        (LIMIT_TOL, codes6, min(past_dates)), role='reader') or []
    hist: dict[str, dict] = {}
    for r in rows:
        hist.setdefault(r['c6'], {})[r['trade_date']] = bool(r['is_zt'])
    out: dict[str, str] = {}
    for r in zt_rows:
        lbc = r.get('board_count')
        if lbc is None:
            out[r['code']] = '板数缺数'
            continue
        if lbc <= 1:
            out[r['code']] = "首板"
            continue
        series = [True] + [hist.get(r['code'], {}).get(d, None) for d in past_dates]
        lab = None
        for s in range(2, min(len(series), LADDER_LOOKBACK + 1) + 1):
            window = series[:s]
            if any(w is None for w in window):
                break                        # 窗口有缺口就不硬凑 X天Y板
            if sum(window) == lbc and window[-1]:
                lab = f"{s}天{lbc}板"
                break
        out[r['code']] = lab or f"{lbc}连板"
    return out


def concept_groups(zt_rows: list[dict], lbc_words: dict[str, str],
                   *, max_members: int) -> list[tuple]:
    """涨停股按概念归组：`[(概念名, 只数, 占比str, 梯队判断, 代表股str, share, top_lbc, members)]`。"""
    codes6 = [r['code'] for r in zt_rows]
    if not codes6:
        return []
    groups, unmapped = sources.sector_groups(codes6, max_members=max_members, min_hits=2, limit=5)
    by_code = {r['code']: r for r in zt_rows}
    out = []
    for g in groups:
        hit = sorted((by_code[c] for c in g['members'] if c in by_code),
                     key=lambda r: (-(r.get('board_count') or 0), -(r.get('seal_fund_yuan') or 0)))
        if len(hit) < 2:
            continue
        top_lbc = hit[0].get('board_count') or 0
        share = len(hit) / len(codes6)
        tier = "mainline" if share >= 0.40 else "sub" if share >= 0.20 else "spot"
        note = ("主线候选(家数第一)" if share >= 0.40 else "辅线(家数居前)" if share >= 0.20 else "点状")
        note += f"，梯队最高{top_lbc}板" if top_lbc >= 2 else "，全首板"
        reps = "、".join(f"{r['name']}{lbc_words.get(r['code'], str(r.get('board_count')) + '板')}"
                        for r in hit[:3])
        out.append((g['name'], len(hit), f"{share:.0%}", note, reps, share, top_lbc,
                    g.get('sector_code'), unmapped))
    out.sort(key=lambda x: -x[1])
    return out[:5]


# ---------------------------------------------------------------- 判读（阈值规则，非预测）
def mood(zt_n: int, dt_n: int, zb_n: int, up: int | None, down: int | None) -> tuple[str, str, str]:
    tried = zt_n + zb_n
    zb_rate = zb_n / tried if tried else None
    has_breadth = up is not None and down is not None
    conds = []
    if zt_n < dt_n:
        conds.append(f"涨跌停倒挂({zt_n}:{dt_n})")
    if zb_rate is not None and zb_rate >= 0.35:
        conds.append(f"炸板率{zb_rate:.0%}")
    if has_breadth and up * 5 < down:
        conds.append(f"涨跌比{up / down:.0%}极端弱势" if down else f"涨跌比缺分母(涨{up}/跌{down})")
    if len(conds) >= 3:
        lab = "冰点偏退潮"
    elif len(conds) == 2:
        lab = "退潮分歧"
    elif len(conds) == 1:
        lab = "分歧偏弱"
    elif zt_n >= 2 * max(dt_n, 1) and has_breadth and up > down:
        lab = "偏暖修复"
    else:
        lab = "中性"
    detail = ("、".join(conds) + " → " if conds else "")
    return (detail + lab + "（阈值规则标签，非预测）",
            f"{zb_rate:.0%}" if zb_rate is not None else "缺数",
            f"{1 - zb_rate:.0%}" if zb_rate is not None else "缺数")


# ---------------------------------------------------------------- 主流程
def one_round(*, dry_run: bool, send: bool, channel: str | None, force: bool = False) -> int:
    global DRY_RUN
    DRY_RUN = dry_run
    now = dt.datetime.now()
    if not force:
        if not zt_watch.is_trading_day(now):
            print(f'[{now:%H:%M:%S}] 非交易日 —— 不播不写')
            return 0
    try:
        zt_map, zb_map, dt_map = zt_watch.fetch_pools(now)
    except Exception as exc:  # noqa: BLE001
        text = TPL['noon_fail_notice'].format(t=f'{now:%H:%M:%S}', why=f'池子取不到 {type(exc).__name__}')
        print(text)
        _log(now, 'noon_stale', err=str(exc)[:200])
        if send and not dry_run:
            _send(now, text, channel)
        return 1
    zt = list(zt_map.values())
    zb, dt_rows = list(zb_map.values()), list(dt_map.values())

    idx, up, down, src = market_stats()
    t = f'{now:%H:%M:%S}'
    labels = board_labels(zt)
    prem = premium_stats()
    groups = concept_groups(zt, labels, max_members=int(CFG.get('max_members', 1500)))
    tops = sorted(zt, key=lambda r: (-(r.get('board_count') or 0), -(r.get('seal_fund_yuan') or 0)))
    b2 = [r for r in zt if (r.get('board_count') or 0) == 2]
    mood_txt, zb_pct, seal_pct = mood(len(zt), len(dt_rows), len(zb), up, down)

    L = [TPL['noon_header'].format(t=t, md=f'{now:%m-%d}'), TPL['noon_s1']]
    L.append(f"涨停{len(zt)} · 跌停{len(dt_rows)} · 炸板{len(zb)}（炸板率{zb_pct} 封板率{seal_pct}）")
    if tops:
        top = tops[0]
        L.append(f"最高板：{top['name']} {labels.get(top['code'], str(top.get('board_count')) + '板')}"
                 f"（{money_text(top.get('seal_fund_yuan'))}）")
    else:
        L.append("最高板：—")
    if b2:
        names = "、".join(r['name'] for r in b2[:8]) + ("…" if len(b2) > 8 else "")
        L.append(f"{len(b2)}只二板：{names}")
    L.append("指数：" + (" ".join(f"{k}{v:+.2f}%" for k, v in idx.items()) or "指数缺数"))
    # 出处不再拼进正文（用户 11-03：最后的来源说明不用发送）—— 它进留痕表的
    # `index_source` 与 `body`，事后要查"这一节的数是哪来的"仍然查得到，只是不占群里一行。
    vol_lines, vol_src = volume.volume_lines(now, note=lambda w, kw: _log(now, w, **kw))
    L.extend(vol_lines)
    if up is None or down is None:
        L.append(f"涨跌家数缺数〔{src}〕")
    else:
        L.append(f"涨跌家数：涨{up}/跌{down}（{f'{up / down:.0%}' if down else '跌家数为0'}）〔{src}〕")
    if prem.get("quotes"):
        L.append(f"昨日({prem['date']:%m-%d})涨停{prem['n']}只：今日正溢价{prem['pos']}只"
                 f"({prem['pos_rate']:.1%}) 平均{prem['avg']:+.2f}%")
    elif prem.get("n"):
        L.append(f"昨日({prem['date']:%m-%d})涨停{prem['n']}只：今日行情未取到，溢价缺数")
    else:
        L.append("昨日涨停名单缺数（PG未更新）")
    L.append(f"判读：{mood_txt}")
    mood_body = L[L.index(TPL['noon_s1']) + 1:]

    L.append(TPL['noon_s2'])
    if groups:
        for i, g in enumerate(groups, 1):
            L.append(f"{i}.{g[0]}×{g[1]}({g[2]}) {g[3]}")
            L.append(f"    代表：{g[4]}")
    else:
        L.append("（无 ≥2 只的概念组）")
    cluster_body = L[L.index(TPL['noon_s2']) + 1:]

    L.append(TPL['noon_s3'])
    watch_from = len(L)
    if tops:
        L.append(f"· 最高板 {tops[0]['name']}（{money_text(tops[0].get('seal_fund_yuan'))}）"
                 "午后封单走向＝情绪风向标：封住续高度，炸板则梯队坍塌")
    if b2:
        L.append(f"· 二板{len(b2)}只（{'、'.join(r['name'] for r in b2[:4])}…）："
                 "分歧后回流封板=主线续命，被砸=退潮确认")
    L.append(f"· 跌停上午{len(dt_rows)}家：午后收敛+翘尾=冰点弱修复，继续扩散=退潮加深")
    if idx:
        k0 = next(iter(idx))
        L.append(f"· {k0}{idx[k0]:+.1f}%：午后跌幅收窄给接力安全边际，续跌则观望为主")
    L.append(f"· {groups[0][0] if groups else '存量热点'}以外若出现新题材涨停＝资金试切换，值得关注")
    watch_body = L[watch_from:]

    text = "\n".join(L)
    print(text)

    missing = {}
    if not idx:
        missing['index'] = '两源皆挂'
    if up is None or down is None:
        missing['breadth'] = f'涨跌家数缺数（源 {src}）'
    if not prem.get("n"):
        missing['premium'] = '昨日涨停名单缺数'
    if any('缺数' in x for x in vol_lines):
        missing['volume'] = '量能节部分缺数'
    if dry_run:
        print('（dry-run：未落库、未发送）')
        return 0

    # 正文里不发的那句来源说明落到这里：出处仍然在库里（`index_source`），只是不占群里一行
    idx_src = "｜".join(x for x in (src, f"量能:{vol_src}" if vol_src else "") if x)

    noon_row = store.noon_row({
        'trade_date': now.date(), 'spec_version': SPEC_VERSION,
        'limit_up_n': len(zt), 'limit_down_n': len(dt_rows), 'unseal_n': len(zb),
        'unseal_rate_pct': (len(zb) / (len(zt) + len(zb))) if (len(zt) + len(zb)) else None,
        'max_board_text': (labels.get(tops[0]['code']) if tops else None),
        'max_board_count': (tops[0].get('board_count') if tops else None),
        'board2_codes': "、".join(r['name'] for r in b2[:8]) if b2 else None,
        'idx_sh_pct': _idx_pct(idx, "上证指数"), 'idx_sz_pct': _idx_pct(idx, "深证成指"),
        'idx_bj_pct': _idx_pct(idx, "北证50"),
        'up_count': up, 'down_count': down,
        'premium_n': prem.get("n"), 'premium_pos_n': prem.get("pos"),
        'premium_pos_rate_pct': (prem['pos_rate'] * 100) if prem.get("pos_rate") is not None else None,
        'premium_avg_pct': prem.get("avg"),
        'mood_label': mood_txt.split("（")[0], 'mood_tags': mood_txt,
        'section_mood': "\n".join(mood_body), 'section_cluster': "\n".join(cluster_body),
        'section_watch': "\n".join(watch_body), 'body': text,
        'pool_source': 'eastmoney_push2ex', 'index_source': idx_src,
        'ladder_basis': f'stock_daily 近{LADDER_LOOKBACK}场回算（容差{LIMIT_TOL}）',
        'delivered': None,
        'missing': json.dumps(missing, ensure_ascii=False),
    })
    store.upsert_noon(noon_row)
    _upsert_noon_concepts(now, groups)
    store.write_broadcast(now, task='noon', what='noon', body=text,
                          counts={'zt_n': len(zt), 'dt_n': len(dt_rows), 'zb_n': len(zb),
                                  'groups': len(groups)})
    if send:
        _send(now, text, channel)
    return 0


def _idx_pct(idx: dict, name: str) -> float | None:
    v = idx.get(name)
    return float(v) if isinstance(v, (int, float)) else None


def _upsert_noon_concepts(now: dt.datetime, groups: list[tuple]) -> None:
    """午间归组也落 `intraday_concept_count`（bucket=noon_zt），与竞价/热度共用一张表一份口径。"""
    if not groups:
        return
    pcts = sources.sector_pcts([g[0] for g in groups])
    rows = []
    for i, g in enumerate(groups, 1):
        name, hit_n, _share_s, _note, reps, share, top_lbc, sector_code, unmapped = g
        got = pcts.get(name)
        rows.append((now.date(), now, 'noon_zt', name, SPEC_VERSION,
                     sector_code, sector_code is None, hit_n, i,
                     round(share * 100, 4),
                     "mainline" if share >= 0.40 else "sub" if share >= 0.20 else "spot",
                     top_lbc, reps,
                     got[0] if got else None, got[1] if got else None, unmapped))
    store.upsert_concept_counts(rows)


def _send(now, text, channel):
    from duanxian import notify
    keys = tasks.send_channels(channel)
    res = notify.send_to(keys, '午间复盘', text)
    store.write_broadcast(now, task='noon', what='sent', body=text,
                          ok=bool(res.get('ok')), delivered=bool(res.get('ok')),
                          channel_key=','.join(keys),
                          err=None if res.get('ok') else str(res.get('detail'))[:300])
    print(f'  发送 → {",".join(keys)}：{"已确认" if res.get("ok") else "未确认"}')


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description='N1 · 午间复盘（12:10 单次）')
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--send', action='store_true')
    ap.add_argument('--channel', default=None,
                    help='逗号分隔的渠道串；不给就读系统配置「盘中播报」那一格')
    ap.add_argument('--force', action='store_true', help='跳过交易日判断（手工看数）')
    a = ap.parse_args(argv)
    return one_round(dry_run=a.dry_run, send=a.send, channel=a.channel, force=a.force)


if __name__ == '__main__':
    raise SystemExit(main())
