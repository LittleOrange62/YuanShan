"""
PowerShell 子进程壳子。

UIA（UI Automation）在 PowerShell 里是一行 `Add-Type -AssemblyName
UIAutomationClient` 的事，在 Python 里要么装 comtypes 搭 COM 桥，
要么自己写 IUIAutomation 的 vtable。既然只需要"dump 控件树 / 读一个
控件的值 / 按名字点一下"这三件事，走子进程更划算。

代价是每次调用有 200~600ms 的 PowerShell 启动开销。所以：
**热路径（看门狗轮询、就绪判定）一律走 ctypes，只有定位阶段才用这里。**
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

from app.config import BASE_DIR
from app.tdx_client.win32 import NO_WINDOW

# 版本库里收录的探测脚本，见 scripts/tdx_client/。
SCRIPT_DIR = BASE_DIR / 'scripts' / 'tdx_client'

_NO_WINDOW = NO_WINDOW


def _exe() -> str:
    return os.environ.get('FT_POWERSHELL') or 'powershell.exe'


def run(code: str, *, timeout: int = 60) -> tuple[int, str, str]:
    """
    执行一段内联 PowerShell。

    `-NoProfile` 不是可选项：用户 profile 里的任何一行输出都会混进 stdout，
    JSON 解析就跟着崩。`-NonInteractive` 保证脚本卡在提示上时直接失败，
    而不是无声等到超时。
    """
    cmd = [_exe(), '-NoProfile', '-NonInteractive',
           '-ExecutionPolicy', 'Bypass', '-Command', code]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True,
                           encoding='utf-8', errors='replace',
                           timeout=timeout, creationflags=_NO_WINDOW)
    except subprocess.TimeoutExpired:
        return -1, '', f'PowerShell 超时（{timeout}s）'
    except OSError as e:
        return -1, '', f'PowerShell 起不来：{e}'
    return r.returncode, r.stdout, r.stderr


def run_script(name: str, args: list[str] | None = None, *,
               timeout: int = 60) -> tuple[int, str, str]:
    """
    跑 `scripts/tdx_client/` 里收录的 .ps1。

    参数用列表传、由 subprocess 负责转义，**不拼字符串** ——
    口令、窗口标题里出现引号或分号时字符串拼接就是命令注入。
    """
    p = SCRIPT_DIR / name
    if not p.is_file():
        return -1, '', f'脚本不存在：{p}'
    cmd = [_exe(), '-NoProfile', '-NonInteractive',
           '-ExecutionPolicy', 'Bypass', '-File', str(p), *(args or [])]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True,
                           encoding='utf-8', errors='replace',
                           timeout=timeout, creationflags=_NO_WINDOW)
    except subprocess.TimeoutExpired:
        return -1, '', f'{name} 超时（{timeout}s）'
    except OSError as e:
        return -1, '', f'{name} 起不来：{e}'
    return r.returncode, r.stdout, r.stderr


def run_json(code: str, *, timeout: int = 60) -> tuple[list | dict | None, str]:
    """
    执行并把 stdout 当 JSON 解析。返回 `(data, err)`，成功时 err 为空串。

    PowerShell 的 `ConvertTo-Json` 在只有一个元素时会退化成对象而不是
    单元素数组，调用方拿到 dict 别当成失败 —— 这里原样返回，由调用方规整。
    """
    rc, out, err = run(code, timeout=timeout)
    if rc != 0:
        return None, (err or out).strip() or f'退出码 {rc}'
    out = out.strip()
    if not out:
        return None, '无输出'
    # BOM 和 Windows 换行都会让 json 挑食。
    out = out.lstrip('\ufeff')
    try:
        return json.loads(out), ''
    except json.JSONDecodeError as e:
        return None, f'JSON 解析失败：{e}；原始输出前 200 字：{out[:200]!r}'


def dump_uia(title_like: str, *, max_depth: int = 8, max_nodes: int = 4000,
             timeout: int = 90) -> tuple[list[dict], str]:
    """
    dump 一个顶层窗口的 UIA 控件树，返回扁平的节点列表。

    每个节点：`{name, cls, type, id, x, y, w, h, depth, enabled, offscreen}`。

    UIA 能看到 Win32 枚举看不到的东西 —— 通达信的顶部菜单
    `TQ策略`、`TQ数据设置` 属于自绘控件，`EnumChildWindows` 拿不到它们的
    文字，UIA 的 `Name` 属性能拿到。定位这两个入口只能靠这条路。

    **筛顶层窗口时用 `TrueCondition`，不能用 `IsWindowPatternAvailable`。**
    第一版用了后者，结果通达信的窗口一个节点都 dump 不出来（返回 `[]`），
    而同时浏览器窗口能正常 dump —— 差别就在通达信的窗口不暴露
    WindowPattern，被那个条件筛掉了，`$target` 是 null。
    报出来的现象是"UIA 里找不到 TQ策略"，跟真实原因（窗口就没找到）无关。

    `max_nodes` 是防呆：通达信主框架的 UIA 子树能有上万个节点，
    不设上限的话这个函数会跑几分钟，看起来就是"卡死"。
    """
    q = title_like.replace("'", "''")
    code = f"""
$ErrorActionPreference='Stop'
Add-Type -AssemblyName UIAutomationClient,UIAutomationTypes
$root=[System.Windows.Automation.AutomationElement]::RootElement
$wins=$root.FindAll([System.Windows.Automation.TreeScope]::Children,
  [System.Windows.Automation.Condition]::TrueCondition)
$target=$null
foreach($w in $wins){{
  try{{ if($w.Current.Name -like '*{q}*'){{ $target=$w; break }} }}catch{{}}
}}
if(-not $target){{ Write-Output '[]'; exit 0 }}
$out=New-Object System.Collections.ArrayList
$walker=[System.Windows.Automation.TreeWalker]::ControlViewWalker
function Walk($el,$d){{
  if($d -gt {max_depth} -or $out.Count -ge {max_nodes}){{ return }}
  $c=$walker.GetFirstChild($el)
  while($c -ne $null -and $out.Count -lt {max_nodes}){{
    try{{
      $i=$c.Current
      $r=$i.BoundingRectangle
      [void]$out.Add([pscustomobject]@{{
        name=$i.Name; cls=$i.ClassName; type=$i.ControlType.ProgrammaticName;
        id=$i.AutomationId; x=[int]$r.X; y=[int]$r.Y;
        w=[int]$r.Width; h=[int]$r.Height; depth=$d;
        enabled=$i.IsEnabled; offscreen=$i.IsOffscreen }})
      Walk $c ($d+1)
    }}catch{{}}
    try{{ $c=$walker.GetNextSibling($c) }}catch{{ break }}
  }}
}}
Walk $target 0
if($out.Count -eq 0){{ Write-Output '[]'; exit 0 }}
$out | ConvertTo-Json -Depth 4 -Compress
"""
    data, err = run_json(code, timeout=timeout)
    if err:
        return [], err
    if isinstance(data, dict):
        return [data], ''
    return list(data or []), ''


def invoke_uia(title_like: str, name: str, *, control_type: str | None = None,
               timeout: int = 60) -> tuple[bool, str]:
    """
    按 Name 找控件并 Invoke（相当于点一下）。找不到 InvokePattern 时
    退回 `LegacyIAccessible.DoDefaultAction()` —— 通达信的菜单项属于后者。
    """
    q = title_like.replace("'", "''")
    n = name.replace("'", "''")
    type_filter = ''
    if control_type:
        t = control_type.replace("'", "''")
        type_filter = f"if($i.ControlType.ProgrammaticName -notlike '*{t}*'){{ continue }}"
    code = f"""
$ErrorActionPreference='Stop'
Add-Type -AssemblyName UIAutomationClient,UIAutomationTypes
$root=[System.Windows.Automation.AutomationElement]::RootElement
$wins=$root.FindAll([System.Windows.Automation.TreeScope]::Children,
  [System.Windows.Automation.Condition]::TrueCondition)
$target=$null
foreach($w in $wins){{ if($w.Current.Name -like '*{q}*'){{ $target=$w; break }} }}
if(-not $target){{ Write-Error 'window-not-found'; exit 2 }}
$cond=New-Object System.Windows.Automation.PropertyCondition(
  [System.Windows.Automation.AutomationElement]::NameProperty,'{n}')
$els=$target.FindAll([System.Windows.Automation.TreeScope]::Descendants,$cond)
foreach($e in $els){{
  $i=$e.Current
  {type_filter}
  if($i.IsOffscreen -or -not $i.IsEnabled){{ continue }}
  try{{
    $p=$e.GetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern)
    $p.Invoke(); Write-Output 'invoke'; exit 0
  }}catch{{}}
  try{{
    $p=$e.GetCurrentPattern(
      [System.Windows.Automation.LegacyIAccessiblePattern]::Pattern)
    $p.DoDefaultAction(); Write-Output 'legacy'; exit 0
  }}catch{{}}
}}
Write-Error 'element-not-actionable'; exit 3
"""
    rc, out, err = run(code, timeout=timeout)
    if rc == 0:
        return True, out.strip()
    return False, (err or out).strip() or f'退出码 {rc}'


def read_uia_value(title_like: str, automation_id: str | None = None,
                   *, index: int = 0, timeout: int = 60) -> tuple[str | None, str]:
    """
    读编辑框当前内容。**用来核对账号有没有写对** ——
    通达信会记住上次的账号，直接键盘输入会追加而不是替换，
    不回读就发现不了 `1896996969618969969696` 这种。
    """
    q = title_like.replace("'", "''")
    id_filter = ''
    if automation_id:
        a = automation_id.replace("'", "''")
        id_filter = f"if($i.AutomationId -ne '{a}'){{ continue }}"
    code = f"""
$ErrorActionPreference='Stop'
Add-Type -AssemblyName UIAutomationClient,UIAutomationTypes
$root=[System.Windows.Automation.AutomationElement]::RootElement
$wins=$root.FindAll([System.Windows.Automation.TreeScope]::Children,
  [System.Windows.Automation.Condition]::TrueCondition)
$target=$null
foreach($w in $wins){{ if($w.Current.Name -like '*{q}*'){{ $target=$w; break }} }}
if(-not $target){{ Write-Error 'window-not-found'; exit 2 }}
$cond=New-Object System.Windows.Automation.PropertyCondition(
  [System.Windows.Automation.AutomationElement]::ControlTypeProperty,
  [System.Windows.Automation.ControlType]::Edit)
$els=$target.FindAll([System.Windows.Automation.TreeScope]::Descendants,$cond)
$hits=New-Object System.Collections.ArrayList
foreach($e in $els){{
  $i=$e.Current
  {id_filter}
  try{{
    $p=$e.GetCurrentPattern([System.Windows.Automation.ValuePattern]::Pattern)
    [void]$hits.Add($p.Current.Value)
  }}catch{{ [void]$hits.Add($i.Name) }}
}}
if($hits.Count -le {index}){{ Write-Error 'index-out-of-range'; exit 3 }}
Write-Output $hits[{index}]
"""
    rc, out, err = run(code, timeout=timeout)
    if rc != 0:
        return None, (err or out).strip() or f'退出码 {rc}'
    return out.strip(), ''


def dump_to_file(title_like: str, path: Path, *, max_depth: int = 8) -> tuple[int, str]:
    """把 dump 结果落盘，方便人肉看。返回 (节点数, 错误)。"""
    nodes, err = dump_uia(title_like, max_depth=max_depth)
    if err:
        return 0, err
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(nodes, ensure_ascii=False, indent=2),
                    encoding='utf-8')
    return len(nodes), ''
