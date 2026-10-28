# -*- coding: utf-8 -*-
"""noon_review.py — 交易日 12:10 午间复盘 → 盯盘群（单次脚本，任务唤起）

内容（2026-10-23 牛哥定版）：一、情绪温度（涨跌停/炸板率/连板梯队X天Y板/三大指数/
涨跌家数/昨日涨停今日溢价）；二、涨停方向聚集（概念分组+代表股+梯队判断）；
三、下午观察点（把上午数值嵌进观察条件里）。

数据源：东财 push2ex 涨/跌/炸板池；东财 ulist 指数+涨跌家数（备源腾讯，仅指数）；
昨日涨停名单=PG stock_daily 收盘触及涨停价（涨停口径同洪杰文档），今日涨幅=腾讯批量行情。
判读=脚本阈值规则贴标签（分类不是预测，不含买卖建议）。宁缺毋假：缺数分项明标。
模板 templates.json（noon_* 键），日志 var/zt_events.jsonl（what=noon）。

用法：python noon_review.py --dry-run [--force]
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.request
from datetime import date, datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import psycopg

import auction_report as ar
import zt_watcher as zw

UA = {"User-Agent": "Mozilla/5.0", "Referer": "https://quote.eastmoney.com/"}


def get(url: str, enc: str = "utf-8") -> str:
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=10) as r:
        return r.read().decode(enc, "replace")


# ---------------------------------------------------------------- 行情
def market_stats() -> tuple[dict, int | None, int | None, str]:
    """三大指数{名称:涨跌幅} + 全市场涨跌家数。东财 ulist 一发拿齐（push2 有风控，断连冷却8s重试一次）；
    挂了退腾讯（只有指数，家数标缺）。"""
    import time as _t
    last_err = ""
    for attempt in (1, 2):
        try:
            u = ("https://push2.eastmoney.com/api/qt/ulist.np/get?fltt=2&invt=2"
                 "&fields=f2,f3,f12,f14,f104,f105,f106&secids=1.000001,0.399001,0.899050")
            diff = (json.loads(get(u)).get("data") or {}).get("diff") or []
            idx = {it["f14"]: it["f3"] for it in diff if isinstance(it.get("f3"), (int, float))}
            up = sum(it.get("f104") or 0 for it in diff) or None
            down = sum(it.get("f105") or 0 for it in diff) or None
            if idx:
                return idx, up, down, "东财ulist"
        except Exception as exc:
            last_err = str(exc)[:120]
            zw.log({"what": "noon_em_ulist_fail", "try": attempt, "err": last_err})
            if attempt == 1:
                _t.sleep(8)
    idx = {}
    try:
        txt = get("https://qt.gtimg.cn/q=sh000001,sz399001,bj899050", "gbk")
        for line in txt.split(";"):
            p = line.split("~")
            if len(p) > 32:
                idx[p[1]] = float(p[32])
        return idx, None, None, "腾讯(涨跌家数缺)"
    except Exception as exc:
        zw.log({"what": "noon_tx_fail", "err": str(exc)[:150]})
        return idx, None, None, "两源皆挂"


def tencent_quotes(codes6: list[str]) -> dict[str, tuple[str, float]]:
    """6位代码 → {代码: (名称, 今日涨幅%)}。腾讯批量，60只/请求。"""
    out: dict[str, tuple[str, float]] = {}
    for i in range(0, len(codes6), 60):
        batch = codes6[i:i + 60]
        syms = []
        for c in batch:
            pre = "bj" if c[0] in "489" else ("sh" if c[0] == "6" else "sz")
            syms.append(pre + c)
        try:
            txt = get("https://qt.gtimg.cn/q=" + ",".join(syms), "gbk")
        except Exception as exc:
            zw.log({"what": "noon_tx_batch_fail", "err": str(exc)[:150]})
            continue
        for line in txt.split(";"):
            p = line.split("~")
            if len(p) > 32 and p[2]:
                try:
                    out[p[2]] = (p[1], float(p[32]))
                except ValueError:
                    pass
    return out


# ---------------------------------------------------------------- PG 计算
def prev_limit_up(cur) -> tuple[date | None, list[str]]:
    """昨日（stock_daily 最新日）涨停名单：收盘触及涨停价（洪杰口径的容差版）。"""
    cur.execute("select max(trade_date) from market_data.stock_daily where trade_date < current_date")
    d = cur.fetchone()[0]
    if d is None:
        return None, []
    cur.execute("""select distinct left(security_code,6) from market_data.stock_daily
                    where trade_date = %s and limit_up is not null and close >= limit_up - 0.005""", (d,))
    return d, [r[0] for r in cur.fetchall()]


def premium_stats(cur) -> dict:
    d, codes = prev_limit_up(cur)
    if not codes:
        return {"date": d, "n": 0}
    q = tencent_quotes(codes)
    pcts = [q[c][1] for c in codes if c in q]
    if not pcts:
        return {"date": d, "n": len(codes), "quotes": 0}
    pos = sum(1 for x in pcts if x > 0)
    return {"date": d, "n": len(codes), "quotes": len(pcts), "pos": pos,
            "pos_rate": pos / len(pcts), "avg": sum(pcts) / len(pcts)}


def board_labels(cur, zt_rows: list[dict]) -> dict[str, str]:
    """{代码: '3天3板'/'首板'/'2连板'(数据不足退化的写法)}。今日按在池记涨停日。"""
    codes6 = [r["code"] for r in zt_rows]
    if not codes6:
        return {}
    cur.execute("""select distinct trade_date from market_data.stock_daily
                    where trade_date < current_date order by 1 desc limit 9""")
    past_dates = [r[0] for r in cur.fetchall()]           # 新→旧
    if not past_dates:
        return {r["code"]: f"{r['lbc']}连板" for r in zt_rows}
    cur.execute("""select left(security_code,6), trade_date,
                          (limit_up is not null and close >= limit_up - 0.005)
                    from market_data.stock_daily
                    where left(security_code,6) = any(%s::text[]) and trade_date >= %s
                      and trade_date < current_date""", (codes6, min(past_dates)))
    hist: dict[str, dict] = {}
    for c, d, is_zt in cur.fetchall():
        hist.setdefault(c, {})[d] = bool(is_zt)
    out = {}
    for r in zt_rows:
        lbc = r["lbc"]
        if lbc <= 1:
            out[r["code"]] = "首板"
            continue
        series = [True] + [hist.get(r["code"], {}).get(d, None) for d in past_dates]
        label = None
        for s in range(2, min(len(series), 9) + 1):
            window = series[:s]
            if any(w is None for w in window):
                break                                      # 有数据缺口，窗口不完整
            if sum(window) == lbc and window[-1]:          # s天内恰好lbc板且首日也是板
                label = f"{s}天{lbc}板"
                break
        out[r["code"]] = label or f"{lbc}连板"
    return out


def concept_groups(cur, zt_rows: list[dict], max_members: int, lbc_words: dict) -> list[tuple]:
    """涨停股按概念归组：[(概念名, 只数, 占比str, 梯队判断, 代表股str)]，家数降序前5。
    注意：本函数所有 psycopg 结果必须在被其他查询复用 cursor 前先 fetchall。"""
    codes6 = [r["code"] for r in zt_rows]
    if not codes6:
        return []
    cur.execute("""
        with big as (select sector_code from reference_data.sector_member
                      group by 1 having count(distinct security_code) > %s)
        select i.name, array_agg(distinct left(m.security_code,6))
          from reference_data.sector_member m
          join reference_data.sector_info i using (sector_code)
         where left(m.security_code,6) = any(%s::text[])
           and i.sector_type = '概念' and coalesce(i.is_active, true)
           and m.sector_code not in (select sector_code from big)
         group by 1""", (max_members, codes6))
    fetched = cur.fetchall()                               # 先取空再复用 cursor，否则被 board_labels 冲掉
    by_code = {r["code"]: r for r in zt_rows}
    groups = []
    for name, members in fetched:
        hit = sorted((by_code[c] for c in members if c in by_code),
                     key=lambda r: (-r["lbc"], -(r["fund"] or 0)))
        if len(hit) < 2:
            continue
        top_lbc = hit[0]["lbc"]
        share = len(hit) / len(codes6)
        note = ("主线候选(家数第一)" if share >= 0.40 else
                "辅线(家数居前)" if share >= 0.20 else "点状") + \
               (f"，梯队最高{top_lbc}板" if top_lbc >= 2 else "，全首板")
        reps = "、".join(f"{r['name']}{lbc_words.get(r['code'], str(r['lbc']) + '板')}" for r in hit[:3])
        groups.append((name, len(hit), f"{share:.0%}", note, reps))
    groups.sort(key=lambda g: -g[1])
    return groups[:5]


# ---------------------------------------------------------------- 判读（阈值规则，非预测）
def mood(zt_n: int, dt_n: int, zb_n: int, up: int | None, down: int | None) -> tuple[str, str]:
    tried = zt_n + zb_n
    zb_rate = zb_n / tried if tried else None
    conds = []
    if zt_n < dt_n:
        conds.append(f"涨跌停倒挂({zt_n}:{dt_n})")
    if zb_rate is not None and zb_rate >= 0.35:
        conds.append(f"炸板率{zb_rate:.0%}")
    if up and down and up * 5 < down:
        conds.append(f"涨跌比{up / down:.0%}极端弱势")
    if len(conds) >= 3:
        label = "冰点偏退潮"
    elif len(conds) == 2:
        label = "退潮分歧"
    elif len(conds) == 1:
        label = "分歧偏弱"
    elif zt_n >= 2 * max(dt_n, 1) and up and down and up > down:
        label = "偏暖修复"
    else:
        label = "中性"
    detail = ("、".join(conds) + " → " if conds else "")
    return detail + label + "（阈值规则标签，非预测）", (f"{zb_rate:.0%}" if zb_rate is not None else "缺数",
                                                    f"{1 - zb_rate:.0%}" if zb_rate is not None else "缺数")


# ---------------------------------------------------------------- 主流程
def run_once(cfg: dict, tpl: dict, dry: bool) -> int:
    now = datetime.now()
    t = now.strftime("%H:%M:%S")
    ymd = now.strftime("%Y%m%d")
    try:
        zt = [zw.normalize(r) for r in zw.fetch_pool("getTopicZTPool", ymd)]
        zb = [zw.normalize(r) for r in zw.fetch_pool("getTopicZBPool", ymd)]
        dt = [zw.normalize(r) for r in zw.fetch_pool("getTopicDTPool", ymd, sort="fund:asc")]
    except Exception as exc:
        text = tpl["noon_fail_notice"].format(t=t, why=f"池子取不到 {type(exc).__name__}")
        zw.log({"what": "noon_stale", "err": str(exc)[:200]})
        if not dry:
            zw.broadcast(cfg, text)
        return 1

    idx, up, down, src = market_stats()
    L = [tpl["noon_header"].format(t=t, md=f"{now:%m-%d}")]
    L.append(tpl["noon_s1"])

    conn = psycopg.connect(ar.dsn_reader(), connect_timeout=8)
    cur = conn.cursor()
    try:
        labels = board_labels(cur, zt)
        prem = premium_stats(cur)
        groups = concept_groups(cur, zt, int(cfg.get("max_members", 1500)), labels)
    finally:
        conn.close()

    tops = sorted(zt, key=lambda r: (-r["lbc"], -(r["fund"] or 0)))
    b2 = [r for r in zt if r["lbc"] == 2]
    mood_txt, (zb_pct, seal_pct) = mood(len(zt), len(dt), len(zb), up, down)

    idx_txt = " ".join(f"{k}{v:+.2f}%" for k, v in idx.items()) or "指数缺数"
    L.append(f"涨停{len(zt)} · 跌停{len(dt)} · 炸板{len(zb)}（炸板率{zb_pct} 封板率{seal_pct}）")
    if tops:
        top = tops[0]
        L.append(f"最高板：{top['name']} {labels.get(top['code'], str(top['lbc']) + '板')}"
                 f"（{zw.money(top['fund'])}）")
    else:
        L.append("最高板：—")
    if b2:
        names = "、".join(r["name"] for r in b2[:8]) + ("…" if len(b2) > 8 else "")
        L.append(f"{len(b2)}只二板：{names}")
    L.append(f"指数：{idx_txt}")
    L.append(f"涨跌家数：{f'涨{up}/跌{down}（{up/down:.0%}）' if up and down else '涨跌家数缺数'}〔{src}〕")
    if prem.get("quotes"):
        L.append(f"昨日({prem['date']:%m-%d})涨停{prem['n']}只：今日正溢价{prem['pos']}只"
                 f"({prem['pos_rate']:.1%}) 平均{prem['avg']:+.2f}%")
    elif prem.get("n"):
        L.append(f"昨日({prem['date']:%m-%d})涨停{prem['n']}只：今日行情未取到，溢价缺数")
    else:
        L.append("昨日涨停名单缺数（PG未更新）")
    L.append(f"判读：{mood_txt}")

    L.append(tpl["noon_s2"])
    if groups:
        for i, (name, n, share, note, reps) in enumerate(groups, 1):
            L.append(f"{i}.{name}×{n}({share}) {note}")
            L.append(f"    代表：{reps}")
    else:
        L.append("（无 ≥2 只的概念组）")

    L.append(tpl["noon_s3"])
    if tops:
        L.append(f"· 最高板 {tops[0]['name']}（{zw.money(tops[0]['fund'])}）午后封单走向＝情绪风向标：封住续高度，炸板则梯队坍塌")
    if b2:
        L.append(f"· 二板{len(b2)}只（{ '、'.join(r['name'] for r in b2[:4]) }…）：分歧后回流封板=主线续命，被砸=退潮确认")
    L.append(f"· 跌停上午{len(dt)}家：午后收敛+翘尾=冰点弱修复，继续扩散=退潮加深")
    if idx:
        k0 = next(iter(idx))
        L.append(f"· {k0}{idx[k0]:+.1f}%：午后跌幅收窄给接力安全边际，续跌则观望为主")
    g0 = groups[0][0] if groups else "存量热点"
    L.append(f"· {g0}以外若出现新题材涨停＝资金试切换，值得关注")

    L.append(tpl["noon_footer"].format(t=t))
    text = "\n".join(L)

    zw.log({"what": "noon", "zt": len(zt), "dt": len(dt), "zb": len(zb), "text": text})
    if dry:
        print("[dry-run]\n" + text)
        return 0
    zw.broadcast(cfg, text)
    print(f"[{t}] 午间复盘已推送（涨停{len(zt)}/跌停{len(dt)}/炸板{len(zb)}）")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()

    cfg, tpl = ar.load_cfg(), ar.load_tpl()
    if not args.force:
        try:
            with psycopg.connect(ar.dsn_reader(), connect_timeout=8) as conn, conn.cursor() as cur:
                if not ar.is_trade_day(cur):
                    return 0
        except Exception as exc:
            if "connection" in str(exc).lower() and date.today().weekday() >= 5:
                return 0
    try:
        return run_once(cfg, tpl, args.dry_run)
    except Exception as exc:
        zw.log({"what": "noon_crash", "err": f"{type(exc).__name__}: {exc}"[:300]})
        try:
            zw.send_feishu(*zw.load_channel(cfg["channel"]),
                           f"【午间复盘 {datetime.now():%H:%M:%S}】任务异常退出：{type(exc).__name__}")
        except Exception:
            pass
        raise


if __name__ == "__main__":
    sys.exit(main())
