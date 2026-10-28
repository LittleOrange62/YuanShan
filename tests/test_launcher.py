import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import signal
import time

import pytest

from scripts import manage

REAL_CONNECT = socket.socket.connect


@pytest.fixture(autouse=True)
def _never_touch_real_backup_or_desktop(monkeypatch):
    """启动器测试一律不许碰真备份目录。

    `start()` 末尾挂着「daily 过期就后台补一份」的钩子；不关它的话，测试会不会真起
    pg_dump 就取决于**跑到这一刻磁盘上的备份新不新** —— 那种测试换个日子就变义。
    （`_spawn_backup` 里还有一道 pytest 硬挡，两道都要有：环境变量管的是确定性，硬挡管的是兜底。）
    """
    monkeypatch.setenv("VIBE_BACKUP_ON_START", "0")
    monkeypatch.setenv("VIBE_BACKUP_DIR", "Z:/no-such-backup-dir")


@pytest.mark.parametrize("stdout,code,expected", [
    ('ASTOCK_IMPORTS=[]', 0, True),
    ('ASTOCK_IMPORTS=["akshare: ModuleNotFoundError"]', 0, False),
    ('ASTOCK_IMPORTS=[]', 1, False),
    ('unrelated output', 0, False),
    ('ASTOCK_IMPORTS={}', 0, False),
])
def test_dependency_diagnostic_requires_completed_explicit_result(tmp_path, monkeypatch, stdout, code, expected):
    monkeypatch.setattr(manage.subprocess, "run", lambda *a, **k: subprocess.CompletedProcess([], code, stdout, "SECRET_CANARY"))
    ok, action = manage.dependency_status(tmp_path)
    assert ok is expected
    assert "SECRET_CANARY" not in action


def test_dependency_timeout_is_not_reported_as_missing_package(tmp_path, monkeypatch):
    def timeout(*a, **k):
        raise subprocess.TimeoutExpired([], 60)
    monkeypatch.setattr(manage.subprocess, "run", timeout)
    ok, action = manage.dependency_status(tmp_path)
    assert not ok and "一分钟" in action


def layout(root):
    for name in ("requirements.txt", "runtime/package.json", "runtime/package-lock.json",
                 "frontend/package-lock.json", "frontend/src/main.ts", "frontend/dist/index.html"):
        p = root / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("{}")


def test_source_changes_invalidate_install_but_generated_files_do_not(tmp_path):
    layout(tmp_path)
    original = manage.fingerprint(tmp_path)
    (tmp_path / "frontend/dist/index.html").write_text("built")
    (tmp_path / "frontend/temp.tsbuildinfo").write_text("generated")
    # 跑浏览器验收脚本会留下 .pyc（2026-09-26 实测到 `frontend/test/__pycache__/
    # browser-acceptance.cpython-314.pyc`）。它是再生成的产物，算进指纹就等于
    # "谁跑一次验收，下次双击启动器白等 8~12 分钟 setup"。
    pyc = tmp_path / "frontend/test/__pycache__/browser-acceptance.cpython-314.pyc"
    pyc.parent.mkdir(parents=True, exist_ok=True)
    pyc.write_bytes(b"\x00generated")
    (tmp_path / "frontend/test/loose.pyc").write_bytes(b"\x00generated")
    assert manage.fingerprint(tmp_path) == original
    (tmp_path / "frontend/src/main.ts").write_text("changed")
    assert manage.fingerprint(tmp_path) != original


def test_doctor_missing_and_outdated_install_and_dependency_conflict(tmp_path, monkeypatch):
    layout(tmp_path)
    monkeypatch.setattr(manage, "probe", lambda *a, **k: True)
    monkeypatch.setattr(manage, "dependency_status", lambda _: (True, ""))
    monkeypatch.setattr(manage.shutil, "which", lambda _: "/npm")
    assert not manage.doctor(tmp_path)[-1]["ok"]
    (tmp_path / ".local").mkdir()
    (tmp_path / ".local/setup.json").write_text(json.dumps({"fingerprint": manage.fingerprint(tmp_path)}))
    assert all(c["ok"] for c in manage.doctor(tmp_path) if not c.get("advisory"))
    monkeypatch.setattr(manage, "probe", lambda argv, *a, **k: "check" not in argv)
    assert not next(c for c in manage.doctor(tmp_path) if c["name"] == "依赖兼容性")["ok"]
    (tmp_path / "requirements.txt").write_text("changed")
    assert not manage.doctor(tmp_path)[-1]["ok"]


def test_stale_backup_is_advisory_and_fingerprint_stays_last(tmp_path, monkeypatch):
    """备份过期只提示；且「安装与源码一致」必须仍是最后一项。"""
    layout(tmp_path)
    monkeypatch.setattr(manage, "probe", lambda *a, **k: True)
    monkeypatch.setattr(manage, "dependency_status", lambda _: (True, ""))
    monkeypatch.setattr(manage.shutil, "which", lambda _: "/npm")
    (tmp_path / ".local").mkdir()
    (tmp_path / ".local/setup.json").write_text(
        json.dumps({"fingerprint": manage.fingerprint(tmp_path)}))
    monkeypatch.setenv("VIBE_BACKUP_DIR", str(tmp_path / "no-backups-here"))
    checks = manage.doctor(tmp_path)
    backup = next(c for c in checks if c["name"] == "数据库备份")
    assert not backup["ok"] and backup["advisory"]
    assert manage.checks_ok(checks)
    # setup() 用 checks[:-1] 剔除尚未落盘的指纹项；任何排在它后面的新项都会被它误剔。
    assert checks[-1]["name"] == "安装与源码一致"


def test_collector_item_is_advisory_and_reports_streak(tmp_path, monkeypatch):
    """采集作业连败只提示，不拦启动；且指纹项仍须是最后一项。

    ⚠️ 断言按 2026-09-28 的新读法：**news 的 `state.json` 只代表"手动触发过"**
    （常驻轮询根本不写它），所以这里"有连败、没有任何 jsonl 产出"必须是红的，
    但措辞要说清依据是手动记录，不能让人以为轮询失败了 9 次。
    """
    home = tmp_path / "datahome"
    (home / "jobs").mkdir(parents=True)
    (home / "jobs" / "state.json").write_text(
        json.dumps({"news": {"last_run_at": "x", "last_ok_at": None,
                             "last_status": "fail", "consecutive_failures": 9,
                             "last_error": "源挂了"}}), encoding="utf-8")
    monkeypatch.setenv("ASTOCK_DATA_HOME", str(home))
    ok, action = manage.collector_status()
    assert not ok and "连败9" in action and "手动触发" in action and str(home) in action
    layout(tmp_path)
    monkeypatch.setattr(manage, "probe", lambda *a, **k: True)
    monkeypatch.setattr(manage, "dependency_status", lambda _: (True, ""))
    monkeypatch.setattr(manage.shutil, "which", lambda _: "/npm")
    (tmp_path / ".local").mkdir()
    (tmp_path / ".local/setup.json").write_text(
        json.dumps({"fingerprint": manage.fingerprint(tmp_path)}))
    monkeypatch.setenv("VIBE_BACKUP_DIR", str(tmp_path / "no-backups-here"))
    checks = manage.doctor(tmp_path)
    assert not next(c for c in checks if c["name"] == "采集作业")["ok"]
    assert manage.checks_ok(checks)          # 红着的采集项不许拦启动
    assert checks[-1]["name"] == "安装与源码一致"


def test_采集项判在不在跑要看文件侧不能只看state(tmp_path, monkeypatch):
    """这条钉的是今天改掉的那个假故障：快讯的 `state.json` 那一行**过去只由手动触发写**
    （循环自己每 30 秒往 jsonl 追加却不记账），体检只看前者，就对着一份刚刚还在变大的
    文件报"最近成功停在 30 小时前"。循环侧现在也记账了，但**体检保留两套口径互验**：
    news 一律以 jsonl 的 mtime 为准。一条常年误报的体检项等于没有体检项。"""
    home = tmp_path / "datahome"
    (home / "jobs").mkdir(parents=True)
    (home / "news").mkdir(parents=True)
    (home / "news" / "news-2026-09-28.jsonl").write_text('{"id":1}\n', encoding="utf-8")
    (home / "jobs" / "state.json").write_text(
        json.dumps({"news": {"last_run_at": "2026-09-26 18:17:46",
                             "last_ok_at": "2026-09-26 18:17:46",
                             "last_status": "ok", "consecutive_failures": 0}}),
        encoding="utf-8")
    monkeypatch.setenv("ASTOCK_DATA_HOME", str(home))
    ok, action = manage.collector_status()
    assert ok and "文件侧" in action and "分钟前" in action
    assert "2026-09-26" not in action        # 那份陈旧的手动记录不再被当成"最近成功"


@pytest.mark.parametrize("value", [[], None, "broken", 42, {"fingerprint": []}])
def test_corrupt_marker_can_be_repaired(tmp_path, monkeypatch, value):
    layout(tmp_path)
    (tmp_path / ".local").mkdir()
    marker = tmp_path / ".local/setup.json"
    marker.write_text(json.dumps(value))
    monkeypatch.setattr(manage, "probe", lambda *a, **k: True)
    monkeypatch.setattr(manage, "dependency_status", lambda _: (True, ""))
    monkeypatch.setattr(manage.shutil, "which", lambda _: "/npm")
    monkeypatch.setattr(manage, "prerequisites", lambda _: None)
    monkeypatch.setattr(manage, "run", lambda *a, **k: None)
    # 本测的是"标记坏了能修好"，不是引擎装没装。`_engine_works` 会真起 node 探版本，
    # 沙箱里没有引擎，不stub 就会把引擎缺失当成 setup 失败（那是另一件事，另测）。
    monkeypatch.setattr(manage, "_engine_works", lambda root: (True, "stub"))
    assert not manage.doctor(tmp_path)[-1]["ok"]
    manage.setup(tmp_path)
    assert manage.doctor(tmp_path)[-1]["ok"]


def test_failed_install_never_marks_success(tmp_path, monkeypatch):
    layout(tmp_path)
    monkeypatch.setattr(manage, "prerequisites", lambda _: None)
    monkeypatch.setattr(manage, "_engine_works", lambda root: (True, "stub"))
    def fail(*a, **k):
        raise manage.SetupError("installation failed")
    monkeypatch.setattr(manage, "run", fail)
    with pytest.raises(manage.SetupError):
        manage.setup(tmp_path)
    assert not (tmp_path / ".local/setup.json").exists()


def test_engine_ready_skips_the_destructive_reinstall(tmp_path, monkeypatch):
    """慢网实证的坑：`npm ci` 先删 `node_modules` 再重下几十 MB 平台包，
    超时就留下"launcher 在、平台包没了"的半截装 —— 复盘当场废掉。
    引擎**实测能跑**时不许再删它。"""
    layout(tmp_path)
    calls = []
    monkeypatch.setattr(manage, "_engine_works", lambda root: (True, "codex 0.153.4"))
    monkeypatch.setattr(manage, "run", lambda argv, root, **k: calls.append(list(argv)))
    manage._prepare_runtime(tmp_path)                    # 没标记但能跑 → 收编，不删
    assert calls == [], "引擎能跑还重装 = 把可用引擎删一遍再赌网络"
    assert (tmp_path / ".local" / "runtime-ready.json").is_file(), "收编后要记下 lock"
    manage._prepare_runtime(tmp_path)                    # 有标记 + lock 未变 → 跳过
    assert calls == []


def test_broken_engine_gets_reinstalled(tmp_path, monkeypatch):
    """不能跑就必须装 —— 记下的标记也不能让它蒙混过关。"""
    layout(tmp_path)
    calls = []
    monkeypatch.setattr(manage, "_engine_works", lambda root: (False, "Missing optional dependency"))
    monkeypatch.setattr(manage, "run", lambda argv, root, **k: calls.append(list(argv)))
    with pytest.raises(manage.SetupError):              # 装完仍不能跑 → 报出来，别装死
        manage._prepare_runtime(tmp_path)
    assert calls and calls[0][0] == "npm"


def test_lock_change_forces_reinstall_even_if_engine_works(tmp_path, monkeypatch):
    layout(tmp_path)
    calls = []
    monkeypatch.setattr(manage, "_engine_works", lambda root: (True, "codex 0.153.4"))
    monkeypatch.setattr(manage, "run", lambda argv, root, **k: calls.append(list(argv)))
    manage._prepare_runtime(tmp_path)                    # 收编 + 记标记
    assert calls == []
    (tmp_path / "runtime" / "package-lock.json").write_text('{"lockfileVersion": 3}')
    manage._prepare_runtime(tmp_path)                    # lock 变了 → pinned 版本必须重来
    assert calls, "lock 变了却不重装 = 跑着与 lockfile 不一致的引擎"


def test_port_collision_never_opens_other_service(tmp_path, monkeypatch):
    monkeypatch.setattr(manage, "doctor", lambda *a, **k: [{"name": "test", "ok": True, "action": "ok"}])
    def forbidden(*a, **k):
        pytest.fail("must not launch/open a different service")
    monkeypatch.setattr(manage.subprocess, "Popen", forbidden)
    monkeypatch.setattr(manage.webbrowser, "open", forbidden)
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0)); sock.listen()
        with pytest.raises(manage.SetupError, match="占用"):
            manage.start(tmp_path, sock.getsockname()[1], True)


def test_recently_closed_port_can_be_reused(tmp_path, monkeypatch):
    monkeypatch.setattr(manage, "doctor", lambda *a, **k: [{"name": "test", "ok": True, "action": "ok"}])
    with socket.socket() as server:
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.bind(("127.0.0.1", 0)); server.listen()
        port = server.getsockname()[1]
        with socket.socket() as client:
            REAL_CONNECT(client, ("127.0.0.1", port))
            accepted, _ = server.accept()
            accepted.close()
            assert client.recv(1) == b""
    def reached_launch(*a, **k):
        raise manage.SetupError("port check passed")
    monkeypatch.setattr(manage.subprocess, "Popen", reached_launch)
    with pytest.raises(manage.SetupError, match="port check passed"):
        manage.start(tmp_path, port, False)


@pytest.mark.skipif(os.name != "posix", reason="POSIX launcher")
def test_real_http_ready_identity_and_interrupt_terminate_child(tmp_path, monkeypatch):
    # Run an actual child HTTP server. Doctor alone cannot prove readiness,
    # and a preexisting/other product's response must never open the browser.
    monkeypatch.setattr(manage, "doctor", lambda *a, **k: [{"name": "test", "ok": True, "action": "ok"}])
    def loopback_only(sock, address):
        assert address[0] == "127.0.0.1"
        return REAL_CONNECT(sock, address)
    monkeypatch.setattr(socket.socket, "connect", loopback_only)
    real_popen = subprocess.Popen
    children = []
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0)); port = sock.getsockname()[1]
    code = '''
from http.server import BaseHTTPRequestHandler,HTTPServer
import json,os,sys
class H(BaseHTTPRequestHandler):
 def do_GET(self):
  self.send_response(200);self.end_headers()
  self.wfile.write(json.dumps({'service':'yuanshan','launch_id':os.environ['ASTOCK_LAUNCH_ID'],'ready':True}).encode())
HTTPServer(('127.0.0.1',int(sys.argv[1])),H).serve_forever()
'''
    def spawn(argv, **kwargs):
        child = real_popen([sys.executable, "-u", "-c", code, str(port)], **kwargs)
        children.append(child)
        return child
    monkeypatch.setattr(manage.subprocess, "Popen", spawn)
    def opened(url):
        assert not manage.healthy(url, "different-launch")
        assert children[0].poll() is None
        raise KeyboardInterrupt
    monkeypatch.setattr(manage.webbrowser, "open", opened)
    with pytest.raises(KeyboardInterrupt):
        manage.start(tmp_path, port, True, timeout=5)
    assert children[0].poll() is not None
    assert not manage.healthy(f"http://127.0.0.1:{port}", "different-launch")


@pytest.mark.skipif(os.name != "posix", reason="POSIX terminal hangup")
def test_terminal_hangup_stops_independent_service(tmp_path):
    # Enter through main(): a manual KeyboardInterrupt in start() does not test
    # OS signal handling when a Terminal window is closed.
    pidfile = tmp_path / "child.pid"
    code = '''
from scripts import manage
from pathlib import Path
import sys,subprocess
manage.ROOT=Path(sys.argv[1])
manage.prerequisites=lambda _: None
manage.doctor=lambda *a,**k: [{'name':'test','ok':True,'action':'ok'}]
manage.healthy=lambda *a: True
real=subprocess.Popen
def spawn(argv,**kwargs):
 p=real([sys.executable,'-c','import time;time.sleep(120)'],**kwargs)
 (manage.ROOT/'child.pid').write_text(str(p.pid))
 return p
manage.subprocess.Popen=spawn
sys.argv=['manage','start','--no-browser','--port',sys.argv[2]]
raise SystemExit(manage.main())
'''
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0)); port = sock.getsockname()[1]
    proc = subprocess.Popen([sys.executable, "-c", code, str(tmp_path), str(port)], cwd=manage.ROOT,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    child = None
    try:
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and not pidfile.exists() and proc.poll() is None:
            time.sleep(.02)
        assert pidfile.exists()
        child = int(pidfile.read_text(encoding="utf-8"))
        proc.send_signal(signal.SIGHUP)
        assert proc.wait(timeout=20) == 130
        with pytest.raises(ProcessLookupError):
            os.kill(child, 0)
    finally:
        manage.stop(proc)
        if child is not None:
            try:
                os.killpg(child, signal.SIGKILL)
            except ProcessLookupError:
                pass


def _chain_home(tmp_path, cfg):
    home = tmp_path / "datahome"
    (home / "jobs").mkdir(parents=True)
    (home / "jobs" / "config.json").write_text(json.dumps(cfg), encoding="utf-8")
    return home


def test_收盘链被关且没人署名必须报红(tmp_path, monkeypatch):
    """09-28 实测：两个会话在同一天里把 `daily_close`/`fill_all` 来回开关三次，
    页面上只留下"已停用"，与"用户自己不想跑"完全同形 —— 而当日不采就是永久少一天。
    所以"关"要求署名：写了 note 就只报状态，没写就红。"""
    home = _chain_home(tmp_path, {"daily_close": {"enabled": False}, "fill_all": {"enabled": False}})
    monkeypatch.setenv("ASTOCK_DATA_HOME", str(home))
    ok, action = manage.close_chain_switches()
    assert not ok
    assert "没人说明" in action and "daily_close" in action and "fill_all" in action


def test_署名过的停用只报状态不拦人(tmp_path, monkeypatch):
    """用户主动停一天是完全正常的用法 —— 体检只能说清是谁停的，不能拦、不能判红。"""
    home = _chain_home(tmp_path, {"daily_close": {"enabled": False, "note": "用户裁定手动跑"},
                                 "fill_all": {"enabled": True}})
    monkeypatch.setenv("ASTOCK_DATA_HOME", str(home))
    ok, action = manage.close_chain_switches()
    assert ok, action
    assert "用户裁定手动跑" in action and "会自动跑：fill_all" in action


def test_有留痕但没写理由的停用只报出不判红(tmp_path, monkeypatch):
    """`_changed_by` 是每次写入自动盖章的（`duanxian/jobs.py`），所以"知道是谁关的"
    不再依赖人记得写 note。这一档介于两者之间：**报出来、点名是谁什么时候改的，但不判红** ——
    红只留给"没人认领的关闭"。这条测试钉的就是这个分界，别把它顺手改成红。"""
    home = _chain_home(tmp_path, {"daily_close": {"enabled": False, "_changed_at": "2026-09-28 13:03:17",
                                                 "_changed_by": "cli:jobs.py disable"},
                                 "fill_all": {"enabled": True}})
    monkeypatch.setenv("ASTOCK_DATA_HOME", str(home))
    ok, action = manage.close_chain_switches()
    assert ok, action
    assert "有留痕但没写理由" in action and "cli:jobs.py disable" in action
    assert "会自动跑：fill_all" in action


def test_配置缺失等于全开(tmp_path, monkeypatch):
    """默认值只有一份语义：没写过 config.json = 按 CLOSE_DEFAULTS 开着。"""
    home = tmp_path / "datahome"
    (home / "jobs").mkdir(parents=True)
    monkeypatch.setenv("ASTOCK_DATA_HOME", str(home))
    ok, action = manage.close_chain_switches()
    assert ok and "会自动跑：daily_close、fill_all" in action


def test_收盘链项是advisory且没把指纹项挤到最后(tmp_path, monkeypatch):
    """setup() 用 `checks[:-1]` 剔指纹项：新项排在指纹之后就会被当成指纹一起剔掉，
    等于把"安装与源码一致"这项静默删了。"""
    home = _chain_home(tmp_path, {"daily_close": {"enabled": False}})
    monkeypatch.setenv("ASTOCK_DATA_HOME", str(home))
    layout(tmp_path)
    monkeypatch.setattr(manage, "probe", lambda *a, **k: True)
    monkeypatch.setattr(manage, "dependency_status", lambda _: (True, ""))
    monkeypatch.setattr(manage.shutil, "which", lambda _: "/npm")
    (tmp_path / ".local").mkdir()
    (tmp_path / ".local/setup.json").write_text(
        json.dumps({"fingerprint": manage.fingerprint(tmp_path)}))
    monkeypatch.setenv("VIBE_BACKUP_DIR", str(tmp_path / "no-backups-here"))
    checks = manage.doctor(tmp_path)
    row = next(c for c in checks if c["name"] == "收盘链自动开关")
    assert not row["ok"] and row.get("advisory") is True
    assert manage.checks_ok(checks), "关着的收盘链不许拦住启动"
    assert checks[-1]["name"] == "安装与源码一致"
