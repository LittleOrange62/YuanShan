"""资金轮动（**份额口径**）的本地读层 —— 只读 `analytics.sector_flow_trend`。

为什么这一屏只能读这张表，而不是读 `market_data.fund_flow_rank` 的当日净额：

  · 实测（1249 场 × 963 板块，脚本 `.local/flow_share_test.py`，结论在
    `docs/数据加工工作指南/08` 坑 12）：**份额连续 n 场抬升 → 第 n+1 场仍抬升**的概率
    从 46%(n=1) 单调掉到 31%(n=9)，而基线本来就是 46% —— 越"连续"越不延续，是均值回复不是惯性。
    所以这一层只交**统计量**（连续场次 / 近 10·20·30 场累计 / 名次），
    不交方向判断，界面也不许把它包装成"主力连续吸金"。

  · 反面教材：用"绝对成交额 ÷ 自身 20 场均额"测连续性会得出 73%→83% 的"延续"，
    看着像资金有惯性，其实全是全场共同放量的自相关。`value` 存的是份额就是这个原因。

  · 参数（窗口 / spec_version / 实证串）**从 `config.setting` 那一行取**，
    不在这里写死，也不按 `max(spec_version)` 字符串比（出 sft-10 会翻车）。
"""

from __future__ import annotations

import datetime as dt

from duanxian import pg

TABLE = "analytics.sector_flow_trend"
PARAMS_KEY = "sector_flow_trend.params"

# 每个窗口两端各交多少个板块；曲线只给两端榜头各前若干条 —— 963 条线画出来
# 没有一张图能读，而"为什么我的板块没有线"必须有明确答案，所以这两个数随结果一起交出。
TOP_PER_WINDOW = 15
SERIES_PER_BOARD = 3
SERIES_SESSIONS = 30


def _active_params() -> dict:
    """生效参数（窗口、basis、spec_version、实证串）。读不到就返回空 dict，由调用方出声。"""
    df = pg.fetch_df("select value from config.setting where key = %s", (PARAMS_KEY,))
    if df is None or not len(df):
        return {}
    v = df.iloc[0]["value"]
    if isinstance(v, str):
        import json
        try:
            v = json.loads(v)
        except Exception:  # noqa: BLE001
            return {}
    return dict(v) if isinstance(v, dict) else {}


def _num(v):
    """numeric → float；NULL/NaN → None。numpy 标量必须落成 Python 类型，
    否则 `json.dumps` 会在 `int64` 上抛 not JSON serializable（NaN 更坏：直接变 `NaN` 字面量）。"""
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if f != f else f


def _int(v):
    f = _num(v)
    return None if f is None else int(f)


def _text(v):
    """字符串列的 NULL 读法：`left join reference_data.sector_info` 没命中时 pandas 给的是
    NaN 而不是 None，`str(NaN)` 会产出字面量 'nan' —— 那既不是"未登记"也不是任何类型，
    会一路穿到界面上变成"类型：nan"。缺就是缺，交 None。"""
    if v is None:
        return None
    try:
        if v != v:              # NaN / NaT
            return None
    except (TypeError, ValueError):
        pass
    s = str(v).strip()
    return None if s in ("", "nan", "None", "<NA>") else s


def _iso(x) -> str | None:
    if x is None:
        return None
    if isinstance(x, (dt.date, dt.datetime)):
        return x.isoformat()
    s = str(x)
    return s[:10] if s else None


BASIS_SPEC_KEY = {"share": "spec_version", "turnover": "turnover_spec_version"}


def rotation_board(date: str | None = None, basis: str | None = None) -> dict:
    """最新一场（或指定那场）的轮动榜 + 连续场次统计 + 头部板块的曲线。

    `basis` 两档：`share`=份额（默认）、`turnover`=换手强度（2026-10-12 加的第二把尺子）。
    **各自的判据版本号从参数行里各取各的**（`spec_version` / `turnover_spec_version`）——
    两条口径共用一个版本号会让"改了换手"把份额那批行一起带走，历史就对不上账了。
    不认识的 basis 直接说清可选值，**不回落默认**（回落会把"我打错了"显示成一份看起来正常的榜）。

    返回 `available:False` 的三种情况都要能分辨：PG 没配、参数行没有、那场没有行。
    指定日期没数时**绝不回落到邻近交易日** —— 那样页面会把 09-29 的榜当成 09-30 的。
    """
    if not pg.enabled():
        return {"available": False,
                "reason": "份额轮动读本地库 analytics.sector_flow_trend；"
                          "未配置 .env 的 PG_USER/PG_PASSWORD 时没有这一项"}
    p = _active_params()
    basis = str(basis or (p.get("basis") or ["share"])[0] or "share").strip() or "share"
    if basis not in BASIS_SPEC_KEY:
        return {"available": False, "basis": basis,
                "reason": f"这一档口径本地没有（basis={basis}）；可选："
                          f"{'、'.join(sorted(BASIS_SPEC_KEY))}。不拿另一档顶上"}
    spec = str(p.get(BASIS_SPEC_KEY[basis]) or "").strip()
    if not spec:
        return {"available": False, "basis": basis,
                "reason": f"读不到生效参数行 config.setting.{PARAMS_KEY} 里 {basis} 的判据版本"
                          f"（键 {BASIS_SPEC_KEY[basis]} 为空），不知道该按哪个版本取数（不猜 max(spec_version)）"}
    windows = [int(w) for w in (p.get("windows") or [10, 20, 30])]

    if date:
        day = pg.ymd(date)
    else:
        df = pg.fetch_df(
            f"select max(trade_date)::text as d from {TABLE} where basis = %s and spec_version = %s",
            (basis, spec))
        day = (df.iloc[0]["d"] if df is not None and len(df) else None)
        if not day:
            return {"available": False, "basis": basis, "spec_version": spec,
                    "reason": f"本地库里 basis={basis}、判据版本 {spec} 一行都还没有"
                              "（这张表由数据加工侧按场写入，不是界面侧现算）"}

    rows_df = pg.fetch_df(
        f"""
        select t.sector_code,
               coalesce(nullif(si.name, ''), t.sector_code) as name,
               si.sector_type,
               t.value::float8 as share, t.value_prev::float8 as share_prev,
               t.day_delta_bps::float8 as delta_bps,
               t.streak_up_days as streak_up, t.streak_dn_days as streak_dn,
               t.cum_10::float8 as cum_10, t.cum_20::float8 as cum_20, t.cum_30::float8 as cum_30,
               t.days_10, t.days_20, t.days_30,
               t.rank_10, t.rank_20, t.rank_30,
               t.universe, t.universe_amount::float8 as universe_amount,
               t.amount_yuan::float8 as amount_yuan,
               t.mv_yuan::float8 as mv_yuan, t.amount_matched::float8 as amount_matched,
               t.members_matched, t.members_total,
               t.reasons ->> 'evidence' as ev,
               t.calculated_at, extract(epoch from (now() - t.calculated_at))::int as age_seconds
        from {TABLE} t
        left join reference_data.sector_info si using (sector_code)
        where t.trade_date = %s and t.basis = %s and t.spec_version = %s
        order by t.rank_30 nulls last, t.sector_code
        """,
        (day, basis, spec))
    if rows_df is None or not len(rows_df):
        return {"available": False, "date": day, "basis": basis, "spec_version": spec,
                "reason": f"本地库 {day} 这一场没有 {basis} 口径的轮动数据"
                          "（指定日期不做邻近交易日回落）"}

    raw = rows_df.to_dict("records")
    first = raw[0]
    # 每行都一样的分母/时间只交一次（963 行重复带 = 白白的几十 KB）
    universe = _int(first.get("universe"))
    universe_amount = _num(first.get("universe_amount"))
    calculated_at = str(first.get("calculated_at") or "") or None
    age_seconds = _int(first.get("age_seconds"))

    rows = []
    for r in raw:
        rows.append({
            "sector_code": str(r["sector_code"]),
            "name": _text(r.get("name")) or str(r["sector_code"]),
            "sector_type": _text(r.get("sector_type")),
            "share": _num(r.get("share")), "share_prev": _num(r.get("share_prev")),
            "delta_bps": _num(r.get("delta_bps")),
            "streak_up": _int(r.get("streak_up")), "streak_dn": _int(r.get("streak_dn")),
            "cum_10": _num(r.get("cum_10")), "cum_20": _num(r.get("cum_20")), "cum_30": _num(r.get("cum_30")),
            "days_10": _int(r.get("days_10")), "days_20": _int(r.get("days_20")), "days_30": _int(r.get("days_30")),
            "rank_10": _int(r.get("rank_10")), "rank_20": _int(r.get("rank_20")), "rank_30": _int(r.get("rank_30")),
            "amount_yuan": _num(r.get("amount_yuan")),
            # 只有 basis=turnover 的行有值：分母（本板块成分流通市值）、分子（匹配成员成交额和）、
            # 剔了多少成员。share 行这四列是 NULL —— 界面按 basis 决定显不显示，不许拿 0 填。
            "mv_yuan": _num(r.get("mv_yuan")), "amount_matched": _num(r.get("amount_matched")),
            "members_matched": _int(r.get("members_matched")), "members_total": _int(r.get("members_total")),
        })

    # 实证句从**行里**读（params_snapshot/reasons 是算出来那一刻存的），而不是从参数行现读：
    # 参数事后被人改过，页面上那句"实测 46%→31%"就会与榜上的数脱节。读不到再退回参数行。
    evidence_row = _text(raw[0].get("ev")) if raw else None

    # 母体口径与**加工侧同一份**：参数行里的 `sector_types`（默认 行业+概念）。
    # 2026-10-06 之前这里是"来者不拒"，于是 880001~880099 那批市场级统计指数
    # （总市值 / 涨跌家数 / 主板总值…，成交额 = 全 A 成交额）天天霸占份额榜的头两名，
    # 而它们的中文名叫不上、看着像"字典缺名字的板块"。库里已把它们改判成 `sector_type='统计'`，
    # 这一层按正列表过滤；`flowtrend` 也在 2026-10-06 全量重跑过 1,252 场，
    # 所以份额分母与名次都是在 915 母体上算的（那批旧行仍物理留在表里，读侧挡住）。
    # 为什么不在这里按代码段排除：下一个混进来的市场级指数就躲过去了 ——
    # 判据只能是"登记成什么类型"。
    allowed = [str(t) for t in (p.get("sector_types") or ["行业", "概念"])]
    kept, dropped = [], {}
    for r in rows:
        if r["sector_type"] in allowed:
            kept.append(r)
        else:
            key = r["sector_type"] or "未登记"
            dropped[key] = dropped.get(key, 0) + 1
    rows = kept

    # 连续场次的分布：这是"统计"，不是"信号"。两边分开数，读者自己看多少板块在续、多少在断。
    up_hist: dict[int, int] = {}
    dn_hist: dict[int, int] = {}
    for r in rows:
        for key, hist in (("streak_up", up_hist), ("streak_dn", dn_hist)):
            n = r.get(key)
            if n and n > 0:
                hist[n] = hist.get(n, 0) + 1

    # 名字就是编号的那批（`reference_data.sector_info` 里这几个板块没登记中文名）：
    # 界面要能说出"这不是我截断了名字，是库里本来只有编号"，所以数一下随结果交出。
    name_is_code = sum(1 for r in rows if str(r["name"]).isdigit())

    # 每个窗口各交两端：这一场里份额累计抬升最多的一批、与回落最多的一批。
    # 两端都按**名次**取而不是按正负取 —— 普涨日两端都可能是正的，
    # 那也正是"份额口径"和"净流入"的区别，界面不能把它写成"资金流入"。
    boards = []
    for w in windows:
        rk = f"rank_{w}"
        ranked = sorted((r for r in rows if r.get(rk) is not None), key=lambda r: r[rk])
        rising = ranked[:TOP_PER_WINDOW]
        rising_codes = {r["sector_code"] for r in rising}
        # 流出端从榜尾往前取，并剔掉与流入端重叠的那些（母体小 / 只有几场时会重叠，
        # 重叠了还不剔，同一个个板块会在同一屏的两边各出现一次，看着像两个数）
        falling = [r for r in reversed(ranked) if r["sector_code"] not in rising_codes][:TOP_PER_WINDOW]
        boards.append({
            "window": w,
            "rising": rising,
            "falling": falling,
            "ranked": len(ranked),          # 这一档有名次的板块数（可以小于 universe）
            "thin": len(ranked) < w,        # 连窗口长度都攒不满
        })

    series_codes: list[str] = []
    for b in boards:
        for r in b["rising"][:SERIES_PER_BOARD]:
            if r["sector_code"] not in series_codes:
                series_codes.append(r["sector_code"])
    series = _series(series_codes, day, basis, spec)
    with_series = {s["code"] for s in series}
    for b in boards:
        for r in b["rising"] + b["falling"]:
            r["has_series"] = r["sector_code"] in with_series

    # 两条口径的"分母"不是同一个东西，措辞必须分开：
    #   share    全场板块成交额合计（每行同一个数，随结果交一次）
    #   turnover 本板块成分流通市值合计（每行各自一个数，住在 mv_yuan；universe_amount 这一档刻意留 NULL，
    #            所以这句不许说"分母未交出" —— 那是把"这口径没有全场分母"说成"数缺了"）
    # 金额必须换成亿/万亿再交出去：2.629e+13 这种科学计数法在屏幕上是没人能读的一串字符，
    # 而这一格恰恰是"份额的分母到底有多大"的唯一出处。
    def _money(v) -> str:
        n = float(v)
        if n >= 1e12:
            return "%.2f 万亿元" % (n / 1e12)
        if n >= 1e8:
            return "%.1f 亿元" % (n / 1e8)
        return "%.4g 元" % n

    denom = (f"{_money(universe_amount)}" if universe_amount is not None
             else ("每行各自的分母见 mv_yuan（本板块成分流通市值合计）" if basis == "turnover"
                   else "分母未交出"))
    body_n = f"母体 {universe} 个板块" if universe is not None else "母体数未交出"
    unit_value = "当日换手率（百分数，1.79 就是 1.79%）" if basis == "turnover" else "份额（0~1 的小数）"

    return {
        "available": True,
        "date": day,
        "basis": basis,
        "spec_version": spec,
        "windows": windows,
        "universe": universe,
        "universe_amount": universe_amount,
        "calculated_at": calculated_at,
        "age_seconds": age_seconds,
        "top_per_window": TOP_PER_WINDOW,
        "boards": boards,
        "name_is_code": name_is_code,
        # 母体过滤的账目随结果交出：界面要能说"少了 48 个、因为它们是市场级统计指数"，
        # 而不是让人以为榜被截断了。
        "sector_types": allowed,
        "excluded_types": [{"sector_type": k, "sectors": v} for k, v in sorted(
            dropped.items(), key=lambda kv: -kv[1])],
        "streak_stats": {
            "up": [{"days": k, "sectors": up_hist[k]} for k in sorted(up_hist, reverse=True)],
            "down": [{"days": k, "sectors": dn_hist[k]} for k in sorted(dn_hist, reverse=True)],
            "no_streak": sum(1 for r in rows
                             if not r.get("streak_up") and not r.get("streak_dn")),
            "total": len(rows),
        },
        "series": series,
        "series_per_board": SERIES_PER_BOARD,
        "series_sessions": SERIES_SESSIONS,
        "evidence": evidence_row or (p.get("evidence") if basis == "share" else None),
        "basis_allowed": sorted(BASIS_SPEC_KEY),
        "unit_value": unit_value,
        "calibers": {
            "value": (
                "换手强度 = 本板块成交额 ÷ 本板块成分流通市值合计，单位当日换手率 %。"
                f"分母每一行各自一个（{denom}），{body_n} —— "
                "这条剔的是「这个板块盘子本来就大」，与份额剔「全场共同放量」是两件事"
                "（同一场的名次对照在下面的实测判据那一行，两处不各写一份，免得改了这边忘了那边）。"
                "市值是当日日线反推的（100×成交额÷换手率），不是快照表 —— 那张表只有 36 场，"
                "用它这条口径就永远攒不满 10/20/30 场的窗口。"
                if basis == "turnover" else
                f"份额 = 本板块当日成交额 ÷ 当日全场板块成交额合计"
                f"（分母 {denom}，{body_n}）。这个分母是把母体里每个板块的成交额重叠相加 —— "
                "一只票同时挂在十几个板块里，同一笔钱被数了很多遍，所以它远大于当日全市场成交额，"
                "不能当市场规模读。"
                "份额剔掉了全场共同放量／缩量，绝对额不剔 —— 实测用绝对额测连续性会得出"
                "「73%→83% 延续」的假惯性。"),
            "cum": ("近 N 场累计 = 窗口末场换手率 − 首场换手率，单位百分点(pp)，不是金额也不是流量。"
                    if basis == "turnover" else
                    "近 N 场累计 = 窗口末场份额 − 首场份额，单位是百分点(pp)，不是金额。"),
            "cum_days": "每一格旁边带着「实际有几场」：攒不满 N 场时那是「前 6 场的差」，"
                        "与「10 场的差」不可比。",
            "streak": ("连续场次按换手率严格递增／递减数（相等即断链），"
                       "不是连续上涨、也不是连续净流入。" if basis == "turnover" else
                       "连续场次按份额严格递增／递减数（相等即断链），"
                       "不是连续上涨、也不是连续净流入 —— 涨得少也算续，跌得少也算断。"),
            "rank": "名次是这一场在当日母体（只含有成交额的行业＋概念板块）里的纯排序，不含好坏。",
            "universe": (
                "母体只认板块字典里登记成「%s」的行；这一场库里 %s 行，按类型排除了 %s 行（%s）。"
                "被排除的那批是通达信做的**市场级统计指数**（880001 总市值、880005 涨跌家数、"
                "880011 主板总值…880099 通用回购），它们的成交额逐日等于全 A 成交额，"
                "混在板块榜里就必然霸榜 —— 2026-10-06 之前「份额抬升端全是六位代码」就是这个原因。"
                "%s已在这一场的 %s 个母体上重算过（1..%s 连续，没有空档）；"
                "那批旧行仍物理留在表里（读侧按类型过滤，页面上看不见），要连表一起清得单独删行。") % (
                "、".join(allowed), len(raw), sum(dropped.values()),
                "、".join("%s %s 个" % (k, v) for k, v in sorted(
                    dropped.items(), key=lambda kv: -kv[1])) or "无",
                "两把尺子的分母与名次用的是同一个母体（不然两条榜并排一比就是假话）。"
                if basis == "turnover" else "份额分母与名次",
                universe, int(universe or 0) - sum(dropped.values())),
            "amount": ("分子的口径：只加「当天既有成交额又有换手率」的那些成员的钱，与分母严格同一母体 —— "
                       "板块日线那个合计数里含着停牌成员，钱留在分子、市值不进分母就会虚高换手率。"
                       "剔了多少成员看「匹配/在册」两格（相等就是没剔）。"
                       if basis == "turnover" else
                       "本板块当日成交额只作辅助列，不是「资金流入」。"
                       "主力净流入那一档（main_net）库里还没有行，界面上就没有那个数，不拿成交额顶。"),
            "evidence": str(evidence_row or (p.get("evidence") if basis == "share" else "") or ""),
        },
    }


def _series(codes: list[str], day: str, basis: str, spec: str) -> list[dict]:
    """头部板块近 SERIES_SESSIONS 场的份额折线（原值交出，界面不重算）。"""
    if not codes:
        return []
    ph = ", ".join(["%s"] * len(codes))
    df = pg.fetch_df(
        f"""
        with days as (
            select distinct trade_date from {TABLE}
            where basis = %s and spec_version = %s and trade_date <= %s::date
            order by trade_date desc limit {int(SERIES_SESSIONS)}
        )
        select t.sector_code as code, coalesce(nullif(si.name,''), t.sector_code) as name,
               t.trade_date::text as d, t.value::float8 as share
        from {TABLE} t
        join days dd on dd.trade_date = t.trade_date
        left join reference_data.sector_info si using (sector_code)
        where t.basis = %s and t.spec_version = %s and t.sector_code in ({ph})
        order by t.sector_code, t.trade_date
        """,
        (basis, spec, day, basis, spec, *codes))
    if df is None or not len(df):
        return []
    out: dict[str, dict] = {}
    for r in df.itertuples():
        bucket = out.setdefault(r.code, {"code": r.code, "name": str(r.name or r.code), "points": []})
        bucket["points"].append({"date": str(r.d), "share": _num(r.share)})
    # 保持榜序（codes 就是 rank_30 的顺序），否则图例顺序和表格顺序对不上
    return [out[c] for c in codes if c in out]
