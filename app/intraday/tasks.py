# -*- coding: utf-8 -*-
"""盘中播报四轮（事件差／竞价／热度／午间）的「什么时候跑、发到哪、谁在跑」这一面。

分工要说死：判据与正文不归这里（住 `ingest/jobs/*.py` 与 `app/intraday/templates.json`），
这个模块只管三件事 —— **配置（`jobs/config.json` 里的 `intraday_broadcast`）→ 计划任务定义
→ 注册与对账**。改时点这件事落到「重新注册 Windows 计划任务」，不是落到某个循环的 sleep。

为什么不并进 `notifyconfig` 那套定时计划：那套的执行体在 8910 进程内
（`server.py` 的推送循环每轮重读配置），而盘中这四条是「到点起一个短进程、跑完就退」——
10-23 那次常驻 loop 在 09:29 建完基线后静默死亡、盘中 8 轮全瞎，之后看门狗就交给了调度器
（原委记在 `ingest/jobs/zt_watch.py` 开头）。把节奏搬回进程内等于把那个坑再挖一遍。

`enabled=false` 的语义是**注销任务**，不是"留着任务每天空跑一次然后不出声"。
空跑的代价：屏上写着已停用、任务计划程序里还躺着一个 Ready 的任务，第二天没人分得清
是"关了"还是"坏了"。注销后判据代码仍在仓库，重新打开就按当前配置再注册回去。
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import importlib.util
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent.parent
#: 重注册用的 XML 落在这里：既是给 PowerShell 的输入，也是「上一次到底注册了什么」的留痕
XML_DIR = ROOT / '.local' / 'tasks'

SETTING_NAME = "intraday_broadcast"
CHANNELS_DEFAULT = "feishu-main"
#: 四条的顺序就是界面上的顺序
SPEC: dict[str, dict] = {
    "watcher": {
        "label": "涨跌停池事件差（盘中每 5 分钟一针）",
        "task_name": "YuanShan-IntradayZtWatch",
        "module": "ingest.jobs.zt_watch",
        "base_args": "--once --send", "shape": "repeat",
        "limit": "PT10M", "retry_interval": "PT1M", "retry_count": 3,
    },
    "auction": {
        "label": "竞价双榜＋一字＋概念计数",
        "task_name": "YuanShan-AuctionReport",
        "module": "ingest.jobs.auction_report",
        "base_args": "--send", "shape": "times",
        "limit": "PT15M", "retry_interval": "PT2M", "retry_count": 2,
    },
    "heat": {
        "label": "概念热度计数（涨跌停池 ≥3 只）",
        "task_name": "YuanShan-HeatReport",
        "module": "ingest.jobs.heat_report",
        "base_args": "--send", "shape": "times",
        "limit": "PT15M", "retry_interval": "PT2M", "retry_count": 2,
    },
    "noon": {
        "label": "午间复盘快照",
        "task_name": "YuanShan-NoonReview",
        "module": "ingest.jobs.noon_review",
        "base_args": "--send", "shape": "times",
        "limit": "PT15M", "retry_interval": "PT2M", "retry_count": 2,
    },
}

#: 默认值只住这一处；作业侧、界面侧、CLI 侧都从这里取（两处各写一份就会出现"页面改得动、代码不读"）
DEFAULTS: dict[str, Any] = {
    "enabled": True,
    "channels": CHANNELS_DEFAULT,
    "watcher_enabled": True, "watcher_start": "09:15",
    "watcher_interval_min": 5, "watcher_duration_h": 6,
    "auction_enabled": True, "auction_times": "09:25:30",
    "heat_enabled": True, "heat_times": "10:05,10:55,14:18",
    "noon_enabled": True, "noon_times": "12:10",
    "note": "",
}

_TIME_RE = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)(?::([0-5]\d))?$")


def _hhmmss(value: object) -> bool:
    """`HH:MM` 或 `HH:MM:SS`（竞价那一档要精确到秒，其余到分够用）。"""
    return bool(_TIME_RE.match(str(value or "").strip()))


def parse_times(raw: object) -> list[str]:
    return [c.strip() for c in str(raw or "").split(",") if c.strip()]


def schedule_of(key: str, cfg: dict) -> dict:
    spec = SPEC[key]
    if spec["shape"] == "repeat":
        return {"shape": "repeat", "start": str(cfg.get(f'{key}_start') or ''),
                "interval_min": int(cfg.get(f'{key}_interval_min') or 0),
                "duration_h": int(cfg.get(f'{key}_duration_h') or 0)}
    return {"shape": "times", "times": parse_times(cfg.get(f'{key}_times'))}


def schedule_text(key: str, cfg: dict) -> str:
    """界面上那一句节奏（也是任务描述里的同一句，别让两处各写各的措辞）。"""
    sch = schedule_of(key, cfg)
    if sch["shape"] == "repeat":
        return (f'每天 {sch["start"]} 起，每 {sch["interval_min"]} 分钟一针，'
                f'持续 {sch["duration_h"]} 小时')
    return '每天 ' + '、'.join(sch["times"])


def channel_arg(cfg: dict | None = None) -> str:
    """作业侧唯一要的那一格：逗号分隔的渠道串。

    空串**不回退默认**：`channels: ""` 是被人写坏的配置，悄悄换成 `feishu-main`
    会把"我改坏了"渲染成一份看起来正常的读数（`jobs.settings` 那套"非法值出声"同理）。
    """
    if cfg is None:
        cfg, _warns = load_cfg()
    raw = str(cfg.get("channels") or "").strip()
    if not raw:
        raise RuntimeError(f'盘中播报的渠道是空的（读到 {cfg.get("channels")!r}）'
                           f'—— 按失败处理，不静默换成默认渠道')
    return raw


def channels_display(cfg: dict) -> str:
    """界面与任务描述里那一格：空就照实说是空，不拿默认值糊。"""
    raw = str(cfg.get("channels") or "").strip()
    return raw or '（空 —— 四条都发不出去，先去消息推送配好渠道）'


def send_channels(channel: str | None = None) -> list[str]:
    """把渠道串切成列表交给 `notify.send_to`（它本来就收列表）。

    空是**判不了**，不是"没有渠道"：这里抛，让这一针记成失败 —— 兜成空列表发出一条
    "已发送"，那就是把"没发"说成"发过了"。
    """
    raw = channel if channel else channel_arg()
    keys = [c.strip() for c in str(raw or "").split(",") if c.strip()]
    if not keys:
        raise RuntimeError(f'渠道为空（读到 {raw!r}）—— 按失败处理，不静默跳过发送')
    return keys


def load_cfg() -> tuple[dict, list[str]]:
    """这一轮生效的配置。非法值由 `jobs.settings()` 出声并回退默认，绝不静默采用。"""
    from duanxian import jobs

    return jobs.settings(SETTING_NAME, DEFAULTS)


# --------------------------------------------------------------------------- 写面校验

def validate_patch(kv: dict, cfg: dict) -> str | None:
    """**一个键非法就整笔不写**（同 `duanxian.jobs.set_setting` 的规矩）。

    渠道那一格要判"是不是在册的渠道"：拼错的键不会报错，只会让 `send_to` 回一句
    "没有可用渠道，未发送"，那就是"存下了、永远发不出去"。
    """
    merged = {**cfg, **{k: v for k, v in kv.items() if v not in (None, "")}}
    for key, value in kv.items():
        if value is None or value == "":
            continue
        if key not in DEFAULTS:
            # 先认在册再判形状：否则 `heat_start` 这种拼错的名字会被 `*_start` 那档收下去，
            # 存进配置却没人读它 —— 正是本项目明令禁止的"写着有其实没接"。
            why = f"{key} 不在册：可用的是 " + "、".join(sorted(DEFAULTS))
        elif key == "channels":
            why = _channels_exist(value)
        elif key.endswith("_times"):
            why = _times_ok(key, value)
        elif key == "watcher_start":
            why = None if _hhmmss(value) else f"{key} 需要 HH:MM，收到 {value!r}"
        elif key == "watcher_interval_min":
            why = _int_range(value, 1, 30, key, "分钟（再密就只是在刷同一个池子）")
        elif key == "watcher_duration_h":
            why = _int_range(value, 1, 12, key, "小时（覆盖 09:15-15:15 已经够了）")
        elif key.endswith("_enabled") or key == "enabled":
            why = None if isinstance(value, bool) else f"{key} 需要 true/false，收到 {value!r}"
        elif key == "note":
            why = None
        else:
            why = f"{key} 不在册：可用的是 " + "、".join(sorted(DEFAULTS))
        if why:
            return f"{why}；这一笔整条没写入，仍按原值运行"
    for key, spec in SPEC.items():
        if spec["shape"] == "times" and merged.get(f'{key}_enabled') \
                and not parse_times(merged.get(f'{key}_times')):
            return (f"{spec['label']} 开着但一个时点都没有（`{key}_times` 是空的）；"
                    f"要么填一个时点，要么把这一路的开关关掉。这一笔整条没写入")
    return None


def _times_ok(key: str, value: object) -> str | None:
    cells = parse_times(value)
    if not cells:
        return f"{key} 至少要留一个时点（想停这一路请把开关关掉，别留空的）"
    if len(cells) > 12:
        return f"{key} 最多 12 个时点，收到 {len(cells)} 个"
    bad = [c for c in cells if not _hhmmss(c)]
    return f"{key} 里这些不是 HH:MM 或 HH:MM:SS：{'、'.join(bad)}" if bad else None


def _int_range(value: object, lo: int, hi: int, key: str, unit: str) -> str | None:
    try:
        number = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return f"{key} 需要 {lo}~{hi} 的整数（{unit}），收到 {value!r}"
    return None if lo <= number <= hi else f"{key} 需要在 {lo}~{hi} 之间（{unit}），收到 {number}"


def _channels_exist(raw: object) -> str | None:
    keys = [c.strip() for c in str(raw or "").split(",") if c.strip()]
    if not keys:
        return "channels 至少要留一条渠道（一条都不想发就关这一路，不是把渠道清空）"
    try:
        import notifyconfig as NC

        known = {c.get("key") for c in NC.load().get("channels") or [] if c.get("key")}
    except Exception as exc:  # noqa: BLE001  读不出来要说"判断不了"，不能当成"都不存在"
        return f"渠道名单读不出来（{type(exc).__name__}），这一笔先不写；能读到之后再改"
    unknown = [k for k in keys if k not in known]
    if unknown:
        return (f"channels 里 {'、'.join(unknown)} 不是在册渠道；在册的是 "
                f"{'、'.join(sorted(known)) or '（一条都还没配）'}（消息推送面板加好渠道再回来填）")
    return None


# --------------------------------------------------------------------------- 任务定义

def module_exists(module: str | None) -> bool:
    if not module:
        return False
    try:
        return importlib.util.find_spec(module) is not None
    except (ImportError, ValueError):
        return False


def task_args(key: str, cfg: dict) -> str:
    """任务命令行：**不带 `--channel`**。

    渠道由作业在每一轮现读配置（`send_channels(None)`），与作业配置那套
    「每轮重读、改完即生效」的规矩一致；写进任务参数就会出现
    "配置说 A、任务参数说 B"那种两边各读各的假生效。
    `--channel` 仍留着给手动补发用（显式给了就按给的发）。
    """
    spec = SPEC[key]
    return f"-X utf8 -m {spec['module']} {spec['base_args']}"


def _boundary(hhmm: str, day: dt.date) -> str:
    parts = (str(hhmm).strip().split(":") + ["0", "0"])[:3]
    return f'{day.isoformat()}T{int(parts[0]):02d}:{int(parts[1]):02d}:{int(parts[2]):02d}'


def _ps_quote(value: str) -> str:
    return str(value).replace("'", "''")


def _user() -> str:
    return os.environ.get("USERNAME") or "Administrator"


def build_xml(key: str, cfg: dict, *, day: dt.date | None = None) -> str:
    """任务 XML。骨架沿用 `scripts/ops/setup_intraday_*.ps1` 那份定版：
    `StartWhenAvailable`（错过就补）、`MultipleInstancesPolicy=IgnoreNew`（互斥）、
    `ExecutionTimeLimit`（卡死由调度器收）、`RestartOnFailure`（重试阶梯）。
    """
    spec = SPEC[key]
    sch = schedule_of(key, cfg)
    day = day or dt.date.today()
    if sch["shape"] == "repeat":
        rep = (f'<Repetition><Interval>PT{sch["interval_min"]}M</Interval>'
               f'<Duration>PT{sch["duration_h"]}H</Duration>'
               f'<StopAtDurationEnd>true</StopAtDurationEnd></Repetition>')
        triggers = _trigger(_boundary(sch["start"], day), extra=rep)
    else:
        if not sch["times"]:
            # 读面（`jobs.settings`）只判类型不判形状，脏值（`",,"`）能进到配置里来。
            # 到这一步抛，比插出一份 `<Triggers></Triggers>` 让 PowerShell 回一句天书好。
            raise RuntimeError(f'{SPEC[key]["label"]} 开着但一个时点都没有（`{key}_times` 是空的）')
        triggers = "".join(_trigger(_boundary(t, day)) for t in sch["times"])
    desc = (f'YuanShan: {schedule_text(key, cfg)} · 发到 {channels_display(cfg)} · '
            f'{spec["label"]}；非交易日脚本内静默退；由 app/intraday/tasks.py 注册')
    return (f'<?xml version="1.0" encoding="UTF-16"?>\n'
            f'<Task version="1.3" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">\n'
            f'  <RegistrationInfo><Description>{desc}</Description>'
            f'<URI>\\{spec["task_name"]}</URI></RegistrationInfo>\n'
            f'  <Principals><Principal id="Author"><UserId>{_user()}</UserId>'
            f'<LogonType>S4U</LogonType><RunLevel>LeastPrivilege</RunLevel></Principal></Principals>\n'
            f'  <Settings>\n'
            f'    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>\n'
            f'    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>'
            f'<StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>\n'
            f'    <AllowHardTerminate>true</AllowHardTerminate>'
            f'<StartWhenAvailable>true</StartWhenAvailable>\n'
            f'    <ExecutionTimeLimit>{spec["limit"]}</ExecutionTimeLimit>\n'
            f'    <RestartOnFailure><Interval>{spec["retry_interval"]}</Interval>'
            f'<Count>{spec["retry_count"]}</Count></RestartOnFailure>\n'
            f'    <IdleSettings><StopOnIdleEnd>true</StopOnIdleEnd>'
            f'<RestartOnIdle>false</RestartOnIdle></IdleSettings>\n'
            f'    <UseUnifiedSchedulingEngine>true</UseUnifiedSchedulingEngine>\n'
            f'  </Settings>\n'
            f'  <Triggers>\n{triggers}  </Triggers>\n'
            f'  <Actions Context="Author"><Exec><Command>{ROOT / ".venv" / "Scripts" / "python.exe"}</Command>'
            f'<Arguments>{task_args(key, cfg)}</Arguments>'
            f'<WorkingDirectory>{ROOT}</WorkingDirectory></Exec></Actions>\n'
            f'</Task>\n')


def _trigger(boundary: str, *, extra: str = "") -> str:
    return ('    <CalendarTrigger>\n'
            f'      <StartBoundary>{boundary}</StartBoundary>\n'
            '      <Enabled>true</Enabled>\n'
            + (f'      {extra}\n' if extra else '') +
            '      <ScheduleByDay><DaysInterval>1</DaysInterval></ScheduleByDay>\n'
            '    </CalendarTrigger>\n')


def want_boundaries(key: str, cfg: dict, *, day: dt.date | None = None) -> list[str]:
    day = day or dt.date.today()
    sch = schedule_of(key, cfg)
    if sch["shape"] == "repeat":
        return [_boundary(sch["start"], day)]
    return [_boundary(t, day) for t in sch["times"]]


#: `StartBoundary` 的日期那半是**注册那天**（每日触发器靠它推下一场），对账只看钟点。
#: 不这么切的话四条会永远显示"时点不一致"—— 一条一直红的对账等于没有对账。
_CLOCK_RE = re.compile(r'T(\d{2}:\d{2}:\d{2})')


def _clock(boundaries: list[str]) -> list[str]:
    out = []
    for raw in boundaries or []:
        found = _CLOCK_RE.search(str(raw))
        if found:
            out.append(found.group(1))
    return out


# --------------------------------------------------------------------------- 起进程的唯一出口

def run_powershell(script: str, *, timeout: int = 120) -> tuple[int, str, str]:
    """唯一起 PowerShell 动计划任务的出口。

    ⚠️ pytest 进程里**硬挡真注册**（与 `_spawn_stage` / `_post_json` 同源的纪律）：
    跑一次测试就往机器上长出四个每日任务，那种事撤不回来。测试要验注册逻辑就
    monkeypatch 这一层，或走 `dry_run`。
    """
    if os.environ.get("PYTEST_CURRENT_TEST"):
        raise RuntimeError('pytest 进程里不许真动计划任务（打这一层，或走 dry_run）')
    if sys.platform != 'win32':
        raise RuntimeError('计划任务只有 Windows 有，这一层在本机以外不可用')
    p = subprocess.run(["powershell", "-NoProfile", "-Command", script],
                       capture_output=True, timeout=timeout)
    return (p.returncode, p.stdout.decode('utf-8', 'replace'),
            p.stderr.decode('utf-8', 'replace'))


_REG_TTL_S = 15
_REG_CACHE: dict = {'ts': 0.0, 'data': None}


def registered(*, force: bool = False) -> dict[str, dict]:
    """现读机器上真注册着什么（状态/上次结果/下次/参数/触发时点）。

    用 powershell 而不是 `schtasks`：后者在本机输出 UTF-16，会误判成"没有相关任务"
    （`scripts/manage.py::_tasks_csv` 踩过，这里保持同一口径）。读不出来要出声，
    不许兜成空表 —— 空表在界面上就是"四条都没注册"。

    起一次 PowerShell 要 4~5 秒，而这一格是面板每开一次都要读的东西，所以留 15 秒缓存；
    改完配置走 POST 那条路会带 `force=True`，绝不让人对着"刚保存却还显示旧状态"发呆。
    """
    now = time.monotonic()
    if not force and _REG_CACHE['data'] is not None and now - _REG_CACHE['ts'] < _REG_TTL_S:
        return _REG_CACHE['data']
    names = [spec['task_name'] for spec in SPEC.values()]
    if sys.platform != 'win32':
        return {'_error': {'reason': '本机不是 Windows，读不到计划任务'}}
    flist = ",".join(f"'{_ps_quote(n)}'" for n in names)
    ps = ("Get-ScheduledTask | Where-Object { $_.TaskName -in @(" + flist + ") } | "
          "ForEach-Object { $t = $_; $i = $_ | Get-ScheduledTaskInfo; "
          "$a = $_.Actions | Select-Object -First 1; "
          "[pscustomobject]@{ Name = $t.TaskName; State = $t.State; Last = $i.LastRunTime; "
          "Next = $i.NextRunTime; Result = $i.LastTaskResult; Args = ('' + $a.Arguments); "
          "Trig = (($_.Triggers | ForEach-Object { '' + $_.StartBoundary }) -join ' ') } } "
          "| ConvertTo-Csv -NoTypeInformation")
    try:
        code, out, errtxt = run_powershell(ps)
    except (RuntimeError, OSError, subprocess.SubprocessError) as exc:
        return {'_error': {'reason': f'查不了（{type(exc).__name__}: {exc}）'[:200]}}
    if code != 0 and not out.strip():
        return {'_error': {'reason': f'powershell 非零退出（{code}）：{errtxt[:160]}'}}
    rows: dict[str, dict] = {}
    lines = [ln for ln in out.replace('\r\n', '\n').replace('\r', '\n').split('\n') if ln.strip()]
    for rec in csv.DictReader(lines):
        name = (rec.get('Name') or '').strip()
        if not name or name == 'Name':
            continue
        rows[name] = {'state': (rec.get('State') or '').strip(),
                      'last': (rec.get('Last') or '').strip() or None,
                      'next': (rec.get('Next') or '').strip() or None,
                      'result': (rec.get('Result') or '').strip() or None,
                      'args': (rec.get('Args') or '').strip(),
                      'triggers': [t for t in (rec.get('Trig') or '').split() if t]}
    _REG_CACHE['ts'], _REG_CACHE['data'] = now, rows
    return rows


# --------------------------------------------------------------------------- 对账与落地

def plan(*, ledger: dict | None = None, force_reg: bool = False) -> dict:
    """配置 × 机器实况的对照。界面那一格就是它。"""
    cfg, warns = load_cfg()
    reg = registered(force=force_reg)
    err = reg.get('_error', {}).get('reason') if '_error' in reg else None
    channels_empty = not str(cfg.get('channels') or '').strip()
    rows = []
    for key, spec in SPEC.items():
        want_on = bool(cfg.get('enabled')) and bool(cfg.get(f'{key}_enabled'))
        exists = module_exists(spec['module'])
        got = None if err else reg.get(spec['task_name'])
        drift = []
        if want_on and channels_empty:
            drift.append('渠道那一格是空的 —— 到点会跑，但一条都发不出去')
        if want_on and not exists:
            drift.append(f'执行体还没搬进仓库（{spec["module"]}）—— 这一路现在由外部任务在发')
        elif got is None and not err and want_on:
            drift.append('配置开着，机器上没有这个任务')
        elif got is not None:
            if got['args'] != task_args(key, cfg):
                drift.append('任务里的命令行参数与当前配置不一致')
            if not schedule_of(key, cfg).get('times') and SPEC[key]['shape'] == 'times':
                drift.append('开着但一个时点都没有（注册会拒，先填时点或关掉这一路）')
            elif sorted(_clock(got['triggers'])) != sorted(_clock(want_boundaries(key, cfg))):
                drift.append(f'触发时点与当前配置不一致（任务里是 '
                             f'{"、".join(_clock(got["triggers"])) or "读不到"}）')
            if got['state'] == 'Disabled' and want_on:
                drift.append('任务处于 Disabled（不是这里关的，或有人在任务计划程序里手动停过）')
        rows.append({'key': key, 'label': spec['label'], 'task_name': spec['task_name'],
                     'module': spec['module'], 'available': exists,
                     'on': want_on, 'editable': exists and not err,
                     'schedule': schedule_text(key, cfg),
                     'channels': channels_display(cfg),
                     'registered': got, 'drift': drift,
                     'ledger': (ledger or {}).get(key)})
    return {'generated_at': dt.datetime.now().astimezone().isoformat(timespec='seconds'),
            'config': {k: cfg.get(k) for k in DEFAULTS},
            'defaults': DEFAULTS, 'warnings': warns, 'tasks': rows,
            'readable': err is None, 'reason': err,
            'global_enabled': bool(cfg.get('enabled')),
            'changed': _changed(cfg)}


def _changed(cfg: dict) -> dict:
    from duanxian import jobs

    return jobs.change_note(SETTING_NAME)


def apply_patch(kv: dict, *, via: str = "", register: bool = True,
                dry_run: bool = False, keys: list[str] | None = None) -> tuple[str | None, dict]:
    """校验 → 写配置 → （可选）重注册。返回 `(错误或 None, 对照表)`。"""
    cfg, _warns = load_cfg()
    why = validate_patch(kv, cfg)
    if why:
        return why, plan()
    from duanxian import jobs

    jobs.write_setting(SETTING_NAME, via=via or '未知调用方', **kv)
    result = plan(force_reg=True)
    if register:
        result['apply'] = register_all(result['config'], via=via, dry_run=dry_run, keys=keys)
    return None, result


def register_all(cfg: dict, *, via: str = "", dry_run: bool = False,
                 keys: list[str] | None = None) -> list[dict]:
    """按配置重注册。`keys` 只用来划范围（那两个 .ps1 各管自己那几条的名字要说得出口），
    判据与 XML 一份都不分叉。"""
    return [register_one(key, cfg,
                         want_on=bool(cfg.get('enabled')) and bool(cfg.get(f'{key}_enabled')),
                         via=via, dry_run=dry_run)
            for key in (keys or list(SPEC))]


def _invalidate() -> None:
    """动过机器就丢掉短缓存：下一格读数必须重新查，不然界面会连着显示"上一个任务的状态"。"""
    _REG_CACHE['ts'], _REG_CACHE['data'] = 0.0, None


def register_one(key: str, cfg: dict, *, want_on: bool, via: str = "",
                 dry_run: bool = False) -> dict:
    spec = SPEC[key]
    name = spec['task_name']
    if not dry_run:
        _invalidate()
    if not want_on:
        return _remove(key, name, reason=f'这一路已关（{via or "命令行"}）', dry_run=dry_run)
    if not module_exists(spec['module']):
        # 不注册一个到点必然空跑的任务，也不发注定被拒的请求
        return {'key': key, 'task_name': name, 'action': 'refused', 'ok': False,
                'detail': f'执行体 {spec["module"]} 不在仓库里，没有注册（先把它搬进来再开这一路）'}
    XML_DIR.mkdir(parents=True, exist_ok=True)
    path = XML_DIR / f'{name}.xml'
    try:
        xml = build_xml(key, cfg)
    except (RuntimeError, ValueError) as exc:
        # 手改坏的配置不该把一个 POST 变成 500：这一路记成失败，其余三路照注册
        return {'key': key, 'task_name': name, 'action': 'refused', 'ok': False,
                'detail': f'配置读不出可用节奏：{exc}'[:220]}
    path.write_text(xml, encoding='utf-16')
    if dry_run:
        return {'key': key, 'task_name': name, 'action': 'dry-run', 'ok': True,
                'detail': f'XML 已生成 {path.name}，没有动任务计划程序'}
    ps = ("$ErrorActionPreference='Stop'; "
          f"$x=[System.IO.File]::ReadAllText('{_ps_quote(str(path))}'); "
          f"Register-ScheduledTask -TaskName '{_ps_quote(name)}' -Xml $x -Force | Out-Null; "
          f"$t=Get-ScheduledTask -TaskName '{_ps_quote(name)}'; $i=$t|Get-ScheduledTaskInfo; "
          "'{0}|{1}' -f $t.State, $i.NextRunTime")
    try:
        code, out, errtxt = run_powershell(ps)
    except (RuntimeError, OSError, subprocess.SubprocessError) as exc:
        return {'key': key, 'task_name': name, 'action': 'blocked', 'ok': False,
                'detail': f'{type(exc).__name__}: {exc}'[:200]}
    if code != 0:
        return {'key': key, 'task_name': name, 'action': 'registered', 'ok': False,
                'detail': f'注册失败：{errtxt[:220]}'}
    return {'key': key, 'task_name': name, 'action': 'registered', 'ok': True,
            'detail': out.strip()[:200], 'xml': str(path)}


def _remove(key: str, name: str, *, reason: str, dry_run: bool) -> dict:
    """注销这一路的任务。失败要说"还在"，不许报成"已注销"。"""
    if dry_run:
        return {'key': key, 'task_name': name, 'action': 'dry-run-remove', 'ok': True,
                'detail': f'按配置该注销（{reason}），dry-run 没动'}
    ps = ("$ErrorActionPreference='SilentlyContinue'; "
          f"Unregister-ScheduledTask -TaskName '{_ps_quote(name)}' -Confirm:$false; "
          # 判定串只用 ASCII：PS 5.1 管道里中文回体到 Python 是乱码（实测 `已注销` 变成
          # `ע`），拿它当证据会把"注销成功"读成"注销没成功"。
          f"if (Get-ScheduledTask -TaskName '{_ps_quote(name)}') {{'STILL-THERE'}} else {{'GONE'}}")
    try:
        code, out, errtxt = run_powershell(ps)
    except (RuntimeError, OSError, subprocess.SubprocessError) as exc:
        return {'key': key, 'task_name': name, 'action': 'blocked', 'ok': False,
                'detail': f'{type(exc).__name__}: {exc}'[:200]}
    gone = 'GONE' in out
    return {'key': key, 'task_name': name, 'action': 'removed', 'ok': code == 0 and gone,
            'detail': f'{out.strip()[:80]}（{reason}）' if gone
                      else f'注销没成功（回体 {out.strip()[:60] or "空"}）：{errtxt[:120]}'}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description='盘中播报四条的计划任务面（看／改／重注册）')
    ap.add_argument('--show', action='store_true', help='配置 × 机器实况对照（只读，默认动作）')
    ap.add_argument('--set', action='append', default=[], metavar='K=V',
                    help='改一个在册键（可重复），如 --set heat_times=10:05,14:18')
    ap.add_argument('--apply', action='store_true', help='按当前配置重新注册四条任务')
    ap.add_argument('--only', default='', metavar='K[,K]',
                    help='只动划出来的那几条（如 `auction,heat`）；名字必须是 ' + '/'.join(SPEC))
    ap.add_argument('--dry-run', action='store_true', help='只生成 XML，不动任务计划程序')
    args = ap.parse_args(argv)

    keys = [k.strip() for k in args.only.split(',') if k.strip()]
    unknown = [k for k in keys if k not in SPEC]
    if unknown:
        print(f"--only 里 {'、'.join(unknown)} 不是这四条之一：可用 {'、'.join(SPEC)}")
        return 2

    kv: dict[str, Any] = {}
    for item in args.set:
        if '=' not in item:
            print(f'--set 要写成 K=V，收到 {item!r}')
            return 2
        k, raw = item.split('=', 1)
        if raw.strip() == '':
            # 空值在作业配置那侧的含义是"删掉这个键、回到默认"，命令行上静静做一次这种
            # 事等于偷偷改口径 —— 要撤回请按名字改回具体值。
            print(f'--set {k}= 留空不写（留空＝删键回默认，屏上看不出来）；'
                  f'要改请给具体值')
            return 2
        if k == 'enabled' or k.endswith('_enabled'):
            kv[k] = raw.strip().lower() in ('1', 'true', 'yes', 'on')
        elif k.endswith('_interval_min') or k.endswith('_duration_h'):
            try:
                kv[k] = int(raw)
            except ValueError:
                print(f'{k} 需要整数，收到 {raw!r}')
                return 2
        else:
            kv[k] = raw

    via = 'cli:app.intraday.tasks'
    only = keys or None
    if kv:
        why, result = apply_patch(kv, via=via, register=args.apply, dry_run=args.dry_run,
                                  keys=only)
        if why:
            print('✗ ' + why)
            return 2
    elif args.apply:
        result = plan()
        result['apply'] = register_all(result['config'], via=via, dry_run=args.dry_run,
                                       keys=only)
    else:
        result = plan()

    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    for row in (result.get('apply') or []):
        print(f"  {'✓' if row['ok'] else '✗'} {row['task_name']} · {row['action']} · {row['detail']}")
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
