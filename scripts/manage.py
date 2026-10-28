# 本文件继承自上游 vibe-astock v1.1.0（Apache-2.0，见根 LICENSE），本机有实质性改动。
# doctor 现在 17 项（9 硬 + 8 建议），其中八项建议项读的是本机 PostgreSQL 与作业台账；归属清单见 docs/上游改动与归属清单.md。
"""Local setup, offline diagnostics and foreground launch (Python standard library).

Uses the existing venv, npm lockfiles and Uvicorn service. No global package,
credential or OS service installation. Supports local browser startup on Windows too.
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
import webbrowser

PLATFORM = os.name
ROOT = Path(__file__).resolve().parents[1]


class SetupError(RuntimeError):
    pass


def python_at(root: Path) -> Path:
    return root / (".venv/Scripts/python.exe" if PLATFORM == "nt" else ".venv/bin/python")


def npm_command():
    if PLATFORM != "nt":
        return ["npm"]
    npm = shutil.which("npm")
    node = shutil.which("node")
    if npm and node:
        # Standard Node/npm installation and nvm-windows layout; no cmd.exe,
        # shell=True, or shell interpolation of a user-chosen checkout path.
        entry = Path(npm).resolve().parent / "node_modules/npm/bin/npm-cli.js"
        if entry.is_file():
            return [node, str(entry)]
    raise SetupError("无法找到 npm 的 Node 入口，请重新安装官方 Node.js 22+（含 npm）。")


def run(argv, root, timeout=900):
    """跑一步安装。npm 的进度只在 TTY 上打，被重定向后**整步可以十几分钟一行不出**，
    所以失败时必须自己把"卡在哪、跑了多久、为什么"说出来 ——
    原来超时和真失败共用一句"请检查上方提示"，而上方根本没有提示（异常还被 `from None` 吞了）。"""
    cmd = (npm_command() + list(argv[1:])) if str(argv[0]) == "npm" else [str(v) for v in argv]
    label = " ".join(str(v) for v in argv)
    print(f"  $ {label}   （工作目录 {root}，上限 {timeout}s）", flush=True)
    t0 = time.monotonic()
    try:
        subprocess.run(cmd, cwd=root, check=True, timeout=timeout,
                       env={**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"})
    except subprocess.TimeoutExpired:
        raise SetupError(
            f"这一步超时了（{time.monotonic() - t0:.0f}s 上限 {timeout}s）：{label}\n"
            f"  多半是在联网下载（npm 走 registry、pip 走 PyPI）。网络慢的时候这一步可以等很久，"
            f"而它删装过的东西是重头下载 —— 网络恢复后重跑 {root / 'scripts' / 'setup'} 即可；"
            f"已有用户数据不会丢。"
        ) from None
    except subprocess.CalledProcessError as exc:
        raise SetupError(f"这一步失败（退出码 {exc.returncode}）：{label}；"
                         f"报错在它自己的输出里，上面最后几行就是") from None
    except OSError as exc:
        raise SetupError(f"这一步起不来（{type(exc).__name__}: {exc}）：{label}；"
                         f"命令在 PATH 里吗？") from None


def fingerprint(root: Path) -> str:
    """Track installation/build inputs, never user data or generated outputs."""
    files = [root / p for p in ("requirements.txt", "runtime/package.json", "runtime/package-lock.json")]
    for directory, dirs, names in os.walk(root / "frontend"):
        # `__pycache__` 与 `*.pyc` 是跑验收脚本时再生成的产物，不是构建输入：
        # 2026-09-26 实测 `frontend/test/__pycache__/browser-acceptance.cpython-314.pyc`
        # 被算进指纹，谁跑一次那个脚本，下次双击启动器就要白等 8~12 分钟的 setup。
        dirs[:] = [d for d in dirs if d not in {"node_modules", "dist", ".git", "__pycache__"}]
        files += [Path(directory) / n for n in names
                  if not n.endswith((".tsbuildinfo", ".pyc", ".pyo"))]
    h = hashlib.sha256()
    for p in sorted(files):
        h.update(str(p.relative_to(root)).encode()); h.update(b"\0"); h.update(p.read_bytes())
    return h.hexdigest()


def probe(argv, root, timeout=30):
    try:
        p = subprocess.run([str(v) for v in argv], cwd=root, capture_output=True, text=True, encoding="utf-8", timeout=timeout)
        return p.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def prerequisites(root):
    if sys.version_info < (3, 10):
        raise SetupError("请安装 Python 3.10 或更高版本，推荐 Python 3.12。")
    if not shutil.which("npm") or not probe(["node", "-e", "process.exit(Number(process.versions.node.split('.')[0])>=22?0:1)"], root):
        raise SetupError("请安装 Node.js 22 或更高版本（需包含 npm），然后重新打开启动器。")


def dependency_status(root):
    code = """
import importlib,json
failures=[]
for name in ['fastapi','uvicorn','langgraph','langchain_openai','akshare','requests','dotenv','mcp','py_mini_racer','bs4','numpy','pandas','baostock']:
 try: importlib.import_module(name)
 except Exception as exc: failures.append(name+': '+type(exc).__name__)
print('ASTOCK_IMPORTS='+json.dumps(failures))
"""
    try:
        p = subprocess.run([str(python_at(root)), "-c", code], cwd=root, capture_output=True, text=True, encoding="utf-8", timeout=60)
    except subprocess.TimeoutExpired:
        return False, "依赖加载超过一分钟；请检查机器负载后重试体检"
    except OSError:
        return False, "Python 环境无法启动；运行 scripts/setup"
    if p.returncode != 0:
        return False, f"依赖检查进程未完成（退出码 {p.returncode}）；请重试体检"
    for line in reversed(p.stdout.splitlines()):
        if line.startswith("ASTOCK_IMPORTS="):
            try:
                failures = json.loads(line.removeprefix("ASTOCK_IMPORTS="))
                if isinstance(failures, list) and all(isinstance(v, str) for v in failures):
                    return not failures, "运行 scripts/setup；依赖加载失败：" + ", ".join(failures)
            except ValueError:
                break
    return False, f"依赖检查进程未完成（退出码 {p.returncode}）；请重试体检"


READCHECK = ".local/backup_readcheck.json"     # 格式见 docs/系统配置工作指南/02-PostgreSQL与备份.md


def _restorable(dump: Path, ttl_hours: float = 24.0) -> tuple[bool, str]:
    """能不能被恢复工具读开。**只看文件存在与 mtime 是不够的** —— 2026-09-25 那次
    收紧 NTFS 权限把备份文件弄成 PermissionError，文件全在、mtime 全新，`pg_restore` 却读不了。

    `pg_restore -l` 对 608MB 的全库 dump 要几秒，所以同一文件（路径+mtime+大小没变）
    在 `ttl_hours` 内复用结论，标记落 `.local/backup_readcheck.json` 供 `/api/jobs` 读。
    """
    key = str(dump)
    stat = dump.stat()
    stamp = {"mtime": int(stat.st_mtime), "size": stat.st_size}
    marker = ROOT / READCHECK
    try:
        cached = json.loads(marker.read_text(encoding="utf-8"))
        if (isinstance(cached, dict) and isinstance(cached.get(key), dict)
                and cached[key].get("stamp") == stamp
                and time.time() - cached[key].get("checked_at", 0) < ttl_hours * 3600):
            return bool(cached[key].get("ok")), str(cached[key].get("why") or "")
    except (OSError, ValueError):
        pass
    exe = Path(os.environ.get("FT_PG_BIN") or r"D:\PostgreSQL\17\bin") / (
        "pg_restore.exe" if PLATFORM == "nt" else "pg_restore")
    if not exe.is_file():
        found = shutil.which("pg_restore")
        exe = Path(found) if found else None
    if exe is None:
        ok, why = False, "找不到 pg_restore（不在 PATH；PG 二进制在 D:\\PostgreSQL\\17\\bin）"
    else:
        try:
            p = subprocess.run([str(exe), "-l", str(dump)], capture_output=True, timeout=180)
            if p.returncode == 0:
                ok, why = True, ""
            else:
                err = (p.stderr or b"").decode("utf-8", "replace").strip().splitlines()
                ok, why = False, (f"{dump.name} 读不开："
                                  f"{err[-1] if err else '退出码 ' + str(p.returncode)}")
        except (OSError, subprocess.SubprocessError) as exc:
            ok, why = False, f"{dump.name} 无法执行 pg_restore：{type(exc).__name__}"
    try:
        marker.parent.mkdir(parents=True, exist_ok=True)
        data = {}
        try:
            data = json.loads(marker.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                data = {}
        except (OSError, ValueError):
            data = {}
        data[key] = dict(stamp=stamp, ok=ok, why=why, checked_at=time.time())
        marker.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass                                  # 标记写不掉主流程：下次重算就是，别为了缓存出声
    return ok, why


EXTERNAL_RE = re.compile(
    r"D:[\\/](FinanceTool|finance[\\/]脚本|Vibe-Trading|TradingAgents|mootdx|quantdash|tdx_skills)")
SCAN_EXTS = {".py", ".cmd", ".bat", ".ps1", ".ts", ".tsx", ".js", ".sql"}
DOC_EXTS = {".md"}
SCAN_SKIP_DIRS = {"node_modules", ".venv", ".git", "dist", "build", "__pycache__"}
SCAN_ARCHIVE_PREFIX = (".local" + os.sep,)             # 诊断件区，单独计数不算红线
# 归档区：里面**本来就该**满是指向别处的路径（它们是搬进来的说明书和遗留脚本，
# 不是本项目要跑的东西）。豁免靠"在哪个目录"而不是靠正则猜——和豁免清单同一个道理。
ARCHIVE_PREFIX = ("docs" + os.sep + "参考" + os.sep,
                  "legacy" + os.sep,
                  "ingest" + os.sep + "legacy" + os.sep)
COMMENT_PREFIX = ("#", "//", "REM", "rem")
# Markdown 的 `**粗体**` 开头行不是注释。若把 "*" 也当注释前缀，
# 整个 09-quantdash逻辑规格 的加粗行就全被跳过 —— 那是系统性漏报（两个代理各发现一次）。
DOC_COMMENT_PREFIX = ("<!--",)


def _allowlist(root: Path) -> set[str]:
    """人审过的豁免清单：`路径:行号 理由`。不靠正则猜"这行是叙述还是依赖"。"""
    path = root / "scripts" / "external_refs_allowlist.txt"
    kept = set()
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            kept.add(line.replace("/", os.sep).split()[0])
    except OSError:
        pass
    return kept


#: 两类"登记了但那个东西不存在"的形状。原来的 `EXTERNAL_RE` 只认**参考项目的绝对路径**，
#: 所以 2026-09-29 那次 `c10_catchup` 用 `scripts/ops/p0_tdx_predownload.py`（参考仓库的
#: 目录结构，本项目里没有这个文件）起下载时，这一项是绿的 —— 每步 `can't open file`、
#: 退码 2 长得像"下载没成"，实际一条命令都没执行，整条加工链的因子/评分/热度被连带跳过。
#: 覆盖面必须吃到"拼子目录起作业"这一类，否则这项体检就是个假闸。
REGISTRY_JOB_RE = re.compile(r"""['"](ingest\.jobs\.[A-Za-z0-9_]{2,50})['"]""")
#: 带分隔符的脚本路径字面量（`.py` / `.ps1` / `.cmd`）
SCRIPT_PATH_RE = re.compile(r"""['"]((?:[A-Za-z0-9_.\-]+[/\\]){1,6}[A-Za-z0-9_.\-]+\.(?:py|ps1|cmd))['"]""")
#: 只在这句（或它上面两行）明显在"起子进程"时才判 —— 否则 `Path("code/signal_engine.py")`
#: 这种带 `.exists()` 守卫的探测会被误报成故障（实测过：全仓扫会命中 2 处，都是无害的）。
SPAWN_CTX_RE = re.compile(r"(subprocess|Popen|check_output|check_call|sys\.executable|run_script|cmd\s*=)")


def missing_job_targets(root: Path, rel: str, lines: list[str]) -> list[str]:
    """这个文件里"登记/拼出来但要跑的东西不存在"的行。返回 `行号 说明` 列表。

    `tests/` 故意跳过：测试里大量字符串是 monkeypatch 的目标名或临时文件名
    （`duanxian.proc.kill_tree`、`nope.py`），按"文件必须存在"去判会把体检项变成常驻红灯，
    而一条一直红的体检等于没有体检（`CHAIN_WORDS` 那次的同一教训）。
    """
    rel = rel.replace("\\", "/")
    if rel.startswith(("tests/", "legacy/")):
        return []
    out = []
    for lineno, line in enumerate(lines, 1):
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        for m in REGISTRY_JOB_RE.finditer(line):
            mod = m.group(1)
            target = root / "ingest" / "jobs" / f"{mod.split('ingest.jobs.', 1)[1]}.py"
            if not target.exists():
                out.append(f"{rel}:{lineno} 登记的作业模块 {mod} 不存在")
        near_spawn = SPAWN_CTX_RE.search(line) or any(
            SPAWN_CTX_RE.search(x) for x in lines[max(0, lineno - 3):lineno - 1])
        if near_spawn:
            for m in SCRIPT_PATH_RE.finditer(line):
                raw = m.group(1)
                if "\\" in raw or "*" in raw:      # 正则串/转义路径不当成文件路径
                    continue
                if not (root / raw.replace("\\", "/")).exists():
                    out.append(f"{rel}:{lineno} 要起的脚本路径不存在：{raw}")
    return out


def external_path_refs(root: Path):
    """扫"指向将被删除的参考项目"的绝对路径。**用户已裁定参考项目最终会删** ——
    这类引用在删除当天不会报错，只会静默变废铁，所以必须平时就看得见。

    分三堆：
    - 运行时代码（`vr/duanxian/ingest/app/scripts/tests/frontend`…）命中就报红；
    - 说明文档（`.md`，四本工作指南在内）命中也报红 —— 用户裁定"说明文件里面不要再
      体现引用和使用其他参考项目的内容"，文档里写 `python "D:\\finance\\脚本\\..."`
      就是让人去跑一个马上要消失的东西；
    - 归档区（`docs/参考/` 搬进来的别家说明书、`legacy/`、`ingest/legacy/`）与
      `.local/` 只计数不报红 —— 那里面全是指向别处的路径才是对的。
    整行是注释的跳过。
    """
    allowed = _allowlist(root)
    runtime, docs, archive, waived = [], [], 0, 0
    for dirpath, dirs, names in os.walk(root):
        dirs[:] = [d for d in dirs if d not in SCAN_SKIP_DIRS]
        for name in names:
            suffix = Path(name).suffix.lower()
            if suffix in DOC_EXTS:
                bucket = "doc"
            elif suffix in SCAN_EXTS:
                bucket = "code"
            else:
                continue
            path = Path(dirpath) / name
            try:
                rel = str(path.relative_to(root))
            except ValueError:
                continue
            if rel.startswith(ARCHIVE_PREFIX) or rel.startswith(SCAN_ARCHIVE_PREFIX):
                is_archive = True
            else:
                is_archive = False
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            comment_prefix = DOC_COMMENT_PREFIX if bucket == "doc" else COMMENT_PREFIX
            for lineno, line in enumerate(text.splitlines(), 1):
                stripped = line.strip()
                if stripped.startswith(comment_prefix):
                    continue
                if EXTERNAL_RE.search(line):
                    if is_archive:
                        archive += 1
                    elif f"{rel}:{lineno}" in allowed:
                        waived += 1
                    elif bucket == "doc":
                        docs.append(f"{rel}:{lineno}")
                    else:
                        runtime.append(f"{rel}:{lineno}")
            if bucket == "code":
                # 第二类覆盖面：登记/拼出来的作业路径不存在（`c10` 那一类）。
                # 与"参考项目绝对路径"是两种坏法，但后果一样：静默不执行。
                runtime += missing_job_targets(root, rel, text.splitlines())
    return runtime, docs, archive, waived


def backup_freshness():
    """最近一次数据库备份的年龄 + **能不能被恢复工具读开**。

    只提示、不拦启动 —— 启动器和备份没有因果关系，拦下来只会逼人绕过启动器直起 uvicorn，
    那才是真正会丢备份的用法。判据与 `backup_on_start` 共用 `backup_ages`：
    两处各写一遍阈值，迟早会出现「体检说不旧、启动器天天补」那种分歧。
    """
    ages, err = backup_ages()
    if not ages:
        return False, err
    stale, busy = backup_stale_kinds(ages)
    parts = []
    for kind, _limit in BACKUP_KINDS:
        hours, readable, _newest, why = ages.get(kind, (None, False, None, ""))
        if hours is None:
            parts.append(f"{kind}＝无")
        elif kind in busy:
            parts.append(f"{kind}＝{hours:.0f} 小时前"
                         f"（{BACKUP_IN_PROGRESS_MIN} 分钟内还被写过：可能正在写、也可能刚写完，"
                         "这一刻不跑 pg_restore，免得半截文件被读成坏文件）")
        else:
            parts.append(f"{kind}＝{hours:.0f} 小时前" + ("" if readable else f"（{why}）"))
    due = backup_last_daily_due()
    head = ("最近备份 " + "、".join(parts)
            + f"（daily 按排程应到 {due}；周末那两晚排的是全库 weekly，那两天不该有 daily。"
              f"weekly≤{BACKUP_KINDS[1][1] // 24} 天；判据=pg_restore -l 能列出 TOC）"
            if stale else "、".join(parts) + f" —— 都在阈值内（daily 按排程应到 {due}）")
    gap = backup_mirror_gap(ages)
    # 跨盘缺一份**只陈述、不改本项成败**：当场没拷成是由备份那一轮自己记失败（`run()` 里
    # `mirror_ok is False` 整轮判失败 → 「采集作业」那项会红并带原因）。
    # 这里若也判红，就会出现"程序已经会自己拷了，但旧的 weekly 那份永远没副本"的连续七天误报
    # （10-19 实测：`weekly＝缺`，而那份是 10-18 18:54 背的，比这一档代码还早）。
    return not stale, head + "；跨盘副本 " + gap["text"]


def close_chain_switches():
    """15:59 收盘链的两个自动开关，**按"有没有人署名"分两种颜色**。

    09-28 实测到的事故形态不是"链坏了"，是"链被人关了而没人知道是谁、为什么"：
    同一天里两个会话各关各开、来回三次，页面上只显示"已停用"，看着跟"用户自己不想跑"
    一模一样。而当日不采就是永久少一天，所以"关"必须是一个署名的动作：

      · 关 + 该作业写了 `note` → 只报状态，**不判红**（这是有人在管的停用）；
      · 关 + 没人署名 → **红**，把作业名点出来。

    仍然只是 advisory，不拦启动 —— 用户主动停一天是完全正常的用法，拦它等于替用户做主。
    这里不 import `duanxian.jobs`（同 `collector_status` 的理由：doctor 要在依赖还没装时就能跑），
    所以默认值 `enabled=True` 与 `duanxian/jobs.py::CLOSE_DEFAULTS` 各留一份，改那边记得改这里。
    """
    root = Path(os.environ.get("ASTOCK_DATA_HOME") or (Path.home() / ".duanxian-agents"))
    try:
        cfg = json.loads((root / "jobs" / "config.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        cfg = {}
    if not isinstance(cfg, dict):
        cfg = {}
    on, claimed, unclaimed, traced = [], [], [], []
    for name in ("daily_close", "fill_all"):
        row = cfg.get(name) if isinstance(cfg.get(name), dict) else {}
        # `_changed_at` / `_changed_by` 是每次写入自动盖章的留痕（`duanxian/jobs.py`）。
        # 有了它，"谁改的"不再依赖人记得写 note；note 只补"为什么"。
        trace = ""
        if row.get("_changed_by"):
            trace = f"{row.get('_changed_at') or '时间未知'} 由 {row['_changed_by']}"
        if bool(row.get("enabled", True)):
            on.append(name)
        elif str(row.get("note") or "").strip():
            claimed.append(f"{name}（{str(row['note']).strip()}"
                           + (f"；{trace}）" if trace else "）"))
        elif trace:
            # 知道是谁改的、只是没写理由 —— 报出来，但不判红（不是"没人认领的关闭"）
            traced.append(f"{name}（{trace}，没写理由）")
        else:
            unclaimed.append(name)
    parts = []
    if on:
        parts.append("会自动跑：" + "、".join(on))
    if claimed:
        parts.append("停用且已署名：" + "、".join(claimed))
    if traced:
        parts.append("停用、有留痕但没写理由：" + "、".join(traced))
    if unclaimed:
        parts.append("停用但**没人说明是谁关的、为什么**：" + "、".join(unclaimed))
    action = ("收盘链开关 —— " + "；".join(parts)
              + "。要自动：scripts/jobs.py enable <名>；"
                "要停一天就停下并署名：scripts/jobs.py disable <名> && scripts/jobs.py set <名> note=\"谁/为什么\"")
    return not unclaimed, action


#: 待人工清单的**判据与落点都只住在 `duanxian/gui_trail.py`**，体检这边只调它的
#: `pending()/items()`，不再自己读 json 数一遍（10-13 就是那第二遍数法造成
#: "页面 0 条、体检 3 条"）。`tdx_gui_trail()` 里连 fallback 都不留本地判据。


def tdx_gui_trail(days: int = 3):
    """系统自己动过通达信界面的动作里，**还没做成的那些** —— 用户 10-06 要的手动兜底入口。

    红 = `gui_trail.pending()` 交回来的那些（机器侧没路了，得人去点那一下）；
    没有清单文件不算红（这台机器还没自动动过界面）。

    ⚠️ 2026-10-13：这里**不许再自己数一遍**。之前这一项是本地 `status != 'done'` 的过滤，
    而那份判据的真住在 `duanxian/gui_trail.py::pending()`（同日同步骤去重、
    以及 `supersedes` 那条"程序后来自己做完了就不该再挂着"）。两份各写一遍的结果是
    **同一件事两个读数**：页面说 0 条待补，doctor 说 3 条要人补（现场就是 `close_client` 那三条）。
    现在只引一份；`duanxian` 连不上时按"没判"处理，绝不回退成一份自己写的判据。
    """
    try:
        # `python scripts/manage.py doctor` 的 `sys.path[0]` 是 `scripts/`，`start()` 那一步才插 ROOT
        # （:1356）。体检单跑时也要能引到那份判据 —— `duanxian.gui_trail` 只用标准库
        # （`paths`/`atomic_write` 都是），所以"依赖还没装"这一条挡不住它。
        if str(ROOT) not in sys.path:
            sys.path.insert(0, str(ROOT))
        from duanxian import gui_trail
    except Exception as exc:  # noqa: BLE001  判据模块真读不了时按"未判"处理，绝不回退成第二份数法
        return True, (f"界面动作待人工清单：判据模块读不了（{type(exc).__name__}: {str(exc)[:80]}）"
                      "—— 这一项按未判处理，既不说要人补也不说没有")
    pend = gui_trail.pending(days=days)
    seen = gui_trail.items(limit=gui_trail.MAX_ITEMS, days=days)
    if not pend:
        return True, (f"界面动作待人工清单：最近 {days} 天登记 {len(seen)} 次，"
                      "没有要人补的（做成干净的不列，程序后来自己做完的按 `supersedes` 不列）")
    last = pend[0]        # `pending()` 是最新在前，这里别再倒过来数
    return False, (f"界面动作有 {len(pend)} 条没做成，要人补："
                   + "；".join(f"{x.get('day')} {x.get('step')}（{x.get('status')}）"
                               for x in pend[:4])
                   + f"。最新一条的手动做法：{last.get('manual') or '见 07-故障恢复手册'}")


#: 本进程**应该**有的常驻循环，判据只住在 `vr/jobsboard.py::HOST_LOOPS` 一处。
#: 这里用正则把那一段字面量读出来而不是 import 它：doctor 要在依赖还没装的时候就能跑
#: （同 `collector_status` 不 import `duanxian.jobs` 的理由）。读不出来就报"判不了"，
#: 绝不退化成"没声明 = 不需要跑"—— 那正是"看着有其实没跑"能藏过去的缝。
LOOP_INVENTORY_SOURCE = "vr/jobsboard.py"
#: 在册作业清单的唯一来源（`CLOSE_DEFAULTS` 那个映射）。doctor 只正则读它，不 import。
JOB_REGISTRY_SOURCE = "duanxian/jobs.py"

#: 只在**有人真用它**时才记一笔的作业：它们没有每日循环，"今天没有记录"就是"今天没人用"。
#: 这张名单唯一的合法加入条件是"我读过那个模块，确认它只在被调用时记账"——
#: 放错一条就等于给一条真故障买了免死牌（`tests/test_collector_manual_probe.py` 盯着这条）。
MANUAL_PROBE_JOBS = ("responses_bridge",)


def declared_loop_jobs(root: Path) -> tuple[list[tuple[str, str]], str | None]:
    """从磁盘上读"该有哪几条常驻循环、各自记到哪一格账"。返回 `[(线程名, 记账键), …]`。

    清单只有一份：`vr/jobsboard.py::LOOP_JOBS`。10-06 之前那里只有 `HOST_LOOPS` 一份**名字**清单，
    于是"这条循环活着、但它一次账都没记过"没人能回答 —— `intraday` 就是这个形状
    （线程在，`state.json` 里连键都没有）。
    这里**正则读、不 import**：doctor 要在三方依赖还没装的时候也能跑。
    """
    src = root / LOOP_INVENTORY_SOURCE
    try:
        text = src.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return [], f"读不到 {LOOP_INVENTORY_SOURCE}（{type(exc).__name__}）"
    m = re.search(r"(?m)^LOOP_JOBS\s*=\s*\((.*?)\n\)\n", text, re.S)
    if m:
        pairs = re.findall(r"""\(\s*['"]([A-Za-z0-9_\-]+)['"]\s*,\s*['"]([a-z0-9_]+)['"]""", m.group(1))
        if pairs:
            return pairs, None
    # 旧形状兜底：只有名字清单时至少还能比"该起哪几条"，但要说清配对读不到。
    m = re.search(r"HOST_LOOPS\s*=\s*\(([^)]*)\)", text, re.S)
    if not m:
        return [], f"{LOOP_INVENTORY_SOURCE} 里找不到 LOOP_JOBS / HOST_LOOPS（清单被改名了？）"
    names = re.findall(r"""['"]([A-Za-z0-9_\-]+)['"]""", m.group(1))
    if not names:
        return [], "LOOP_JOBS / HOST_LOOPS 是空的（那这项体检就没有判据了）"
    return [(n, n.replace("-", "_")) for n in names], "只读到名字清单，没配到记账键（循环活着但从不记账这一类判不了）"


def declared_on_demand(root: Path) -> list[str]:
    """从磁盘上读"哪几条循环是**按需起**的"（第一次有人订阅才起线程，夜里本来就不该在）。

    判据只住 `vr/jobsboard.py::ON_DEMAND_LOOPS` 一份，这里正则读、不 import（同上）。
    读不出来就当**没有按需起**⇒ 退回"该活着却没有就算缺"的旧判据：这条宁可多报一句红，
    不许静默放行 —— 静默放行会让"其实从没起过"的循环躲在豁免里。
    """
    src = root / LOOP_INVENTORY_SOURCE
    try:
        text = src.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    m = re.search(r"(?m)^ON_DEMAND_LOOPS\s*=\s*\(([^)]*)\)", text)
    if not m:
        return []
    return re.findall(r"""['"]([A-Za-z0-9_\-]+)['"]""", m.group(1))


def declared_loops(root: Path) -> tuple[list[str], str | None]:
    """从磁盘上读"该有哪几条常驻循环"。返回 `(清单, 读不出来的原因)`。"""
    pairs, why = declared_loop_jobs(root)
    return [name for name, _key in pairs], why


def declared_jobs() -> tuple[list[str], str | None]:
    """从磁盘上读"在册的作业有哪些"。返回 `(清单, 读不出来的原因)`。

    为什么非要有这一条：`collector_status` 原来只遍历 `state.json` 里**已有的键**，
    于是"一个从没往 state.json 记过一笔的作业"在这项里**根本不存在** —— 10-06 实测到
    `coverage` 就是这样：循环在 `host_loops` 里活着，state.json 却没有这个键，页面上也
    只有它自己那一格。10-28 那类"看着有其实没跑"最省事的藏法就是不记账，因为它连"红"
    都触发不了。清单只住 `duanxian/jobs.py::CLOSE_DEFAULTS`，这里正则读、不 import
    （doctor 要在三方依赖还没装的时候也能跑）。
    """
    src = ROOT / JOB_REGISTRY_SOURCE
    try:
        text = src.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return [], f"读不到 {JOB_REGISTRY_SOURCE}（{type(exc).__name__}）"
    m = re.search(r"(?m)^CLOSE_DEFAULTS\s*=\s*\{(.*?)\}\n", text, re.S)
    if not m:
        return [], f"{JOB_REGISTRY_SOURCE} 里找不到 CLOSE_DEFAULTS 这个映射（清单被改名了？）"
    names = re.findall(r"""['"]([a-z_0-9]+)['"]\s*:""", m.group(1))
    return names, (None if names else "CLOSE_DEFAULTS 是空的（那这项体检就没有判据了）")


def live_loops(port: int, timeout: float = 8.0) -> tuple[dict | None, str | None]:
    """问正在跑的那个进程它自己活着哪几条。服务没起就返回错误，不猜。"""
    url = f"http://127.0.0.1:{port}/api/jobs"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            body = json.loads(resp.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as exc:
        return None, f"接口返回 HTTP {exc.code}"
    except (OSError, ValueError) as exc:
        return None, f"{type(exc).__name__}: {str(exc)[:80]}"
    data = body.get("data") if isinstance(body, dict) else None
    sched = (data or {}).get("scheduler") if isinstance(data, dict) else None
    if not isinstance(sched, dict):
        return None, "返回体里没有 scheduler 这一节（服务版本比这份体检旧？）"
    return sched, None


def loop_inventory(root: Path, port: int, phase: str = "steady"):
    """**磁盘上声明的清单 vs 进程里真活着的清单**。

    为什么不能只看服务自报的 `host_loops_missing`：那份"缺谁"是拿**进程自己那份编译进去的
    清单**比的 —— 代码里新增了第 7 条循环而进程没重启，它会心满意足地报"一条不缺"。
    10-06 实测就是这个形状：`HOST_LOOPS` 已经是 7 条（含 `tdx-watch`），跑着的服务报
    "活着 6 条、缺 0 条"。所以两边都得读，并把"进程是旧的"写成一句可执行动作。

    `phase="startup"`：`start()` 是在**绑定端口之前**跑体检的（先体检再起进程，
    这是启动链的既有顺序），此时"服务没在跑"是流程本身决定的，不是故障。
    不加这个区分，这项会在每一次启动、在所有人最需要看清它的时候，固定报一条红 ——
    一条必然误报的体检项等于没有体检项（`CHAIN_WORDS` 那次同样的教训）。
    """
    want, why = declared_loops(root)
    if not want:
        return False, f"判不了该起哪些循环：{why} —— 判不了不等于「循环都在」"
    sched, err = live_loops(port)
    if sched is None:
        if phase == "startup":
            return True, (f"{len(want)} 条循环待起（体检跑在绑定端口之前，此刻连不上是正常流程）："
                          + "、".join(want))
        return False, (f"服务没在跑或读不到（{err}）⇒ 一条常驻循环都没有 ⇒ "
                       f"15:59 的收盘链、20:40 的自选同步今天都不会自己跑")
    alive = list(sched.get("host_loops") or [])
    self_reported = list(sched.get("host_loops_missing") or [])
    pid = sched.get("pid")
    # 按需起的那几条不进"缺"（没人订阅时线程本来就不该在），但要用台账反证它**起得来**：
    # 连一笔账都没记过 ⇒ "按需"是借口，接线断了，那才是真红。
    pairs_map = dict(declared_loop_jobs(root)[0])
    on_demand = [n for n in declared_on_demand(root) if n in want]
    lazy_note = ""
    if on_demand:
        keys = state_keys()
        never = [n for n in on_demand if n not in alive and pairs_map.get(n) not in keys]
        if never:
            return False, ("按需起的 " + "、".join(never) + " 一次账都没记过（台账里连键都没有）"
                           f"（pid {pid}）⇒ 线程从没起来过，\"按需起\"只是借口；"
                           "要人查的是那个订阅接口有没有真调到 `ensure_started()`")
        idle = [n for n in on_demand if n not in alive]
        if idle:
            lazy_note = ("；按需起的 " + "、".join(idle) +
                         " 此刻没起（没人订阅，按设计）—— 台账里有它的账，确实起得来")
    must = [n for n in want if n not in on_demand]
    stale = [n for n in must if n not in alive and n not in self_reported]
    missing = [n for n in must if n not in alive]
    if not missing:
        # "该起几条"之外还要问一句"起着的这几条有没有在记账"：
        # 10-06 的 `intraday` 就是线程活着、`state.json` 里连键都没有。
        # 这不是红（还没到自己那一档就是这个形状），但必须点名，否则"接线"会被读成"在跑"。
        note = ""
        pairs, pair_why = declared_loop_jobs(root)
        if not pair_why:
            keys = state_keys()
            quiet = [f"{name}（记账键 {key}）" for name, key in pairs if name in alive and key not in keys]
            if quiet:
                note = ("；⚠ 活着但至今零笔记账：" + "、".join(quiet)
                        + " —— 可能只是还没到自己那一档，也可能是跑了不记账，两者都在这一格里现形")
        return True, (f"{len(must)} 条常驻循环都在（pid {pid}）："
                      + "、".join(n for n in alive if n in must) + note + lazy_note)
    if stale:
        # 进程连"该有这一条"都不知道 ⇒ 它跑的是旧代码
        return False, ("进程里跑的是旧代码：磁盘上声明了 " + "、".join(stale)
                       + f" 而服务连这几条都不认（它自己的清单只有 {'、'.join(alive)}，pid {pid}）"
                       "⇒ 重启 8910 才装得上；判生效只看这一项，别看 state.json 的时间戳")
    return False, ("缺 " + "、".join(missing) + f"（pid {pid}）⇒ 这些机制今天不会自己跑；"
                   "起线程的代码在 server.py 的 _lifespan 里，看启动日志找「为什么没起」那句")


def data_home() -> Path:
    """业务数据根。路径规则与 `duanxian/paths.py` 各留一份（doctor 不 import 三方依赖），
    改那边记得改这里。"""
    return Path(os.environ.get("ASTOCK_DATA_HOME") or (Path.home() / ".duanxian-agents"))


def state_keys() -> set[str]:
    """`jobs/state.json` 里**实际存在**的记账键。读不出来就当空 —— 调用方只用它做
    "有没有这一格"的判断，判错的方向是"多提示一句"，不是误判红。"""
    try:
        loaded = json.loads((data_home() / "jobs" / "state.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return set()
    return set(loaded) if isinstance(loaded, dict) else set()


def collector_status(expected_session: str | None = None):
    """采集作业的四种状态必须**分开写**，不能都塞进"最近成功 X"一个格式里。

    2026-09-28/29 两天缺场时，这一项是绿的 —— 因为它只看"最后一次成功是什么时候"，
    而那条自选同步作业其实**从未成功过（连败 91）**，页面照常显示 41 个组（旧存量）。
    "从未成功"和"成功过但今天没跑"是完全不同的处置，混写会让人以为一切正常。

    四类：
      · **从未成功且一次都没跑过** —— 刚接入，只提示不判红（判红等于造一条天天红的项）；
      · **从未成功但反复在跑**（连败 N）—— **红**，把 N 与最近一次归因点出来；
      · **成功过、但最近一个应跑的那一场没有成功记录** —— **红**（判据是 `expected_session`，
        即库里最近一个已收盘交易日；长假期间它本来就还是旧日期，不会误报）；
      · 其余 —— 绿。

    ⚠️ **`state.json` 不能当唯一的依据**（2026-09-28 实测）：快讯那条**常驻轮询过去根本不往
    state.json 记账**（只有 `scripts/jobs.py run-now news` 和页面手动触发会写），
    于是这项对着一份每 30 秒都在变大的 jsonl 报了假故障。循环侧现已补上记账
    （`server.py::_newswire_journal`，稳定期 15 分钟一次心跳），但**体检仍另看文件侧证据**：
    两份口径互相印证，任何一份说谎都当场看得见。

    这里**不 import duanxian.jobs**：doctor 要在依赖还没装的时候就能跑。路径规则
    （`ASTOCK_DATA_HOME` 或 `~/.duanxian-agents`）与 `duanxian/paths.py` 各留一份，改那边记得改这里。
    """
    root = data_home()
    try:
        state = json.loads((root / "jobs" / "state.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        state = {}
    if not isinstance(state, dict):
        state = {}
    try:
        cfg = json.loads((root / "jobs" / "config.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        cfg = {}
    if not isinstance(cfg, dict):
        cfg = {}

    def enabled_of(name):
        row = cfg.get(name)
        return bool(row.get("enabled", True)) if isinstance(row, dict) else True

    parts, bad = [], []
    # `ON_DEMAND_LOOPS` 里存的是**线程名**，而 state.json 的键是记账键（`newswire`→`news` 这类不一致
    # 是真存在的），所以要经 `LOOP_JOBS` 配对翻译一次；配对读不出来就一条都不豁免（宁可多念一句红）。
    pairs_map = dict(declared_loop_jobs(ROOT)[0])
    od_threads = declared_on_demand(ROOT)
    on_demand = [pairs_map[n] for n in od_threads if n in pairs_map]
    if od_threads and len(on_demand) != len(od_threads):
        missing = [n for n in od_threads if n not in pairs_map]
        parts.append("按需作业的记账键配对不上：%s —— 这几条不参与豁免，照旧按「该有今天」判"
                     % "、".join(missing))
    # 在册作业清单**在循环之前就要拿到**（下面"从未成功"那一半要按它判该不该报红）。
    # 读不出来 ⇒ 当成"全都不在册"是错的（那会把真故障糊成手动探针），所以此时一律按在册判红。
    want_jobs, registry_why = declared_jobs()
    if registry_why:
        want_jobs = list(state)
    for name, job in sorted(state.items()):
        if name == "news":
            continue          # 这份记录不代表轮询在不在跑，一律走下面的文件侧证据
        if not isinstance(job, dict):
            continue
        ok_at, run_at = job.get("last_ok_at"), job.get("last_run_at")
        fails = int(job.get("consecutive_failures") or 0)
        why = str(job.get("last_error") or "").strip()[:90]
        # **第三种结果不是失败**（`jobs.record` 里 `detail['skipped']` 那一档）：这一轮它按设计
        # 什么都不动（收盘链正跑、数据还没判全、按口径拒写……）。它必须排在"从未成功"那段**之前**：
        # 观望不刷新 `last_ok_at`，一个长期干净的收窗循环会一直是 `ok_at=None + 连败 0`，
        # 按老顺序就会被念成"从未成功过"—— 2026-10-12 的 `tdx_shell_reap` 正是这样被念成"连败 46"，
        # 而那 46 轮每一轮都做得对。
        if str(job.get("last_status") or "") == "skipped":
            parts.append(f"{name}:观望 {str(run_at or ok_at or '?')}"
                         + (f"（{why[:70]}）" if why else "")
                         + (f"/连败{fails}" if fails else ""))
            if fails:
                bad.append(f"{name} 最近一次失败（连败 {fails}；归因：{why}）")
            continue
        if not ok_at:
            if run_at or fails:
                # 反复在跑却一次都没成 —— 这才是"从未成功"里危险的那一半。
                # 但**只有"它本该每天跑"或"它今天确实又被触发过"才配判红**：
                # `responses_bridge` 这类只是有人手动点过一次的探针（不在册、上次跑是五天前），
                # 判红会让这一项从那天起天天红，而"今天没成"对它没有任何含义
                # （与 #85 夜间假红、天天红的 `sector_member_valid` 同一类：天天误报的体检项等于没有体检项）。
                ran_recently = str(run_at or "")[:10] >= (expected_session or "")
                if name in want_jobs or ran_recently:
                    bad.append(f"{name} 从未成功过（跑过 {run_at or '?'}，连败 {fails}"
                               + (f"；最近一次归因：{why}" if why else "") + "）")
                    parts.append(f"{name}:从未成功/连败{fails}")
                else:
                    parts.append(f"{name}:只有手动触发且从未成功（跑过 {run_at}，连败 {fails}"
                                 + (f"；归因：{why}" if why else "")
                                 + "）—— 不在册、这一场也没再触发，不判红")
            else:
                parts.append(f"{name}:从未运行（刚接入，还没跑过第一场）")
            continue
        last_day = str(ok_at)[:10]
        # （观望那一档已在上面这段之前处理掉了，这里不再重复判一次）
        # **按需起的作业**（`jobsboard.ON_DEMAND_LOOPS`，目前是盯盘轮询）不按"该有今天"判：
        # 没人打开盯盘页的那些天，它本来就不该有记录。判不了谁属于按需时不豁免任何一条。
        if name in on_demand:
            parts.append(f"{name}:{ok_at}（按需起，没人订阅的场次没有记录是正常的）")
            if fails:
                bad.append(f"{name} 最近一次失败（连败 {fails}；归因：{why}）")
            continue
        # 「成功过但过期」这一档**只豁免点名过的手动探针**：`responses_bridge` 是
        # 「有人真用 AI 才转发一次」的转换层（`review_agent/responses_bridge.py` 里那个 `_note` 只在
        # `do_POST` 被调，没有任何每日循环），"今天没人用"对它没有任何含义。
        # ⚠️ 不许改成"不在 CLOSE_DEFAULTS 就豁免"：`daily_close_client`、`tdx_shell_reap` 同样不在册，
        # 而它们**每天都在跑**，那是真该判的。清单钉在 `tests/test_collector_manual_probe.py`。
        manual_probe = name in MANUAL_PROBE_JOBS
        stale = bool(expected_session and last_day < expected_session and enabled_of(name)
                     and not manual_probe)
        parts.append(f"{name}:{'停用 ' if not enabled_of(name) else ''}{ok_at}"
                     + (f"/连败{fails}" if fails else ""))
        if manual_probe and expected_session and last_day < expected_session:
            parts[-1] += "（手动触发才记一笔，没有每日循环——无人使用的那些天没有记录是正常的，不判红）"
        if stale:
            bad.append(f"{name} 最近成功停在 {last_day}，而应到 {expected_session}"
                       + (f"（且连败 {fails}）" if fails else "")
                       + " —— 这一场要么没跑要么没跑成")
        elif fails:
            bad.append(f"{name} 最近一次失败（连败 {fails}"
                       + (f"；归因：{why}" if why else "）"))

    if registry_why:
        parts.append(f"在册作业清单判不了：{registry_why} —— 判不了不等于都在跑")
    else:
        untracked = [k for k in want_jobs if k not in state]
        if untracked:
            parts.append("在册但至今没有运行记录：" + "、".join(untracked)
                         + "（可能是还没到自己那一档，也可能是跑了不记账 —— 后者去查 /api/jobs 的 host_loops）")

    # 文件侧：最新的 `news-YYYY-MM-DD.jsonl` 的 mtime 才是轮询的真实心跳
    newest, newest_mtime = None, 0.0
    try:
        for f in (root / "news").glob("news-*.jsonl"):
            m = f.stat().st_mtime
            if m > newest_mtime:
                newest, newest_mtime = f, m
    except OSError:
        pass
    if newest:
        age_min = (time.time() - newest_mtime) / 60
        parts.insert(0, f"news:文件侧 {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(newest_mtime))}"
                        f"（{age_min:.0f} 分钟前，jsonl {newest.stat().st_size} 字节）")
        if age_min > 30:
            bad.append(f"快讯 {age_min:.0f} 分钟没有新落盘（30 秒一轮的循环，这等于没在跑）")
    elif state.get("news"):
        job = state["news"] if isinstance(state["news"], dict) else {}
        fails = int(job.get("consecutive_failures") or 0)
        why = str(job.get("last_error") or "").strip()[:90]
        # 「手动触发」这三个字是 09-28 立的：这份 state.json 只代表有人点过一次按钮，
        # 不代表轮询在跑；连败数字要留着，否则"跑了 9 次全挂"和"从没跑过"看着一样。
        parts.insert(0, f"news:只有手动触发记录 {job.get('last_ok_at') or '从未成功'}"
                        + (f"/连败{fails}" if fails else "")
                        + "（常驻循环没记账，且没有任何 jsonl 产出）")
        bad.append("快讯既无 jsonl 产出也无循环记账"
                   + (f"（手动触发连败 {fails}" + (f"，最近一次归因：{why}）" if why else "）") if fails else ""))

    if not parts:
        return True, f"尚无作业运行记录（data home={root}）—— 作业未接入时这是正常状态"
    ok = not bad
    return ok, ("最近成功 " + "、".join(parts) + f"（data home={root}）"
                + ("" if ok else "；要人看的：" + "；".join(bad)))


# 收盘链最坏的坏法是**安静地缺一天**：2026-09-28/29 两天就是这样过的 ——
# 链在 16:31 判 stop、通达信的盘后文件 17:22 才落盘，页面上一切正常，
# 缺的那一天要等用户自己在首页看出来（"连板股没更新""情绪周期没更新"）。
# 体检这一项把那个空档接住：判据只认**库里的日期**，不认作业说的"我跑成功了"。
#: (键, 中文名, 取值 SQL)。这几张是用户每天要看的那几块的直接输入 ——
#: 日线是骨干，评分喂趋势评分页与四维象限，qd 三张喂情绪/连板天梯/板块轮动。
FRESHNESS_TABLES = (
    ("stock_daily", "全市场日线", "select max(trade_date)::date from market_data.stock_daily"),
    ("score", "趋势评分（含四维象限输入）", "select max(trade_date)::date from analytics.score"),
    ("sentiment", "情绪周期", "select max(trade_date)::date from qd.sentiment_cycle"),
    ("ladder", "连板天梯", "select max(trade_date)::date from qd.board_ladder"),
    ("rotation", "板块轮动", "select max(trade_date)::date from qd.sector_rotation"),
)
#: 分钟线**不参与红绿灯**：vipdoc 只滚存约 100 个交易日，而入库需要 GUI 那条腿，
#: 这是已知且已被接受的缺口（见《数据获取》与 coverage 的硬缺口说明）。
#: 把它算进红灯等于造一条永远红的体检项 —— 一条一直红的项等于没有体检（同 `CHAIN_WORDS` 的教训）。
FRESHNESS_APPENDIX = (
    ("minute_1m", "1 分钟线",
     "select max(dt)::date from market_data.stock_minute where period = 1"),
)


def data_freshness(root):
    """当天（应为最近一个已收盘交易日）的数据到位没有。

    ⚠️ 这里**不 import duanxian/pg**：与 `collector_status` 同一条理由，体检要在
    依赖还没装的时候就能跑。所以照 `dependency_status` 的样子起一个子进程，
    用项目的 Python 问库，再把结果按一行 marker 交回来。

    返回第三个值（原始读数）是给 `doctor()` 用的：`collector_status` 判"该跑的那场跑了没"
    要用同一个 `expected`，再开一次子进程查库就是第三次为一个数字花时间。
    措辞上特意把"今天开不开市"和"下一场是哪天"一起报出来：长假期间这一项必然显示
    "到位 = 09-30 那场"，不写清今天休市就会被读成"数据停了六天还是绿的"。
    """
    code = """
import json, datetime
out = {}
try:
    import pandas
    from duanxian import pg, trade_calendar as tc
    out['expected'] = tc.latest_session()
    # 显式探一次连接：**"表是空的"与"库连不上"是两种诊断**，不能靠"全读成 None"倒推。
    # 实测（2026-10-06 把 PG_PASSWORD 改错）：`latest_session()` 仍能从别的通道给出日期，
    # 于是"五张表全空"看起来像缺数，而真相是凭据坏了 —— 报错方向能引人去补数、把库写坏。
    out['probe_ok'] = pg.fetch_df("select 1 as x") is not None
    today = datetime.date.today()
    out['today'] = str(today)
    row = pg.fetch_df("select is_open from reference_data.trade_calendar"
                      " where calendar_date = %%s", (str(today),))
    if row is None or row.empty:
        out['today_open'] = None          # 日历没覆盖今天 = 判不了，不是"不开市"
    else:
        flag = row.iloc[0]["is_open"]
        out['today_open'] = None if flag is None else bool(flag)
    # `next_open_date` 这一列**只在开市日那行有值**（休市日是 NULL，实测 09-25~27 全 NULL），
    # 所以"下一场"必须自己查，不能读那一列 —— 读到的话长假期间永远显示"下一场 未知"。
    nxt = pg.fetch_df("select min(calendar_date) d from reference_data.trade_calendar"
                      " where is_open and calendar_date > %%s", (str(today),))
    if nxt is not None and len(nxt) and nxt.iloc[0]["d"] is not None:
        out['next_open'] = str(nxt.iloc[0]["d"])[:10]
    for key, _name, sql in %r:
        df = pg.fetch_df(sql)
        v = None if df is None or df.empty else df.iloc[0, 0]
        out[key] = None if v is None or pandas.isna(v) else str(v)[:10]
except Exception as exc:
    out['error'] = type(exc).__name__ + ': ' + str(exc)[:150]
print('ASTOCK_FRESH=' + json.dumps(out, ensure_ascii=False))
""" % (list(FRESHNESS_TABLES + FRESHNESS_APPENDIX),)
    try:
        p = subprocess.run([str(python_at(root)), "-c", code], cwd=root,
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=90)
    except subprocess.TimeoutExpired:
        return False, "判不了：查库超过 90 秒（库没连、或机器负载高）—— 这条不等于「今天数据没问题」", None
    except OSError as exc:
        return False, f"判不了：Python 起不来（{type(exc).__name__}）—— 先跑 scripts/setup", None
    payload = None
    for line in reversed((p.stdout or "").splitlines()):
        if line.startswith("ASTOCK_FRESH="):
            try:
                payload = json.loads(line.removeprefix("ASTOCK_FRESH="))
            except ValueError:
                payload = None
            break
    if not isinstance(payload, dict):
        return False, ("判不了：查库进程没交出结果（退出码 %s，stderr 尾部 %s）"
                       % (p.returncode, (p.stderr or "")[-120:].replace("\n", " "))), None
    if payload.get("error") or not payload.get("expected"):
        return False, ("判不了：%s —— 判不了不等于「今天数据没问题」，也不许据此认定缺一天"
                       % (payload.get("error") or "日历给不出最近一个已收盘交易日")), payload
    expected = payload["expected"]
    behind = [(name, payload.get(key)) for key, name, _sql in FRESHNESS_TABLES
              if not payload.get(key) or payload[key] < expected]
    # **连不上库 ≠ 数据缺失**（2026-10-06 把口令改错实测到的措辞缺陷）：改错之后每张表都读成
    # None，如果照"停在 空"报，就等于把一次凭据故障诊断成"这几天没采"，指人去做补数 ——
    # 而那正好是毁掉数据的方法。判据读不到就说读不到，绝不下"缺数"的结论。
    if payload.get("probe_ok") is False:
        return False, ("读不到库（`select 1` 就失败）—— 这**不等于缺数**：先确认 PostgreSQL 服务在跑、"
                       "凭据没改错（.env 的 PG_*），再看《故障恢复手册》「库连不上」那一节"), payload
    extra = "；".join("%s 到 %s" % (name, payload.get(key) or "空")
                      for key, name, _sql in FRESHNESS_APPENDIX)
    # 今天开不开市一起报：长假里这一项必然显示"到位 = 09-30 那场"，
    # 不说清今天休市就会被读成"数据停了六天还是绿的"。
    if payload.get("today_open") is True:
        day_note = f"今天 {payload['today']} 是交易日"
    elif payload.get("today_open") is False:
        day_note = f"今天 {payload['today']} 休市，下一场 {payload.get('next_open') or '未知'}"
    else:
        day_note = f"今天 {payload['today']} 开不开市判不了（日历没覆盖或读不到）"
    if not behind:
        return True, (f"{expected} 全部到位（日线/评分/情绪/天梯/轮动）；{day_note}"
                      + (f"；{extra}" if extra else "")), payload
    detail = "、".join(f"{name} 停在 {d or '空'}" for name, d in behind)
    return False, (f"应到 {expected}（{day_note}），但 {detail} —— 缺的这一天要么没采要么没加工。"
                   f"先看 /system 的收盘链那一格；无桌面补数："
                   f"python -m ingest.jobs.c12_tq_day --date {expected}，然后 "
                   f"python -m ingest.jobs.e1_fill_all --date {expected} --skip tdxdl"
                   + (f"；{extra}" if extra else "")), payload


# 只收行情链专属的词。曾经把 `backup`/`sync` 也放进来，结果撞上 Windows 自带的
# MareBackup / Backup / UnifiedConsentSyncTask 等 17 个 Ready 任务 → 这项永久红，
# 一条一直红的体检等于没有体检。要窄到"只有我们的链路会用到"。
CHAIN_WORDS = re.compile(r"(?i)(financetool|vibe|astock|quantdash|tdx|tq_|daily_close|ingest|vipdoc|TataPG)")


def _tasks_csv(name_filter: str) -> tuple[list[tuple[str, str, str]], str | None]:
    """按计划任务名的匹配式取 `(名字, 状态, 上次结果)`。

    用 powershell 而不是 `schtasks`：后者在本机输出 UTF-16，直接管道给 grep 会 0 命中
    （这个坑今天踩过一次，会误判成"没有相关任务"）。
    """
    ps = (f"Get-ScheduledTask | Where-Object {{ $_.TaskName {name_filter} 'YuanShan-*' }} | "
          "ForEach-Object { $t = $_; $i = $_ | Get-ScheduledTaskInfo; "
          "[pscustomobject]@{ Name = $t.TaskName; State = $t.State; Result = $i.LastTaskResult } } "
          "| ConvertTo-Csv -NoTypeInformation")
    try:
        p = subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                           capture_output=True, timeout=90)
    except (OSError, subprocess.SubprocessError) as exc:
        return [], f"查不了（{type(exc).__name__}）"
    rows = []
    # ⚠️ 不能简单 `splitlines()[1:]` 去表头：Windows 的 stdout 是 CRLF，`splitlines()` 之后
    # 表头那行还在（cells[0] 会变成 `"Name"`），于是页面上多出一行假任务、`State` 空着。
    # 认表头那几个字面量才跳过它 —— 顺序变了也不会又漏跳。
    for line in p.stdout.decode("utf-8", "replace").splitlines():
        cells = [c.strip().strip('"') for c in line.split('","')]
        if not line.strip() or cells[0].strip('"') in ("Name", "Name\""):
            continue
        if len(cells) >= 2:
            rows.append((cells[0], cells[1], cells[2] if len(cells) > 2 else "?"))
    return rows, (None if p.returncode == 0 or rows else "powershell 非零退出，结果不可信")


def own_scheduled_tasks():
    """**本项目自己**注册了哪些计划任务。2026-10-08 之前这里恒为空（一条都没注册），
    现在有了第一条 `YuanShan-KeepAlive8910`（用户点头，只负责把 8910 拉起来，不跑数据）。
    这一格必须现读现说：文档里那句"`YuanShan-*` 零条"是从前的事实，现在已经不是了，
    而"注册了别的东西在后台跑数据"正是用户最在意的那件事。
    """
    if PLATFORM != "nt":
        return [], None
    return _tasks_csv("-like")


def foreign_scheduled_tasks():
    """列出**不叫 `YuanShan-*` 却指向行情链**的 Windows 计划任务。

    为什么要有这一项：`FinanceTool-DailyCloseSync` 被多处文档写成"从未跑过"，
    实际 09-25 16:05 真跑、写库成功、结尾强杀通达信 —— 靠记忆一定会衰，只能机器查。
    用 powershell 而不是 `schtasks`：后者在本机输出 UTF-16，直接管道给 grep 会 0 命中
    （这个坑今天踩过一次，会误判成"没有相关任务"）。
    """
    if PLATFORM != "nt":
        return [], None
    ps = ("Get-ScheduledTask | Where-Object { $_.TaskName -notlike 'YuanShan-*' } | "
          "ForEach-Object { $t = $_; $a = ($_.Actions | Select-Object -First 1); "
          "$i = $_ | Get-ScheduledTaskInfo; "
          "[pscustomobject]@{ Name = $t.TaskName; State = $t.State; "
          "Cmd = ('' + $a.Execute + ' ' + $a.Arguments); Last = $i.LastRunTime; "
          "Result = $i.LastTaskResult } } | ConvertTo-Csv -NoTypeInformation")
    try:
        p = subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                           capture_output=True, timeout=90)
    except (OSError, subprocess.SubprocessError) as exc:
        return [], f"查不了（{type(exc).__name__}）"
    text = p.stdout.decode("utf-8", "replace")
    found = []
    for line in text.splitlines():
        if CHAIN_WORDS.search(line):
            # CSV 形如 "Name","State","Cmd","Last","Result"
            cells = [c.strip().strip('"') for c in line.split('","')]
            found.append((cells[0], cells[1] if len(cells) > 1 else "?"))
    return found, (None if p.returncode == 0 or found else "powershell 非零退出，结果不可信")


def checks_ok(checks) -> bool:
    """硬项全过才算过；advisory 行只报告，不参与启动与退出码判定。"""
    return all(c["ok"] for c in checks if not c.get("advisory"))


def doctor(root: Path, port: int = 8910, phase: str = "steady") -> list[dict]:
    checks = []
    def add(name, ok, action, msg=None, advisory=False):
        row = {"name": name, "ok": bool(ok),
               "action": msg if ok and msg else ("通过" if ok else action)}
        if advisory:
            row["advisory"] = True
        checks.append(row)
    add("平台", PLATFORM in ("posix", "nt"), "请使用 macOS、Linux 或 Windows 10/11")
    py = python_at(root)
    add("Python 环境", probe([py, "-c", "import sys;sys.exit(sys.version_info < (3,10))"], root), "运行 scripts/setup")
    add("Node.js / npm", bool(shutil.which("npm")) and probe(["node", "-e", "process.exit(Number(process.versions.node.split('.')[0])>=22?0:1)"], root), "安装 Node.js 22+（包含 npm）")
    dependencies_ok, dependency_action = dependency_status(root)
    add("Python 依赖", dependencies_ok, dependency_action)
    add("依赖安装工具", probe([py, "-m", "pip", "--version"], root), "运行 scripts/setup 自动补齐 pip")
    add("依赖兼容性", probe([py, "-m", "pip", "check"], root), "运行 scripts/setup；如仍失败，检查 Python 依赖冲突")
    add("官方 Agent 引擎", probe(["node", root / "runtime/node_modules/@openai/codex/bin/codex.js", "--version"], root), "运行 scripts/setup 安装本机引擎")
    add("浏览器界面", (root / "frontend/dist/index.html").is_file(), "运行 scripts/setup 构建界面")
    # 备份项必须在「安装与源码一致」之前：setup() 用 checks[:-1] 剔除**最后一项**
    # （那时指纹还没落盘），排在指纹后面就会被它当成指纹项一起剔掉。
    backup_ok, backup_action = backup_freshness()
    add("数据库备份", backup_ok, backup_action, msg=backup_action, advisory=True)
    # 数据到位排在采集作业**之前**：两处要用同一个"最近一个已收盘交易日"，
    # 而那个数只有查库拿得到 —— 查一次复用，别为同一个数字开两次子进程。
    fresh_ok, fresh_action, fresh_payload = data_freshness(root)
    add("当日数据到位", fresh_ok, fresh_action, msg=fresh_action, advisory=True)
    expected_session = (fresh_payload or {}).get("expected")
    jobs_ok, jobs_action = collector_status(expected_session)
    add("采集作业", jobs_ok, jobs_action, msg=jobs_action, advisory=True)
    # 收盘链的自动开关单独一项，而且排在指纹项之前（同上，`checks[:-1]` 只剔最后一项）。
    chain_ok, chain_action = close_chain_switches()
    add("收盘链自动开关", chain_ok, chain_action, msg=chain_action, advisory=True)
    # 自动动过界面之后，"哪一步没做成、人怎么补"必须有个不用翻源码的入口。
    trail_ok, trail_action = tdx_gui_trail()
    add("界面动作待人工", trail_ok, trail_action, msg=trail_action, advisory=True)
    # 「看着有其实没跑」的通用闸：磁盘上声明的常驻循环 vs 进程里真活着的。
    loops_ok, loops_action = loop_inventory(root, port, phase)
    add("常驻循环完整性", loops_ok, loops_action, msg=loops_action, advisory=True)
    runtime, docs, archived, waived = external_path_refs(root)
    refs = runtime + docs
    where = []
    if runtime:
        where.append(f"代码 {len(runtime)}")
    if docs:
        where.append(f"文档 {len(docs)}")
    refs_action = ("代码与说明文档 0 处未登记的外部路径引用"
                   + (f"（已豁免 {waived} 处，理由见 scripts/external_refs_allowlist.txt）" if waived else "")
                   + (f"；另有 {archived} 处在归档区（docs/参考、legacy、ingest/legacy、.local），那正是归档件该有的样子，不计入"
                      if archived else ""))
    add("外部路径引用", not refs,
        f"命中 {len(refs)} 处{'（' + '，'.join(where) + '）' if where else ''}，"
        "参考项目被删后当场变废铁且不报错：" + "; ".join(refs[:6])
        + (f" …共 {len(refs)} 处" if len(refs) > 6 else ""),
        msg=refs_action, advisory=True)
    tasks, tasks_err = foreign_scheduled_tasks()
    own, own_err = own_scheduled_tasks()
    ready = [n for n, state in tasks if state.lower() == "ready"]
    add("外部计划任务", not ready,
        (f"指向行情链的非本项目任务里有 {len(ready)} 个还在 Ready（会被系统在后台真触发）："
         + "、".join(ready[:6])) if ready else (tasks_err or "没有非本项目的行情链任务"),
        msg=("本项目自己注册的计划任务："
             + ("、".join(f"{n}={st}" for n, st, *_r in own) if own else "0 条")
             + (" —— 看护类只负责把服务拉起来，不跑数据；跑数据的仍只有常驻循环" if own else "")
             + (f"（{own_err}）" if own_err else "")
             + "｜非本项目命名（`YuanShan-*` 之外）且指向行情链的任务："
             + ("、".join(f"{n}={st}" for n, st in tasks) if tasks else "0 个")
             + (f"（{tasks_err}）" if tasks_err else "")),
        advisory=True)
    try:
        stamp = json.loads((root / ".local/setup.json").read_text())
        current = isinstance(stamp, dict) and stamp.get("fingerprint") == fingerprint(root)
    except (OSError, ValueError):
        current = False
    add("安装与源码一致", current, "首次使用或源码更新后运行 scripts/setup")
    return checks


def print_checks(checks):
    for c in checks:
        flag = "✓" if c["ok"] else ("!" if c.get("advisory") else "✗")
        print(f"{flag} {c['name']}：{c['action']}", flush=True)
    print("体检不请求模型。AI 登录和连接测试请在浏览器的「AI 探索」页完成。", flush=True)


RUNTIME_CLI = "runtime/node_modules/@openai/codex/bin/codex.js"


def _engine_works(root: Path) -> tuple[bool, str]:
    """引擎**跑不跑得起来**，不是文件在不在。

    必须实测：`npm ci` 会先把 `node_modules` 整个删掉再重下，而平台二进制
    `@openai/codex-win32-x64` 有几十 MB。本机链路实测约 190 KB/s，这一步经常在
    `run()` 的 900 秒上限里超时 —— 于是留下一个"launcher 在、平台包没了"的半截装。
    `review_agent/runtime.py` 的就绪探测看的是 `CLI.is_file()`，那种半截装照样报"已安装"，
    一点真活才抛 `Missing optional dependency @openai/codex-win32-x64`。
    """
    node = shutil.which("node")
    cli = root / RUNTIME_CLI
    if not node or not cli.is_file():
        return False, "找不到 node 或引擎入口"
    try:
        r = subprocess.run([node, str(cli), "--version"], capture_output=True,
                           text=True, encoding="utf-8", errors="replace", timeout=60)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, f"{type(exc).__name__}"
    if r.returncode != 0:
        lines = (r.stderr or r.stdout or "").strip().splitlines()
        # 取 `Error:` 那一条，不是最后一行 —— node 最后印的是自己的版本号，不是原因。
        reason = next((l for l in lines if l.startswith("Error:")), "")
        return False, (reason or lines[-1] if lines else f"退出码 {r.returncode}")[:150]
    return True, (r.stdout or "").strip()[:40]


def _prepare_runtime(root: Path) -> None:
    """装复盘引擎，但**别把已经能用的那份删了重装**。

    `npm ci` 是不可跳过的重装：慢网下它要么等到超、要么把可用引擎换成半截装，
    表现就是复盘突然跑不通、而体检还说"已安装"。这里按 lock 的内容判定：
    lock 没变 + 引擎实测能跑 → 跳过重下；否则才真装，装完记 lock 指纹。
    失败时把标记删掉，下一次 setup 一定会重装 —— 不许有"以为装好了"的中间态。
    """
    lock = root / "runtime" / "package-lock.json"
    marker = root / ".local" / "runtime-ready.json"
    try:
        now = hashlib.sha256(lock.read_bytes()).hexdigest()
    except OSError:
        raise SetupError(f"读不到 {lock}") from None
    ok, why = _engine_works(root)
    recorded = ""
    if marker.is_file():
        try:
            recorded = str(json.loads(marker.read_text(encoding="utf-8")).get("lock") or "")
        except (OSError, ValueError):
            recorded = ""
    if ok and recorded == now:
        print(f"复盘引擎已就绪（{why}），lock 未变 → 跳过 npm ci（避免慢网重下几十 MB 平台包）",
              flush=True)
        return
    if ok and not recorded:
        # 没有标记但引擎**实测能跑**：认下这份现装的，别再删一遍。
        # 依据只能是"跑起来了"，不是"目录里有个 launcher"——半截装就是那样的。
        print(f"复盘引擎实测可用（{why}），本地没有安装标记 → 收编现装，不重装", flush=True)
    else:
        # 走到这里要么引擎不可用，要么 **lock 变了**：pinned 引擎的意义就在于此，
        # 版本号不一致就别拿旧的凑。`npm ci` 会先删后下，慢网要等几分钟。
        why2 = "lock 与已装引擎不是同一批" if ok else why
        print(f"正在准备 runtime…（{why2}）", flush=True)
        run(["npm", "ci", "--no-audit", "--no-fund"], root / "runtime")
    ok2, why2 = _engine_works(root)
    if not ok2:
        marker.unlink(missing_ok=True)
        raise SetupError(
            f"这一步之后引擎仍起不来：{why2}\n"
            "  多半是平台包 `@openai/codex-*` 没下完（几十 MB、慢网会超单步 900 秒上限）。"
            "  单独重跑：npm ci --no-audit --no-fund --prefix runtime")
    marker.parent.mkdir(mode=0o700, exist_ok=True)
    marker.write_text(json.dumps({"lock": now, "engine": why2, "at": time.time()},
                                 ensure_ascii=False), encoding="utf-8")


def setup(root: Path):
    prerequisites(root)
    py = python_at(root)
    if not py.exists():
        if (root / ".venv").exists():
            raise SetupError("现有 .venv 缺少解释器。请先将该目录移走保留，再重新安装。")
        print("正在准备独立 Python 环境…", flush=True)
        run([sys.executable, "-m", "venv", root / ".venv"], root)
    if not probe([py, "-c", "import sys;sys.exit(sys.version_info < (3,10))"], root):
        raise SetupError("现有 .venv 无法运行或 Python 版本过低。请先移走保留，再重新安装。")
    if not probe([py, "-m", "pip", "--version"], root):
        run([py, "-m", "ensurepip", "--upgrade"], root)
    print("正在安装 Python 依赖…", flush=True)
    run([py, "-m", "pip", "install", "-r", root / "requirements.txt"], root)
    _prepare_runtime(root)
    print("正在准备 frontend…", flush=True)
    run(["npm", "ci", "--no-audit", "--no-fund"], root / "frontend")
    print("正在构建浏览器界面…", flush=True)
    run(["npm", "run", "build"], root / "frontend")
    checks = doctor(root)
    print_checks(checks[:-1])
    if not checks_ok(checks[:-1]):
        raise SetupError("安装后的体检未通过，尚未标记安装完成。")
    local = root / ".local"
    local.mkdir(mode=0o700, exist_ok=True)
    tmp = local / ("setup-" + uuid.uuid4().hex + ".tmp")
    tmp.write_text(json.dumps({"fingerprint": fingerprint(root)}, indent=2))
    tmp.replace(local / "setup.json")
    print("安装完成。下次直接打开「启动 YuanShan.cmd」。" if PLATFORM == "nt" else "安装完成。下次直接打开「启动 YuanShan.command」。", flush=True)


#: 三档备份各自的阈值与「该谁负责」。写在这里而不是散在字符串里：
#: daily 是四个不可再生 schema（约 2~5 分钟），weekly 是全库（600MB+、十几分钟），
#: 只有 daily 适合挂在启动路径上兜底 —— 把全库 dump 塞进「打开网站」这个动作里，
#: 用户只会觉得「怎么每次都卡」，然后绕过启动器直接起 uvicorn，那才是真正会丢备份的用法。
BACKUP_KINDS = (("daily", 36), ("weekly", 24 * 8))
#: 最新 dump 在这之内被改过 = 有一份正在写（pg_dump 是边写边长的）。
BACKUP_IN_PROGRESS_MIN = 20
BACKUP_ROOT_DEFAULT = r"D:\finance\TataPG_backup"


def backup_kind_due(day):
    """这一天该背哪一档 —— **与 `app/ops/backup.kind_for()` 必须是同一套日历**：
    每月 1 号 monthly、周六周日 weekly、其余 daily。
    doctor 这里自己再算一遍的理由和 `collector_status` 不 import `duanxian.jobs` 一样：
    体检要在依赖还没装的时候也能跑。两处不许分叉由 `tests/test_backup_round.py` 钉。
    """
    if day.day == 1:
        return "monthly"
    return "weekly" if day.weekday() >= 5 else "daily"


def backup_last_daily_due(today=None):
    """最近一个"按排程该背 daily"的日期，**不含今天**。

    为什么不能沿用裸的 36 小时文件年龄：周末那两晚按设计背的是全库 weekly，daily 根本不该有，
    于是周日晚必然报「daily＝52 小时前，超阈值」而那一档昨晚明明跑成了（10-18 实测到的假红就是这个）。
    反过来，今天那一份要等今天那一档跑完才算"该有"，所以周一早上也不该红。
    代价是"昨天该背而没背"要到今天结束才报得出来 —— 这一项本来就是 advisory，
    宁可晚一天，也不要一条天天误报的判据（天天误报的体检项等于没有体检项）。
    """
    d = today or datetime.date.today()
    for _ in range(40):
        d -= datetime.timedelta(days=1)
        if backup_kind_due(d) == "daily":
            return d
    return None


def backup_dir():
    # `FT_BACKUP_DIR` 是 `app/config.py` 里备份真正用的那个名字，两边都要认，
    # 否则「改了 .env 但体检还在看旧目录」这种假绿就是这么来的。
    return Path(os.environ.get("VIBE_BACKUP_DIR") or os.environ.get("FT_BACKUP_DIR") or BACKUP_ROOT_DEFAULT)


#: 第二块盘（跨盘副本）的默认根目录。与 `app/config.py::BACKUP_MIRROR_DIR` 必须是同一个默认值 ——
#: doctor 不 import `app.config`（它要在依赖还没装的时候也能跑），两处一致由
#: `tests/test_backup_round.py` 现读两边钉住。
BACKUP_MIRROR_DEFAULT = r"C:\finance_backup\TataPG"


def backup_mirror_dir():
    return Path(os.environ.get("FT_BACKUP_MIRROR_DIR")
                if os.environ.get("FT_BACKUP_MIRROR_DIR") is not None
                else BACKUP_MIRROR_DEFAULT)


def backup_mirror_gap(ages=None):
    """最新那一份档在**第二块盘**上有没有同名同大小的副本。返回 `{'missing': [kind…], 'text': '…'}`。

    判的是"这一档有没有第二份"，不是"跨盘那棵树一共有几份"——
    体检原先只看 D 盘那棵树，于是 09-24 之后跨盘副本再没被写过也一切正常（10-18 实测：C 盘里还是 09-25 两份）。
    """
    ages = ages if ages is not None else backup_ages()[0]
    root = backup_mirror_dir()
    if str(root) in ("", "."):
        return {"missing": [], "text": "没配第二块盘（FT_BACKUP_MIRROR_DIR 置空＝这一档不做，不判红）"}
    if not ages:
        return {"missing": [], "text": f"本机那棵备份树判不了，跨盘这一半也不判（{root}）"}
    seen, missing = [], []
    for kind, _limit in BACKUP_KINDS:
        hours, _readable, newest, _why = ages.get(kind, (None, False, None, ""))
        if newest is None:
            continue
        twin = root / kind / newest.name
        if twin.is_file() and twin.stat().st_size == newest.stat().st_size:
            seen.append(f"{kind}＝有")
        else:
            missing.append(kind)
            seen.append(f"{kind}＝缺（{root / kind / newest.name}）")
    if not seen:
        return {"missing": [], "text": f"本机一份档都没有，谈不上跨盘（第二块盘 {root}）"}
    text = "、".join(seen)
    if missing:
        text += (" —— 缺的是「程序学会自己拷之前」背的那几份，只陈述不判红；"
                 "从现在起每背成一档就当场补一份，没拷成的那一轮自己记失败（见「采集作业」）")
    return {"missing": missing, "text": text}


def backup_ages():
    """每档的 `{kind: (年龄小时, 恢复工具读不读得开, 那个文件, 读不开的原因)}` + 一句错误。

    这是备份新旧的**唯一判据入口**：体检项与启动补跑都读它，不许各自再算一遍阈值。
    """
    root = backup_dir()
    if not root.is_dir():
        return None, f"备份目录不存在：{root}（可设 VIBE_BACKUP_DIR）"
    out, now = {}, time.time()
    for kind, _limit in BACKUP_KINDS:
        dumps = sorted((root / kind).glob("*.dump"), key=lambda p: p.stat().st_mtime, reverse=True)
        if not dumps:
            out[kind] = (None, False, None, "")
            continue
        newest = dumps[0]
        hours = (now - newest.stat().st_mtime) / 3600
        readable, why = _restorable(newest)
        out[kind] = (hours, readable, newest, why)
    return out, None


def backup_stale_kinds(ages=None, today=None):
    """返回 `(过期的档, 正在写的档)`。正在写的不算过期 —— 那是同一件事的另一种状态。

    `today` 只为单测留的口子（不传就按本机今天）。
    """
    ages = ages if ages is not None else backup_ages()[0]
    if not ages:
        return [], []
    stale, busy = [], []
    daily_due = backup_last_daily_due(today)
    for kind, limit in BACKUP_KINDS:
        hours, readable, newest, _why = ages.get(kind, (None, False, None, ""))
        if hours is None:
            stale.append(kind)
            continue
        if time.time() - newest.stat().st_mtime < BACKUP_IN_PROGRESS_MIN * 60:
            busy.append(kind)          # 有一份正在写，不该再叠一份上去
            continue
        if not readable:
            stale.append(kind)         # 读不开＝没有这份备份，跟多新无关
        elif kind == "daily":
            # 日历判据（见 `backup_last_daily_due` 那段）：daily 只看"最近一个该背它的那天有没有档"
            if daily_due is not None and datetime.date.fromtimestamp(
                    newest.stat().st_mtime) < daily_due:
                stale.append(kind)
        elif hours > limit:
            stale.append(kind)
    return stale, busy


def _backup_age_text(ages, kind):
    hours = (ages.get(kind) or (None,))[0]
    return "无" if hours is None else f"{hours:.0f} 小时前"


#: weekly/monthly 是全库级别，**不挂在启动路径上**（见 `BACKUP_KINDS` 上方那段），
#: 所以它们过期时这句话只能把人指到命令前，不能混进"补跑"的理由里 ——
#: 第一版把两个理由并列写在一句话里，读起来像"这次启动把过期的都补了"，那是假的。
def _backup_other_stale_note(ages, stale):
    others = [k for k in stale if k != "daily"]
    if not others:
        return ""
    return ("；⚠ " + "、".join(f"{k}＝{_backup_age_text(ages, k)}" for k in others)
            + " 也过期，但全库 dump 不挂在启动路径上（十几分钟、600MB+），"
            "要人工跑 python -m app.ops.backup " + others[0])


def _spawn_backup(argv, root: Path, out):
    """起备份的**唯一入口** —— 测试要打它，不要打 `subprocess.Popen`。

    为什么要在这里硬挡 pytest：2026-10-06 同一天，另一处把 `subprocess.run` 换成
    `Popen` 的改动让宿主测试里假的 `run` 再也不被调用，于是 pytest **真的**起了下载链、
    在用户桌面上打开了通达信窗口。备份同样是起子进程（pg_dump 十几分钟、写 600MB），
    所以这条通道先挡死：宁可测试红，也不能让 `pytest` 碰用户的盘和界面。
    """
    if os.environ.get("PYTEST_CURRENT_TEST"):
        raise RuntimeError("pytest 进程里不许真起备份 —— 请 monkeypatch manage._spawn_backup")
    # 参数全是常量，没有任何外部输入能拼进命令行；不起 shell。
    return subprocess.Popen(argv, cwd=root, stdout=out, stderr=out, stdin=subprocess.DEVNULL,
                            start_new_session=PLATFORM != "nt")


def backup_on_start(root: Path) -> str:
    """**不注册计划任务也能每天有一份新备份**：启动时若 daily 过期，就在后台补一次。

    为什么这样做：注册计划任务是用户画的红线（「不要注册任何计划任务，要点头」），
    而备份这件事实测拖到 238 小时 —— 库里 11GB 是唯一真资产，六天的新数据没有可恢复副本。
    这台机器每天要开网站才看得见数据，所以「启动即补」能兜住日常节奏，又不多一个没人盯的入口。

    三条边界都是今晚实测出来的，不是设计时想出来的：
      · **只补 daily**。weekly 是全库级别，触发条件与执行动作必须分得清 ——
        第一版这里写的是「因为 weekly 过期所以去补 daily」，那句话本身就是假的；
      · **正在写就不再叠一份**（实测 17:41 我的钩子与 17:42 另一份手工备份同时在长，
        两份 pg_dump 抢同一块盘，谁都不会更快）；
      · 锁文件**过期要能重新拿起来**：第一版用 `O_EXCL` 又不清旧锁，一次崩溃之后
        这条通道永久关闭 —— 那比没有这条通道更糟，因为它看起来还在工作。
    """
    if os.environ.get("VIBE_BACKUP_ON_START", "").strip().lower() in ("0", "false", "off", "no"):
        return "已关掉（VIBE_BACKUP_ON_START）"
    ages, err = backup_ages()
    if not ages:
        return f"判不了备份新旧：{err} —— 判不了不等于「备份新」"
    stale, busy = backup_stale_kinds(ages)
    others = _backup_other_stale_note(ages, stale)
    if "daily" in busy:
        return f"daily 在 {BACKUP_IN_PROGRESS_MIN} 分钟内还被写过（可能正在写、也可能刚写完），不叠第二份" + others
    if "daily" not in stale:
        return f"daily 不旧（{_backup_age_text(ages, 'daily')}），不补跑" + others
    local = root / ".local"
    log, lock = local / "backup_on_start.log", local / "backup_on_start.daily.lock"
    local.mkdir(mode=0o700, exist_ok=True)
    try:
        if lock.exists():
            if time.time() - lock.stat().st_mtime < 1800:
                return "已有一次补排在跑（锁 30 分钟内），这次不重复起" + others
            lock.unlink()                   # 过期锁 = 上一次已经死了，不能把通道永久焊死
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.write(fd, str(os.getpid()).encode())
        os.close(fd)
    except OSError as exc:
        return f"想补跑但拿不到锁（{type(exc).__name__}）：{exc}"
    why = f"daily＝{_backup_age_text(ages, 'daily')}"
    argv = [str(python_at(root)), "-X", "utf8", "-m", "app.ops.backup", "daily"]
    try:
        out_fd = os.open(log, os.O_CREAT | os.O_APPEND | os.O_WRONLY, 0o600)
        with os.fdopen(out_fd, "a") as out:
            out.write("\n===== 启动补跑 daily（触发理由：" + why + "）=====\n")
            out.flush()
            os.fsync(out.fileno())
            _spawn_backup(argv, root, out)
    except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
        # 备份起不起来都**不该把网站带崩**：这条钩子挂在启动路径最后，抛出去就是"打开网站打不开"。
        return f"想补跑但起不来（{type(exc).__name__}）：{exc}"
    return (f"已在后台补跑一次 daily（触发理由：{why}；日志 {log}）" + others
            + "；判据见《02-PostgreSQL与备份》")


def healthy(url: str, launch_id: str) -> bool:
    # Loopback traffic must never use the user's HTTP proxy.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(url + "/api/astock/health", timeout=1) as r:
            body = json.loads(r.read(10000))
        return (body.get("service") == "yuanshan" and body.get("launch_id") == launch_id
                and body.get("ready") is True)
    except (OSError, ValueError, urllib.error.URLError):
        return False


def stop(child):
    if child.poll() is not None:
        return
    if PLATFORM == "nt":
        # child is the Job-owning guard, never the bare Uvicorn process.
        child.kill()
        child.wait(timeout=10)
        return
    try:
        os.killpg(child.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    try:
        child.wait(timeout=15)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(child.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        child.wait(timeout=5)


def start(root: Path, port: int, browser: bool, timeout=60):
    # 绑定地址来自 `netconfig`（只用标准库 —— 这一步发生在"依赖装没装好"之前，
    # 不能拉 duanxian 那一串三方包）。默认只绑回环；开了局域网访问才 0.0.0.0。
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    import netconfig
    net_cfg = netconfig.load()
    net_bind_host = netconfig.bind_host(net_cfg)
    checks = doctor(root, port, phase="startup")
    print_checks(checks)
    if not checks_ok(checks):
        raise SetupError("尚未就绪。请运行 scripts/setup，或双击启动文件自动准备环境。")
    try:
        with socket.socket() as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind((net_bind_host, port))
    except OSError:
        raise SetupError(f"端口 {port} 已被占用。请关闭原服务，或使用 scripts/start --port 8911。") from None
    launch_id = uuid.uuid4().hex
    env = {**os.environ, "ASTOCK_LAUNCH_ID": launch_id, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}
    log = root / ".local/startup.log"
    log.parent.mkdir(mode=0o700, exist_ok=True)
    fd = os.open(log, os.O_CREAT | os.O_APPEND | os.O_WRONLY, 0o600)
    with os.fdopen(fd, "a") as output:
        command = [str(python_at(root)), "-m", "uvicorn", "server:app", "--host", net_bind_host,
                   "--port", str(port), "--no-access-log"]
        if PLATFORM == "nt":
            # No time limit for the foreground web service; parent death remains
            # guarded, including forcibly closing the Windows terminal window.
            # The stdlib guard must bypass the Windows venv redirector process.
            command = [getattr(sys, "_base_executable", sys.executable), str(root / "review_agent/engine_guard.py"),
                       "0", str(os.getpid()), *command]
        child = subprocess.Popen(command, cwd=root, env=env, stdout=output, stderr=output,
                                 start_new_session=PLATFORM != "nt")
        url = f"http://127.0.0.1:{port}"
        try:
            deadline = time.monotonic() + timeout
            while child.poll() is None and time.monotonic() < deadline:
                if healthy(url, launch_id):
                    break
                time.sleep(.3)
            else:
                raise SetupError(f"服务未能启动。已有服务占用数据目录或启动失败，请查看 {log}")
            print(f"已启动：{url}\n保留此窗口；按 Control+C 安全停止。日志：{log}", flush=True)
            # 报**实际绑定**而不是配置说什么：两者不一致时（改了没重启）说假话最难查
            if net_bind_host == "0.0.0.0":
                allow = "、".join(net_cfg["allowlist"]) or "（空 —— 除本机外谁都进不来）"
                ips = "、".join(f"http://{ip}:{port}" for ip in netconfig.local_ipv4s()) or "（读不到本机局域网地址）"
                print(f"局域网访问：已开放，绑定 0.0.0.0:{port}；白名单 {len(net_cfg['allowlist'])} 台：{allow}\n"
                      f"  对面可用：{ips}\n"
                      f"  ⚠ 白名单外的机器一律 403（连页面都拿不到）。对面连不上先查 Windows 防火墙是否放行 {port}。",
                      flush=True)
            else:
                print(f"局域网访问：未开放（只允许 127.0.0.1 本机访问；"
                      f"要开在「系统配置」页勾选，勾完必须重启才换绑定地址）", flush=True)
            # 启动顺手把"每天一份可恢复备份"这件事兜住（不新建计划任务）。
            # 放在健康检查之后：备份起不起来都不该影响网站本身。
            print(f"备份补跑：{backup_on_start(root)}", flush=True)
            if browser:
                webbrowser.open(url)
            code = child.wait()
            if code:
                raise SetupError(f"服务意外退出，请查看 {log}")
        finally:
            stop(child)


def main():
    parser = argparse.ArgumentParser(description="YuanShan 安装、体检与启动")
    parser.add_argument("command", choices=["setup", "doctor", "start", "auto"])
    parser.add_argument("--port", type=int, default=8910)
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    if not 1024 <= args.port <= 65535:
        parser.error("端口请使用 1024–65535")
    def interrupted(_sig, _frame):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, interrupted)
    if hasattr(signal, "SIGHUP"):
        signal.signal(signal.SIGHUP, interrupted)
    try:
        if args.command == "doctor":
            checks = doctor(ROOT, args.port)
            if args.json:
                print(json.dumps({"ok": checks_ok(checks), "checks": checks}, ensure_ascii=False))
            else:
                print_checks(checks)
            return 0 if checks_ok(checks) else 1
        prerequisites(ROOT)
        if args.command == "setup" or (args.command == "auto" and not checks_ok(doctor(ROOT, args.port))):
            setup(ROOT)
        if args.command != "setup":
            start(ROOT, args.port, not args.no_browser)
        return 0
    except KeyboardInterrupt:
        print("已停止启动器与本次服务。", flush=True)
        return 130
    except SetupError as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
