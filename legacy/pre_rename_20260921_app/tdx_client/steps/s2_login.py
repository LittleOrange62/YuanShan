"""
s2 · 填账号口令并登录。

这一步踩过的坑最多，逐条记下来：

1. **通达信会记住上次的账号。** 直接"打字"是追加，会变成
   `1896996969618969969696`，然后报"用户名或密码错误"，
   查半天以为密码记错了。所以填之前必须清空 —— `WM_SETTEXT` 整体替换
   天生没这个问题，但 SafeEdit 拒收它，于是只能靠 `win32.clear_edit()`
   把 `EM_SETSEL+WM_CLEAR`、选中退格、逐次退格/Delete 全走一遍。
   **2026-08-28 实测：这个坑复发过一次**，原因是清空用的退格
   `lParam` 传了 0 —— 见 `win32.key_lparam` 的注释。

2. **两个编辑框的枚举顺序不保证。** 早期脚本按 `EnumChildWindows`
   的顺序取 `[0]` 当账号框，某些版本上正好反了。
   **必须按 Y 坐标排序**：账号在上、口令在下，这个布局是稳定的。
3. **口令框跨进程读不出来。** `ES_PASSWORD` 的编辑框
   `WM_GETTEXT` 返回空是系统有意为之，所以口令不做回读校验，
   只校验账号。
4. **"已在别处登录"关掉没用。** 关掉之后行情不会恢复。看门狗把它标成
   alert，这里检测到就直接 handoff，让人决定是踢掉别处的会话还是等。
"""

from __future__ import annotations

import time

from app.tdx_client import creds, locator, ps, win32
from app.tdx_client.machine import RunContext, Step, StepResult
from app.tdx_client.steps.s1_launch import LOGIN_BUTTON_TEXTS, _login_box

# 出现这些字样说明登录本身出了问题，重试没意义。
HARD_FAIL_HINTS = ('别处登录', '密码错', '用户名或密码', '账号不存在', '锁定')

# V7.73 登录框里两个 SafeEdit 的控件 ID（实测抓到的）：
#   SafeEdit#1443  用户   y=553
#   SafeEdit#1058  密码   y=598
# 控件 ID 是编译进资源里的，比坐标稳；但整合版可能改，所以只作首选，
# 认不出来就退回按 Y 坐标定序。
CTRL_ID_ACCOUNT = 1443
CTRL_ID_PASSWORD = 1058
# 登录按钮的控件 ID（实测 V7.73）：Button#1 '登录'，Button#2 '取消'。
# 别把这两个搞混 —— 都在同一行，只差 180 像素。
CTRL_ID_LOGIN = 1


def _pick_fields(edits: list, ctx: RunContext) -> tuple:
    """
    从编辑框里挑出账号框和口令框。

    两条路：**先认控件 ID，认不出来按 Y 坐标定序**（账号在上、口令在下）。

    绝对不能用枚举顺序 —— 实测 `EnumChildWindows` 先返回的是
    `SafeEdit#1058`（口令，y=598），后返回 `SafeEdit#1443`（账号，y=553）。
    按枚举顺序取 `[0]` 当账号，正好把口令填进账号栏。
    """
    by_id = {c.ctrl_id: c for c in edits}
    a = by_id.get(CTRL_ID_ACCOUNT)
    p = by_id.get(CTRL_ID_PASSWORD)
    if a is not None and p is not None:
        ctx.log(f'  按控件 ID 认出：账号 #{a.ctrl_id} {a.rect}，'
                f'口令 #{p.ctrl_id} {p.rect}')
        return a, p
    # find_child 已按 (top, left) 排好序。
    a, p = edits[0], edits[1]
    ctx.log(f'  控件 ID 没对上（拿到 {sorted(by_id)}），'
            f'退回按 Y 坐标定序：账号 {a.rect}，口令 {p.rect}')
    return a, p


def _fill_by_real_input(field, value: str, ctx: RunContext, *,
                        secret: bool) -> tuple[bool, str]:
    """
    用**真实键盘事件**填 SafeEdit。这是唯一被证明能写进去的路。

    顺序不能变：抢前台（顶层窗口）→ 真鼠标点一下控件 → 真按键清空 → 打字。

    - 抢前台必须传**顶层窗口**。之前 `locator.send_keys` 把 SafeEdit 子控件的
      hwnd 交给 `SetForegroundWindow`，那个 API 只接受顶层窗口，于是永远报
      "抢不到前台 hwnd=3998984"，真实按键这条路整条被一个参数错误堵死。
    - 点一下控件而不是 `SetFocus`：SafeEdit 在钩子层取键，`AttachThreadInput`
      + `SetFocus` 只改窗口焦点，它的取键上下文不一定跟着切；真鼠标点击
      是它自己的窗口过程处理的，插入点和取键状态一起就位。
    - 清空用退格 + Delete 各按一遍：不知道点击后光标落在头还是尾。
    """
    root = win32.root_of(field.hwnd) or field.hwnd
    if not win32.set_foreground(root):
        return False, f'抢不到前台（顶层 hwnd={root}）'
    time.sleep(0.2)
    ok, why = win32.click_center(field.hwnd)
    if not ok:
        # 点不动就退回 SetFocus：真实按键打的是前台窗口，
        # 只要焦点在目标控件上，一样能落进去。
        ok, why = win32.focus_control(field.hwnd)
        if not ok:
            return False, f'既点不到也拿不到焦点：{why}'
    time.sleep(0.15)
    win32.clear_by_input()
    ok, why = win32.send_input_text(value)
    if not ok:
        return False, why
    return _confirm(field, value, ctx, secret=secret,
                    how=f'前台 + 真实按键（{why}）')


def _fill(field, value: str, ctx: RunContext, *, secret: bool) -> tuple[bool, str]:
    """
    往编辑框里填内容。三级，**每一级都整体覆盖已有内容**。

    1. `WM_SETTEXT` —— 整体替换，不要焦点不要前台。最省事，但
       **实测通达信的 `SafeEdit` 拒收它**（返回成功但内容没变），
       所以 SafeEdit 直接跳过这一级。
    2. 抢前台 + 真鼠标点击 + `SendInput` 真实按键。**SafeEdit 只认这一级。**
    3. `AttachThreadInput` + `SetFocus` → 逐字符 `WM_CHAR`。
       普通 `Edit` 够用；拿不到输入桌面时也只剩这条。

    **`SafeEdit` 必须跳过第 1 级，这一条是踩出来的。**
    `win32.set_text(verify=False)` 在写不进去时也返回 True —— 它没法判断。
    而口令框为了不回读明文，传的正是 `verify=False`，于是口令永远"WM_SETTEXT
    成功"、永远走不到后面真正有效的那一级。实测后果：登录按钮点了，
    但口令是空的，登录框 60 秒后还在原地，日志上却是一串"成功"。

    **第 3 级对 SafeEdit 只能写、清不掉，这是第二个坑。**
    早先的结论是"`PostMessage(WM_CHAR)` 对 SafeEdit 是假成功"。
    **2026-08-28 有了反证**：用户在界面上看到账号框变成
    `1896996969618969969696` —— 说明 `WM_CHAR` 的可见字符**确实写进去了**，
    真正失效的是清空用的退格（当时 `lParam` 传 0，被控件当成无效按键丢掉）。
    修法见 `win32.key_lparam` / `win32.clear_edit`。
    所以这一级现在是"能写、能清，但都验不了"，成败仍交给 `verify()`
    按登录结果判定；输入桌面可用时依旧优先走第 2 级。

    """
    is_safe = field.cls.startswith('SafeEdit')
    desk_ok, desk_why = win32.input_desktop_ok()

    if is_safe:
        ctx.log('    SafeEdit：跳过 WM_SETTEXT（已证明拒收且无法回读核实）')
        if desk_ok:
            ctx.log(f'    输入桌面可用（{desk_why}），走前台 + 真实按键')
            ok, why = _fill_by_real_input(field, value, ctx, secret=secret)
            if ok:
                return True, why
            ctx.log(f'    真实按键这条没成（{why}），退回 SetFocus + WM_CHAR')
        else:
            ctx.log(f'    输入桌面不可用（{desk_why}），只剩 SetFocus + WM_CHAR'
                    ' —— 可见字符能写进 SafeEdit，清空靠 clear_edit 兜')
    elif win32.set_text(field.hwnd, value, verify=not secret):
        return True, 'WM_SETTEXT 成功' + ('' if secret else '（已回读核对）')
    else:
        ctx.log('    WM_SETTEXT 没成，改走 SetFocus + WM_CHAR')

    # 焦点抢一次不一定成：上一轮登录失败弹出的提示框会把焦点带走，
    # 实测第 2 次重试就是这么挂的（"SetFocus 后焦点在 1641080"）。
    for attempt in range(3):
        ok, why = win32.focus_control(field.hwnd)
        if ok:
            break
        if attempt < 2:
            ctx.log(f'    焦点没拿到（{why}），{attempt + 1}/3 重试')
            time.sleep(0.6)
    if ok:
        _, how = win32.type_text(field.hwnd, value, clear=True)
        return _confirm(field, value, ctx, secret=secret,
                        how=f'SetFocus + WM_CHAR（{why}；{how}）')


    if not desk_ok:
        return False, (f'全部路径都挂了。焦点：{why}；输入桌面：{desk_why}。'
                       '这种情况下自动填表做不到，需要人工在通达信窗口里'
                       '手动登录一次（登录框会记住账号，之后点"登录"即可）。')
    return False, f'拿不到焦点：{why}'



def _safe(text: str, limit: int = 60) -> str:
    """
    把回读出来的内容变成能安全打印的样子。

    **不要直接把 SafeEdit 的回读结果塞进日志。** 它返回的是反键盘记录的
    混淆缓冲区，里面有 '\\u0368' 这类组合符号；一旦控制台是 GBK 编码，
    打印时就抛 `UnicodeEncodeError`，而这个异常发生在 act() 里，
    会被记成"登录失败" —— 实测就这么白跑了两轮重试，报错还指向编码，
    跟通达信毫无关系。这里只保留可打印的 ASCII 与中日韩字符，其余转义。
    """
    out = []
    for ch in text[:limit]:
        cp = ord(ch)
        if ch in '\t\n\r' or 0x20 <= cp < 0x7F or 0x4E00 <= cp <= 0x9FFF:
            out.append(ch)
        else:
            out.append(f'\\u{cp:04x}')
    tail = '…' if len(text) > limit else ''
    return ''.join(out) + tail


def _confirm(field, value: str, ctx: RunContext, *, secret: bool,
             how: str) -> tuple[bool, str]:
    """
    确认内容真的写进去了。

    **`SafeEdit` 两个方向都封**：`WM_SETTEXT` 写不进，`WM_GETTEXT` 也读不出
    （实测账号框回读是空串，不只是口令框）。所以回读失败 **不等于** 写失败，
    不能据此判 act 失败 —— 第一版就是这么判的，明明填对了却一直报
    "回读不一致：期望 11 字符，读到 ''"。

    改成三段：Win32 读到就直接核对；读不到再试 UIA 的 ValuePattern；
    两个都读不到就**承认这一步不可验证**，把结论交给 s2 的 `verify()`
    —— "登录框消失 + 主框架出现"才是真判据。这也符合整个包的原则：
    按结果判定，不按中间状态判定。
    """
    if secret:
        return True, f'{how}；口令框按设计不回读'

    got = win32.window_text(field.hwnd)
    if got == value:
        return True, f'{how}；Win32 回读核对通过'
    if got:
        return False, (f'{how} 后回读到 {_safe(got)}（{len(got)} 字符），'
                       '与目标不符（可能是没清空干净）')

    val, err = ps.read_uia_value(ctx.bag.get('login_title') or '通达信金融终端',
                                 index=0)
    if val == value:
        return True, f'{how}；UIA 回读核对通过'
    if val:
        return False, f'{how} 后 UIA 读到 {_safe(val)}，与目标不符'
    ctx.log(f'    回读不可用（Win32 空串，UIA：{_safe(err or "空", 120)}）—— '
            'SafeEdit 双向都封，改由登录结果判定')
    return True, f'{how}；内容不可回读，交给 verify 按登录结果判定'


def _escape_keys(text: str) -> str:
    """
    SendKeys 的元字符转义。

    `+^%~(){}[]` 在 SendKeys 里都有特殊含义 —— 口令 `Chengzi.888` 没事，
    但换成含 `+` 或 `%` 的口令就会被解释成 Shift/Alt 组合键，
    输进去的东西跟口令完全不同，而且报出来的还是"密码错误"。
    """
    return ''.join('{' + c + '}' if c in '+^%~(){}[]' else c for c in text)


class LoginStep(Step):
    key = 's2_login'
    title = '填账号口令并登录'
    retry = 2
    verify_timeout = 60
    verify_interval = 1.5

    def precheck(self, ctx: RunContext) -> tuple[bool, str]:
        pid = ctx.bag.get('pid')
        if _login_box(pid) is None and ctx.refresh_main() is not None:
            return True, '没有登录框且主窗口在，已经是登录状态'
        return False, ''

    def act(self, ctx: RunContext) -> StepResult:
        try:
            acct, pwd = creds.tdx_account()
        except creds.MissingCredentials as e:
            return StepResult.ask_human(str(e))

        pid = ctx.bag.get('pid')
        box = _login_box(pid)
        if box is None:
            # 主窗口在但没登录框：可能登录框还没弹出来，等一会儿。
            ok, why = self.wait_until(
                lambda: ((_login_box(pid) is not None), '登录框还没出现'),
                timeout=30, interval=1.0)
            if not ok:
                return StepResult.fail(f'等不到登录框：{why}')
            box = _login_box(pid)
        assert box is not None
        ctx.bag['login_hwnd'] = box.hwnd
        ctx.bag['login_title'] = box.text
        ctx.log(f'  登录框：{box.text!r} {box.rect}')

        edits = locator.find_child(box.hwnd, classes=locator.EDIT_CLASSES)
        if len(edits) < 2:
            return StepResult.ask_human(
                f'登录框里只找到 {len(edits)} 个编辑框（需要 2 个：账号、口令）。'
                '可能是这个版本的登录框改成了自绘控件，'
                '看现场包里的 tree_*.txt 和 uia_*.json 确认实际控件。')

        acct_box, pwd_box = _pick_fields(edits, ctx)
        if acct_box is None or pwd_box is None:
            return StepResult.ask_human('分不清哪个是账号框哪个是口令框')

        ok, why = _fill(acct_box, acct, ctx, secret=False)
        ctx.log(f'  账号 {creds.mask(acct)} → {why}')
        if not ok:
            return StepResult.fail(f'账号写入失败：{why}')
        ok, why = _fill(pwd_box, pwd, ctx, secret=True)
        ctx.log(f'  口令 {creds.mask_secret(pwd)} → {why}')
        if not ok:
            return StepResult.fail(f'口令写入失败：{why}')

        # **搜按钮必须 visible_only=False。**
        # 实测控件树：登录框自身是 [VE]，但它的按钮全是 [-E] ——
        # `IsWindowVisible` 报 false，因为通达信的登录框是自绘换肤的，
        # 真控件存在且能收 BM_CLICK，只是不被系统当成"可见"。
        # 默认的 visible_only=True 会把 `Button#1 '登录'` 直接滤掉，
        # 于是明明按钮就在那儿，却报"找不到登录按钮"。
        #
        # 先按控件 ID 认（实测 V7.73 登录按钮是 Button#1，取消是 Button#2），
        # 认不出来再按文字找。号段比文字稳：文字会随皮肤和版本变。
        btn = None
        hits = locator.find_child(box.hwnd, ctrl_id=CTRL_ID_LOGIN,
                                  classes=locator.BUTTON_CLASSES,
                                  visible_only=False)
        if hits and hits[0].text.strip() in LOGIN_BUTTON_TEXTS:
            btn = hits[0]
        if btn is None:
            for text in LOGIN_BUTTON_TEXTS:
                hits = locator.find_child(box.hwnd, text=text, exact=True,
                                          classes=locator.BUTTON_CLASSES,
                                          visible_only=False)
                if hits:
                    btn = hits[0]
                    break
        if btn is None:
            return StepResult.ask_human(
                f'找不到登录按钮（试过 ID {CTRL_ID_LOGIN} 与文字 {LOGIN_BUTTON_TEXTS}）。'
                '看现场包 tree_*.txt 里登录框的按钮实际叫什么。')
        win32.bm_click(btn.hwnd)
        return StepResult.done(f'已点击 {btn.text!r}（{btn.cls}#{CTRL_ID_LOGIN}）')

    def verify(self, ctx: RunContext) -> tuple[bool, str]:
        """
        登录成功的判据：**登录框消失 且 主窗口在**。

        只看"登录框消失"不够 —— 密码错误时登录框也会短暂消失再重建。
        中途出现硬失败字样就立刻返回，不要傻等到超时。
        """
        pid = ctx.bag.get('pid')

        def check():
            for w in win32.top_windows():
                if pid is not None and w.pid != pid:
                    continue
                for hint in HARD_FAIL_HINTS:
                    if hint in w.text:
                        return False, f'**出现 {w.text!r}**，登录被拒，重试无意义'
            if _login_box(pid) is not None:
                return False, '登录框还在'
            main = ctx.refresh_main()
            if main is None:
                return False, '登录框没了但主窗口也没了'
            return True, f'登录框已消失，主窗口 {main.text!r}'

        ok, why = self.wait_until(check)
        if ok:
            # 登录成功后行情连接还要几秒，给个短暂缓冲再交给下一步。
            time.sleep(2.0)
        return ok, why
