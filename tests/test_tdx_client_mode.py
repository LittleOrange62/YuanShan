"""通达信客户端的两档模式（用户 2026-10-13 点名：保持运行 / 需要时才运行）。

钉的都是这一类功能最容易糊过去的地方：

1. **两档对"该不该有一台开着登录着的客户端"必须给不同答案**，而"坏"的判据不能两档各写一份
   （假死 / 停在登录框 / 交易时段里 vipdoc 不往前写，都住 `_tdx_stuck_reason` 一处）。
2. **判不了不算坏**：读不到窗口响应、读不到 vipdoc 进度、读不到键鼠空闲时间 ——
   都按"不知道"处理，绝不拿"取不到"当"坏了"去重启或关闭客户端
   （《06-自查表》#111 那条"探不到≠false"的同一纪律；这里错了会白手重启用户的行情软件）。
3. **自动重启必须有当天上限**：夜里"收→起→登录→又判坏"会无限循环，每一轮都在点界面。
4. **带外关客户端前要确认人在不在用**：`input_idle_sec()` 不够长就不关，改记待人工清单
   （用户 10-06 那句"万一我当时也在操作电脑"）。
5. **接线**：循环体真的按 mode 分了两条动作、`/api/jobs/loops` 交得出这一格、
   前端真的有那个下拉 —— "后端交了没有前端消费者"是 10-07 已经挖过的洞。
"""

from __future__ import annotations

import datetime as dt
import pathlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("ASTOCK_DATA_HOME", str(tmp_path / "datahome"))
    monkeypatch.setenv("VIBE_BACKUP_DIR", str(tmp_path / "backup"))
    return tmp_path


@pytest.fixture
def srv():
    import server
    return server


# ---------------------------------------------------------------- 该不该在

def _calendar(monkeypatch, opened):
    from duanxian import pg, util
    monkeypatch.setattr(pg, "trade_day_open", lambda d: opened)
    monkeypatch.setattr(util, "china_today", lambda: "2026-10-13")
    monkeypatch.setattr(util, "china_now",
                        lambda: dt.datetime(2026, 10, 13, 6, 0))


def test_需要时才运行_清晨不该有客户端(srv, monkeypatch, home):
    from duanxian import jobs
    jobs.write_setting("tdx_watch", client_mode="on_demand")
    _calendar(monkeypatch, True)
    monkeypatch.setattr(srv, "_daily_close_slot", lambda: "15:59")
    inside, why = srv._tdx_expected_now()
    assert inside is False and "时间带" in why, why


def test_保持运行_清晨也算该在_坏了自己重启(srv, monkeypatch, home):
    from duanxian import jobs
    jobs.write_setting("tdx_watch", client_mode="keep_alive")
    _calendar(monkeypatch, True)
    inside, why = srv._tdx_expected_now()
    assert inside is True and "keep_alive" in why, why
    # 但不开市的日子不覆盖：没有数可取，让它整夜挂着只是费电
    _calendar(monkeypatch, False)
    inside2, why2 = srv._tdx_expected_now()
    assert inside2 is False and "不开市" in why2, why2


# ---------------------------------------------------------------- 坏的判据

class _Probe:
    def __init__(self, ok): self._ok = ok
    def fresh(self, target): return self._ok


class _Ready:
    count = 7
    raise_it = False

    def __init__(self, extra_samples=0):
        if type(self).raise_it:
            raise OSError("vipdoc 目录读不到")

    def day_ready(self, target):
        cls = type(self)
        return type("V", (), {"target": target,
                              "probes": [_Probe(True)] * cls.count})()


@pytest.fixture
def readiness(monkeypatch):
    from app.tdx_client import readiness as rd
    _Ready.count = 7
    _Ready.raise_it = False
    monkeypatch.setattr(rd, "Readiness", _Ready)
    monkeypatch.setattr(rd, "expected_trade_date", lambda: dt.date(2026, 10, 13))
    return _Ready


def _reset_watch(srv):
    srv._vipdoc_watch.update({"count": None, "advanced_at": None})


def test_盘中数据不往前走算坏_收盘后不算(srv, monkeypatch, readiness):
    _reset_watch(srv)
    base = 1_700_000_000.0
    assert srv._tdx_data_stale("10:00", now_ts=base) is None, "第一轮只建基线，不判坏"
    got = srv._tdx_data_stale("10:00", now_ts=base + 31 * 60)
    assert got and "31 分钟" in got and "vipdoc" in got, got
    # 收盘后同一份"没往前走"不是坏 —— 那条判据会把每天 15:30 之后都判成故障
    assert srv._tdx_data_stale("16:30", now_ts=base + 240 * 60) is None


def test_数据往前走就不算坏(srv, monkeypatch, readiness):
    _reset_watch(srv)
    base = 1_700_000_000.0
    srv._tdx_data_stale("10:00", now_ts=base)
    readiness.count = 9                       # 抽样数在涨 = 客户端还在收数
    assert srv._tdx_data_stale("10:00", now_ts=base + 300 * 60) is None


def test_读不到进度时不判坏(srv, monkeypatch, readiness):
    """探测工具坏了 ≠ 客户端坏了：拿"取不到"当"坏了"会白手重启用户的行情软件。"""
    _reset_watch(srv)
    readiness.raise_it = True
    assert srv._tdx_data_stale("10:00", now_ts=1_700_000_000.0) is None
    assert srv._tdx_data_stale("10:00", now_ts=1_700_000_000.0 + 400 * 60) is None


def test_窗口不响应是假死_读不到句柄不算(srv, monkeypatch):
    from app.tdx_client import locator, win32

    monkeypatch.setattr(locator, "main_window", lambda: type("W", (), {"hwnd": 1234})())
    monkeypatch.setattr(srv, "_tdx_data_stale", lambda hhmm, **kw: None)
    monkeypatch.setattr(win32, "window_hung", lambda hwnd: True)
    why, read = srv._tdx_stuck_reason({"pid": 1}, "10:00")
    assert read["hung"] is True and "假死" in why, (why, read)

    monkeypatch.setattr(win32, "window_hung", lambda hwnd: None)   # 判不了
    why2, read2 = srv._tdx_stuck_reason({"pid": 1}, "10:00")
    assert why2 is None and read2["hung"] is None, (why2, read2)


def test_探针把开着但用不了报成不通(srv, monkeypatch):
    """这一格从前会报"正常·连败 0"，而 TQ 那条腿其实取不到数 —— 最坏的一种假绿。"""
    from app.tdx_client import watchman
    monkeypatch.setattr(watchman, "probe", lambda: {"running": True, "pid": 40672,
                                                     "login_box": False, "sync": None,
                                                     "window": None, "minimized": False})
    monkeypatch.setattr(srv, "_tdx_stuck_reason",
                        lambda st, hhmm: ("主窗口消息线程不响应（假死，人点它也没反应）", {"hung": True}))
    ok, detail = srv._tdx_probe_round()
    assert ok is False and detail["stuck"] is True and detail["hung"] is True, detail
    assert "用不了" in detail["reason"], detail


# ---------------------------------------------------------------- 两档的动作

def test_keep_alive_重启有当天上限_零就等于只查不重启(srv, monkeypatch, home):
    from duanxian import jobs
    calls = []
    srv._keep_alive_restarts.update({"day": None, "count": 0})
    monkeypatch.setattr(srv, "_restart_tdx",
                        lambda reason, **kw: (calls.append(reason), {"restarted": True})[1])
    monkeypatch.setattr(srv, "_gui", lambda *a, **kw: None)

    jobs.write_setting("tdx_watch", client_mode="keep_alive", restart_max_per_day=0)
    out0 = srv._tdx_keep_alive_restart({"reason": "假死"})
    assert out0["did"] is False and "restart_max_per_day=0" in out0["reason"]
    assert calls == [], "上限 0 就是明确不让重启，一次都不许多"

    jobs.write_setting("tdx_watch", restart_max_per_day=2)
    a = srv._tdx_keep_alive_restart({"reason": "假死"})
    b = srv._tdx_keep_alive_restart({"reason": "假死"})
    assert a["did"] and b["did"] and len(calls) == 2
    c = srv._tdx_keep_alive_restart({"reason": "假死"})
    assert c["did"] is False and "不再重启" in c["reason"], c
    assert len(calls) == 2, "超过上限还去点界面 = 夜里死循环"


def test_带外关闭_人在用就不关_判不了也不关(srv, monkeypatch, home):
    from app.tdx_client import win32
    from duanxian import jobs

    guis, closed = [], []
    monkeypatch.setattr(srv, "_gui", lambda step, what, status, **kw: guis.append((step, status)))
    monkeypatch.setattr(srv, "_close_tdx_client",
                        lambda **kw: (closed.append(1), {"closed": True, "reason": "已退出"})[1])
    srv._client_down_day = None

    monkeypatch.setattr(win32, "input_idle_sec", lambda: 30)      # 刚刚还在动键盘
    out = srv._tdx_close_when_idle("2026-10-13")
    assert out["closed"] is False and out["skipped"] is True
    assert closed == [], "人正在用就把客户端关掉，比多开着一个窗口糟得多"
    assert ("close_client", "need_human") in guis, guis
    st = jobs.state().get("daily_close_client") or {}
    assert st.get("last_status") == "skipped" and st.get("consecutive_failures") == 0, st

    srv._client_down_day = None
    closed.clear()
    monkeypatch.setattr(win32, "input_idle_sec", lambda: None)     # 判不了
    out2 = srv._tdx_close_when_idle("2026-10-13")
    assert out2["closed"] is False and closed == [], "判不了不等于没人用"

    srv._client_down_day = None
    monkeypatch.setattr(win32, "input_idle_sec", lambda: 3600)     # 空闲一小时
    out3 = srv._tdx_close_when_idle("2026-10-13")
    assert out3["closed"] is True and closed == [1]
    assert jobs.state()["daily_close_client"]["last_detail"]["trigger"].startswith("on_demand")
    # 一天只试一次：关掉之后再进这一格不该把客户端重新拉起来又关
    srv_out = srv._tdx_close_when_idle("2026-10-13")
    assert srv_out is None and closed == [1]


# ---------------------------------------------------------------- 接线

def test_桌上已挂确认框时算观望而不是假装关掉(srv, monkeypatch, home):
    """10-13 实测：点 X 之后通达信停在「退出 / 重新登录 / 取消」那个框上，程序不替人点。
    这一格的读数必须分清三种：关掉了=成；发了请求但卡在框上=失败（该出声）；
    **根本没动手（框还挂着、找不到主窗口、本来就没在跑）= 观望**，不涨连败也不报成成功。"""
    from duanxian import jobs
    from app.tdx_client import win32

    monkeypatch.setattr(win32, "input_idle_sec", lambda: 3600)
    srv._client_down_day = None
    monkeypatch.setattr(srv, "_close_tdx_client", lambda **kw: {
        "acted": False, "closed": False,
        "dialogs": [{"hwnd": 1, "title": "通达信金融终端64", "buttons": ["退出", "重新登录", "取消"]}],
        "reason": "上一次请退留下的退出确认框还挂着 → 不再发第二次退出请求"})
    srv._tdx_close_when_idle("2026-10-13")
    d = (jobs.state().get("daily_close_client") or {})
    assert d.get("last_status") == "skipped" and d.get("consecutive_failures") == 0, d
    assert "退出确认框" in (d.get("last_error") or ""), d

    # 而"发了请求却卡在框上"必须是失败（桌上正挂着东西，不能糊成观望）
    srv._client_down_day = None
    monkeypatch.setattr(srv, "_close_tdx_client", lambda **kw: {
        "acted": True, "closed": False, "exit_confirm": True, "reason": "停在确认框上"})
    srv._tdx_close_when_idle("2026-10-13")
    d2 = jobs.state().get("daily_close_client") or {}
    assert d2.get("last_status") == "fail" and d2.get("consecutive_failures") == 1, d2
    assert (d2.get("last_detail") or {}).get("exit_confirm") is True


def test_关客户端前先恢复最小化的主窗口而测试进程仍然硬挡(srv):
    """10-13 第一次实测就没退掉：主窗口停在 `(-32000,-32000,…)`（最小化位），
    而最小化的窗口常常直接无视 `SC_CLOSE` ⇒ 先 `restore` 再发退出请求。
    这条只能钉源码顺序：`_close_tdx_client` 自己第一件事就是"测试进程里不发任何窗口消息"，
    那道守卫必须留在最前面（否则单测会去点用户桌面上的通达信）。"""
    src = (ROOT / "server.py").read_text(encoding="utf-8")
    body = src.split("def _close_tdx_client(")[1].split("\ndef ")[0]
    head = body.split("win32.close_window")[0]     # 发退出请求**之前**的那一段
    assert "PYTEST_CURRENT_TEST" in head, "测试守卫被挪到了动作之后（单测就会去点用户桌面上的通达信）"
    assert "win32.restore(" in head, "恢复必须排在发退出请求之前，否则等于没改"
    assert "is_iconic" in head, "只在真最小化时才恢复（展开着的窗口不该被程序动 ShowWindow）"


def test_两档都接到了调用点且界面有下拉(srv):
    src = (ROOT / "server.py").read_text(encoding="utf-8")
    body = src.split("def _tdx_watch_scheduler(")[1].split("\ndef ")[0]
    assert 'mode == "keep_alive"' in body and "_tdx_keep_alive_restart(" in body
    assert "_tdx_close_when_idle(" in body, "「需要时才运行」没有关闭动作 = 这条模式是假的"
    # 后端交得出这一格
    jb = (ROOT / "vr" / "jobsboard.py").read_text(encoding="utf-8")
    assert '"client_mode_choices"' in jb and "CLIENT_MODE_MEANING" in jb
    # 接口收得进这个键（不在 `_JOB_KEYS` 里就会被 400 挡掉）
    app_src = (ROOT / "vr" / "app.py").read_text(encoding="utf-8")
    assert '"client_mode"' in app_src.split("_JOB_KEYS = ")[1].split(")")[0]
    assert "client_mode: str | None = None" in app_src.split("class LoopPatch")[1].split("class ")[0]
    # 前端真的有那个框（后端交了没人消费 = 10-07 挖过的那个洞）
    fe = (ROOT / "frontend" / "src" / "components" / "LoopSwitchboard.tsx").read_text(encoding="utf-8")
    assert "l.client_mode_choices" in fe and 'onClientMode' in fe
    assert "需要时才运行（带外自动关闭）" in fe and "保持运行（判坏就自动重启）" in fe
    assert (ROOT / "frontend" / "src" / "lib" / "closeChain.ts").read_text(
        encoding="utf-8").count("client_mode") >= 2, "类型里没有这两项，select 拿不到当前值"


def test_自动关掉了就把先前那条交人工的记录从清单里抹掉(srv, monkeypatch):
    """10-13 现场：07:48 那轮写了一条"需要人工点一次退出"，08:09 程序自己答了那只框。
    旧那条继续挂在待人工清单上就是一句假警报 —— 人会为了补它把刚关掉的客户端再打开。

    只抹**同一件事**（同 day + step + target）且**显式声明做完了**的那类；
    默认口径（`done` 不藏前面的冲突事实）由 `test_gui_trail` 钉着，不许被这里放宽。
    """
    from duanxian import gui_trail, jobs
    from app.tdx_client import win32

    monkeypatch.setattr(win32, "input_idle_sec", lambda: 3600)   # 没人在用键盘鼠标才走得到关闭
    gui_trail.add(step='close_client', what='带外该关通达信但没关掉', status='need_human',
                  detail='停在确认框上', target='2026-10-13')
    assert len(gui_trail.pending()) == 1
    monkeypatch.setattr(srv, "_close_tdx_client", lambda **kw: {
        "acted": True, "closed": True, "clicked_exit": True,
        "reason": "确认框已答（已投给「退出」按钮），重探客户端确实不在了"})
    srv._client_down_day = None
    out = srv._tdx_close_when_idle("2026-10-13")
    assert out["closed"] is True
    d = jobs.state().get("daily_close_client") or {}
    assert d.get("last_status") == "ok" and (d.get("last_detail") or {}).get("closed") is True
    assert gui_trail.pending() == [], "程序做完了的事不该还挂着让人补"
    assert any(x["status"] == "done" and x.get("supersedes") for x in gui_trail.items(limit=10))


def test_两档的取值与措辞是一份(jobs_mod):
    """面板下拉里的两项必须正好等于 `CLIENT_MODES`，多一个少一个都是「框里有选了不生效」。"""
    import jobsboard
    assert set(jobsboard.CLIENT_MODE_MEANING) == set(jobs_mod.CLIENT_MODES)
    assert jobs_mod.TDX_WATCH_DEFAULTS["client_mode"] == "on_demand", \
        "默认档改成 keep_alive 就等于替用户同意「夜里也允许程序点他的客户端」"
    bad = jobs_mod._valid("client_mode", "keeplive", "on_demand")
    assert bad and "on_demand" in bad, "拼错要指认出可选项，不悄悄回落"
    assert jobs_mod._valid("client_mode", "keep_alive", "on_demand") is None
    assert jobs_mod._valid("restart_max_per_day", "0", 3) is None, "0 = 只查不重启，那一档合法"
    assert jobs_mod._valid("restart_max_per_day", "11", 3)


@pytest.fixture
def jobs_mod():
    from duanxian import jobs
    return jobs


# ------------------------------------------------- 退出确认框（10-13 的假读数）

def _fake_win(hwnd, pid, cls, text, visible=True):
    """只造 `_exit_confirm_boxes` / `_answer_exit_confirm` 真读的那几个字段。

    桩的形状照 10-13 现场抄：类名 `#32770`、标题「通达信金融终端64」、
    按钮「退出 / 重新登录 / 取消」+ 一段 `Static` 正文。
    """
    import types
    return types.SimpleNamespace(hwnd=hwnd, pid=pid, cls=cls, text=text, visible=visible)


def test_认得出桌上挂着的那只退出确认框(srv, monkeypatch):
    """这一条是钉那次**假读数**的：框明明挂在桌上，台账却写"没看到决定框"。

    根因不是判据写错，是**实现里的洞**：那行读 `s8_finish.BUTTON_CLASSES`，而那个元组其实
    住在 `locator` 上 ⇒ `AttributeError` 被同一个函数里的 `except` 咽掉 ⇒ 交回空表 ⇒
    程序老实说"没看到"。所以这一条要钉住"真认得出"，下一条钉"坏了要出声"。
    """
    from app.tdx_client import win32
    from app.tdx_client.steps import s8_finish

    dlg = _fake_win(9001, 40672, "#32770", "通达信金融终端64")
    main = _fake_win(9002, 40672, "TdxW_MainFrame_Class", "通达信金融终端V7.73 - [行情报价-自选股]")
    kids = [_fake_win(11, 40672, "Button", "退出"),
            _fake_win(12, 40672, "Button", "重新登录"),
            _fake_win(13, 40672, "Button", "取消"),
            _fake_win(14, 40672, "Static", "确定要退出通达信金融终端吗？")]
    monkeypatch.setattr(win32, "top_windows", lambda *a, **k: [dlg, main])
    monkeypatch.setattr(win32, "children", lambda hwnd, **kw: kids if hwnd == 9001 else [])
    monkeypatch.setattr(s8_finish, "button_text", lambda w: w.text)

    boxes = srv._exit_confirm_boxes(40672)
    assert len(boxes) == 1, boxes
    assert boxes[0]["hwnd"] == 9001 and "退出" in boxes[0]["buttons"], boxes
    assert boxes[0]["buttons"] == ["退出", "重新登录", "取消"], "按钮要原样交出去，界面与台账都读这一份"
    # 主窗口不是对话框，不能因为同 pid 就被认成框
    monkeypatch.setattr(win32, "top_windows", lambda *a, **k: [main])
    assert srv._exit_confirm_boxes(40672) == []
    # 只有下载按钮的窗口不算退出框（没有「退出」这个全文相等的按钮）
    dl = _fake_win(9003, 40672, "#32770", "TdxW")
    monkeypatch.setattr(win32, "top_windows", lambda *a, **k: [dl])
    monkeypatch.setattr(win32, "children", lambda hwnd, **kw: [
        _fake_win(21, 40672, "Button", "取消下载"), _fake_win(22, 40672, "Button", "关闭")])
    assert srv._exit_confirm_boxes(40672) == []


def test_探测本身坏了要说出来而不是当作什么都没看到(srv, monkeypatch):
    from app.tdx_client import win32

    def boom(*a, **k):
        raise RuntimeError("EnumWindows 挂了")

    monkeypatch.setattr(win32, "top_windows", boom)
    boxes, err = srv._exit_boxes_safe(40672)
    assert boxes == [] and "RuntimeError" in err and "EnumWindows" in err, err
    # 本体不吞异常：异常要能冒出来（吞掉的那一版就是把"探测坏了"写成了"没看到框"）
    with pytest.raises(RuntimeError):
        srv._exit_confirm_boxes(40672)

    import inspect
    assert "box_error" in inspect.getsource(srv._close_tdx_client), \
        "探测坏了要一起交出台账，那句「没看到决定框」才不会被当成证据"


def test_点退出只走那一个出口且三重守卫齐全(srv, monkeypatch):
    """「只认全文等于「退出」的那一个按钮」＋「框里有黑名单文字就不点」＋「pytest 硬挡」。

    10-06 18:3x 的教训就在这一条上：子串匹配 `text='取消'` 按到了「取消下载」，
    掉的是正在下的那一轮数据。所以这里必须 `exact=True`，且放行只认 `TDX_ALLOW_TEST_CLICK`。
    """
    from app.tdx_client import locator, win32

    box = {"hwnd": 9001, "title": "通达信金融终端64",
           "buttons": ["退出", "重新登录", "取消"]}
    # ① 黑名单先判：连找按钮都不找（闸在点击出口里，不靠调用方自觉）
    dirty = dict(box, buttons=["退出", "取消下载"])
    r1 = srv._answer_exit_confirm(dirty)
    assert r1["clicked"] is False and "不能点的按钮" in r1["reason"], r1
    # ② pytest 里形状再干净也不许碰用户桌面上的真窗口
    r2 = srv._answer_exit_confirm(box)
    assert r2["clicked"] is False and "PYTEST" in r2["reason"], r2
    # ③ 显式放行才真点，而且必须按全文相等找控件
    calls: list = []
    monkeypatch.setenv("TDX_ALLOW_TEST_CLICK", "1")
    monkeypatch.setattr(locator, "find_child",
                        lambda root, **kw: calls.append(kw)
                        or [_fake_win(11, 40672, "Button", "退出")])
    monkeypatch.setattr(win32, "bm_click",
                        lambda hwnd: calls.append(("click", hwnd)) or True)
    r3 = srv._answer_exit_confirm(box)
    assert r3["clicked"] is True, r3
    assert calls[0]["text"] == "退出" and calls[0]["exact"] is True, calls[0]
    assert calls[1] == ("click", 11)
    # 找不到「正好等于退出」的按钮 ⇒ 不点（不猜位置、不猜默认分支）
    monkeypatch.setattr(locator, "find_child", lambda root, **kw: [])
    assert srv._answer_exit_confirm(box)["clicked"] is False


def test_自动答确认框有开关且面板交得出这一格(srv, jobs_mod):
    import inspect

    assert jobs_mod.TDX_WATCH_DEFAULTS["exit_confirm_click"] is True, \
        "默认关着 = 「需要时才运行」每天仍要人对着确认框点一次退出，那条模式是半真的"
    assert jobs_mod._valid("exit_confirm_click", "不是布尔", True), "bool 键要挡住脏值"
    assert "exit_confirm_click" in inspect.getsource(srv._close_tdx_client)

    jb = (ROOT / "vr" / "jobsboard.py").read_text(encoding="utf-8")
    assert '"exit_confirm_click"' in jb and "EXIT_CLICK_MEANING" in jb
    app_src = (ROOT / "vr" / "app.py").read_text(encoding="utf-8")
    assert '"exit_confirm_click"' in app_src.split("_JOB_KEYS = ")[1].split(")")[0], \
        "接口不认这个键，页面上就是个改了也不生效的框"
    fe = (ROOT / "frontend" / "src" / "components" / "LoopSwitchboard.tsx").read_text(encoding="utf-8")
    assert "exit_confirm_click" in fe, "后端交了没人消费（10-07 挖过的同一个洞）"
    assert (ROOT / "frontend" / "src" / "lib" / "closeChain.ts").read_text(
        encoding="utf-8").count("exit_confirm_click") >= 2, "类型与 patch 都要有这一项"


# ------------------------------------------------------ 时间带可配 + 每日定时重启（用户 10-19 点名）

def test_时间带_留空是按链推导_填了就以填的为准(srv, monkeypatch, home):
    """09:00–19:00 那一格必须真的把盘中包进来（用户原话：允许 9 点到 19 点都可以保持在线）。"""
    from duanxian import jobs, util
    import datetime as _dt
    jobs.write_setting("tdx_watch", client_mode="on_demand")
    monkeypatch.setattr(util, "china_today", lambda: "2026-10-19")
    monkeypatch.setattr(util, "china_now", lambda: _dt.datetime(2026, 10, 19, 10, 0))
    from duanxian import pg
    monkeypatch.setattr(pg, "trade_day_open", lambda d: True)
    monkeypatch.setattr(srv, "_daily_close_slot", lambda: "15:59")

    inside, why = srv._tdx_expected_now()
    assert inside is False and "推导" in why, f"默认（两格留空）仍按链推导：{why}"

    jobs.write_setting("tdx_watch", client_band_lo="09:00", client_band_hi="19:00")
    inside, why = srv._tdx_expected_now()
    assert inside is True and "09:00" in why and "面板拨的" in why, why
    assert jobs.TDX_WATCH_DEFAULTS["client_band_lo"] == "", "默认必须是空＝不替用户决定盘中长开"


def test_时间带_填反了两种坏法都要出声(srv, monkeypatch, home):
    """`lo >= hi` 会被读成「整天都不在带内」，屏幕上一句正常话都说得通 —— 必须挡。"""
    from duanxian import jobs, util
    import datetime as _dt
    from duanxian import pg
    monkeypatch.setattr(util, "china_today", lambda: "2026-10-19")
    monkeypatch.setattr(util, "china_now", lambda: _dt.datetime(2026, 10, 19, 10, 0))
    monkeypatch.setattr(pg, "trade_day_open", lambda d: True)
    monkeypatch.setattr(srv, "_daily_close_slot", lambda: "15:59")
    # ① 两格一起填反：写面就拒
    err, _ = jobs.set_setting("tdx_watch", client_band_lo="19:00", client_band_hi="09:00")
    assert err and "开始早于结束" in err, f"配对方向没判：{err}"
    # ② 只拨一头、另一头是推导出来的 ⇒ 读面必须把这句喊出来，不许静默"今天不该开"
    jobs.write_setting("tdx_watch", client_band_lo="19:30", client_band_hi="")
    inside, why = srv._tdx_expected_now()
    assert inside is False and "反的" in why, f"半配配反时沉默：{why}"
    # ③ 脏格式要拒
    assert jobs._valid("client_band_hi", "25:99", ""), "HH:MM 没判"
    assert jobs._valid("client_band_hi", "", "") is None, "空串是合法值（＝那一头按推导），不许当没填拒掉"


def test_每日定时重启_pytest进程里硬挡(srv, home):
    """这个执行体会 `taskkill` 收掉用户的行情客户端 —— 测试里绝不能真做（同 `_close_tdx_client` 那一族洞）。"""
    assert srv._tdx_daily_timed_restart().get("skipped") is True, "pytest 里居然走到了真重启"


def test_每日定时重启_到点才做_当天只做一次(srv, monkeypatch, home):
    """到点 = 调唯一出口 `_restart_tdx`；没到点/做过了/不开市 都不许动界面。"""
    import datetime as _dt
    from duanxian import jobs, pg, util
    calls = []
    monkeypatch.setattr(srv, "_restart_tdx",
                        lambda reason, trigger="", count_key="": (calls.append(trigger),
                                                                  {"restarted": True})[1])
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.setattr(jobs, "settings",
                        lambda n, d: ({**d, "restart_daily_enabled": True,
                                       "restart_daily_at": "15:38"}, []))
    monkeypatch.setattr(util, "china_today", lambda: "2026-10-19")
    monkeypatch.setattr(pg, "trade_day_open", lambda d: True)
    srv._daily_restart_day = None
    monkeypatch.setattr(util, "china_now", lambda: _dt.datetime(2026, 10, 19, 15, 37))
    assert srv._tdx_daily_timed_restart() is None and not calls, "还没到点就动手了"
    monkeypatch.setattr(util, "china_now", lambda: _dt.datetime(2026, 10, 19, 15, 38))
    assert srv._tdx_daily_timed_restart()["did"] is True and calls == ["daily_timed"], "到点没走唯一出口"
    assert srv._tdx_daily_timed_restart() is None and len(calls) == 1, "同一天重启了两次"
    # 关掉这一格 = 一次都不做
    srv._daily_restart_day = None
    monkeypatch.setattr(jobs, "settings",
                        lambda n, d: ({**d, "restart_daily_enabled": False,
                                       "restart_daily_at": "15:38"}, []))
    assert srv._tdx_daily_timed_restart() is None and len(calls) == 1, "关了还在重启"
    # 不开市不空转
    srv._daily_restart_day = None
    monkeypatch.setattr(jobs, "settings",
                        lambda n, d: ({**d, "restart_daily_enabled": True,
                                       "restart_daily_at": "15:38"}, []))
    monkeypatch.setattr(pg, "trade_day_open", lambda d: False)
    assert srv._tdx_daily_timed_restart().get("skipped") is True and len(calls) == 1


def test_每日定时重启_服务重启后不再重复拉起(srv, monkeypatch, home):
    """「当天只做一次」那份记忆必须跨进程。

    案底（10-26 现查台账 `tdx_daily_restart` 的 runs）：15:39 / 20:05:48 / 20:15:22 三笔都在，
    后两笔是 20:05 与 20:14 两次 8910 重启带出来的 —— 内存里的日期标记一重启就清零，
    而 15:38 之后每一轮探测都判"还没做过"，于是白动三遍界面（收进程→重起→登录→清弹窗）。
    """
    import datetime as _dt
    from duanxian import jobs, pg, util
    calls = []
    keys = []

    def _stub(reason, trigger="", count_key=""):
        calls.append(trigger)
        keys.append(count_key)
        return {"restarted": True}

    # ⚠️ 情景日期必须跟着**真今天**走，不许写死 2026-10-26：台账那一笔的 `last_run_at`
    # 是 `jobs.record()` 用真表钟落的（`dt.datetime.now(...).astimezone()`），写死日期会让
    # "桩里的今天"和"台账里的今天"不是同一天 —— 10-27 早上这条就被测成了"闸没挡住"的假红。
    _today = util.china_today()
    _d0 = _dt.date.fromisoformat(_today)
    _yday = (_d0 - _dt.timedelta(days=1)).isoformat()

    def _clock(h, m):
        return lambda: _dt.datetime(_d0.year, _d0.month, _d0.day, h, m)

    monkeypatch.setattr(srv, "_restart_tdx", _stub)
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.setattr(jobs, "settings",
                        lambda n, d: ({**d, "restart_daily_enabled": True,
                                       "restart_daily_at": "15:38"}, []))
    monkeypatch.setattr(pg, "trade_day_open", lambda d: True)
    # 真文件、真读法（不桩 `state()`）：先让这一手在今天做掉一次
    monkeypatch.setattr(util, "china_now", _clock(15, 38))
    srv._daily_restart_day = None
    assert srv._tdx_daily_timed_restart()["did"] is True and calls == ["daily_timed"]
    # 台账那一笔在真出口 `_restart_tdx` 里由它自己写（`jstore.record(count_key, …)`）；
    # 出口被桩掉了，所以按**它自己那个键**补一笔 —— 键名对不对，下面最后一条断言钉住。
    assert keys == ["tdx_daily_restart"], f"这一手记的账不是那个键，读侧就对不上：{keys}"
    jobs.record("tdx_daily_restart", ok=True, detail={"reason": "每日定时（15:38）"})
    # 服务重启：进程内的日期标记没了，但台账里那一笔还在 ⇒ 不许再动界面
    monkeypatch.setattr(util, "china_now", _clock(20, 5))
    srv._daily_restart_day = None
    assert srv._tdx_daily_timed_restart() is None and calls == ["daily_timed"], \
        "服务一重启就把「今天已经拉起过」忘了，又在 20:0x 动了一遍界面"
    assert srv._daily_restart_day == _today, "台账那笔没补回快路，之后每两分钟白读一次文件"
    # 昨天做过、今天还没做 ⇒ 到点就该做（证明这道闸不会把明天也一起挡死）
    st = jobs.state()
    st["tdx_daily_restart"]["last_run_at"] = f"{_yday} 15:38:52"
    util.atomic_write_json(jobs.state_path(), st)
    srv._daily_restart_day = None
    assert srv._tdx_daily_timed_restart()["did"] is True and calls == ["daily_timed", "daily_timed"], \
        "改成读台账之后把每天都挡死了"


def test_新格子前后端接齐了(srv, home):
    """「后端交了没人消费」与「前端有框后端不认」是两个方向同样的假功能，一次钉全。"""
    import inspect
    from duanxian import jobs as jobs_mod
    for key in ("client_band_lo", "client_band_hi", "restart_daily_enabled", "restart_daily_at"):
        assert key in jobs_mod.TDX_WATCH_DEFAULTS, f"{key} 没进默认表（`set_setting` 会整笔拒）"
    assert jobs_mod._valid("restart_daily_at", "下午三点三十八", "15:38"), "时刻没做 HH:MM 校验"
    assert '"restart_daily_at": "15:38"' in inspect.getsource(jobs_mod) or \
        jobs_mod.TDX_WATCH_DEFAULTS["restart_daily_at"] == "15:38", "默认时刻不是 15:38"

    jb = (ROOT / "vr" / "jobsboard.py").read_text(encoding="utf-8")
    for token in ("CLIENT_BAND_MEANING", "DAILY_RESTART_MEANING", "client_band_lo",
                  "restart_daily_enabled", "restart_daily_at"):
        assert token in jb, f"读层没交 {token} —— 面板上就渲染不出这一格"
    app_src = (ROOT / "vr" / "app.py").read_text(encoding="utf-8")
    keys = app_src.split("_JOB_KEYS = ")[1].split(")")[0]
    for key in ("client_band_lo", "client_band_hi", "restart_daily_enabled", "restart_daily_at"):
        assert f'"{key}"' in keys, f"接口不认 {key}，改了就是个不生效的框"
    fe = (ROOT / "frontend" / "src" / "components" / "LoopSwitchboard.tsx").read_text(encoding="utf-8")
    assert "client_band_help" in fe and "restart_daily_help" in fe, "前端没有这两格"
    assert "保存时间带" in fe and "每日定时重启通达信" in fe
    cc = (ROOT / "frontend" / "src" / "lib" / "closeChain.ts").read_text(encoding="utf-8")
    for key in ("client_band_lo", "restart_daily_at"):
        assert cc.count(key) >= 2, f"{key} 类型或 patch 少一处"
    assert "_tdx_daily_timed_restart" in inspect.getsource(srv._tdx_watch_scheduler), \
        "执行体写了但循环没调它 —— 那就是写着有、其实没接"
