# -*- coding: utf-8 -*-
"""个股主力资金流入库（`ingest/jobs/c21_fundflow_sina.py`）的三条口径约束。

为什么值得单独钉：这张表 2026-09-27 之前**一直是 0 行**，而原因不是"取不到数"
（新浪这个口实测 65 页 / 0 失败 / 覆盖 99.9%），是**没有宿主** —— 链里只有走东财的 `c11`。
"实现存在"和"每天都会落库"是两件事，所以这里既测口径也测宿主。
全程不打网络、不连库（`conftest.py` 本就拦出站）。
"""

from __future__ import annotations

import json

import pytest

from ingest.jobs import c21_fundflow_sina as c21


# ---------------------------------------------------------------- 归属日

def test_报文没有日期字段_所以归属日一律由日历判():
    """新浪这一发的全部字段就是这些 —— 没有任何日期位，所以"历史"根本不存在。
    这条测试是那份实测的化石：谁哪天加了个 `opendate` 之类的解析，说明源变了，要重测。
    """
    fields = {"symbol", "name", "trade", "price", "changeratio", "amount", "turnover",
              "netamount", "inamount", "outamount", "ratioamount",
              "r0_ratio", "r0x_ratio", "r0_in", "r0_out", "r0_net",
              "r3_in", "r3_out", "r3_net", "r3_ratio"}
    assert not {f for f in fields if "date" in f or f in ("opendate", "day")}


@pytest.mark.parametrize("want", ["2026-08-01", "2025-12-31", "昨天"])
def test_date_参数不接受历史日期(want):
    """给 `--date 2026-08-01` 不会取回 8 月的数，只会把**今天**的截面盖上 8 月的戳。"""
    with pytest.raises(SystemExit) as e:
        c21.check_date_arg(None, want, "2026-09-24", False)
    assert "没有历史查询能力" in str(e.value) or "只接受" in str(e.value)


def test_date_只放行最近一个已收盘交易日或今天():
    import datetime as dt
    assert c21.check_date_arg(None, None, "2026-09-24", True) == "2026-09-24"
    assert c21.check_date_arg(None, "2026-09-24", "2026-09-24", False) == "2026-09-24"
    assert c21.check_date_arg(None, str(dt.date.today()), "2026-09-24", True) == str(dt.date.today())


# ---------------------------------------------------------------- 同一天不重采

def test_同一天已写过就跳过_两个入口不许互相覆盖(monkeypatch, tmp_path):
    """收盘链与常驻 20:50 线程都会调它。这个口是**即时截面**，盘中/盘后两次的值不一样，
    后一发会盖掉前一发，而事后只看得到"今天有数"，看不出那是哪一刻的。"""
    monkeypatch.setattr(c21, "read_state",
                        lambda: {"trade_date": "2026-09-28", "written": 5572,
                                 "ran_at": "2026-09-28T20:51:03+08:00"})
    hit = c21.already_written("2026-09-28")
    assert hit and hit["written"] == 5572
    assert c21.already_written("2026-09-29") is None          # 换一天照采


def test_写过零行不算已写(monkeypatch):
    """失败的那一轮也会留 state（`written: 0`）。不能让它把第二天挡掉。"""
    monkeypatch.setattr(c21, "read_state", lambda: {"trade_date": "2026-09-28", "written": 0})
    assert c21.already_written("2026-09-28") is None


def test_没有state时读成空不炸(monkeypatch, tmp_path):
    monkeypatch.setattr("duanxian.paths.data_path", lambda *p: str(tmp_path))
    assert c21.read_state() == {}


# ---------------------------------------------------------------- 宿主（0 行的真因）

def test_c21_在收盘链步表里_而且不是骨干步():
    """2026-09-27 之前它就死在这条上：实现存在，但 `e1_fill_all` 的步表里只有走东财的 `c11`。
    非骨干也是要求之一 —— 新浪挂了不该把日线主链拦下来。"""
    from ingest.jobs import e1_fill_all as e1
    hit = [s for s in e1.STAGES if s[0] == "fundflow_rank"]
    assert hit, "e1 步表里没有 fundflow_rank 这一步"
    assert hit[0][2] is False, "网络源不许设成骨干：它失败不该拦住日线主链"


def test_步表与参数表必须同时有这一步():
    """只加步表不加参数表 = 链子跑到这里静默什么都不做，比没有更坏（看着像跑过了）。
    参数表在 `main()` 里拼、没法导入，所以这条只能读源码配对 —— 但它挡的正是
    "名字打错了/漏了一处"这种一上线就静默失灵的错。"""
    import pathlib
    import re
    src = pathlib.Path(e1_src()).read_text(encoding="utf-8")
    assert re.search(r"'fundflow_rank'\s*:\s*\(\s*'ingest\.jobs\.c21_fundflow_sina'", src), \
        "步表有 fundflow_rank，参数表却没把它指到 c21"


def test_时点默认值只有一份_宿主真的读它(monkeypatch):
    """系统配置的规矩：默认值只许住在 `duanxian/jobs.py::CLOSE_DEFAULTS`。
    宿主那边再写一份字面量，就会出现"页面显示 20:50、实际按别的跑"或反过来 ——
    这类假生效最难查，因为两边各自看都自洽。"""
    import server
    from duanxian import jobs
    assert "fundflow" in jobs.CLOSE_DEFAULTS and "watchlist" in jobs.CLOSE_DEFAULTS
    assert server._fundflow_slot() == jobs.CLOSE_DEFAULTS["fundflow"]["run_at"]
    assert server._watchlist_slot() == jobs.CLOSE_DEFAULTS["watchlist"]["run_at"]
    monkeypatch.setenv("FUNDFLOW_AT", "21:30")
    assert server._fundflow_slot() == "21:30"          # 环境变量这一层真的生效
    monkeypatch.setenv("FUNDFLOW_AT", "25:99")
    assert server._fundflow_slot() == jobs.CLOSE_DEFAULTS["fundflow"]["run_at"]


def test_页面列出的作业里要有这两条采集():
    """`scripts/jobs.py` 是页面/CLI 的唯一清单来源。不在册的作业 = 改不动也看不见，
    和"没有宿主"是同一个坑的另一种形态。"""
    import scripts.jobs as sj
    known = sj._known()
    assert "fundflow" in known and "watchlist" in known
    for name in ("fundflow", "watchlist"):
        d = sj._defaults(name)
        assert d and d.get("run_at") and "enabled" in d, "%s 的默认值没接上" % name
        assert "interval_seconds" not in d, "这两条是时点型，给了 interval 会被误读成可反复跑"


def test_资金流宿主的交易日判据不许用is_weekend顶():
    """09-25 是周五但中秋休市（日历 `is_open=false`）。`util.is_weekend` 只看周六日，
    拿它当交易日判据会在节假日各撞一次空。

    判**代码**不判文字：docstring 里为了讲清理由本来就会出现 `is_weekend` 这个词
    —— 第一版这条测试就是被自己的说明打红的，那种断言以后没人敢信。
    """
    import ast
    import pathlib

    src = pathlib.Path(__file__).resolve().parents[1].joinpath("server.py").read_text(encoding="utf-8")
    fn = next(n for n in ast.walk(ast.parse(src))
              if isinstance(n, ast.FunctionDef) and n.name == "_fundflow_scheduler")
    doc = fn.body[0] if (isinstance(fn.body[0], ast.Expr)
                         and isinstance(fn.body[0].value, ast.Constant)
                         and isinstance(fn.body[0].value.value, str)) else None
    body = "\n".join(ast.unparse(n) for n in fn.body[1 if doc else 0:])
    assert "is_weekend" not in body
    assert "_trade_day_or_none" in body


def e1_src() -> str:
    from ingest.jobs import e1_fill_all as e1
    return e1.__file__


def test_常驻进程里也有一个兜底宿主():
    """链要有人跑才跑；常驻 uvicorn 那条 20:50 是不依赖人按按钮的那一半。"""
    import pathlib
    body = pathlib.Path(__file__).resolve().parents[1].joinpath("server.py").read_text(encoding="utf-8")
    assert "_fundflow_scheduler" in body and "_start_fundflow()" in body
    assert "c21_fundflow_sina" in body


def test_非交易日拒写的退出码是3_链才不会当失败():
    import inspect
    src = inspect.getsource(c21.main)
    assert "return 3" in src                              # 3 = 今天不采，不是失败
    assert "return 2" in src                              # 2 = 有页失败且不写（半份榜单更坏）
