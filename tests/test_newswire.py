"""快讯采集层（`duanxian/newswire.py`）的回归。

全部打桩 `_http_json`：不联网（`conftest.py` 本就拦出站）、不连库。
守的四件事都是这层**独有的诚实性风险**，不是随便覆盖行数：
① 发布时刻只能来自报文，格式坏了要丢行而不是回填采集时刻（quantdash 的 D-25）；
② `level`/`sentiment` 必须是 NULL，不许被"看着像"的字段（titleColor / is_focus）冒充；
③ 单源挂掉不许推进它的游标 —— 快讯不可回补，推错一次就永久缺一段；
④ 去重键带源前缀，两源的 id 空间不能撞。
"""

from __future__ import annotations

import datetime

import pytest

from duanxian import newswire as nw

CAPTURED = datetime.datetime(2026, 9, 24, 15, 0, 0, tzinfo=datetime.timezone(datetime.timedelta(hours=8)))

EM_ITEM = {"code": "202609243883922828", "title": "英国央行布雷登：退出量化宽松的计划至关重要",
           "summary": "英国央行布雷登表示，这使其成为更可信的工具。",
           "showTime": "2026-09-24 21:57:24", "realSort": "1790258244022828",
           "titleColor": 3, "stockList": []}
SINA_ITEM = {"id": 5112220, "create_time": "2026-09-24 22:08:19",
             "rich_text": "汇丰将<b>梅赛德斯-奔驰</b>目标价下调", "docurl": "https://live.finance.sina.com.cn/x",
             "tag": [{"name": "国际"}], "is_focus": 1}


# ------------------------------------------------------------------------ 归一化

def test_east_row_发布时刻带东八区且与采集时刻分开():
    r = nw.normalize_east(EM_ITEM, CAPTURED)
    assert r["published_at"] == datetime.datetime(2026, 9, 24, 21, 57, 24, tzinfo=nw._CN_TZ)
    assert r["captured_at"] == CAPTURED
    assert r["published_at"] != r["captured_at"]
    assert r["item_id"] == "em-202609243883922828"
    assert r["source"] == "eastmoney-7x24"


def test_sina_row_去HTML且标题是截断不是原文字段():
    r = nw.normalize_sina(SINA_ITEM, CAPTURED)
    assert r["content"] == "汇丰将梅赛德斯-奔驰目标价下调"
    assert "<" not in r["content"]
    assert r["item_id"] == "sina-5112220"
    assert any(d["code"] == "SINA-T" for d in r["deviation"])     # 截断这件事必须留痕


@pytest.mark.parametrize("bad", ["", None, "不是时间", "2026-13-45 99:99:99"])
def test_发布时刻解析不了就丢行_绝不回填采集时刻(bad):
    assert nw._parse_published(bad) is None
    assert nw.normalize_east({**EM_ITEM, "showTime": bad}, CAPTURED) is None
    assert nw.normalize_sina({**SINA_ITEM, "create_time": bad}, CAPTURED) is None


def test_标题为空丢行():
    assert nw.normalize_east({**EM_ITEM, "title": "   "}, CAPTURED) is None


def test_level_与_sentiment_必须_NULL_不被_titleColor_is_focus_冒充():
    for r in (nw.normalize_east(EM_ITEM, CAPTURED), nw.normalize_sina(SINA_ITEM, CAPTURED)):
        assert r["level"] is None
        assert r["sentiment"] is None
        assert any(d["code"] == "SRC-2" for d in r["deviation"])   # 源替了要说


def test_缺稳定id时的兜底去重键可复现():
    a = nw.normalize_east({k: v for k, v in EM_ITEM.items() if k != "code"}, CAPTURED)
    b = nw.normalize_east({k: v for k, v in EM_ITEM.items() if k != "code"}, CAPTURED)
    assert a["item_id"].startswith("em-noid-") and a["item_id"] == b["item_id"]


def test_两源的id空间不互撞():
    assert nw.normalize_east({**EM_ITEM, "code": "5112220"}, CAPTURED)["item_id"] \
        != nw.normalize_sina(SINA_ITEM, CAPTURED)["item_id"]


# ------------------------------------------------------------------------ 游标

def _stub(monkeypatch, east=None, sina=None):
    def fake(url, timeout=12):
        if "eastmoney" in url:
            if isinstance(east, Exception):
                raise east
            return {"data": {"fastNewsList": east, "sortEnd": ""}}
        if isinstance(sina, Exception):
            raise sina
        return {"result": {"data": {"feed": {"list": sina}}}}
    monkeypatch.setattr(nw, "_http_json", fake)
    monkeypatch.setattr(nw, "china_now", lambda: CAPTURED)


def test_东财挂时新浪照采_且东财游标不动(monkeypatch):
    _stub(monkeypatch, east=IOError("RemoteDisconnected"), sina=[SINA_ITEM])
    res = nw.collect_once({"em": "1790258000000000"}, backfill_minutes=240)
    assert res["available"] is True
    assert len(res["rows"]) == 1
    assert res["cursors"]["em"] == "1790258000000000"        # 原值保留
    assert res["cursors"]["sina"] == "5112220"


def test_两源全挂_available_为假且游标一字不改(monkeypatch):
    boom = IOError("connection reset")
    _stub(monkeypatch, east=boom, sina=boom)
    cursors = {"em": "111", "sina": "222"}
    res = nw.collect_once(cursors, backfill_minutes=240)
    assert res["available"] is False
    assert "抓取失败" in res["reason"]
    assert res["cursors"] == cursors
    assert res["rows"] == []


def test_超出回溯窗口的不入库(monkeypatch):
    _stub(monkeypatch, east=[{**EM_ITEM, "showTime": "2020-01-01 09:00:00"}], sina=[])
    res = nw.collect_once({}, backfill_minutes=240)
    assert res["rows"] == []
    assert res["cursors"]["em"] == EM_ITEM["realSort"]        # 拉到了就推进游标，与是否入库无关


def test_同轮内重复条目只留一条(monkeypatch):
    _stub(monkeypatch, east=[EM_ITEM, EM_ITEM], sina=[SINA_ITEM, SINA_ITEM])
    res = nw.collect_once({}, backfill_minutes=240)
    assert len(res["rows"]) == 2


def test_游标取各条中最大的realSort(monkeypatch):
    _stub(monkeypatch, east=[{**EM_ITEM, "realSort": "100"}, {**EM_ITEM, "realSort": "300"}], sina=[])
    assert nw.collect_once({}, backfill_minutes=240)["cursors"]["em"] == "300"


def test_东财那一页最大值比已存游标小时不许回退(monkeypatch):
    """实测会发生（realSort 不是严格最新在前）。回退=每轮重取一段老消息，
    去重挡得住写重复，但挡不住"最近采到哪"这个观测值变假。"""
    _stub(monkeypatch, east=[{**EM_ITEM, "realSort": "100"}], sina=[])
    res = nw.collect_once({"em": "900"}, backfill_minutes=240)
    assert res["cursors"]["em"] == "900"


def test_monotonic对非数字游标取新值():
    assert nw._monotonic("900", "100") == "900"
    assert nw._monotonic("100", "900") == "900"
    assert nw._monotonic("", "7") == "7"
    assert nw._monotonic("7", "") == "7"
    assert nw._monotonic("abc", "def") == "def"


# ------------------------------------------------------------------------ 落盘

@pytest.fixture
def data_home(tmp_path, monkeypatch):
    """把业务数据目录指到 tmp —— 测试不碰真 `~\\.duanxian-agents`。"""
    monkeypatch.setenv("ASTOCK_DATA_HOME", str(tmp_path))
    return tmp_path


def _result(rows):
    return {"rows": rows, "cursors": {"em": "1790258877002929", "sina": "5112234"}}


def _lines(path):
    import json
    out = []
    for x in path.read_text(encoding="utf-8").splitlines():
        try:
            out.append(json.loads(x))
        except json.JSONDecodeError:      # 半行（上次写崩）应当被跳过而不是炸整档
            continue
    return out


def test_按发布时刻分日落盘而不是采集时刻(data_home):
    rows = [nw.normalize_east(EM_ITEM, CAPTURED),                      # 09-24 21:57
            nw.normalize_sina({**SINA_ITEM, "create_time": "2026-09-23 23:50:00"}, CAPTURED)]
    out = nw.persist(_result(rows))
    assert out["ok"] and sorted(out["days"]) == ["2026-09-23", "2026-09-24"]
    d23 = data_home / "news" / "news-2026-09-23.jsonl"
    assert len(_lines(d23)) == 1                       # 夜里 11:50 的消息归昨天，不归"今天"


def test_重跑幂等同一条不写两遍(data_home):
    rows = [nw.normalize_east(EM_ITEM, CAPTURED)]
    nw.persist(_result(rows))
    again = nw.persist(_result(rows))
    assert again["written"] == 0 and again["already_had"] == 1
    f = data_home / "news" / "news-2026-09-24.jsonl"
    assert len(_lines(f)) == 1


def test_落盘失败时游标不推进(data_home, monkeypatch):
    def boom(*a, **k):
        raise OSError("disk full")
    monkeypatch.setattr(nw, "atomic_write_json", boom)
    out = nw.persist(_result([nw.normalize_east(EM_ITEM, CAPTURED)]))
    assert out["ok"] is False and "OSError" in out["reason"]
    assert not (data_home / "news" / nw.CURSOR_FILE).exists()     # 下轮会重取，不静默丢


def test_文件里有半行也不连累整档(data_home):
    nw.persist(_result([nw.normalize_east(EM_ITEM, CAPTURED)]))
    f = data_home / "news" / "news-2026-09-24.jsonl"
    f.write_text(f.read_text(encoding="utf-8") + '{"item_id": "tru', encoding="utf-8")  # 上次写崩
    out = nw.persist(_result([nw.normalize_sina(SINA_ITEM, CAPTURED)]))
    assert out["ok"] and out["written"] == 1
    assert len(_lines(f)) == 2                                   # 半行被跳过，两条好的还在


def test_没游标文件时从零开始而非报错(data_home):
    assert nw.read_cursors() == {}
    nw.persist(_result([nw.normalize_east(EM_ITEM, CAPTURED)]))
    assert nw.read_cursors()["em"] == "1790258877002929"


def test_落的是业务数据目录不是仓库local(data_home):
    assert nw.news_dir().startswith(str(data_home))
    assert ".local" not in nw.news_dir()


# ------------------------------------------------------------------ 常驻轮询宿主

def test_测试进程内不起轮询线程():
    """conftest 拦了出站 socket；线程照起就是每 30 秒打一条"假故障"日志。"""
    import server
    assert server._newswire_enabled() is False


def test_VIBE_NEWS_开关与幂等(monkeypatch):
    import threading

    import server
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    for value, want in (("0", False), ("off", False), ("", True), ("1", True)):
        monkeypatch.setenv("VIBE_NEWS", value)
        assert server._newswire_enabled() is want, value

    started = []

    class _FakeThread:
        def __init__(self, *a, **k):
            started.append(k.get("name"))

        def start(self):
            pass

    monkeypatch.setattr(threading, "Thread", _FakeThread)
    monkeypatch.setattr(server, "_newswire_thread_started", False)
    monkeypatch.setenv("VIBE_NEWS", "1")
    server._start_newswire()
    server._start_newswire()                 # 第二次必须不重复起
    assert started == ["newswire"]

def test_stockList_提取成六位码但不猜市场后缀():
    r = nw.normalize_east({**EM_ITEM, "stockList": ["999.673646", "1.688498", "坏东西", "002119"]},
                          CAPTURED)
    assert r["related_codes"] == ["673646", "688498", "002119"]     # 只留 6 位数字，不补 .SH/.SZ
    assert nw.normalize_sina(SINA_ITEM, CAPTURED)["related_codes"] == []


def test_每轮运行记录要落盘且区分从未跑过(data_home, monkeypatch):
    assert nw.read_state() == {}                                     # 从未跑过的样子
    _stub(monkeypatch, east=[EM_ITEM], sina=[SINA_ITEM])
    res = nw.collect_once({}, backfill_minutes=999999)
    out = nw.persist(res)
    assert out["ok"] is True
    st = nw.read_state()
    assert st["last_ok_at"] and st["ever_ran"] is True and st["consecutive_failures"] == 0
    assert st["written"] == out["written"] and st["rows_in_round"] == 2
    assert st["sources"]["eastmoney-7x24"]["ok"] is True
    assert st["cursors"]["em"] == EM_ITEM["realSort"]


def test_失败也要留证据而不是清空上一轮(data_home):
    monkey = nw.persist.__globals__
    real = monkey["atomic_write_json"]
    monkey["atomic_write_json"] = lambda *a, **k: (_ for _ in ()).throw(OSError("disk full"))
    try:
        out = nw.persist({"rows": [], "cursors": {}})
    finally:
        monkey["atomic_write_json"] = real
    assert out["ok"] is False and "OSError" in out["reason"]


# ------------------------------------------------------------------ 常驻轮询的记账
# 2026-09-28：`/api/jobs`、任务调度大全和 `manage.py doctor` 都读 `jobs/state.json`，
# 而这条常驻线程过去只写 `newswire_state.json` + jsonl。结果是体检对着一个每 30 秒
# 都在变大的文件报"最近成功停在 30 小时前"——**假故障**，界面和日志互相矛盾，
# 比真故障更难查。下面三条钉的是"记了、按什么节奏记、归因放在哪个键"。

def test_一轮采集要把结果与归因交给记账(monkeypatch, data_home):
    import server

    # 直接 patch 真模块上的函数：`server` 里是 `from duanxian import newswire` 再取属性，
    # 换成假模块不会被读到（包属性已经指向真模块了）—— 第一版就是这么假的绿了。
    monkeypatch.setattr(nw, "read_cursors", lambda: {"em": "100"})
    monkeypatch.setattr(nw, "collect_once", lambda cursors: {
        "available": True, "rows": [{"item_id": "1"}, {"item_id": "2"}],
        "cursors": {"em": "102"}, "dropped": 0, "sources": {}})
    monkeypatch.setattr(nw, "persist", lambda res: {"ok": True, "written": 2})
    ok, detail, gap = server._newswire_round()
    assert ok is True and detail["written"] == 2 and detail["reason"] is None
    assert gap == 30.0


def test_本轮无数据也要把原因记下来不许只留个失败状态(monkeypatch, data_home):
    import server

    monkeypatch.setattr(nw, "read_cursors", lambda: {})
    monkeypatch.setattr(nw, "collect_once", lambda cursors: {
        "available": False, "reason": "两个快讯源均抓取失败", "rows": []})
    ok, detail, _ = server._newswire_round()
    assert ok is False
    assert detail["reason"] == "两个快讯源均抓取失败"     # `jobs.record()` 只认 reason 键


def test_记账节奏_翻转立刻记稳定期十五分钟一次():
    import server
    due = server._newswire_due_to_record
    assert due(True, None, 0) is True          # 头一轮必记，否则页面永远"从未成功"
    assert due(False, True, 1) is True         # 成功→失败：立刻记
    assert due(True, False, 1) is True         # 失败→成功：立刻记（这是恢复的信号）
    assert due(True, True, 899) is False       # 稳定期不刷
    assert due(True, True, 901) is True        # 最多 15 分钟一次心跳


def test_记账写进jobs状态且失败次数会累计(monkeypatch, data_home):
    """这一条是那个假故障的正解：记上了，页面才看得到"最近成功"。"""
    import server
    from duanxian import jobs

    server._newswire_journal(True, {"written": 2, "reason": None})
    st = jobs.state().get("news") or {}
    assert st["last_ok_at"] and st["last_status"] == "ok"
    assert st["consecutive_failures"] == 0
    server._newswire_journal(False, {"reason": "两个快讯源均抓取失败"})
    st2 = jobs.state()["news"]
    assert st2["consecutive_failures"] == 1 and st2["last_error"] == "两个快讯源均抓取失败"
    assert st2["last_ok_at"] == st["last_ok_at"]     # 失败不许把成功时间往前推
