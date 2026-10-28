"""
弹窗看门狗。

通达信在整条链路上会弹一堆东西：`通达信信息`（公告）、升级提示、
`已在别处登录`、下载完成提示、行情连接断开……它们出现的时机不固定，
所以不能在流程里写死"第几步之后关一个弹窗"，只能开一个后台线程一直盯着。

**两条铁律：**

1. **不许盲关所有窗口。** 早期脚本用"标题不在白名单里就 WM_CLOSE"，
   结果把 `TQ数据设置` 对话框自己关掉了 —— 下载还没点就没了，
   然后流程等到超时，日志上完全看不出是自己关的。
   所以这里只关**规则明确匹配上**的窗口，规则以外的一律放过并记一笔。
2. **有些弹窗关掉等于放弃。** `已在别处登录` 关掉之后行情不会恢复，
   必须重新登录；`磁盘空间不足` 关掉之后下载还是会失败。
   这类规则标 `action='alert'`，看门狗只报不关，让状态机去处理。

规则存在 `config.popup_rule` 表里，页面上能改 —— 遇到没见过的弹窗时，
不用改代码，加一行规则就行。数据库连不上时退回代码里的内置规则。
"""

from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass

from app.tdx_client import win32
from app.tdx_client.locator import BUTTON_CLASSES
from app.tdx_client.win32 import Win

# 内置兜底规则。**数据库里的 config.popup_rule 优先**，这里只是数据库连不上
# 或者表还没建时的保底，字段含义跟表一致。
#   match_type: title / class
#   action:     close（关掉）/ button（点指定按钮）/ alert（只报不关）/ ignore（放过）
BUILTIN_RULES: tuple[dict, ...] = (
    # —— 必须放过的（关掉就等于破坏流程）——
    {'rule_key': 'keep_tq_setting', 'match_type': 'title', 'pattern': 'TQ数据设置',
     'action': 'ignore', 'priority': 1,
     'note': '**这个千万不能关**：手动下载的主对话框，关掉整条链路就断了'},
    {'rule_key': 'keep_login', 'match_type': 'title', 'pattern': '用户登录',
     'action': 'ignore', 'priority': 1, 'note': '登录框由登录步骤自己处理'},
    {'rule_key': 'keep_main', 'match_type': 'title', 'pattern': '通达信金融终端',
     'action': 'ignore', 'priority': 1, 'note': '主窗口'},
    {'rule_key': 'keep_download', 'match_type': 'title', 'pattern': '数据下载',
     'action': 'ignore', 'priority': 1,
     'note': '下载进度窗口，它自己消失是完成判据之一'},
    # 「您确定取消下载吗？」这类决定框：实测**标题就是 `'TdxW'`**（通达信没给它起名字，
    # 默认拿 exe 名当标题），标题规则能捞到的只有这一种窗口。判 `alert` 而不是 `close`
    # 是因为两个分支一个继续下、一个放弃整轮，而按 X 走哪个分支没人知道 ——
    # 由 `steps/s8_finish.answer_confirm()` 按"这一轮是不是已经判完成/判死"来点按钮。
    # `_close` 里还有一道**看正文**的硬闸：标题不是稳定信息，两道都要在。
    {'rule_key': 'cancel_download_confirm', 'match_type': 'title', 'pattern': 'TdxW',
     'action': 'alert', 'priority': 1,
     'note': '**只报不关**：这是"要不要放弃这一轮下载"的决定框，关它等于替用户赌默认分支'},

    # —— 只报不关的（关了流程就白跑）——
    {'rule_key': 'login_elsewhere', 'match_type': 'title', 'pattern': '别处登录',
     'action': 'alert', 'priority': 5,
     'note': '账号在别的机器登录了，关掉也没用，必须重新登录'},
    {'rule_key': 'disk_full', 'match_type': 'title', 'pattern': '空间不足',
     'action': 'alert', 'priority': 5, 'note': '磁盘满了，关掉下载照样失败'},
    {'rule_key': 'wrong_pwd', 'match_type': 'title', 'pattern': '密码',
     'action': 'alert', 'priority': 5,
     'note': '口令相关提示，可能是密码错了，交给登录步骤判断'},

    # —— 关掉的 ——
    {'rule_key': 'tdx_info', 'match_type': 'title', 'pattern': '通达信信息',
     'action': 'close', 'priority': 10, 'note': '登录后的公告窗口，每次都有'},
    {'rule_key': 'notice', 'match_type': 'title', 'pattern': '公告',
     'action': 'close', 'priority': 10},
    {'rule_key': 'upgrade', 'match_type': 'title', 'pattern': '升级',
     'action': 'close', 'priority': 10, 'note': '版本升级提示，不升'},
    {'rule_key': 'tips', 'match_type': 'title', 'pattern': '提示',
     'action': 'close', 'priority': 20},
    {'rule_key': 'ad', 'match_type': 'title', 'pattern': '资讯',
     'action': 'close', 'priority': 20},
)

# 一个弹窗连着关不掉这么多次就不再尝试，改成 alert。
MAX_CLOSE_ATTEMPTS = 3


@dataclass
class PopupEvent:
    when: float
    title: str
    cls: str
    hwnd: int
    rule_key: str
    action: str
    result: str

    def __str__(self) -> str:
        stamp = time.strftime('%H:%M:%S', time.localtime(self.when))
        return f'[{stamp}] {self.action:<6} {self.title!r} ({self.rule_key}) → {self.result}'


def load_rules() -> tuple[list[dict], str]:
    """
    从 `config.popup_rule` 读规则，读不到就用内置的。

    返回 `(rules, source)`，source 写进日志 —— 排查"为什么这个弹窗没关掉"时
    第一个要确认的就是当时用的是数据库规则还是内置规则。
    """
    try:
        from app.db import engine

        rows = engine.query(
            """
            SELECT rule_key, match_type, pattern, action, button_text,
                   priority, note
              FROM config.popup_rule
             WHERE is_active
             ORDER BY priority, rule_key
            """, role='reader')
        if rows:
            return [dict(r) for r in rows], f'config.popup_rule（{len(rows)} 条）'
    except Exception as e:
        return list(BUILTIN_RULES), f'内置规则（数据库不可用：{e}）'
    return list(BUILTIN_RULES), '内置规则（config.popup_rule 为空）'


def match_rule(w: Win, rules: list[dict]) -> dict | None:
    """
    找第一条匹配的规则。规则已按 priority 排序，**ignore/alert 的优先级
    数字要比 close 小**，否则 `TQ数据设置` 会先被 `提示` 之类的宽泛规则捞走。
    """
    for r in sorted(rules, key=lambda x: (x.get('priority') or 100,
                                          x.get('rule_key') or '')):
        mt = (r.get('match_type') or 'title').lower()
        pat = r.get('pattern') or ''
        if not pat:
            continue
        hay = w.text if mt == 'title' else w.cls
        if pat in hay:
            return r
    return None


class PopupWatchdog:
    """
    后台线程，按固定间隔扫顶层窗口并按规则处理。

    用线程而不是在每步之间检查一次：弹窗可能在某一步**执行中**冒出来
    并夺走焦点，那时候主线程正卡在等待里，事后再检查已经晚了。

    `pid` 限定只处理通达信自己的窗口 —— 不加这个限制，别的程序的
    "提示" 窗口也会被关掉，那属于越界。
    """

    def __init__(self, *, interval: float = 1.5, pid: int | None = None,
                 rules: list[dict] | None = None):
        self.interval = interval
        self.pid = pid
        self.rules, self.rules_source = (rules, '调用方传入') if rules else load_rules()
        self.events: list[PopupEvent] = []
        self.alerts: list[PopupEvent] = []
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._attempts: dict[int, int] = {}
        self._seen_ignored: set[int] = set()
        self.unknown: list[Win] = []

    # -- 生命周期 --
    def start(self) -> PopupWatchdog:
        if self._thread and self._thread.is_alive():
            return self
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name='popup-watchdog',
                                        daemon=True)
        self._thread.start()
        return self

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=self.interval * 3)

    def __enter__(self) -> PopupWatchdog:
        return self.start()

    def __exit__(self, *_exc) -> None:
        self.stop()

    # -- 主循环 --
    def _loop(self) -> None:
        while not self._stop.wait(self.interval):
            try:
                self.sweep()
            except Exception:
                # 看门狗自己挂掉不能影响主流程，但要留痕。
                with self._lock:
                    self.events.append(PopupEvent(
                        time.time(), '', '', 0, '-', 'error',
                        '看门狗本轮扫描异常，已跳过'))

    def sweep(self) -> list[PopupEvent]:
        """扫一轮。也可以在主线程里手动调，用于步骤之间的确定性清理。"""
        out: list[PopupEvent] = []
        for w in win32.top_windows():
            if self.pid is not None and w.pid != self.pid:
                continue
            if not w.text and not w.cls:
                continue
            # 太大的窗口不是弹窗，是主界面或对话框本体。
            if w.w > 1200 and w.h > 800:
                continue
            rule = match_rule(w, self.rules)
            if rule is None:
                if w.hwnd not in self._seen_ignored:
                    self._seen_ignored.add(w.hwnd)
                    # **_record 自己拿锁，不能在 `with self._lock` 里调它** ——
                    # threading.Lock 不可重入，2026-09-23 探测自愈第一次碰到
                    # "没匹配规则"的窗口就整条线程死锁在这里。
                    ev = self._record(w, {'rule_key': '-', 'action': 'unknown'},
                                      '没有匹配规则，**放过不关**；'
                                      '如需处理请在 config.popup_rule 加一条')
                    with self._lock:
                        self.unknown.append(w)
                    out.append(ev)
                continue
            action = (rule.get('action') or 'close').lower()
            if action == 'ignore':
                continue
            if action == 'alert':
                if w.hwnd in self._seen_ignored:
                    continue
                self._seen_ignored.add(w.hwnd)
                ev = self._record(w, rule, '**只报不关**，交给状态机处理')
                with self._lock:
                    self.alerts.append(ev)
                out.append(ev)
                continue
            out.extend(self._close(w, rule))
        return out

    def _close(self, w: Win, rule: dict) -> list[PopupEvent]:
        if os.environ.get('PYTEST_CURRENT_TEST') and not os.environ.get('TDX_ALLOW_TEST_CLICK'):
            # 看门狗会真的 WM_CLOSE / BM_CLICK 匹配上的窗口。pytest 里没打桩就走到这儿，
            # 动的就是用户桌面上的真窗口。测试办法：假 `win32` + `TDX_ALLOW_TEST_CLICK=1`。
            return [self._record(w, {'rule_key': rule.get('rule_key')},
                                 'PYTEST 硬挡：没 monkeypatch win32，不在用户桌面上点窗')]
        # **硬闸：正文里是"要不要放弃这一轮下载"的框，一律不盲关、不代答。**
        # 这条故意写在代码里而不是只写在 `config.popup_rule`：
        # ① 规则表只能按**标题/类名**匹配，而这类框的标题实测是 `'TdxW'`（零信息），
        #    正文才是内容，表表达不出来；
        # ② 表能在页面上被人改，"绝不掉数据"这种不该依赖一条可编辑的数据。
        # 决定由 `steps/s8_finish.answer_confirm()` 按判据做：已判完成/判卡死才点确定，
        # 还在下就点取消（= 不取消、继续等）。
        marks = self._decision_marks(w)
        if marks:
            if w.hwnd not in self._seen_ignored:
                self._seen_ignored.add(w.hwnd)
                ev = self._record(w, rule,
                                  f'正文含 {marks}：**这是要不要放弃本轮下载的决定框，'
                                  '绝不盲关**（按 X 走哪个分支没人知道）—— '
                                  '交给 s8 按判据点按钮')
                with self._lock:
                    self.alerts.append(ev)
                return [ev]
            return []

        n = self._attempts.get(w.hwnd, 0)
        if n >= MAX_CLOSE_ATTEMPTS:
            if w.hwnd not in self._seen_ignored:
                self._seen_ignored.add(w.hwnd)
                ev = self._record(w, rule,
                                  f'连续 {n} 次关不掉，放弃并升级为告警 —— '
                                  '可能是模态框需要点具体按钮，'
                                  '请在 config.popup_rule 里给它配 button_text')
                with self._lock:
                    self.alerts.append(ev)
                return [ev]
            return []
        self._attempts[w.hwnd] = n + 1

        btn = (rule.get('button_text') or '').strip()
        if btn:
            # 只认**按钮类**且文字精确相等。上一版是 `btn in c.text` 且不看类名，
            # 于是 `确定` 会命中正文 Static「您确定取消下载吗？」并 BM_CLICK 一段静态文字 ——
            # 按钮没点着、框还在，而我们已经以为自己处理过了。
            hits = [c for c in win32.children(w.hwnd, max_depth=4)
                    if c.visible and c.cls.startswith(BUTTON_CLASSES)
                    and c.text.strip() == btn]
            if hits:
                win32.bm_click(hits[0].hwnd)
                return [self._record(w, rule, f'点了按钮 {btn!r}')]
            return [self._record(w, rule, f'找不到按钮 {btn!r}，改用 WM_CLOSE')]

        win32.close_window(w.hwnd)
        time.sleep(0.2)
        if not win32.user32.IsWindow(w.hwnd):
            return [self._record(w, rule, 'WM_CLOSE 已关闭')]
        # WM_CLOSE 被吃掉的情况：改走系统菜单的关闭命令。
        win32.close_window(w.hwnd, syscommand=True)
        time.sleep(0.2)
        gone = not win32.user32.IsWindow(w.hwnd)
        return [self._record(w, rule,
                             'SC_CLOSE 已关闭' if gone else f'仍未关闭（第 {n + 1} 次）')]

    @staticmethod
    def _decision_marks(w: Win) -> str:
        """这个窗口的正文里有没有"要不要放弃本轮下载"的字样。返回命中的字样（没中给空串）。"""
        try:
            from app.tdx_client.dl_dialog import CONFIRM_MARKS
            texts = [c.text or '' for c in win32.children(w.hwnd, max_depth=4)]
        except Exception:  # noqa: BLE001  枚举坏了就当"不确定"，宁可不动手
            return ''
        for m in CONFIRM_MARKS:
            if any(m in t for t in texts):
                return m
        return ''

    def _record(self, w: Win, rule: dict, result: str) -> PopupEvent:
        ev = PopupEvent(time.time(), w.text, w.cls, w.hwnd,
                        rule.get('rule_key') or '-',
                        (rule.get('action') or 'close').lower(), result)
        with self._lock:
            self.events.append(ev)
        return ev

    # -- 汇报 --
    def report(self) -> str:
        with self._lock:
            evs = list(self.events)
        if not evs:
            return f'看门狗（规则来源：{self.rules_source}）：本次运行没遇到弹窗'
        lines = [f'看门狗（规则来源：{self.rules_source}）共处理 {len(evs)} 次：']
        lines += [f'  {e}' for e in evs]
        if self.alerts:
            lines.append('  **以上带 alert 的需要人工确认**')
        return '\n'.join(lines)

    def pending_alerts(self) -> list[PopupEvent]:
        with self._lock:
            return list(self.alerts)


def sweep_once(*, pid: int | None = None, rounds: int = 3,
               interval: float = 0.8) -> tuple[list[PopupEvent], list[PopupEvent]]:
    """
    确定性地清几轮弹窗，返回 `(处理记录, 待人工确认)`。

    **为什么要多轮**：通达信的公告窗口是连着弹的 —— 关掉第一个，
    第二个才出现。一轮只能清掉一层，实测三轮足够。
    """
    wd = PopupWatchdog(pid=pid)
    for i in range(rounds):
        wd.sweep()
        if i < rounds - 1:
            time.sleep(interval)
    return wd.events, wd.alerts
