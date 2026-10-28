"""份额轮动读层 `duanxian/flow_trend.py` 的口径回归（全程打桩 `pg.fetch_df`，不连库）。

这里守的四条都是这一屏最容易糊过去的地方：

1. **指定日期不许回落到邻近交易日。** 回落了页面就会把 09-29 的榜当 09-30 的读，
   而这两场的份额结构本来就不同 —— 那是造假，不是容错。
2. **判据版本从 `config.setting` 那一行取**，不按 `max(spec_version)` 字符串比
   （出 `sft-10` 会排在 `sft-9` 前面，取回旧参数算新榜）。
3. **两端榜不重叠**：母体小或场次攒不满时，同一个板块同时出现在"抬升端"和"回落端"
   会被读成两个数。
4. **NaN 不出门**：pandas 的 NaN 直接进 JSON 是非法字面量，进界面是 "NaN"，
   一律落成 None（01 第三条：没数就是没数）。
"""

from __future__ import annotations

import json

import pandas as pd
import pytest

from duanxian import flow_trend as ft
from duanxian import pg

PARAMS = {"windows": [10, 20, 30], "basis": ["share"], "spec_version": "sft-1",
          "min_days_ratio": 0.6,
          "evidence": "份额连续 n 天抬升→次日仍抬升 46%(n=1) 单调降到 31%(n=9)；基线 46%"}

BOARD_COLS = ["sector_code", "name", "sector_type", "share", "share_prev", "delta_bps",
              "streak_up", "streak_dn", "cum_10", "cum_20", "cum_30",
              "days_10", "days_20", "days_30", "rank_10", "rank_20", "rank_30",
              "universe", "universe_amount", "amount_yuan",
              # 2026-10-12 加的第二把尺子（换手）专用：桩少了这几列，读侧的新字段就等于没测
              "mv_yuan", "amount_matched", "members_matched", "members_total", "ev",
              "calculated_at", "age_seconds"]


def _board_row(code, name, rank, cum, days=10, up=0, dn=0, share=0.01, rank30=None,
               universe_amount=1.0e13, mv=None, amt_matched=None, matched=None,
               members=None, ev=None):
    return {
        "sector_code": code, "name": name, "sector_type": "行业",
        "share": share, "share_prev": share, "delta_bps": 0.0,
        "streak_up": up, "streak_dn": dn,
        "cum_10": cum, "cum_20": cum, "cum_30": cum,
        "days_10": days, "days_20": days, "days_30": days,
        "rank_10": rank, "rank_20": rank, "rank_30": rank if rank30 is None else rank30,
        "universe": 4, "universe_amount": universe_amount, "amount_yuan": 1.0e9,
        "mv_yuan": mv, "amount_matched": amt_matched,
        "members_matched": matched, "members_total": members, "ev": ev,
        "calculated_at": "2026-09-30 16:53:41+08:00", "age_seconds": 3600,
    }


_UNSET = object()


def _patch(monkeypatch, board_rows, *, params=_UNSET, latest="2026-09-30", series_rows=None):
    """按 SQL 里出现的表名分派假结果 —— 打桩要能识别"这是在问哪一屏"，不然一改 SQL 就全红。
    `params=None` 表示**参数行读不到**（不是"用默认参数"）。
    榜查询按传入的 `trade_date` 过滤：桩不过日期的话，"指定日期没数"那条永远测不到。"""
    monkeypatch.setattr(pg, "enabled", lambda: True)
    p = PARAMS if params is _UNSET else params
    calls: list[str] = []

    def fake(sql, params_=()):
        calls.append(sql)
        if "config.setting" in sql:
            return pd.DataFrame([{"value": p}]) if p is not None else None
        if "analytics.sector_flow_trend" in sql and "max(trade_date)" in sql:
            return pd.DataFrame([{"d": latest}])
        if "with days as" in sql:            # _series 的那条
            return pd.DataFrame(series_rows if series_rows is not None else [],
                                 columns=["code", "name", "d", "share"])
        asked = str(params_[0]) if params_ else latest
        if asked != latest:
            return pd.DataFrame(columns=BOARD_COLS)
        return pd.DataFrame(board_rows, columns=BOARD_COLS)

    monkeypatch.setattr(pg, "fetch_df", fake)
    return calls


def test_pg_未配置时说自己没配而不是没有轮动(monkeypatch):
    monkeypatch.setattr(pg, "enabled", lambda: False)
    out = ft.rotation_board()
    assert out["available"] is False
    assert "PG" in out["reason"] or "本地库" in out["reason"]


def test_参数行读不到时不猜_max_spec_version(monkeypatch):
    _patch(monkeypatch, [], params=None)
    out = ft.rotation_board()
    assert out["available"] is False
    assert "spec_version" in out["reason"] or "参数" in out["reason"]


def test_指定日期没数就说什么日期没数绝不回落邻近场(monkeypatch):
    rows = [_board_row("TDX_1", "一号", 1, 0.01)]
    _patch(monkeypatch, rows)
    out = ft.rotation_board("2026-09-25")
    assert out["available"] is False
    assert out["date"] == "2026-09-25"
    assert "2026-09-25" in out["reason"]
    assert not out.get("boards"), "回落邻近交易日就会带着别一天的榜出门"


def test_两端榜不重叠且名次只数有名行的(monkeypatch):
    rows = [
        _board_row("TDX_A", "甲", 1, 0.03),
        _board_row("TDX_B", "乙", 2, 0.02),
        _board_row("TDX_C", "丙", 3, -0.01),
        _board_row("TDX_D", "丁", None, -0.05, days=2),   # 新板块：没名次
    ]
    _patch(monkeypatch, rows)
    out = ft.rotation_board()
    assert out["available"] is True
    board = out["boards"][0]
    assert board["ranked"] == 3, "days 不够、名次为空的行不许进名次母体"
    rising = {r["sector_code"] for r in board["rising"]}
    falling = {r["sector_code"] for r in board["falling"]}
    assert not (rising & falling), "同一个板块不能同时出现在两端"
    assert all(r["rank_10"] is not None for r in board["rising"])


def test_空值出门口是_none_不是_nan(monkeypatch):
    rows = [_board_row("TDX_A", "甲", 1, float("nan"), up=2)]
    _patch(monkeypatch, rows)
    out = ft.rotation_board()
    r = out["boards"][0]["rising"][0]
    assert r["cum_10"] is None, "NaN 会被 json 写成非法字面量，也会印成 NaN"
    assert r["streak_up"] == 2
    assert "NaN" not in json.dumps(out, ensure_ascii=False)


def test_名字就是编号的板块数随结果交出(monkeypatch):
    rows = [_board_row("TDX_880099", "880099", 1, 0.01), _board_row("TDX_B", "乙", 2, 0.02)]
    _patch(monkeypatch, rows)
    assert ft.rotation_board()["name_is_code"] == 1


def test_曲线只给各档榜头并去重_has_series_跟着回写(monkeypatch):
    """三个窗口的榜头不一样：曲线要覆盖"每一档的前若干名"的并集，且不重复给同一个板块。"""
    rows = []
    for i in range(6):
        r = _board_row(f"TDX_{i}", f"名{i}", i + 1, 0.01 * (6 - i))
        r["rank_30"] = 6 - i          # 近 30 场的名次与近 10 场反过来
        r["rank_20"] = i + 1
        rows.append(r)
    expected: set[str] = set()
    for w in PARAMS["windows"]:
        by_rank = sorted((r for r in rows if r[f"rank_{w}"] is not None), key=lambda r: r[f"rank_{w}"])
        expected |= {r["sector_code"] for r in by_rank[: ft.SERIES_PER_BOARD]}

    _patch(monkeypatch, rows, series_rows=[
        {"code": c, "name": c[-1:], "d": f"2026-09-{20 + p:02d}", "share": 0.01 + 0.001 * p}
        for c in sorted(expected) for p in range(3)])
    out = ft.rotation_board()

    got = {s["code"] for s in out["series"]}
    assert got == expected, f"曲线该是各档榜头的并集：{got} vs {expected}"
    assert len(out["series"]) == len(got), "同一板块不许在三个窗口各带一条线"
    for b in out["boards"]:
        for r in b["rising"] + b["falling"]:
            assert r["has_series"] == (r["sector_code"] in got)


def test_实证串从参数行带出来不由界面抄一份(monkeypatch):
    _patch(monkeypatch, [_board_row("TDX_A", "甲", 1, 0.01)])
    out = ft.rotation_board()
    assert out["evidence"] == PARAMS["evidence"]
    assert "46%" in out["calibers"]["value"] or "假惯性" in out["calibers"]["value"]


def _retyped(row, sector_type):
    r = dict(row)
    r["sector_type"] = sector_type
    return r


def test_市场级统计指数不进份额榜且账目交出(monkeypatch):
    """880001 总市值 / 880099 通用回购这类是**指数不是板块**（成交额=全 A，必然霸榜）。
    2026-10-06 库里把它们改判成 `sector_type='统计'`，读层按加工侧同一份参数行的
    `sector_types` 过滤；"少了几个、为什么少"必须随结果交出，不能让读者以为榜被截断了。"""
    rows = [_retyped(_board_row("TDX_880001", "总市值", 1, 0.09), "统计"),
            _retyped(_board_row("TDX_880099", "通用回购", 2, 0.08), "统计"),
            _board_row("TDX_B", "乙", 3, 0.02),
            _board_row("TDX_C", "丙", 4, 0.01)]
    _patch(monkeypatch, rows)
    out = ft.rotation_board()

    codes = {r["sector_code"] for b in out["boards"] for r in b["rising"] + b["falling"]}
    assert "TDX_880001" not in codes and "TDX_880099" not in codes
    assert out["streak_stats"]["total"] == 2, "统计指数不该进连续场次的分布"
    assert out["excluded_types"] == [{"sector_type": "统计", "sectors": 2}], out["excluded_types"]
    assert out["sector_types"] == ["行业", "概念"]
    assert "排除" in out["calibers"]["universe"] and "2" in out["calibers"]["universe"]


def test_未登记类型的行同样被排除且单独报数(monkeypatch):
    """`sector_info` 里没有这行 = 名字与类型都没有，进榜只会再产出一行代码。"""
    rows = [_retyped(_board_row("TDX_X", "TDX_X", 1, 0.05), None), _board_row("TDX_B", "乙", 2, 0.02)]
    _patch(monkeypatch, rows)
    out = ft.rotation_board()
    assert {r["sector_code"] for r in out["boards"][0]["rising"]} == {"TDX_B"}
    assert out["excluded_types"] == [{"sector_type": "未登记", "sectors": 1}]


def test_母体口径只认参数行不认读层自己的默认(monkeypatch):
    """判据只有一份：加工侧写 `sector_flow_trend` 时用的 `sector_types` 与读侧过滤同一个来源。
    参数行给了 `概念`，行业行就要跟着出局 —— 读层那份 `['行业','概念']` 只是**参数行没这个键时**
    的兜底（与 `app/compute/sector_flow_trend.py` 的默认同值），不许变成第二个真相源。"""
    p = dict(PARAMS)
    p["sector_types"] = ["概念"]
    rows = [_retyped(_board_row("TDX_H", "行业甲", 1, 0.05), "行业"),
            _retyped(_board_row("TDX_G", "概念乙", 2, 0.04), "概念")]
    _patch(monkeypatch, rows, params=p)
    out = ft.rotation_board()
    assert [r["sector_code"] for r in out["boards"][0]["rising"]] == ["TDX_G"]
    assert out["sector_types"] == ["概念"]
    assert out["excluded_types"] == [{"sector_type": "行业", "sectors": 1}]


def test_界面不许自己重算份额榜():
    """这条守的是"判据只住一处"：前端拿到多少榜就显示多少，不在 JS 里再排一遍名次、
    也不在前端算 streak（前端的窗口切片与后端不同，会出现两个版本的"连续几天"）。
    路径按本文件位置解析 —— 从仓库根跑与从别处跑都同一条命令成立。"""
    import pathlib
    root = pathlib.Path(__file__).resolve().parents[1]
    src = (root / "frontend" / "src" / "components" / "home" / "SectorRotationBoard.tsx").read_text(encoding="utf-8")
    # 10-08 这个读法从"合并键"换成了 `sideStreak`（合并键把回落记成负数，拿它排回落端会排反）——
    # 钉的是**"连续场次从 lib 来、不在组件里现写"**这件事，不是某个函数名。
    assert "sideStreak(" in src, "连续场次的读法应从 lib/rotation.ts 来，不在组件里现写"
    assert "streakSortKey" not in src, "旧的合并键已从库里删掉，组件不许再引用"
    for banned in ("sort((a, b) => a.streak_up", "cum_30 - cum_10", "Math.max(...rows",
                   "streak_up > 0 ?", "streak_dn > 0 ?"):
        assert banned not in src, f"组件里出现了前端重算的影子：{banned}"


# ------------------------------------------------------ 2026-10-12 第二把尺子：basis 选择

PARAMS_2 = dict(PARAMS)
PARAMS_2.update({"basis": ["share", "turnover"], "turnover_spec_version": "stn-1",
                 "turnover_chunk_days": 60})


def _turn_row(code, name, rank, cum, **kw):
    """换手口径的一行：value 是百分数（4.0 就是 4%），universe_amount 这一档刻意 NULL。"""
    return _board_row(code, name, rank, cum, universe_amount=None,
                      mv=2.5e11, amt_matched=1.0e10, matched=kw.pop("matched", 100),
                      members=kw.pop("members", 100), share=4.0, **kw)


def test_不认识的口径说清可选值且不拿另一档顶上(monkeypatch):
    _patch(monkeypatch, [], params=PARAMS_2)
    out = ft.rotation_board(basis="main_net")
    assert out["available"] is False and out["basis"] == "main_net"
    assert "share" in out["reason"] and "turnover" in out["reason"], "要把可选项列出来，别只说没有"
    assert not out.get("rows"), "打错的口径不许悄悄回落成份额榜"


def test_换手那一档用自己的判据版本不共用sft1(monkeypatch):
    """两条口径共用一个版本号 ⇒ 改换手会把份额那批行一起带走，历史对不上账。"""
    _patch(monkeypatch, [_turn_row("TDX_A", "甲", 1, 0.9)], params=PARAMS_2)
    out = ft.rotation_board(basis="turnover")
    assert out["available"] is True and out["basis"] == "turnover"
    assert out["spec_version"] == "stn-1" and out["spec_version"] != PARAMS["spec_version"]


def test_换手的分母在每行自己身上全场合计那句措辞不许照抄(monkeypatch):
    """turnover 行的 universe_amount 是 NULL（那一列在 share 行里是全场成交额合计）。
    读侧如果沿用旧措辞，屏幕上就会出现「分母未交出」—— 把"这口径没有全场分母"说成"数缺了"。"""
    _patch(monkeypatch, [_turn_row("TDX_A", "甲", 1, 0.9, matched=98, members=100)], params=PARAMS_2)
    out = ft.rotation_board(basis="turnover")
    assert out["universe_amount"] is None
    cal = out["calibers"]
    assert "分母未交出" not in cal["value"] and "mv_yuan" in cal["value"]
    assert "换手率" in cal["cum"] and cal["cum"].startswith("近 N 场累计")
    # 原来这句里还带着"银行 份额名次 57 而换手名次 822"那份同日对照，2026-10-12 把它收回到
    # `evidence` 一处（同一张卡上两处各写一份，改了这边忘那边就是两句假话）。判据没降：
    # 这一句仍然必须把"两把尺子剔的是两件事"说清，只是换了承载它的短语。
    assert "与份额剔" in cal["value"] and "是两件事" in cal["value"], \
        "两把尺子的差别要在这句里说清，不然读者以为换个名字而已"
    assert "**" not in cal["value"], "这句会进列头悬停（title 不过 plain()），星号会原样出现在屏幕上"
    r = out["boards"][0]["rising"][0]
    assert (r["mv_yuan"], r["members_matched"], r["members_total"]) == (2.5e11, 98, 100), \
        "剔了几只成员必须带出门，否则分子分母同母体这件事在界面上不可验证"
    assert "停牌成员" in cal["amount"]


def test_份额分母要写成人能读的量并说清那是重叠相加(monkeypatch):
    """两个形状都是这一屏真踩过的：
    ① `:.4g` 把 26.29 万亿印成 `2.629e+13 元` —— 一串没人能读的字符，而这一格是"份额的分母有多大"的唯一出处；
    ② 这个"全场合计"是 915 个板块成交额**重叠相加**（实测 2026-10-12：分母 26.29 万亿 vs 当日全 A
       1.55 万亿 ≈ 17 倍，因为一只票同时挂在十几个板块里）。只说「合计」就会被当成市场规模读。"""
    _patch(monkeypatch, [_board_row("TDX_A", "甲", 1, 0.03, universe_amount=2.629e13)], params=PARAMS_2)
    cal = ft.rotation_board()["calibers"]["value"]
    assert "e+" not in cal, "科学计数法不许上屏"
    assert "万亿" in cal, "金额要换成亿/万亿"
    assert "重叠" in cal and "市场规模" in cal, "分母不是市场规模，这句必须自己说出口"


def test_份额那一档的措辞与字段不许被换手改坏(monkeypatch):
    """回归保护：加第二条口径最容易顺手把默认档的文案改成"两条都说"，
    结果 share 的悬停里冒出一句换手率的话 —— 屏幕说的是另一把尺子。"""
    _patch(monkeypatch, [_board_row("TDX_A", "甲", 1, 0.03)], params=PARAMS_2)
    out = ft.rotation_board()
    assert out["basis"] == "share" and out["spec_version"] == "sft-1"
    assert "换手" not in out["calibers"]["value"] and "全场板块成交额合计" in out["calibers"]["value"]
    assert out["boards"][0]["rising"][0]["mv_yuan"] is None, "share 行不该带换手字段"
