"""
s5 · 打开「盘后数据下载」对话框。

**2026-08-31 走通了，用的是 D 级。** 在此之前 08-28/29 连着栽在这一步，
当时的三级 tier 全灭，结论写成了"这台机器上 GUI 入口走不通"——
**那个结论是错的，真正的原因是窗口没最大化**：通达信不满屏时会把菜单栏
后面几项折叠进 `▼`，`TQ策略` 整项消失。满屏之后菜单栏是
`行情 市场 资讯 数据 发现 问小达 财富圈 TQ策略 交易`，TQ策略 就是第 8 项。

四级 tier，**D 级最先试**（唯一实测走通的）：

    D. 最大化 → 点菜单坐标 → TQ数据设置 → 盘后数据下载   ← 实测可行
    A. TQ 插件 `exec_to_tdx('.933')`     —— 不碰菜单、不要焦点、不要前台
    B. 主窗口键盘向导：`.933` + Enter    —— 只发 WM_CHAR，不用鼠标
    C. 自绘菜单按名字定位                —— 本机必然失败，菜单没有可定位文字

`.933` 是通达信官方点系列命令里的「盘后数据下载」
（见 https://www.tdx.com.cn/products/helpfile/tdxjysm/ ）。

**A/B/C 三级为什么留着**：D 级依赖输入桌面（`GetCursorPos` 不能被拒），
而这台机器上这个能力**会变** —— 08-29 是 ACCESS_DENIED、08-31 又可用了
（差别在有没有活的交互式会话）。所以 D 不可用时还得有别的路可退。

**2026-08-29 那次三级全灭的原因，逐条留着省得重查：**

- A 级：`exec_to_tdx('.933')` 返回 `{'ErrorId': '0', 'Msg': '.933'}`，
  看着像成功，但**前后对比顶层窗口列表，一个新窗口都没多**（等到 +6s 仍然没有）。
  所以 `ErrorId=0` 只代表"命令被收下了"，不代表"界面动了"，不能当判据。
  **08-31 换成 HTTP 通道复测，结果一样**：`exec_to_tdx('.933')` 返回 null、
  顶层窗口 69 → 69。坏的是"点系列命令 → 界面"这条通道本身，不是命令码写错。
- B 级：主窗口当时停在**加载失败的「系统页面」**，客户区是 Chrome 内嵌
  webview，键盘精灵的字符大概率被它吃掉了。
- C 级：菜单栏文字全是自绘的 —— `GetMenu()` 返回 0、1,481 个子控件里没有
  一个带菜单文字、UIA dump 是空的，两级定位同时抓瞎。**能点但不能按名字找**，
  这就是 D 级要写坐标的原因。

**`retry` 从 3 改成 1。** 四级 tier 已经在一次 `act()` 里全试过了，
外层再重试只是把同样的失败重演，还每次留一个现场包。定位失败直接 handoff。

明确不采用的做法：

- **盲发 `Alt+S` → `Enter`。** 焦点不对时这两下会打到别的程序上。
- **在菜单栏上横向扫描着点。** 点错会打开别的功能（TQ策略管理器等），
  有副作用。坐标写在 `config.setting['tdx.menu']` 里，点不中就 handoff 留截图，
  让人看图给个新坐标 —— 比乱点安全。
  （Tab 标签行上扫描是另一回事，那里没有副作用，见 `dl_dialog.switch_tab`。）
"""


from __future__ import annotations

import time

from app.tdx_client import dl_dialog, locator, ps, tq_bridge, win32
from app.tdx_client.machine import RunContext, Step, StepResult

MENU_TOP = 'TQ策略'
MENU_ITEM = 'TQ数据设置'
# 点系列命令：盘后数据下载。
CMD_DOWNLOAD = tq_bridge.CMD_DAY_DOWNLOAD

# ---- D 级（2026-08-31 实测唯一走通的一条）的坐标 ------------------------
#
# **为什么只能写坐标**：菜单栏是自绘的，1,481 个子控件里没有一个带菜单文字，
# UIA dump 是空的 —— 没有任何可按名字定位的东西。但点开之后弹出的是标准
# `#32768` 菜单窗口，所以"点中没点中"有硬判据，不是盲点。
#
# **必须先最大化**：窗口不满屏时后面几项会被折叠进 `▼`，`TQ策略` 直接消失。
# 满屏（1920 宽）时菜单栏是
# `行情 市场 资讯 数据 发现 问小达 财富圈 TQ策略 交易`，TQ策略 是第 8 项。
#
# 下拉菜单五项：TQ策略管理器 / TQ数据设置 / TQ策略信号 / TQ策略数据浏览 /
# TQ函数帮助。第 2 项相对菜单窗口顶边 +32px（实测窗口 top=23、项中心 y=55）。
MENU_POINT = (558, 11)
MENU_ITEM_DY = 32
#: 库里可以改：`config.setting['tdx.menu']`，值形如
#: `{"x":558,"y":11,"item_dy":32}`。换皮肤/换分辨率时不用改代码。
MENU_SETTING_KEY = 'tdx.menu'


def menu_point() -> tuple[int, int, int]:
    """`(x, y, item_dy)`。库里配了用库里的，否则用实测默认值。"""
    x, y = MENU_POINT
    dy = MENU_ITEM_DY
    try:
        from app.db import engine
        val = engine.scalar('SELECT value FROM config.setting WHERE key = %s',
                            (MENU_SETTING_KEY,))
    except Exception:            # noqa: BLE001  库连不上时不该把这条路也带死
        val = None
    if isinstance(val, dict):
        x = int(val.get('x', x))
        y = int(val.get('y', y))
        dy = int(val.get('item_dy', dy))
    return x, y, dy



# 这条链路会开出来的两个窗口：「TQ数据设置」是上一层，「盘后数据下载」是
# 真正要操作的那个。`find_dialog` 两个都认，因为 **s8 收尾要把两个都关掉**。
# 判"开成功了没有"绝对不能用它 —— 见 `OpenDialogStep.verify`。
DIALOG_TITLES = (dl_dialog.SETTINGS_TITLE, dl_dialog.TITLE)


def find_dialog(pid: int | None = None) -> win32.Win | None:
    """
    找「盘后数据下载 / TQ数据设置」对话框。

    加尺寸下限是因为主窗口标题在某些皮肤下也可能被匹配到；
    对话框是个几百像素的中等窗口，不会是 0×0 也不会是全屏。
    """
    best = None
    for w in win32.top_windows():
        if pid is not None and w.pid != pid:
            continue
        if not any(t in w.text for t in DIALOG_TITLES):
            continue
        if locator.MAIN_TITLE in w.text:
            continue
        if w.w < 200 or w.h < 120:
            continue
        if best is None or w.w * w.h < best.w * best.h:
            best = w
    return best


def _appeared(pid: int | None, *, timeout: float = 8.0) -> win32.Win | None:
    """
    等**「盘后数据下载」**冒出来。每一级 tier 试完都用它判一次。

    刻意用 `dl_dialog.find`（只认这一个标题）而不是上面的 `find_dialog`
    （两个标题都认）：只开到「TQ数据设置」就算成功的话，s6 会在里面找不到
    1424/1 这些 ctrl_id，失败信息会指向"控件不见了"，跟真实原因差得很远。
    """
    deadline = time.monotonic() + timeout
    while True:
        d = dl_dialog.find(pid)
        if d is not None:
            return d
        if time.monotonic() >= deadline:
            return None
        time.sleep(0.5)



def tier_d_menu_point(ctx: RunContext, main: win32.Win) -> tuple[bool, str]:
    """
    D 级：**2026-08-31 实测唯一走通的一条。** 全屏 → 点菜单坐标 →
    点「TQ数据设置」→ 在设置窗里 `BM_CLICK`「盘后数据下载」。

    每一下都有硬判据，不靠"点了就算成功"：

        点 TQ策略      → 有没有多出 `#32768` 顶层窗口
        点 TQ数据设置  → 有没有出现标题为 `TQ数据设置` 的 `#32770`
        点 盘后数据下载 → 有没有出现标题为 `盘后数据下载` 的 `#32770`

    菜单点歪了就发 Esc 把菜单收掉再退回别的 tier —— 留着一个展开的菜单
    会挡住后面所有点击。
    """
    if not win32.input_desktop_ok()[0]:
        return False, '输入桌面不可用，坐标点击发不出去（这一级只能放弃）'

    x, y, item_dy = menu_point()
    win32.set_foreground(main.hwnd)
    if not win32.is_zoomed(main.hwnd):
        win32.maximize(main.hwnd)
        time.sleep(0.8)
        main = ctx.refresh_main() or main
        ctx.log(f'    已最大化 → {main.rect}（不最大化 TQ策略 会被折叠进 ▼）')

    before = {w.hwnd for w in win32.top_windows()}
    ok, why = win32.click_point(x, y)
    if not ok:
        return False, f'点菜单 {MENU_TOP!r} ({x},{y}) 失败：{why}'
    time.sleep(1.2)
    popup = [w for w in win32.top_windows()
             if w.hwnd not in before and w.cls == '#32768']
    if not popup:
        return False, (f'点了 ({x},{y}) 但没弹出 #32768 菜单 —— '
                       f'坐标不对（改 config.setting[{MENU_SETTING_KEY!r}]）'
                       f'或者主窗口没在最大化状态')
    mrect = win32.rect_of(popup[0].hwnd)
    ctx.log(f'    菜单已弹出 {mrect}')

    iy = mrect[1] + item_dy
    ix = mrect[0] + max(20, (mrect[2] - mrect[0]) // 3)
    ok, why = win32.click_point(ix, iy)
    if not ok:
        return False, f'点 {MENU_ITEM!r} ({ix},{iy}) 失败：{why}'
    time.sleep(2.5)
    st = dl_dialog.find_settings(ctx.bag.get('pid'))
    if st is None:
        # 菜单可能还开着，Esc 收掉，否则挡住后面所有点击
        win32.post_message(main.hwnd, win32.WM_KEYDOWN, win32.VK_ESCAPE,
                           win32.key_lparam(win32.VK_ESCAPE))
        return False, (f'点了菜单第 2 项 ({ix},{iy}) 但 {MENU_ITEM!r} 没出现 —— '
                       f'菜单项偏移 item_dy={item_dy} 可能不对')
    ctx.log(f'    {MENU_ITEM} 已打开 {st.rect}')

    btns = [k for k in win32.children(st.hwnd, max_depth=6)
            if k.text == dl_dialog.SETTINGS_BUTTON]
    if not btns:
        texts = [k.text for k in win32.children(st.hwnd, max_depth=6) if k.text]
        return False, (f'{MENU_ITEM} 里没有 {dl_dialog.SETTINGS_BUTTON!r} 按钮；'
                       f'可见文字：{texts[:15]}')
    win32.bm_click(btns[0].hwnd)
    return True, (f'D 级链路走通：菜单({x},{y}) → 第2项({ix},{iy}) → '
                  f'{dl_dialog.SETTINGS_BUTTON}')


def tier_a_tq() -> tuple[bool, str]:

    """
    A 级：让已登录的客户端自己执行 `.933`。

    **完全不碰界面** —— 不要前台、不要焦点、不管菜单是不是自绘的，
    所以它同时也是本机唯一可能在输入桌面不可用时还能走通的一级。
    """
    ok, why = tq_bridge.available()
    if not ok:
        return False, f'TQ 插件不可用：{why}'
    try:
        r = tq_bridge.exec_url(CMD_DOWNLOAD)
    except Exception as exc:
        return False, f'exec_to_tdx({CMD_DOWNLOAD}) 抛异常：{exc}'
    if not r.get('ok'):
        return False, f'exec_to_tdx 失败：{str(r.get("error"))[:200]}'
    return True, f'exec_to_tdx({CMD_DOWNLOAD}) 返回 {r.get("ret")!r}'


def tier_b_command(ctx: RunContext, main: win32.Win) -> tuple[bool, str]:
    """
    B 级：主窗口里敲 `.933` 再回车 —— 通达信的键盘精灵。

    只用 `PostMessage(WM_CHAR)`，不用 SendInput，所以不依赖输入桌面。
    **前提是主窗口拿到焦点**：键盘精灵是主窗口自己弹的输入框，
    焦点在别处时这几个字符会被丢掉（不会打到别的程序上，因为是定向 Post）。
    """
    if not win32.set_foreground(main.hwnd):
        ctx.log('    抢不到前台，键盘精灵可能收不到字符，仍然试一次')
    win32.focus_control(main.hwnd)
    for ch in CMD_DOWNLOAD:
        win32.post_message(main.hwnd, win32.WM_CHAR, ord(ch), 1)
        time.sleep(0.08)
    time.sleep(0.5)
    win32.post_message(main.hwnd, win32.WM_KEYDOWN, win32.VK_RETURN,
                       win32.key_lparam(win32.VK_RETURN))
    win32.post_message(main.hwnd, win32.WM_KEYUP, win32.VK_RETURN,
                       win32.key_lparam(win32.VK_RETURN, up=True))
    return True, f'已向主窗口发 {CMD_DOWNLOAD} + Enter（WM_CHAR，不依赖鼠标）'


def tier_c_menu(ctx: RunContext, main: win32.Win) -> tuple[bool, str]:
    """
    C 级：老路 —— 自绘菜单 `TQ策略 → TQ数据设置`。

    留着不是因为它可靠，而是别的整合版上菜单可能是标准菜单、这条路能走。
    本机上它必然失败（`TQ策略` 是个加载失败的 webview），失败信息里点明这一点，
    省得接手的人再查一遍。
    """
    ctx.log(f'    先点开顶级菜单 {MENU_TOP!r}')
    top = locator.locate(MENU_TOP, root=main, uia_title=locator.MAIN_TITLE,
                         allow_uia=True, allow_keys=False, allow_point=False)
    ctx.log(f'      {top.summary()}（{top.tier} 级）')
    ok, detail = locator.click(top, root=main)
    if not ok:
        return False, f'点 {MENU_TOP!r} 失败：{detail}'
    # 下拉菜单渲染需要一点时间，太快去 dump 会拿到还没展开的树。
    time.sleep(0.8)

    # 展开的菜单是独立的弹出窗口，不在主窗口的控件树里，所以直接走 UIA
    # 并且**从桌面根往下找**，`uia_title` 用主窗口标题会漏。
    ok, detail = ps.invoke_uia(locator.MAIN_TITLE, MENU_ITEM)
    if ok:
        return True, f'UIA 点了 {MENU_ITEM!r}：{detail}'
    ctx.log(f'      UIA 直接 Invoke 没成：{detail}，改走定位+点击')
    item = locator.locate(MENU_ITEM, root=None, uia_title=locator.MAIN_TITLE,
                          allow_uia=True, allow_keys=False, allow_point=True,
                          point=None)
    ok, detail = locator.click(item, root=main)
    if not ok:
        return False, f'点 {MENU_ITEM!r} 失败：{detail}'
    return True, f'已点 {MENU_ITEM!r}：{detail}'


class OpenDialogStep(Step):
    key = 's5_dialog'
    title = '打开盘后数据下载（D:菜单坐标 / A:TQ命令 / B:键盘精灵 / C:自绘菜单）'
    # 见模块说明：四级 tier 已经在一次 act 里试完，外层重试只会重演失败。
    retry = 1
    verify_timeout = 30
    verify_interval = 1.0

    def precheck(self, ctx: RunContext) -> tuple[bool, str]:
        # **只认「盘后数据下载」**。上一层的「TQ数据设置」还开着不算数：
        # 跳过这一步的话 s6 会在设置窗里找 1424，报"控件不见了"。
        d = dl_dialog.find(ctx.bag.get('pid'))
        if d is not None:
            ctx.bag['dialog_hwnd'] = d.hwnd
            return True, f'对话框已经开着：{d.text!r} {d.rect}'
        return False, ''

    def act(self, ctx: RunContext) -> StepResult:
        pid = ctx.bag.get('pid')
        tried: list[str] = []

        # **D 级最先试** —— 2026-08-31 实测它是唯一真的能打开对话框的一条，
        # A/B/C 三级当天全灭（A 返回 ErrorId=0 但零新窗口）。前提是输入桌面可用。
        main = ctx.refresh_main()
        if main is None:
            return StepResult.ask_human(
                '找不到主框架窗口。注意登录框的标题跟主窗口一样'
                '（都是 `通达信金融终端Vx.xx`），主框架的判据是 class 不为 #32770 —— '
                '这里找不到通常意味着**还没登录成功**，先跑 s2_login。')
        try:
            ok, why = tier_d_menu_point(ctx, main)
        except Exception as exc:                     # noqa: BLE001
            ok, why = False, f'{type(exc).__name__}: {exc}'
        ctx.log(f'  D 级（菜单坐标）：{why}')
        tried.append(f'D: {why}')
        if ok:
            d = _appeared(pid)
            if d is not None:
                ctx.bag['s5_tier'] = 'D'
                return StepResult.done(f'D 级打开：{d.text!r} {d.rect}',
                                       tried=tried)
            tried.append('D: 点完按钮后对话框没出现')

        # A 级不需要主窗口也不需要前台，代价最小，留作备用。
        ok, why = tier_a_tq()
        ctx.log(f'  A 级（TQ {CMD_DOWNLOAD}）：{why}')
        tried.append(f'A: {why}')
        if ok:
            d = _appeared(pid)
            if d is not None:
                ctx.bag['s5_tier'] = 'A'
                return StepResult.done(f'A 级打开：{d.text!r} {d.rect}',
                                       tried=tried)
            ctx.log('    命令发出去了但没看到对话框，退到 B 级')
            tried.append('A: 命令返回成功但对话框没出现')

        main = ctx.refresh_main() or main

        # **动界面之前先最大化。** 窗口被拖到屏幕边缘时菜单栏可能有一半在
        # 屏幕外；最大化之后位置稳定、也不会被别的程序盖住。
        # s8 收尾时照旧最小化（run_agent 的 finally 里无条件跑）。
        if not win32.is_zoomed(main.hwnd):
            win32.maximize(main.hwnd)
            time.sleep(0.6)
            main = ctx.refresh_main() or main
            ctx.log(f'  已最大化主窗口 → {main.rect}')


        ok, why = tier_b_command(ctx, main)
        ctx.log(f'  B 级（键盘精灵）：{why}')
        tried.append(f'B: {why}')
        d = _appeared(pid)
        if d is not None:
            ctx.bag['s5_tier'] = 'B'
            return StepResult.done(f'B 级打开：{d.text!r} {d.rect}', tried=tried)
        tried.append('B: 发完 .933 + Enter 后对话框没出现')

        try:
            ok, why = tier_c_menu(ctx, main)
        except locator.LocateError as e:
            ok, why = False, f'定位失败：{e}'
        ctx.log(f'  C 级（自绘菜单）：{why}')
        tried.append(f'C: {why}')
        if ok:
            d = _appeared(pid)
            if d is not None:
                ctx.bag['s5_tier'] = 'C'
                return StepResult.done(f'C 级打开：{d.text!r} {d.rect}',
                                       tried=tried)
            tried.append('C: 点完菜单后对话框没出现')

        # 四级全灭。**这不是"程序错了"，是"这台机器上这条路暂时走不通"**，
        # 所以 handoff 而不是 fail：出口码 2，页面和智能体据此知道要人看一眼。
        return StepResult.ask_human(
            '四级都没打开下载对话框：\n  ' + '\n  '.join(tried) + '\n'
            'D 级是唯一实测走通过的一条（2026-08-31），它失败时按这个顺序查：'
            '① 输入桌面可用吗（GetCursorPos 被拒时坐标点击发不出去）；'
            '② 主窗口最大化了吗（不满屏时 TQ策略 会被折叠进 ▼）；'
            f'③ 菜单坐标对吗 —— 改 config.setting[{MENU_SETTING_KEY!r}]，'
            f'默认 {MENU_POINT}、菜单项偏移 {MENU_ITEM_DY}px，'
            '看现场包里的 screen.png 量一下。\n'
            'A 级如果返回 ErrorId=0 却没窗口，就是实测过两次的那种情况：'
            'exec_to_tdx 收下了命令但界面没动，别把它当成功；'
            'B 级失败通常是主窗口停在加载失败的「系统页面」，'
            '字符被内嵌 webview 吃了 —— 先手动切到一个行情页面再重跑；'
            'C 级在本机必然失败 —— 菜单栏文字是自绘的，按名字定位不到。\n'
            '**日线数据本身不靠这个对话框**：'
            'python scripts\\ingest\\c12_tq_day.py --date <日期> '
            '直接走 TQ 接口把个股/指数/板块日线写进库。'
            '这个对话框真正不可替代的是**分钟线落盘**（vipdoc 的 .lc1/.lc5）。',
            tried=tried)


    def verify(self, ctx: RunContext) -> tuple[bool, str]:
        """
        判据：**「盘后数据下载」窗口在，而且 s6/s7 要用的 ctrl_id 都在里面。**

        两处刻意的选择：

        - 用 `dl_dialog.find`（只认「盘后数据下载」）而不是本模块的
          `find_dialog`（连「TQ数据设置」也认）。只开到设置窗就判成功的话，
          s6 会在里面找不到 1424/1，失败信息指向"控件不见了"，跟真实原因
          （对话框根本没开）差得很远。
        - 判 **ctrl_id** 而不是文字。五个 tab 的控件同时挂在控件树上
          （见 `dl_dialog` 说明第 3 条），所以 `day`/`start` 能不能枚举到
          跟"当前停在哪一页"无关 —— 页的事由 s6/s7 各自 `switch_tab` 负责，
          在这里查文字反而会因为停在别的页而误判失败。
        """
        pid = ctx.bag.get('pid')
        need = ('day', 'min1', 'min5', 'start', 'status', 'tab')

        def check():
            d = dl_dialog.find(pid)
            if d is None:
                st = dl_dialog.find_settings(pid)
                if st is not None:
                    return False, (f'只开到了上一层 {dl_dialog.SETTINGS_TITLE!r}'
                                   f' {st.rect}，'
                                   f'{dl_dialog.SETTINGS_BUTTON!r} 按钮那一下没生效')
                return False, f'没有标题为 {dl_dialog.TITLE!r} 的对话框'
            ctx.bag['dialog_hwnd'] = d.hwnd
            ctl = dl_dialog.controls(d.hwnd)
            missing = [f'{n}(id={dl_dialog.CTRL[n]})'
                       for n in need if dl_dialog.CTRL[n] not in ctl]
            if missing:
                return False, (f'对话框在（{d.rect}）但缺控件 {missing}；'
                               f'共枚举到 {len(ctl)} 个带 id 的控件 —— '
                               f'通达信版本换了的话 dl_dialog.CTRL 要重新对')
            page = dl_dialog.page_of(d.hwnd)
            return True, (f'对话框已打开 {d.rect}，{len(ctl)} 个控件，'
                          f'{need} 全在；当前页={page}')

        return self.wait_until(check)
