"""同花顺自选解析 + 同步计划（`ingest/ths_local.py` / `duanxian/watchlist_sync.py`）。

全打桩：不联网、不连库（`conftest.py` 本就拦出站）。守的是三件会**默默丢数据**的事：
① 多行组名（用户在组名里写策略笔记）不能被解析成丢组或截断到没人知道；
② 认不出的市场号必须丢弃并计数，猜市场会造出 join 不上主档的孤行；
③ 全量覆盖不许把源里没有的列（`is_focus`、`note`）刷成默认值 —— 那是用户手工标记。
"""

from __future__ import annotations

import os

import pytest

from duanxian import watchlist_sync as ws
from ingest import ths_local as ths

INI = (
    "[ConfigInfo]\r\n"
    "ConfigName=stockblock_同花顺方案\r\n"
    "[BLOCK_NAME_MAP_TABLE]\r\n"
    "23=犇犇熊\r\n"
    "141=隔夜持股法：\r\n"
    "1.?下午2点40左右打开涨幅榜，选择涨幅3%-5%的主板股票。\r\n"
    "2.?按量比排序，删除量比小于1的股票。\r\n"
    "2A=先进封装\r\n"
    "[BLOCK_STOCK_CONTEXT]\r\n"
    "23=33:301308,17:603000,-105:920599,48:881270,36:000001,坏token\r\n"
    "141=33:000001\r\n"
    "2A=\r\n"
    "[encrypt_section]\r\n"
    "x=1\r\n"
    "[@7]\r\n"
    "26=536871424\r\n"
)


def _write_ini(tmp_path, text=INI, quote=None):
    udir = tmp_path / "测试用户"
    udir.mkdir(parents=True, exist_ok=True)
    (udir / "stockblock.ini").write_bytes(text.encode("gb18030"))
    if quote is not None:
        import json
        (udir / "SelfStockInfo.json").write_text(json.dumps(quote), encoding="utf-8")
    return udir


# ------------------------------------------------------------------------ 解析

def test_多行组名只取第一行但续行要计数(tmp_path):
    udir = _write_ini(tmp_path)
    r = ths.read_watchlist(udir.parent)
    assert r["available"] is True
    names = [g["name"] for g in r["groups"]]
    assert "隔夜持股法：" in names                      # 组没丢
    assert len(names) == 3
    assert r["multiline_name_lines"] == 2               # 续了两行必须报出来
    assert not any("?" in n for n in names)             # 也没把笔记并进组名


def test_顺序就是客户端左栏顺序(tmp_path):
    r = ths.read_watchlist(_write_ini(tmp_path).parent)
    assert [g["display_order"] for g in r["groups"]] == [1, 2, 3]
    assert [g["name"] for g in r["groups"]] == ["犇犇熊", "隔夜持股法：", "先进封装"]


def test_未知段不解析但键值不串段(tmp_path):
    r = ths.read_watchlist(_write_ini(tmp_path).parent)
    codes = {g["code"] for g in r["groups"]}
    assert "26" not in codes and "x" not in codes        # @7 / encrypt_section 的键没混进来


def test_市场号认不出就丢并计数_绝不猜(tmp_path):
    r = ths.read_watchlist(_write_ini(tmp_path).parent)
    g0 = r["groups"][0]
    assert g0["members"] == ["301308.SZ", "603000.SH", "920599.BJ"]   # 保序
    assert r["dropped"].get("48") == 1 and r["dropped"].get("36") == 1
    assert r["dropped"].get("格式不认识") == 1
    assert "2A" in {g["code"] for g in r["groups"]}      # 空组成员也要保留组


def test_文件不在时明确不可用不返回半成品(tmp_path):
    r = ths.read_watchlist(tmp_path / "没有这个目录")
    assert r["available"] is False and "stockblock.ini" in r["reason"]


def test_缺段时报格式变了而不是给空表(tmp_path):
    udir = _write_ini(tmp_path, "[ConfigInfo]\r\nConfigName=x\r\n")
    r = ths.read_watchlist(udir.parent)
    assert r["available"] is False and "BLOCK_NAME_MAP_TABLE" in r["reason"]


def test_成本与加入日只认A股且空值给None():
    q = ths.parse_costs([
        {"C": "001208", "M": "33", "P": "21.87", "T": "20251225"},
        {"C": "SOXX", "M": "185", "P": "507.68", "T": "20260803"},   # 美股 → 不收录
        {"C": "600000", "M": "17", "P": "", "T": ""},                 # 空串 → None，不填 0
        {"C": "688260", "M": "17", "P": "-1", "T": "20260101"},       # 负价 → None
    ])
    assert q["001208.SZ"] == {"cost": 21.87, "added": "2025-12-25"}
    assert set(q) == {"001208.SZ", "600000.SH", "688260.SH"}
    assert q["600000.SH"] == {"cost": None, "added": None}
    assert q["688260.SH"]["cost"] is None
    assert "SOXX" not in str(q)


def test_gb18030坏字节不致命(tmp_path):
    udir = tmp_path / "u"
    udir.mkdir()
    raw = (b"[BLOCK_NAME_MAP_TABLE]\r\n23=" + b"\xff\xfe" + "坏字节".encode("gb18030")
           + b"\r\n[BLOCK_STOCK_CONTEXT]\r\n23=17:600000\r\n")
    (udir / "stockblock.ini").write_bytes(raw)
    r = ths.read_watchlist(tmp_path)
    assert r["available"] and r["group_count"] == 1
    assert r["groups"][0]["members"] == ["600000.SH"]


# ------------------------------------------------------------------------ 计划

def _plan(ini_groups, db):
    return ws.build_plan({"groups": ini_groups}, db)


def test_计划区分新建组删除组与成员增减():
    ini = [{"name": "留着", "display_order": 1, "members": ["600000.SH", "000001.SZ"]},
           {"name": "新组", "display_order": 2, "members": ["300750.SZ"]}]
    db = {"留着": {"group_id": 1, "is_focus": False, "note": "同花顺导入", "members": {"600000.SH": {}}},
          "要删": {"group_id": 2, "is_focus": False, "note": None,
                   "members": {"002119.SZ": {}, "600119.SH": {}}}}
    p = _plan(ini, db)
    assert [g["name"] for g in p["new_groups"]] == ["新组"]
    assert [g["name"] for g in p["drop_groups"]] == ["要删"]
    assert p["add_members"] == 2 and p["drop_members"] == 2
    assert p["changed_groups"] == [{"name": "留着", "add": 1, "drop": 0}]


def test_增量模式一条都不删并把留下的条数点名():
    """用户 2026-10-07 要的那一档：`sync_mode=append` = 只加不删。

    钉的是"不许静默"：留在库里的那些必须被数出来并交出去，否则 `append` 会被读成
    "同步没变化"，而实际是"同花顺已经删掉的票这里还留着" —— 镜像从此不等于真源。
    """
    ini = [{"name": "留着", "display_order": 1, "members": ["600000.SH", "000001.SZ"]}]
    db = {"留着": {"group_id": 1, "is_focus": False, "note": None,
                   "members": {"600000.SH": {}, "002119.SZ": {}}},
          "ini 已删": {"group_id": 2, "is_focus": False, "note": None,
                        "members": {"300750.SZ": {}}}}
    full = ws.build_plan({"groups": ini}, db)
    assert [g["name"] for g in full["drop_groups"]] == ["ini 已删"]
    assert full["drop_members"] == 2 and full["mode"] == "full"

    app = ws.build_plan({"groups": ini}, db, "append")
    assert app["drop_groups"] == [] and app["drop_members"] == 0
    assert [g["name"] for g in app["kept_absent_groups"]] == ["ini 已删"]
    assert app["kept_absent_members"] == 2          # 组里少的那 1 只 + 整组那 1 只
    assert app["add_members"] == 1                  # 该加的照加
    assert app["changed_groups"] == [{"name": "留着", "add": 1, "drop": 0}]
    assert app["mode"] == "append"


def test_默认档就是全量镜像_不是一句口号():
    """`build_plan` 的默认参数、`jobs.WATCHLIST_DEFAULTS`、页面文案三处必须是同一个意思。"""
    import inspect

    from duanxian import jobs
    assert inspect.signature(ws.build_plan).parameters["mode"].default == "full"
    assert inspect.signature(ws.apply_plan).parameters["mode"].default == "full"
    assert jobs.WATCHLIST_DEFAULTS["sync_mode"] == "full"
    assert ws.build_plan({"groups": []}, {})["mode"] == "full"


def test_is_focus_与_note_按名接续不被默认值抹掉():
    ini = [{"name": "重点组", "display_order": 1, "members": ["600000.SH"]}]
    db = {"重点组": {"group_id": 7, "is_focus": True, "note": "我自己写的备注",
                     "members": {"600000.SH": {}}}}
    p = _plan(ini, db)
    assert p["focus_kept"] == ["重点组"] and p["notes_kept"] == 1
    assert p["add_members"] == 0 and p["drop_members"] == 0        # 零变化就是零变化


# ------------------------------------------------------------------ 常驻宿主

def test_测试进程内不起同步线程且开关可关():
    import threading

    import server
    assert server._watchlist_enabled() is False          # pytest 下不真出网/不写库
    monkey_off = os.environ.pop("PYTEST_CURRENT_TEST", None)
    try:
        for value, want in (("0", False), ("off", False), ("1", True)):
            os.environ["VIBE_WATCHLIST_SYNC"] = value
            assert server._watchlist_enabled() is want, value
        os.environ["VIBE_WATCHLIST_SYNC"] = "1"
        started = []

        class _T:
            def __init__(self, *a, **k):
                started.append(k.get("name"))

            def start(self):
                pass

        import threading as th
        real = th.Thread
        th.Thread = _T
        try:
            server._watchlist_thread_started = False
            server._start_watchlist_sync()
            server._start_watchlist_sync()               # 幂等：不许起第二个
        finally:
            th.Thread = real
        assert started == ["watchlist-sync"]
    finally:
        if monkey_off is not None:
            os.environ["PYTEST_CURRENT_TEST"] = monkey_off
        os.environ.pop("VIBE_WATCHLIST_SYNC", None)


def test_同步时点配置坏了要回落默认值不炸():
    import server
    for value, want in (("", "20:40"), ("20:40", "20:40"), ("乱七八糟", "20:40"),
                        ("8:30", "20:40")):
        os.environ["WATCHLIST_SYNC_AT"] = value
        assert server._watchlist_slot() == want, value
    os.environ["WATCHLIST_SYNC_AT"] = "21:15"
    assert server._watchlist_slot() == "21:15"
    os.environ.pop("WATCHLIST_SYNC_AT", None)

def test_锁互斥且陈旧锁可接管(tmp_path, monkeypatch):
    monkeypatch.setattr(ws, "data_path", lambda *p: str(tmp_path))
    got, why = ws.acquire_lock()
    assert got is not None and why == ""
    again, why2 = ws.acquire_lock()
    assert again is None and "另一个同步在跑" in why2      # pid 还活着 → 不接管
    ws.release_lock(got)
    third, _ = ws.acquire_lock()
    assert third is not None
    ws.release_lock(third)


def test_pid已消失的陈旧锁被接管(tmp_path, monkeypatch):
    monkeypatch.setattr(ws, "data_path", lambda *p: str(tmp_path))
    path, _ = ws.acquire_lock()
    ws.release_lock(path)
    got, _ = ws.acquire_lock()
    got.write_text("999999 2020-01-01T00:00:00\n", encoding="utf-8")
    import time
    old = time.time() - 99999
    import os
    os.utime(got, (old, old))
    monkeypatch.setattr(ws, "_pid_alive", lambda pid: False)
    got2, _ = ws.acquire_lock()
    assert got2 is not None                                # 崩溃残留不该永久锁死同步
    ws.release_lock(got2)


def test_从未跑过与跑过零变化是两种状态(tmp_path, monkeypatch):
    monkeypatch.setattr(ws, "data_path", lambda *p: str(tmp_path))
    empty = ws.read_state()
    assert empty["last_ok_at"] is None and empty.get("ever_ran") is not True
    ws._write_state(empty, True, None, {"members_added": 0, "members_dropped": 0})
    ran = ws.read_state()
    assert ran["last_ok_at"] and ran["ever_ran"] is True
    assert ran["members_added"] == 0                       # "跑了但零变化"有独立证据


def test_失败要累计连续失败次数并留原因(tmp_path, monkeypatch):
    monkeypatch.setattr(ws, "data_path", lambda *p: str(tmp_path))
    st = ws.read_state()
    ws._write_state(st, False, "锁被占", {})
    st2 = ws.read_state()
    assert st2["consecutive_failures"] == 1 and st2["last_ok_at"] is None
    ws._write_state(st2, True, None, {})
    st3 = ws.read_state()
    assert st3["consecutive_failures"] == 0        # 成功才清零
    assert st3["last_ok_at"] == st3["last_run_at"]


# ------------------------------------------------------------- 成员每日快照
# 2026-09-26 与数据加工对齐：唯一落点 `reference_data.watchlist_member_snapshot`，
# 唯一写者是"读 ini 的这一个"。下面守的是这次改动引入的四个可回退点。

def _ini_parsed(groups):
    return {"available": True, "ini": "…/stockblock.ini", "ini_mtime": "2026-09-24T10:42:10",
            "user": "u", "group_count": len(groups), "member_rows": 0, "unique_codes": 0,
            "dropped": {}, "multiline_name_lines": 0, "duplicate_codes": 0,
            "quote_source": None, "quote": {}, "groups": groups}


class _Cur:
    def __init__(self, log, fetchone_result=None):
        self.log = log
        self.rowcount = 0
        self.fetchone_result = fetchone_result

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params=None):
        self.log.append((" ".join(sql.split()), params))
        self.rowcount = 7

    def fetchone(self):
        return self.fetchone_result

    def executemany(self, sql, seq):
        self.log.append((" ".join(sql.split()), list(seq)))


class _Conn:
    def __init__(self, log, fetchone_result=(True,)):
        self.log = log
        self.committed = False
        self.rolled = False
        self.fetchone_result = fetchone_result

    def cursor(self):
        return _Cur(self.log, self.fetchone_result)

    def commit(self):
        self.committed = True

    def rollback(self):
        self.rolled = True

    def close(self):
        pass


def test_快照行取ini成员_id只按组名查库_新组不许编id():
    ini = _ini_parsed([{"name": "犇犇熊", "members": ["301308", "603000", "301308"]},
                       {"name": "昨天刚建的组", "members": ["000001"]}])
    db = {"犇犇熊": {"group_id": 23, "members": ["600000"]}}     # 镜像落后于 ini，故意给不同的票
    rows, skipped = ws.snapshot_rows(ini, db)
    assert rows == [(23, "犇犇熊", "301308"), (23, "犇犇熊", "603000")]   # 取源、组内去重保序
    assert "600000" not in [r[2] for r in rows]                  # 镜像里的陈旧成员不许混进来
    assert skipped == ["昨天刚建的组"]                    # 查不到 id 就跳过并报名，编出来的会撞真 id


def test_快照落点是reference_data且整条SQL不碰ys影子表(monkeypatch):
    log = []
    conn = _Conn(log)
    monkeypatch.setattr(ws, "_connect_etl", lambda: conn)
    monkeypatch.setattr(ws, "read_db", lambda c: {"犇犇熊": {"group_id": 23, "members": []}})
    out = ws.write_snapshot(_ini_parsed([{"name": "犇犇熊", "members": ["600000", "000001"]}]),
                            "2026-09-28")
    assert ws.SNAPSHOT_TABLE == "reference_data.watchlist_member_snapshot"
    assert out["rows"] == 2 and out["replaced"] == 7
    joined = "\n".join(s for s, _ in log)
    assert "delete from reference_data.watchlist_member_snapshot" in joined   # 当日整批替换
    assert "ys." not in joined
    assert conn.committed and not conn.rolled                     # 独立事务，自己提交


def test_probe落快照必须回滚不留痕(monkeypatch):
    conn = _Conn([])
    monkeypatch.setattr(ws, "_connect_etl", lambda: conn)
    monkeypatch.setattr(ws, "read_db", lambda c: {"犇犇熊": {"group_id": 23, "members": []}})
    out = ws.write_snapshot(_ini_parsed([{"name": "犇犇熊", "members": ["600000"]}]),
                            "2026-09-28", probe=True)
    assert conn.rolled and not conn.committed
    assert out["rolled_back"] is True


def test_非交易日落快照直接被日历闸拒绝(monkeypatch):
    """表里那行 `2026-09-26`（周六、`is_open=false`、1283 行）就是没这道闸时写进去的。
    `run()` 上面虽有 `open_state`，但手动跑/新调用方把归属日传成周六照样能绕 —— 所以
    落库函数自己再查一次。"""
    conn = _Conn([], fetchone_result=(False,))     # 日历说：这天不开市
    monkeypatch.setattr(ws, "_connect_etl", lambda: conn)
    monkeypatch.setattr(ws, "read_db", lambda c: {"犇犇熊": {"group_id": 23, "members": []}})
    with pytest.raises(RuntimeError) as e:
        ws.write_snapshot(_ini_parsed([{"name": "犇犇熊", "members": ["600000.SH"]}]),
                          "2026-09-26")
    assert "不是交易日" in str(e.value)
    assert not conn.committed
    assert not any(s.startswith("insert into") for s, _ in conn.log)   # 一条都没写


def test_日历查无此日也不许写(monkeypatch):
    conn = _Conn([], fetchone_result=None)
    monkeypatch.setattr(ws, "_connect_etl", lambda: conn)
    monkeypatch.setattr(ws, "read_db", lambda c: {"犇犇熊": {"group_id": 23, "members": []}})
    with pytest.raises(RuntimeError) as e:
        ws.write_snapshot(_ini_parsed([{"name": "犇犇熊", "members": ["600000.SH"]}]),
                          "2026-12-31")
    assert "查无此日" in str(e.value)


def test_probe允许假归属日因为一定回滚(monkeypatch):
    conn = _Conn([])
    monkeypatch.setattr(ws, "_connect_etl", lambda: conn)
    monkeypatch.setattr(ws, "read_db", lambda c: {"犇犇熊": {"group_id": 23, "members": []}})
    out = ws.write_snapshot(_ini_parsed([{"name": "犇犇熊", "members": ["600000.SH"]}]),
                            "2099-01-01", probe=True)
    assert out["rolled_back"] is True and conn.rolled


def test_非交易日一律不落快照哪怕备份和库都齐备(monkeypatch, tmp_path):
    """`open_state` 是唯一的闸。周六写进去一行，`W=21` 的窗口起点就落在没有行情的日子上
    —— 2026-09-26 实测这张表里那唯一一行就是这么来的（`is_open=false`、1283 行）。"""
    calls = []
    monkeypatch.setattr(ws, "data_path", lambda *p: str(tmp_path))
    monkeypatch.setattr(ws.ths_local, "read_watchlist",
                        lambda *a, **k: _ini_parsed([{"name": "犇犇熊", "members": ["600000.SH"]}]))
    monkeypatch.setattr(ws, "etl_ready", lambda: True)
    monkeypatch.setattr(ws, "acquire_lock", lambda: (object(), None))
    monkeypatch.setattr(ws, "release_lock", lambda lk: None)
    monkeypatch.setattr(ws, "_connect_etl", lambda: _Conn([]))
    monkeypatch.setattr(ws, "job_state", lambda c: (False, False, None))
    monkeypatch.setattr(ws, "read_db", lambda c: {"犇犇熊": {"group_id": 23, "members": []}})
    monkeypatch.setattr(ws, "current_owner", lambda c, fallback="fin": "fin")
    monkeypatch.setattr(ws, "build_plan", lambda i, d, *a: {
        "new_groups": [], "drop_groups": [], "changed_groups": [],
        "add_members": 0, "drop_members": 0, "members_total": 1})
    monkeypatch.setattr(ws, "backup_current", lambda c, t: 0)
    monkeypatch.setattr(ws, "apply_plan", lambda *a, **k: {"groups_new": 0})
    monkeypatch.setattr(ws, "open_state", lambda c: (False, "2026-09-26"))
    monkeypatch.setattr(ws, "write_snapshot",
                        lambda *a, **k: calls.append(a) or {"trade_date": a[1], "rows": 1})

    rep = ws.run(write=True)
    assert rep["ok"] is True
    assert calls == []                                        # 一天都没写
    assert rep["member_snapshot"]["rows"] == 0
    assert "非交易日" in rep["member_snapshot"]["skipped"]


def test_config覆盖被权限顶回时当天名单照样落快照(monkeypatch, tmp_path):
    """这条就是这次改动的全部理由：`config.*` 卡在 `config_history` 触发器权限上 → 整笔回滚，
    而成员序列**只能当天存、事后补不出来**。旧写法把快照挂在覆盖同一个事务里，
    于是 09-26 那天名单一行没留下。"""
    calls = []
    monkeypatch.setattr(ws, "data_path", lambda *p: str(tmp_path))
    monkeypatch.setattr(ws.ths_local, "read_watchlist",
                        lambda *a, **k: _ini_parsed([{"name": "犇犇熊", "members": ["600000"]}]))
    monkeypatch.setattr(ws, "etl_ready", lambda: True)
    monkeypatch.setattr(ws, "acquire_lock", lambda: (object(), None))
    monkeypatch.setattr(ws, "release_lock", lambda lk: None)
    monkeypatch.setattr(ws, "_connect_etl", lambda: _Conn([]))
    monkeypatch.setattr(ws, "job_state", lambda c: (False, False, None))
    monkeypatch.setattr(ws, "read_db", lambda c: {"犇犇熊": {"group_id": 23, "members": []}})
    monkeypatch.setattr(ws, "current_owner", lambda c, fallback="fin": "fin")
    monkeypatch.setattr(ws, "build_plan", lambda i, d, *a: {
        "new_groups": [], "drop_groups": [], "changed_groups": [],
        "add_members": 0, "drop_members": 0, "members_total": 1})
    monkeypatch.setattr(ws, "backup_current", lambda c, t: 0)

    def boom(*a, **k):
        raise RuntimeError("InsufficientPrivilege: permission denied for table config_history")

    monkeypatch.setattr(ws, "apply_plan", boom)
    monkeypatch.setattr(ws, "write_snapshot",
                        lambda ini, day, probe=False: (calls.append(day),
                                                       {"trade_date": day, "rows": 1,
                                                        "table": ws.SNAPSHOT_TABLE})[1])

    rep = ws.run(write=True, force_open_date="2026-09-28")
    assert calls == ["2026-09-28"]                          # 覆盖失败也没挡住快照
    assert rep["ok"] is False and rep["fatal"] is True      # 主错误照报，不被快照的成功盖掉
    assert rep["member_snapshot"]["rows"] == 1
    st = ws.read_state()
    assert st["member_snapshot_date"] == "2026-09-28"       # 状态里看得见"名单已留下"
    assert st["consecutive_failures"] >= 1


# ------------------------------------------------------------- 成员入库的两个硬约束
# 这两条挡的是"那条 20:40 的作业从未成功过"（2026-09-29/30 实测连败 91 次）：
# 库里 41 组能显示，是因为**旧数据还在**；新加的票一只都没进来过。

def test_没有成本记录的票_added_at不许绑裸NULL():
    """`config.watchlist_stock.added_at` 是 `not null default now()`，
    而 `SelfStockInfo.json` 只覆盖 1,008 个代码、ini 里有 1,093 个 ——
    显式传 NULL 会**绕过默认值**当场抛 NotNullViolation，并且整笔事务回滚：
    一只没有成本记录的票 = 整份自选同步不下来。
    正确写法是把默认值交还给库（`coalesce(%s, now())`），语义正好是"这次同步第一次看见它"。
    """
    ini = _ini_parsed([{"name": "犇犇熊", "code": "1", "display_order": 1,
                        "members": ["002373.SZ", "600000.SH"]}])
    log: list = []
    out = ws.apply_plan(_Conn(log, fetchone_result=(718,)), ini, {}, "fin")
    inserts = [(s, p) for s, p in log if "insert into config.watchlist_stock" in s]
    assert len(inserts) == 2, inserts
    assert any(p[3] is None for _s, p in inserts), \
        '这条要覆盖的正是「成本表里没有的那只票」'
    for sql, params in inserts:
        assert "coalesce(%s, now())" in sql, \
            "added_at 必须由 coalesce 兜住默认值，不许把 NULL 直接绑进 not null 列：%s" % sql
        assert params[0] == 718 and len(params) == 4
    assert out["members_added"] == 14            # 7 × 两行（`_Cur` 固定 rowcount=7）


def test_新组要取号_ft_etl必须有序列权限():
    """序列是**另一类对象**：表授了 `arwd` 不等于能取号。
    ini 里出现新组名那天才走 `nextval`，所以这条平时不响，一响就是整笔回滚。
    授权写在迁移 22（`app/db/sql/22_watchlist_sequence_grant.sql`），
    这里钉的是"那个作业真的会为新组取号"—— 一旦哪天改成不取号，迁移也该跟着撤。"""
    ini = _ini_parsed([{"name": "新建的组", "code": "9", "display_order": 1,
                        "members": ["600000.SH"]}])
    log: list = []
    out = ws.apply_plan(_Conn(log, fetchone_result=(999,)), ini, {}, "fin")
    group_inserts = [(s, p) for s, p in log if "insert into config.watchlist_group" in s]
    assert len(group_inserts) == 1 and "returning group_id" in group_inserts[0][0]
    assert out["groups_new"] == 1
    # 成员行必须挂在**刚取到的那个号**上，而不是写死或留空
    member = [(s, p) for s, p in log if "insert into config.watchlist_stock" in s]
    assert member and member[0][1][0] == 999
