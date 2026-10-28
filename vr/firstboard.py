"""首板分析数据层 —— 涨停池按连板数分四档（首板／二板／三板／四板及以上）+ 涨停原因题材串 + 节点分析。

短线投资实例专属模块（不回推开源仓库）。
- 名单：东财涨停池（astock.em_zt_topic_pool，免费无 key），档位取池内连板数 `lbc`。
- 涨停原因：同花顺问财 query2data「YYYYMMDD 涨停的股票 涨停原因」，需要环境变量
  IWENCAI_API_KEY（不入库）；拿不到时优雅降级为空串，页面照常显示其余字段。
- 节点分析：读 `analytics.board_node_daily`，共用块只住 `duanxian/node_view.py`，
  首页连板榜调的是同一份（口径文字也由那一份交出，这里不重述）。
"""

from __future__ import annotations

import os
import time

import astock
from duanxian import reason_tags
from market import _num

_CACHE: dict = {}
_TTL = 600  # 10 分钟；涨停原因盘中变化不快


def _clean_reason(text: str, max_tags: int = 4, max_len: int = 40) -> str:
    """题材串清洗：统一分隔符、限标签数与总长（问财多用 '+' 分隔）。"""
    text = text.replace("，", "+").replace(",", "+").strip()
    tags = [t.strip() for t in text.split("+") if t.strip()]
    out = "+".join(tags[:max_tags])
    return out[:max_len]


def _fetch_reasons(date: str) -> tuple[dict, str | None]:
    """问财拉**指定交易日**的涨停原因，返回 ({6位代码: 题材串}, 错误说明或 None)。

    ⚠️ 查询必须带上日期。写成"今日涨停的股票"有两个问题：
    ① 涨停池会往前回退到最近有数据的交易日（周末、节假日、当天接口短暂失败），
       这时"今日"取回来的原因和池子不是同一天，拼在一起看着完全正常；
    ② 实测收盘当天"今日"这个词本身就常常返回 0 行，而带日期的查询正常有数据 ——
       写死"今日"会让题材串整体为空。
    返回的列名形如 `涨停原因[20260828]`，下面按子串匹配，不要改成精确等于。
    """
    if not os.environ.get("IWENCAI_API_KEY"):
        from duanxian.fetchers import fetch_zt_reasons
        return fetch_zt_reasons(date)
    try:
        from iwencai_client import IwencaiClient

        client = IwencaiClient()
    except Exception as e:  # noqa: BLE001
        return {}, f"问财客户端初始化失败：{type(e).__name__}"

    reasons: dict[str, str] = {}
    try:
        for page in range(1, 4):  # 单页 50，涨停一般 1~3 页
            df = client.query(f"{date}涨停的股票 涨停原因", page=page, limit=50)
            if df is None or len(df) == 0:
                break
            code_cols = [c for c in df.columns if "代码" in c]
            reason_cols = [c for c in df.columns if "涨停原因" in c]
            if not code_cols or not reason_cols:
                return reasons, "问财返回缺少涨停原因列"
            cc, rc = code_cols[0], reason_cols[0]
            for _, row in df.iterrows():
                code6 = str(row[cc])[:6]
                reason = str(row[rc]).strip()
                if reason and reason.lower() != "nan" and code6 not in reasons:
                    reasons[code6] = _clean_reason(reason)
            if len(df) < 50:
                break
    except Exception as e:  # noqa: BLE001
        return reasons, f"问财查询异常：{type(e).__name__}"
    return reasons, None


_REASONS_CACHE: dict = {}


def get_reasons(date: str) -> tuple[dict, str | None]:
    """当日全部涨停股的涨停原因（带缓存，首板页与每日复盘连板表共用）。"""
    hit = _REASONS_CACHE.get(date)
    if hit and time.time() - hit[0] < _TTL:
        return hit[1], hit[2]
    reasons, err = _fetch_reasons(date)
    if reasons:
        _REASONS_CACHE[date] = (time.time(), reasons, err)
    return reasons, err


# 题材串拆词与词频汇总**只住 `duanxian/reason_tags.py` 一份**（10-21 收成共用块）：
# 首页「首板分析」那块要按库里整场汇总，与本屏这份内存名单必须是同一个算法，
# 否则同一个"哪个题材重复出现"在两屏会给出两个答案。四条判据（分隔符、同一只票内去重、
# 只留 count>1 并把被滤掉的条数交回、单列 no_reason）与"不做属性词过滤"的理由都在那个块里。


def _tally(stocks: list[dict]) -> dict:
    """转发给共用块，参数形状不变（吃本屏当场那一批票）。"""
    return reason_tags.tally(stocks, scope='这一批票（首板口径，涨停原因来自源站编辑归因，不是公司确认事实）')


def _hhmm(v) -> str:
    """池内时间字段（HHMMSS 整数）→ 'HH:MM'。"""
    s = str(_num(v)).zfill(6)
    return f"{s[:2]}:{s[2:4]}" if len(s) == 6 else ""


def _db(query: str, params: tuple = ()):
    """读 PG（本项目的只读层）。单独包一层是为了让"库里有没有这一场"这个问题只有一处答案。"""
    from duanxian import pg

    return pg.fetch_df(query, params)


def data_session() -> tuple[str | None, str | None]:
    """库里**实际有涨停数据**的最近一场，返回 `(YYYY-MM-DD 或 None, 说明)`。

    规则只有一处实现：`duanxian/trade_calendar.py:latest_data_session()`
    （① 日历上最近已收盘交易日 → ② 库里 `stock_daily` 最大日，显示取②，①晚于②时把缺口
    一起交出来）。复盘页的 `/api/review/expected-date` 走的是同一个函数，所以不会出现
    "首板说 09-23、复盘让你去看 09-24"这种两个页面各说一层的日子。
    """
    from duanxian import trade_calendar

    return trade_calendar.latest_data_session()


def available_sessions(limit: int = 30) -> list[str]:
    """页面上"交易日"可点的候选：库里真有的那几天（降序）。

    不给"今天"、也不给日历上的交易日 —— 只给有数的日子，选了就一定有结果。
    """
    df = _db("select distinct trade_date::text d from market_data.stock_daily "
             "order by d desc limit %s", (limit,))
    return [str(x) for x in (df["d"].tolist() if df is not None and len(df) else [])]


def zt_count_from_db(date: str) -> int | None:
    """库里严格口径的涨停家数（收盘=涨停价），用来核对在线池子给的数是不是这一场。

    严格口径与本项目统一：`close == limit_up`（±0.005），档位从 `limit_up/pre_close` 推，
    不靠代码前缀猜、不信任陈旧的 `is_st` 快照。
    """
    df = _db("""select count(*) n from market_data.stock_daily
                where trade_date = %s::date and limit_up is not null
                  and abs(close - limit_up) <= 0.005""", (date,))
    if df is None or not len(df):
        return None
    return int(df["n"][0])


def _first_board(date: str | None = None) -> dict:
    """首板列表。`date` 不给就用 `data_session()`（库里最近一场有数的）。

    ⚠️ 这里以前是"从今天往回找 8 天，第一个非空的池子算谁"。那个写法有两条致命处，
    2026-09-26 被用户当场抓到：
      - 不过日历 → 09-25 是中秋休市日，东财池子却照样吐回一份数（返回记录里首封时间是
        `09:25` —— 休市日不可能有 09:25 集合竞价），于是**把 09-23 那一场贴成 09-25 显示**；
      - 不读库 → 同一次响应里自己就矛盾了：`total_zt=52` 而涨停原因源回"20260925 未返回涨停记录"。
    所以改成：**先定目标日（日历 + 库里实际），再按那一个日子去取**；不请求非交易日，
    取回来还要与库里的涨停家数交叉核对，对不上就在页面上说明，不静默取一边。
    """
    want, gap = (date, None) if date else data_session()
    if not want:
        return {"date": "", "stocks": [], "reason_note": gap or "取不到可用交易日"}
    if date and date not in available_sessions(60):
        return {"date": date, "stocks": [],
                "reason_note": f"{date} 库里没有日线数据，换一天；"
                               f"可选：{'、'.join(available_sessions(6)[:3])} 等"}

    ymd = want.replace("-", "")
    zt = astock.em_zt_topic_pool("getTopicZTPool", ymd, "fbt:asc")
    # `get_reasons` 的入参契约是 `YYYYMMDD`（`vr/market.py:137` 也按这个传），别在这改格式。
    reasons, reason_note = get_reasons(ymd)

    db_n = zt_count_from_db(want)
    caliber = ""
    if zt and db_n is not None and abs(len(zt) - db_n) > max(3, db_n * 0.1):
        # 差太多说明这份池子不是 want 那一场（或口径不同），必须说出来而不是混着显示
        caliber = (f"⚠ 在线涨停池给 {len(zt)} 家，库里严格口径这一场是 {db_n} 家，"
                   f"两者对不上：名单按在线池显示，家数请以库里为准")

    rows_all = []
    for p in zt:
        code = str(p.get("c", ""))
        fbt = _num(p.get("fbt"))
        rows_all.append({
            "code": code,
            "name": p.get("n", ""),
            "boards": _num(p.get("lbc")) or 1,      # 池内连板数（缺了按 1 板），四档切换就看这一列
            "price": round((astock._numf(p.get("p")) or 0) / 1000, 2),
            "pct": round(astock._numf(p.get("zdp")) or 0, 2),
            "amount": astock._numf(p.get("amount")),
            "float_cap": astock._numf(p.get("ltsz")),
            "industry": p.get("hybk", ""),
            "seal_time": _hhmm(fbt),
            "seal_raw": fbt,                        # 排序用的原始 HHMMSS；缺数是 None，不当 0（0 不是 00:00）
            "break_count": _num(p.get("zbc")),
            "reason": reasons.get(code, ""),
        })

    # 四档（用户 2026-10-15 点名要和那份说明一样分档）：首板 / 二板 / 三板 / 四板及以上。
    # 排序两套：1~3 档按首次封板时间从早到晚（池子本身就是 fbt:asc，不再打乱）；
    # 四板及以上先按连板高度从高到低，同一高度再按首封从早到晚。
    # 首封缺数排到本档最后，**不拿 0 当成"最早"**。
    tiers = []
    for key, label in (("1", "首板"), ("2", "二板"), ("3", "三板"), ("4p", "四板及以上")):
        if key == "4p":
            sel = [r for r in rows_all if r["boards"] >= 4]
            sel.sort(key=lambda r: (-r["boards"], r["seal_raw"] if r["seal_raw"] else 10 ** 9))
        else:
            sel = [r for r in rows_all if r["boards"] == int(key)]
        tiers.append({"key": key, "label": label, "count": len(sel), "stocks": sel})
    # 页面默认那一档 = 首板。题材/行业统计的分母也用它，与屏幕上「首板名单」看到的是同一批票。
    stocks = tiers[0]["stocks"]

    # 「节点分析」：共用块，一处实现（`duanxian/node_view.py`），与首页连板榜读同一份。
    # 判据与口径文字都不在这里重算、不复述。
    from duanxian import node_view
    node_meta = node_view.attach(rows_all, want)
    node_warn = node_meta.pop('warn', '')
    # 池内连板数与本地日线数出来的板数对不上时要点名：档位读池子，节点列读本地日线。
    node_clash = [r["code"] for r in rows_all
                  if r.get("node_detail") and r["node_detail"].get("boards") is not None
                  and r["node_detail"]["boards"] != r["boards"]]
    if node_clash and not node_warn:
        node_warn = (f'有 {len(node_clash)} 只票的档位（东财池内连板数）与节点分析里的板数'
                     f'（本地日线数出来的）不是同一个数：{("、".join(node_clash[:5]))}'
                     f'{(" 等" if len(node_clash) > 5 else "")} —— '
                     '档位按池子排，那一格判据按本地日线算，两边不是同一把尺子，别当成有一边算错。')

    notes = "；".join(x for x in (gap, caliber) if x)
    return {
        "date": want,
        "total_zt": len(zt),
        "db_zt": db_n,
        "first_count": len(stocks),
        "reason_note": reason_note or "",
        "caliber_note": notes,
        "available_dates": available_sessions(10),
        "stocks": stocks,
        "tiers": tiers,
        "node": node_meta,
        "node_rule": node_view.RULE_NOTE,
        "node_warn": node_warn,
        **_tally(stocks),
    }


def get_first_board(date: str | None = None) -> dict:
    """首板列表（TTL 缓存；空结果不缓存，下次直接重试）。缓存按日期分键。"""
    key = date or "__latest__"
    now = time.time()
    hit = _CACHE.get(key)
    if hit and now - hit[0] < _TTL:
        return hit[1]
    val = _first_board(date)
    if val.get("stocks"):
        _CACHE[key] = (now, val)
    return val
