# -*- coding: utf-8 -*-
"""复盘笔记与关注提醒（`ys.review_note` / `ys.reminder`）的回归。

这个模块里每件被钉住的事，都属于"错了不会报错、只会静默给出别的东西"那一类：

 1. **DDL 与字段注册表不许分叉**。`FIELDS` 决定接口交出哪些列、界面渲染哪些格子，
    `19_review_note.sql` 决定库里有哪些列。两边不一致时保存会当场炸，但**读旧库配新界面**
    不会炸 —— 它只是安静地少给一栏，而用户以为那一栏自己写过了。
 2. **到期日只由交易日历算**，日历覆盖不到时必须说"这是估的"。
    自然日加 7 天会落进国庆，用户到时候看到的是一条根本没有行情的提醒。
 3. **`machine_baseline` 只增不改**。覆盖写会让「初始为机器填入·你已改」翻回「未改」，
    这份记录就再也问不出"这句话到底是谁写的"。
 4. **草稿一律不联网**。问财与 akshare 那两条路在没有落盘缓存时都要发请求；
    一抖就留下一格空的，而"空"与"这一天确实没有涨停"长得一模一样。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from duanxian import review_notes as rn

pytestmark = pytest.mark.unit

SQL = Path(__file__).resolve().parent.parent / "app" / "db" / "sql" / "19_review_note.sql"
_SKIP_PREFIXES = ("--", "constraint", "comment", "id ", "trade_date", "machine_baseline",
                  "machine_generated_at", "created_at", "updated_at")


def _ddl_columns(name: str) -> list[str]:
    """从建表 SQL 里按声明顺序刨出正文列（跳过注释、约束与时间戳列）。"""
    text = SQL.read_text(encoding="utf-8")
    body = text.split(f"create table if not exists ys.{name} (", 1)[1].split("\n);", 1)[0]
    cols: list[str] = []
    for raw in body.splitlines():
        line = raw.strip()
        if not line or line.startswith(_SKIP_PREFIXES):
            continue
        cols.append(line.split()[0])
    return cols


# ---------------------------------------------------------------- 列结构只有一个真相源

def test_注册表与建表SQL的正文列逐字一致且同序():
    assert rn.FIELD_KEYS == _ddl_columns("review_note"), \
        "FIELDS 加了列没加 SQL（或反过来）—— 保存会炸，但读旧库只安静少一栏"


def test_提醒表正文列与SQL一致():
    assert _ddl_columns("reminder") == [
        "kind", "security_code", "security_name", "note", "reason", "base_date",
        "sessions", "due_date", "calendar_covered", "status", "done_at"]


def test_字段清单每项都有中文名列且七个自问落到具体格子():
    meta = rn.fields_meta()
    assert [m["key"] for m in meta] == rn.FIELD_KEYS
    assert all(m["label"].strip() for m in meta), "界面拿 label 当列标题，空标签会渲染出一排无名格子"
    asked = [m["key"] for m in meta if m["question"]]
    assert len(asked) >= 5, "用户列的七个自问必须落在格子上，否则这屏只是一堆空白框"


def test_没有事实源的列写明为什么给不出():
    for key, meta in rn.FIELDS.items():
        if meta["no_default"]:
            assert len(meta["no_default"]) > 8, f"{key} 的理由太空"
    # 大V 那一格是"本机根本没有这类源"，不是"这次没取到"，两者不许混
    assert "数据源" in rn.FIELDS["view_bigv"]["no_default"]


# ---------------------------------------------------------------- 到期日：交易日，不是自然日

class _Cal:
    """假日历：只认识给定的那串开市日，覆盖末日由参数给。"""

    def __init__(self, monkeypatch, dates: list[str], horizon: str):
        import duanxian.pg_sources as pgs
        import duanxian.trade_calendar as cal

        self.dates = dates
        monkeypatch.setattr(pgs, "trade_dates", lambda a, b: [d for d in self.dates if a <= d <= b])
        monkeypatch.setattr(cal, "next_trade_date",
                            lambda d: min([x for x in self.dates if x > d], default=None))
        monkeypatch.setattr(rn, "_calendar_horizon", lambda: horizon)


SEP = ["2026-09-28", "2026-09-29", "2026-09-30", "2026-10-08", "2026-10-09",
       "2026-10-12", "2026-10-13", "2026-10-14", "2026-10-15", "2026-10-16"]


def test_七日后是第七个交易日而不是加七天(monkeypatch):
    _Cal(monkeypatch, SEP, "2026-12-31")
    got = rn.due_on("2026-09-28", 7)
    # 09-29 是第 1 个，10-01~10-07 是国庆，所以第 7 个是 10-14；按自然日加 7 天会得到 10-05（休市）
    assert got["due_date"] == "2026-10-14", got
    assert got["calendar_covered"] is True


def test_起算日落在周末时从下一个交易日往后数(monkeypatch):
    _Cal(monkeypatch, ["2026-10-08", "2026-10-09", "2026-10-12", "2026-10-13"], "2026-12-31")
    got = rn.due_on("2026-10-04", 2)              # 周日：08 是第 1 个，09 是第 2 个
    assert got["due_date"] == "2026-10-09" and got["calendar_covered"] is True


def test_超出日历覆盖时如实标注这不是交易日(monkeypatch):
    _Cal(monkeypatch, ["2026-12-28", "2026-12-29", "2026-12-30"], "2026-12-31")
    got = rn.due_on("2026-12-28", 20)
    assert got["calendar_covered"] is False
    assert "跳过周末" in got["explain"] and "不是交易日" in got["explain"]


@pytest.mark.parametrize("n", [0, -3, 251, 10_000])
def test_交易日数越界一律拒绝(n):
    with pytest.raises(rn.NoteError):
        rn.due_on("2026-09-28", n)


def test_日期写法不对就点名要求横杠分隔的年月日():
    with pytest.raises(rn.NoteError) as exc:
        rn._dash("2026-9-8")
    assert "YYYY-MM-DD" in str(exc.value)


# ---------------------------------------------------------------- 来源标记与保存校验

def test_baseline只补没有的键已有的一个字不改():
    merged = rn._merge_baseline({"env": "机器原文"},
                                {"env": "第二次算出来的别的原文", "pattern": "新草稿"})
    assert merged["env"] == "机器原文", "覆盖写会把「初始为机器填入·你已改」翻回「未改」"
    assert merged["pattern"] == "新草稿"


def test_baseline不收没登记的列():
    assert rn._merge_baseline({}, {"nope": "x"}) == {}


def test_未知字段存不进去并且点名():
    with pytest.raises(rn.NoteError) as exc:
        rn.save_note("2026-09-24", {"pice": "1"})
    msg = str(exc.value)
    assert "pice" in msg and "env" in msg, "要点名认不出的、并把可用的列出来（静默丢一栏最坏）"


def test_提醒类型与代码校验在写库之前就拒掉():
    with pytest.raises(rn.NoteError):
        rn.add_reminder("2026-09-28", 7, kind="whatever")
    with pytest.raises(rn.NoteError) as exc:
        rn.add_reminder("2026-09-28", 7, kind="stock", security_code="1")
    assert "6 位代码" in str(exc.value)
    with pytest.raises(rn.NoteError):
        rn.set_reminder_status(1, "archive")


# ---------------------------------------------------------------- 快讯粗筛：错分会让整格没人看

def test_外国央行与荷兰天然气不算国内政策():
    """两条实测抓到的：标题里的"央行"把外国央行新闻捞进国内桶，"交易所"把荷兰天然气捞进来。"""
    drug = {"title": "【通化安睿特重组人白蛋白国内审批卡壳】9月22日，国家药监局官网显示…"}
    mofcom = {"title": "商务部：1-8月我国企业在共建一带一路国家非金融类直接投资1672.4亿元"}
    bucket = rn._news_bucket([
        {"title": "巴西央行行长：不会被数据冲昏头脑。"},
        {"title": "洲际交易所（ICE）数据显示，荷兰近月批发天然气价格上涨5.5%"},
        drug, mofcom,
        {"title": "某公司发布人事变动说明"},
    ])
    assert bucket["domestic"] == [drug, mofcom]
    assert len(bucket["macro"]) == 2
    assert len(bucket["other"]) == 1, "归不了类的要计入「其余」，不能假装 244 条都分好类了"


# ---------------------------------------------------------------- 草稿：只走本机

class _Fake:
    """把 `defaults_for` 用到的每个源都换成假的。漏掉一个 = 那条测试会去碰真库。"""

    def __init__(self, monkeypatch, tmp_path: Path, settled: bool = True):
        import duanxian.breadth as breadth
        import duanxian.emotion_metrics as em
        import duanxian.journal as journal
        import duanxian.market_facts as mf
        import duanxian.pg as pg
        import duanxian.qd as qd
        import duanxian.theme_tree as tt
        import duanxian.trade_calendar as cal

        self.news_items = [{"title": "中国人民银行公告", "source": "sina-7x24",
                            "published_at": "2026-09-24 09:00:00", "news_type": "政策"}]
        monkeypatch.setattr(qd, "news", lambda date="", limit=200: {"available": True, "items": self.news_items})
        monkeypatch.setattr(em, "build_metrics", lambda date, with_cycle=True: {"date": date})
        monkeypatch.setattr(em, "render_metrics", lambda m: f"[派生 {m['date']}]")
        monkeypatch.setattr(em, "ladder_gap", lambda date: {"available": True, "continuous": True, "gaps": []})
        monkeypatch.setattr(em, "promotion_rates", lambda date: {
            "available": True, "prev_limit_up_count": 52,
            "tiers": {"1进2": {"base": 40, "promoted": 8, "rate": 0.2}},
            "overall": {"base": 52, "promoted": 13, "rate": 0.25}})
        monkeypatch.setattr(breadth, "market_breadth", lambda date: {
            "available": True, "up": 1078, "down": 4005, "flat": 126,
            "deep_down_5": 260, "amount_yi": 16531.0})
        monkeypatch.setattr(qd, "sentiment_cycle", lambda date: {"qd": {
            "volume_state": "缩量再缩量", "stage_ui": "试错", "metrics": {"amount_change_pct": -6.32}}})
        monkeypatch.setattr(qd, "board_ladder", lambda date: {
            "tiers": [{"tier_label": "二连板", "stock_count": 8}],
            "board_height": {"main_board_highest": 5}})
        monkeypatch.setattr(qd, "sector_cycle", lambda date: {"rotation": [{"items": [
            {"sector_name": "林业", "pct_change": 8.17, "up_count": 12, "down_count": 1,
             "member_count": 30}]}]})
        monkeypatch.setattr(mf, "seal_quality", lambda date: {
            "available": True, "total": 52, "never_broken": 22, "never_broken_rate": 0.423,
            "opening_seconds": 18, "late_seal": 8, "broken_pool": 10, "broken_rate": 0.161})
        monkeypatch.setattr(mf, "loss_effect", lambda date: {"limit_down_count": 2})
        self.trades: list[dict] = []
        monkeypatch.setattr(journal, "all_trades", lambda: self.trades)
        self.opened = pg.trade_day_open          # 摸一下真函数在不在，缺席就该当场 AttributeError
        monkeypatch.setattr(pg, "trade_day_open", lambda date: True)
        monkeypatch.setattr(cal, "is_settled", lambda date: settled)
        monkeypatch.setattr(cal, "prev_trade_date", lambda date: "2026-09-23")
        monkeypatch.setattr(cal, "next_trade_date", lambda date: "2026-09-28")
        # 库里实际有数的最近一场：落盘判据用的是这个，不是 is_settled
        monkeypatch.setattr(rn, "expected_date", lambda: "2026-09-24")
        # 三个缓存目录全指到 tmp（默认"什么都没有"）：谁指望真机有缓存，这条就会红
        (tmp_path / "facts").mkdir(parents=True, exist_ok=True)
        (tmp_path / "reasons").mkdir(parents=True, exist_ok=True)
        monkeypatch.setattr(mf, "_CACHE_DIR", str(tmp_path / "facts"))
        monkeypatch.setattr(tt, "_CACHE_DIR", str(tmp_path / "reasons"))
        monkeypatch.setattr(rn, "_DEFAULTS_DIR", str(tmp_path / "defaults"))
        self.tmp = tmp_path


def test_草稿一次都不联网(monkeypatch, tmp_path):
    """把两条联网入口换成"被调用就炸"：草稿必须根本不碰它们。"""
    import duanxian.market_facts as mf
    from duanxian import fetchers

    _Fake(monkeypatch, tmp_path)

    def boom(*a, **k):
        raise AssertionError("默认值不许联网")

    monkeypatch.setattr(fetchers, "fetch_zt_reasons", boom)
    monkeypatch.setattr(mf, "pools", boom)
    out = rn.defaults_for("2026-09-24")
    assert out["network_free"] is True
    assert "view_stock" in out["missing"], "涨停池没有落盘缓存 → 这一格要点名缺失，不能给 0 充数"


def test_草稿给出的列都有来源标注(monkeypatch, tmp_path):
    fake = _Fake(monkeypatch, tmp_path)
    fake.trades = [{"date": "2026-09-24", "code": "002879", "name": "测试", "playbook": "低吸",
                    "as_planned": False, "note": "看了二十日线"}]
    out = rn.defaults_for("2026-09-24")
    assert set(out["fields"]) <= set(rn.FIELD_KEYS)
    for key, v in out["fields"].items():
        assert v["from"], f"{key} 给了草稿却没说是从哪个读数算的"
    assert "计划外" in out["fields"]["exec_review"]["text"]


def test_没有事实源的列进empty而不是硬凑草稿(monkeypatch, tmp_path):
    _Fake(monkeypatch, tmp_path)
    out = rn.defaults_for("2026-09-24")
    assert "view_bigv" in out["empty"] and "view_bigv" not in out["fields"]
    text = out["fields"]["view_market"]["text"]
    assert "林业 +8.17%" in text, "板块涨幅前列必须进「对大盘、板块」那一格"
    assert "缩量再缩量" in text and "涨 1078 / 跌 4005" in text
    assert "上一场 2026-09-23" in out["fields"]["plan_market"]["text"], "开盘前的预判只能用上一场的读数"


def test_已定稿的草稿落盘第二次读缓存并交年龄(monkeypatch, tmp_path):
    fake = _Fake(monkeypatch, tmp_path)
    first = rn.defaults_for("2026-09-24")
    assert first.get("from_cache") is None
    assert (fake.tmp / "defaults" / "2026-09-24.json").is_file()
    second = rn.defaults_for("2026-09-24")
    assert second["from_cache"] is True and second["cache_written_at"]


def test_当天不落盘以免把没数据的样子留到明天(monkeypatch, tmp_path):
    fake = _Fake(monkeypatch, tmp_path, settled=False)
    rn.defaults_for("2026-09-28")
    assert not (fake.tmp / "defaults" / "2026-09-28.json").exists()


def test_已收盘但库里还没这一场同样不落盘(monkeypatch, tmp_path):
    """09-28 实测踩到的就是这一档：15:40 时 `is_settled('2026-09-28')` 已经 True，
    而 `stock_daily` 最大日还是 09-24。只按"收盘了"落盘，明天进来读的就是那份空草稿。"""
    fake = _Fake(monkeypatch, tmp_path, settled=True)     # expected_date 仍是 2026-09-24
    out = rn.defaults_for("2026-09-28")
    assert out["has_data"] is False and out["cacheable"] is False
    assert not (fake.tmp / "defaults" / "2026-09-28.json").exists()


def test_算法版本变了旧缓存必须失效(monkeypatch, tmp_path):
    """09-28 实测：改完分桶判据，不带版本号的缓存还在发旧内容 —— 代码改了、界面还是旧的。"""
    fake = _Fake(monkeypatch, tmp_path)
    rn.defaults_for("2026-09-24")
    path = fake.tmp / "defaults" / "2026-09-24.json"
    env = json.loads(path.read_text(encoding="utf-8"))
    env["builder"] = "rb-old"
    path.write_text(json.dumps(env, ensure_ascii=False), encoding="utf-8")
    assert rn.defaults_for("2026-09-24").get("from_cache") is None


def test_题材串只在落盘缓存里找(monkeypatch, tmp_path):
    fake = _Fake(monkeypatch, tmp_path)
    reasons, note = rn.cached_reasons("2026-09-24")
    assert reasons == {} and "没有落盘" in note

    from duanxian.theme_tree import _SCHEMA

    (fake.tmp / "reasons" / "2026-09-24.json").write_text(json.dumps(
        {"schema": _SCHEMA, "date": "2026-09-24", "reasons": {"002879": "光纤光缆+产能扩张"}},
        ensure_ascii=False), encoding="utf-8")
    got, why = rn.cached_reasons("2026-09-24")
    assert got == {"002879": "光纤光缆+产能扩张"} and why == "已落盘缓存"


def test_日期对不上的缓存一律不用(monkeypatch, tmp_path):
    """宁可空着，也不能把别的交易日的题材串当成这一场的。"""
    fake = _Fake(monkeypatch, tmp_path)
    (fake.tmp / "reasons" / "2026-09-24.json").write_text(json.dumps(
        {"schema": 1, "date": "2026-09-23", "reasons": {"002879": "别场题材"}}, ensure_ascii=False),
        encoding="utf-8")
    reasons, _note = rn.cached_reasons("2026-09-24")
    assert reasons == {}


# ---------------------------------------------------------------- 时间轴

def _row(id_=1, due="2026-10-14", status="open", **kw):
    return {"id": id_, "kind": "stock", "security_code": "002879", "security_name": "测试",
            "note": "等回调", "reason": "缩量回踩二十线", "base_date": "2026-09-28", "sessions": 7,
            "due_date": due, "calendar_covered": True, "status": status, "done_at": None,
            "created_at": None, "updated_at": None, **kw}


def test_过期未处理的不被过滤掉只把最近那条当next(monkeypatch):
    """用户问"当前往后最近的一条"，但过期那条更该被看见 —— 只交未来会让人以为过期=完成。"""
    rows = [_row(id_=1, due="2026-10-09"), _row(id_=2, due="2026-10-14"), _row(id_=3, due="2026-09-20")]
    monkeypatch.setattr(rn, "_reminder_rows", lambda status=None: [dict(r) for r in rows])
    got = rn.list_reminders(today="2026-09-28")
    assert got["next"]["id"] == 1
    assert [r["id"] for r in got["overdue"]] == [3]
    assert got["overdue"][0]["overdue_days"] == 8 and got["open_count"] == 3


def test_日历未覆盖那条在到期文案里说清楚(monkeypatch):
    monkeypatch.setattr(rn, "_reminder_rows",
                        lambda status=None: [_row(due="2027-01-18", calendar_covered=False)])
    monkeypatch.setattr(rn, "_calendar_horizon", lambda: "2026-12-31")
    got = rn.list_reminders(today="2026-09-28")
    assert "日历未覆盖" in got["rows"][0]["due_label"]
