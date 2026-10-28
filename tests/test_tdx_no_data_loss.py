# -*- coding: utf-8 -*-
"""**关窗/收窗不许掉正在下的数据**（2026-10-06 18:3x 实测缺陷的回归）。

形状来自现场，不是假设：

- 「盘后数据下载」`#32770 562x417`，状态栏 id=1224，
  按钮 **id=1 在正在下的时候写的是「取消下载」**、id=2 是「最小化」
  （没在下时 id=1 才叫「开始下载」，同一个 ctrl_id 含义相反）；
- 我 18:3x 调过一次 `close_dialog()`，它回 `["点了 '取消' 按钮", '对话框仍在']`
  —— 子串匹配把「取消下载」当成了「取消」，那一下是**放弃这一轮下载**，
  于是通达信弹出「您确定取消下载吗？」（用户截图里那个框就是这么来的）；
- 那个决定框 18:5x 实测：**标题是 `'TdxW'`（零信息）**，正文 Static id=65535
  「您确定取消下载吗？」，按钮 id=1「确定」/ id=2「取消」。

全部离线：`win32` 换成假世界，真桌面上一个窗都不碰。
硬挡钉在 `close_dialog` / `watchdog._close` 头上（要测就 `TDX_ALLOW_TEST_CLICK=1`）。
"""
from __future__ import annotations

import datetime as dt

import pytest

from app.tdx_client import win32
from app.tdx_client import dl_dialog
from app.tdx_client import watchdog
from app.tdx_client.steps import s8_finish

PID = 14800
DLG = 1707378          # 盘后数据下载
BOX = 57020768         # 'TdxW' 决定框
MAIN = 2229272         # 主窗口
TARGET = dt.date(2026, 9, 30)


def _w(hwnd, cls, text, ctrl_id=0, *, l=0, t=0, w=100, h=30,
       visible=True, pid=PID):
    return win32.Win(hwnd=hwnd, cls=cls, text=text,
                     rect=(l, t, l + w, t + h), visible=visible,
                     enabled=True, pid=pid, ctrl_id=ctrl_id)


class _World:
    """假窗口世界：记录每一个发出去的点击与关闭 —— 断言的就是"没发出去"。"""

    def __init__(self, *, status='正在下载5分钟线数据..[20260924-20260930] 9560/9604',
                 start_label='取消下载', with_box=True):
        self.hwnd_seq = 9000
        self.clicks: list[tuple[int, str]] = []
        self.closes: list[tuple[int, bool]] = []
        self.minimized: list[int] = []
        self.status = status
        self.start_label = start_label
        self.box_alive = with_box
        self.dlg_alive = True
        self._boxes: dict[int, list] = {}
        self.dlg_children = [
            _w(0, 'Button', '日线和实时行情数据', 1424),
            _w(0, 'Button', '1分钟线数据', 1425),
            _w(0, 'Button', '5分钟线数据', 1426),
            _w(0, 'Button', '下载所有A股类品种的日线数据', 1420),
            _w(0, 'Button', start_label, 1),
            _w(0, 'Button', '最小化', 2),
            _w(0, 'Static', status, 1224),
        ]
        for c in self.dlg_children:
            self.hwnd_seq += 1
            object.__setattr__(c, 'hwnd', self.hwnd_seq)
        self.cancel_hwnd = self.dlg_children[4].hwnd      # id=1 = 取消下载
        self.start_hwnd = self.dlg_children[4].hwnd
        self.box_children = [
            _w(0, 'Button', '确定', 1),
            _w(0, 'Button', '取消', 2),
            _w(0, 'Static', '您确定取消下载吗？\r\n\r\n\r\n', 65535),
        ]
        for c in self.box_children:
            self.hwnd_seq += 1
            object.__setattr__(c, 'hwnd', self.hwnd_seq)
        # 两个答案都会把框答掉（区别在**下载**那边：确定=放弃，取消=继续下）
        self.click_ok = {self.box_children[0].hwnd: self._kill_box,
                         self.box_children[1].hwnd: self._kill_box}

    # -- win32 接口 --
    def top_windows(self):
        out = [_w(MAIN, 'TdxW_MainFrame_Class', '通达信金融终端V7.73 - [行情报价]',
                  w=1925, h=1046)]
        if self.dlg_alive:
            out.insert(0, _w(DLG, '#32770', '盘后数据下载', w=562, h=417))
        if self.box_alive:
            out.insert(0, _w(BOX, '#32770', 'TdxW', w=237, h=181))
        return out

    def children(self, hwnd, max_depth=8):
        if hwnd == DLG:
            return list(self.dlg_children)
        if hwnd == BOX:
            return list(self.box_children)
        return []

    def window_text(self, hwnd):
        for c in self.dlg_children + self.box_children:
            if c.hwnd == hwnd:
                return c.text
        return ''

    def is_window(self, hwnd):
        if hwnd == DLG:
            return self.dlg_alive
        if hwnd == BOX:
            return self.box_alive
        return hwnd == MAIN

    def close_window(self, hwnd, syscommand=False):
        self.closes.append((hwnd, syscommand))
        if hwnd == DLG:
            self.dlg_alive = False
        elif hwnd == BOX:
            self.box_alive = False        # 绝不该发生：决定框只许按按钮

    def bm_click(self, hwnd):
        label = self.window_text(hwnd)
        self.clicks.append((hwnd, label))
        if hwnd in self.click_ok:
            self.click_ok[hwnd]()
        return True          # PostMessage 的投递结果：投出去了就算，判定交给 verify

    def minimize(self, hwnd):
        self.minimized.append(hwnd)

    def _kill_box(self):
        self.box_alive = False

    def clicked(self, label):
        return [h for h, t in self.clicks if t == label]


@pytest.fixture
def world(monkeypatch):
    """把 s8_finish / dl_dialog / watchdog / **locator** 四处的 `win32` 全换成同一个假世界。

    `locator` 那一处不能漏：`close_dialog` 里的按钮是 `locator.find_child()` 找的，
    漏了它就会去枚举**真实桌面**（`TDX_ALLOW_TEST_CLICK` 一开就真点了）。
    """
    def install(w: _World):
        fake = _FakeWin32(w)
        for mod in (s8_finish, dl_dialog, watchdog, s8_finish.locator):
            monkeypatch.setattr(mod, 'win32', fake)
        monkeypatch.setenv('TDX_ALLOW_TEST_CLICK', '1')   # 假世界已就位，允许过闸
        monkeypatch.setattr(s8_finish, 'find_dialog',
                            lambda pid=None: next((x for x in w.top_windows()
                                                   if x.hwnd == DLG), None))
        monkeypatch.setattr(s8_finish.locator, 'main_window',
                            lambda: _w(MAIN, 'TdxW_MainFrame_Class', '主窗口',
                                       w=1925, h=1046))
        monkeypatch.setattr(s8_finish, 'files_settled',
                            lambda target, **kw: (True, '桩：文件侧已到'))
        return w
    return install


class _FakeUser32:
    def __init__(self, w):
        self._w = w

    def IsWindow(self, hwnd):
        return self._w.is_window(hwnd)


class _FakeWin32:
    def __init__(self, w):
        self._w = w
        self.user32 = _FakeUser32(w)
        self.Win = win32.Win

    def top_windows(self):
        return self._w.top_windows()

    def children(self, hwnd, max_depth=8):
        return self._w.children(hwnd)

    def window_text(self, hwnd):
        return self._w.window_text(hwnd)

    def is_window(self, hwnd):
        return self._w.is_window(hwnd)

    def close_window(self, hwnd, syscommand=False):
        return self._w.close_window(hwnd, syscommand)

    def bm_click(self, hwnd):
        return self._w.bm_click(hwnd)

    def minimize(self, hwnd):
        return self._w.minimize(hwnd)

    def pids_by_name(self, name):
        return [PID]

    def is_iconic(self, hwnd):
        return True


# ---------------------------------------------------------------- 那条掉数据的误点

def test_还在下载时故意不关窗(world):
    """现场形状：id=1 写着「取消下载」、状态栏 9560/9604 —— 这时候关窗就是放弃这一轮。"""
    w = world(_World())
    notes = s8_finish.close_dialog(PID, target=TARGET)
    assert any('故意不关窗' in n for n in notes), notes
    assert w.closes == [], '还在下就发 WM_CLOSE，等于把这一轮下载的去向交给默认分支'
    assert w.clicked('取消下载') == [], '一次都不许点「取消下载」'


def test_关窗按钮精确匹配且永不点取消下载(world):
    """即使窗口没在下载，「取消下载」这个文字也**永不点**（它是决定，不是关窗）。"""
    w = world(_World(status='', start_label='取消下载'))
    notes = s8_finish.close_dialog(PID, target=None)
    assert w.clicked('取消下载') == []
    assert any('故意不关窗' in n or '不强点' in n for n in notes), notes


def test_下载完毕后才真的关窗(world):
    w = world(_World(status='下载完毕.', start_label='开始下载'))
    notes = s8_finish.close_dialog(PID, target=TARGET)
    assert (DLG, False) in w.closes, notes
    assert any('对话框已关' in n for n in notes), notes
    assert w.clicked('取消下载') == []


# ---------------------------------------------------------------- 决定框要会答

def test_还在下时决定框点取消也就是继续下(world):
    """`allow_cancel=False` → 点「取消」。这里的「取消」意思是**不取消下载**。"""
    w = world(_World())
    notes = s8_finish.answer_confirm(PID, allow_cancel=False)
    assert w.clicked('取消') and not w.clicked('确定'), notes
    assert any('决定框点了' in n for n in notes), notes
    assert (BOX, False) not in w.closes, '决定框绝不 WM_CLOSE：按 X 走哪个分支没人知道'


def test_判完成或判卡死后决定框点确定(world):
    w = world(_World(status='下载完毕.', start_label='开始下载'))
    s8_finish.close_dialog(PID, target=TARGET)
    assert w.clicked('确定'), w.clicks
    assert not w.box_alive


def test_找不到确切按钮就原样留着(world):
    """宁可不答也不猜相邻按钮 —— 猜错的代价是掉一整轮数据。"""
    w = _World()
    w.box_children = [_w(0, 'Static', '您确定取消下载吗？', 65535)]
    world(w)
    notes = s8_finish.answer_confirm(PID, allow_cancel=False)
    assert any('原样留着' in n for n in notes), notes
    assert w.clicks == [] and w.closes == []


def test_决定框按正文认因为标题没有信息(world):
    """实测标题是 `'TdxW'` —— 任何按标题写的规则/判据都认不出它。"""
    w = world(_World())
    boxes = s8_finish.confirm_boxes(PID)
    assert [b.text for b in boxes] == ['TdxW']


# ---------------------------------------------------------------- 卡住 vs 慢

class _Clock:
    def __init__(self):
        self.t = 1_000_000.0

    def monotonic(self):
        return self.t

    def sleep(self, s):
        self.t += s


@pytest.fixture
def clock(monkeypatch):
    c = _Clock()
    monkeypatch.setattr(dl_dialog, 'time', c)
    return c


def test_状态栏不动但文件在写就继续等(world, clock, monkeypatch):
    """这是 09-28 那类"链的耐心短于下载"：只 ① 成立 = 慢，不是死，不许收窗。"""
    w = world(_World())
    monkeypatch.setattr(dl_dialog, 'status_text', lambda hwnd: w.status)
    monkeypatch.setattr(dl_dialog.win32, 'is_window', w.is_window)
    n = {'i': 0}

    def coverage():
        n['i'] += 1
        return ('写入了', n['i'])            # 每次都变 = 还在往前写

    ok, why = dl_dialog.wait_done(DLG, timeout=200, interval=1, stall_sec=60,
                                  coverage=coverage, coverage_gap=20)
    assert not ok and '超时' in why, why     # 不许出现"卡住"
    assert n['i'] >= 2, '文件侧对照得真的被取过'


def test_状态栏不动且覆盖不涨判卡住(world, clock, monkeypatch):
    w = world(_World())
    monkeypatch.setattr(dl_dialog, 'status_text', lambda hwnd: w.status)
    ok, why = dl_dialog.wait_done(DLG, timeout=600, interval=1, stall_sec=60,
                                  coverage=lambda: ('不动', 1), coverage_gap=20)
    assert not ok and why.startswith('卡住'), why
    assert '没变过' in why and '一个字节都没动' in why


def test_卡住之后主动收窗并记一条not_ok(world, monkeypatch):
    """用户问的"没动静了你就结束了吗" —— 结束可以，但收窗 + 出声。"""
    w = world(_World())
    recs = []
    from duanxian import jobs
    monkeypatch.setattr(jobs, 'record',
                        lambda key, ok=True, detail=None: recs.append((key, ok, detail)))
    monkeypatch.setattr(s8_finish, 'files_settled',
                        lambda target, **kw: (False, '桩：文件侧未到'))
    out = s8_finish.reap_stalled(PID, reason='卡住：状态栏 300s 没变过', target=TARGET)
    assert (DLG, False) in w.closes, out
    assert w.clicked('确定'), '判过死 → 确认框该点确定（放弃这一轮已经死了的下载）'
    assert recs and recs[0][0] == 'tdx_close_verify' and recs[0][1] is False
    assert '卡住' in recs[0][2]['reason']


# ---------------------------------------------------------------- 同一个 id 的反向陷阱

def test_start在按钮变成取消下载时拒绝点(world):
    """`CTRL['start']=1` 的文字会变。s5 重进时盲点 id=1 = 亲手放弃正在下的那一轮。"""
    w = world(_World())
    ok, why = dl_dialog.start(DLG)
    assert not ok
    assert '取消下载' in why and '掉数据' in why
    assert w.clicks == []


def test_start在正常形状下点得动(world):
    w = world(_World(status='下载完毕.', start_label='开始下载'))
    ok, _why = dl_dialog.start(DLG)
    assert ok and w.clicks


# ---------------------------------------------------------------- 看门狗

def test_看门狗绝不盲关决定框(monkeypatch):
    """规则表按标题匹配，而决定框标题是 `'TdxW'` —— 一条 `title='TdxW'` 的 close
    规则就会把它关掉，走的是我们不知道是哪个的默认分支。所以硬闸写在代码里。"""
    w = _World()
    fake = _FakeWin32(w)
    monkeypatch.setattr(watchdog, 'win32', fake)
    monkeypatch.setenv('TDX_ALLOW_TEST_CLICK', '1')
    wd = watchdog.PopupWatchdog(pid=PID, rules=[
        {'rule_key': 'tips', 'match_type': 'title', 'pattern': 'TdxW',
         'action': 'close', 'priority': 20}])
    evs = wd.sweep()
    assert (BOX, False) not in w.closes, '决定框被盲关了'
    assert any('绝不盲关' in e.result for e in evs), [e.result for e in evs]
    assert wd.alerts, '要升级成 alert 交判据，不能悄悄放过'


def test_看门狗点按钮只点按钮类不点正文(monkeypatch):
    """`modal_ok` 那条 `button_text='确定'`：上一版是 `btn in c.text` 且不看类名，
    于是命中正文 Static「您确**定**取消下载吗？」，BM_CLICK 一段静态文字，
    框没关、记录却说"点了按钮"。"""
    w = _World()
    monkeypatch.setattr(watchdog, 'win32', _FakeWin32(w))
    monkeypatch.setenv('TDX_ALLOW_TEST_CLICK', '1')
    wd = watchdog.PopupWatchdog(pid=PID, rules=[
        {'rule_key': 'modal_ok', 'match_type': 'class', 'pattern': '#32770',
         'action': 'button', 'button_text': '继续等', 'priority': 8}])
    # 把那个静态正文改成含"继续等"，看会不会被当成按钮点掉
    w.box_children[2] = _w(0, 'Static', '请稍后继续等待结果', 65535)
    wd.sweep()
    assert [t for _h, t in w.clicks] == [], '静态文字不许被 BM_CLICK：%s' % w.clicks


def test_pytest进程里close_dialog硬挡真点击(monkeypatch):
    """没打桩的测试走到这里就是要在用户桌面上点窗 —— 挡下来并点名该打哪个洞。"""
    monkeypatch.delenv('TDX_ALLOW_TEST_CLICK', raising=False)
    monkeypatch.setattr(s8_finish, 'win32', _FakeWin32(_World()))
    monkeypatch.setattr(s8_finish, 'find_dialog',
                        lambda pid=None: _World().top_windows()[0])
    notes = s8_finish.close_dialog(PID, target=TARGET)
    assert any('硬挡' in n and 'close_dialog' in n for n in notes), notes


def test_pytest进程里看门狗硬挡真关窗(monkeypatch):
    w = _World()
    monkeypatch.setattr(watchdog, 'win32', _FakeWin32(w))
    monkeypatch.delenv('TDX_ALLOW_TEST_CLICK', raising=False)
    wd = watchdog.PopupWatchdog(pid=PID, rules=[
        {'rule_key': 'r', 'match_type': 'title', 'pattern': '盘后数据下载',
         'action': 'close', 'priority': 10}])
    # 先让硬闸（正文）放行，才能验到 pytest 那道闸：把决定框摘掉
    w.box_alive = False
    evs = wd.sweep()
    assert w.closes == [], 'pytest 里真的发了 WM_CLOSE'
    assert any('硬挡' in e.result for e in evs), [e.result for e in evs]
