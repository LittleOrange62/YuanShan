"""引擎就绪判据：**跑不跑得起来**，不是文件在不在；实测结论必须缓存。

背景（2026-09-25 实测）：`setup` 对 `runtime/` 跑 `npm ci`（先删 `node_modules` 再重下，
平台包 `@openai/codex-win32-x64` 几十 MB、本机链路 ~190 KB/s），中途失败就留下
"launcher 在、平台包没了"的半截装。旧判据 `CLI.is_file()` 照样报"已装"，体检绿灯、
页面显示引擎已装，用户点一次真活才抛 `Missing optional dependency`。
这几条测试钉住三件事：判据是实测、报错原文要交出去、健康检查不能每次起一个 node。
"""
from __future__ import annotations

import json

import pytest

from review_agent import runtime as rt


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    """把缓存文件与引擎路径都搬到临时目录，别碰真的 runtime/。"""
    cache = tmp_path / "engine-check.json"
    packages = tmp_path / "runtime/node_modules/@openai"
    cli = packages / "codex/bin/codex.js"
    cli.parent.mkdir(parents=True)
    cli.write_text("// launcher", encoding="utf-8")
    (packages / "codex-win32-x64").mkdir()
    lock = tmp_path / "runtime/package-lock.json"
    lock.parent.mkdir(exist_ok=True)
    lock.write_text('{"name":"runtime"}', encoding="utf-8")
    monkeypatch.setattr(rt, "CLI", cli)
    monkeypatch.setattr(rt, "LOCK", lock)
    monkeypatch.setattr(rt, "ENGINE_CHECK_FILE", cache)
    return {"cache": cache, "cli": cli, "packages": packages, "lock": lock}


def test_probe_runs_the_engine_and_caches_the_verdict(isolated, monkeypatch):
    calls = []
    monkeypatch.setattr(rt.shutil, "which", lambda name: "node")
    monkeypatch.setattr(rt, "_engine_run_once", lambda: (calls.append(1) or (True, "codex-cli 0.153.4")))
    assert rt.engine_probe() == (True, "codex-cli 0.153.4")
    assert rt.engine_probe() == (True, "codex-cli 0.153.4")
    assert len(calls) == 1, "健康检查路径上第二次调用必须吃缓存，不能再起 node"
    assert json.loads(isolated["cache"].read_text(encoding="utf-8"))["ok"] is True


def test_half_install_reports_false_with_the_real_error(isolated, monkeypatch):
    """半截装的原文要原样递出去 —— "未安装"和"平台包没下完"是两种行动。"""
    monkeypatch.setattr(rt.shutil, "which", lambda name: "node")
    why = "Error: Missing optional dependency @openai/codex-win32-x64"
    monkeypatch.setattr(rt, "_engine_run_once", lambda: (False, why))
    ok, reason = rt.engine_probe()
    assert ok is False and "Missing optional dependency" in reason
    assert rt.engine_probe() == (False, why)      # 失败结论也缓存（只留 ENGINE_BAD_TTL 秒）


def test_cache_expires_sooner_for_failures(isolated, monkeypatch):
    monkeypatch.setattr(rt.shutil, "which", lambda name: "node")
    calls = []
    monkeypatch.setattr(rt, "_engine_run_once", lambda: (calls.append(1) or (False, "Error: boom")))
    rt.engine_probe()
    monkeypatch.setattr(rt.time, "time", lambda: 10 ** 12)   # 过了 60 秒
    rt.engine_probe()
    assert len(calls) == 2, "失败结论不该像成功结论那样留几小时"


def test_a_missing_package_invalidates_a_cached_success(isolated, monkeypatch):
    """关键回归：平台包消失时 lock 与 launcher 都没动。

    只按"lock + launcher mtime"做缓存键的话，缓存会继续报"可用"，
    比原来的 `is_file()` 更会骗人 —— 所以已装包清单必须在键里。
    """
    monkeypatch.setattr(rt.shutil, "which", lambda name: "node")
    calls = []
    monkeypatch.setattr(rt, "_engine_run_once", lambda: (calls.append(1) or (True, "codex-cli 0.153.4")))
    assert rt.engine_probe()[0] is True
    assert len(calls) == 1
    (isolated["packages"] / "codex-win32-x64").rename(isolated["packages"] / "codex-win32-x64.disabled")
    assert rt.engine_probe()[0] is True          # 缓存键已变 → 必须重测（这里仍返回桩的成功）
    assert len(calls) == 2, "包清单变了就必须重跑实测，不能吃旧的成功结论"


def test_identity_changes_with_lock_and_launcher(isolated, monkeypatch):
    monkeypatch.setattr(rt.shutil, "which", lambda name: "node")
    before = rt._engine_identity()
    isolated["lock"].write_text('{"name":"runtime","lockfileVersion":9}', encoding="utf-8")
    after_lock = rt._engine_identity()
    assert before != after_lock
    import os
    stat = isolated["cli"].stat()
    os.utime(isolated["cli"], ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000))
    assert after_lock != rt._engine_identity()


def test_probe_error_parsing_takes_the_error_line_not_nodes_own_output(isolated, monkeypatch):
    """node 失败时最后印的是它自己的行，原因在 `Error:` 那一条。"""
    monkeypatch.setattr(rt.shutil, "which", lambda name: "node")

    class Done:
        returncode = 1
        stdout = ""
        stderr = ("node.exe : ... \n"
                  "Error: Missing optional dependency @openai/codex-win32-x64\n"
                  "    at Module._resolveFilename (node:internal/modules/cjs/loader)")

    monkeypatch.setattr(rt.subprocess, "run", lambda *a, **k: Done())
    ok, why = rt._engine_run_once()
    assert ok is False and why.startswith("Error: Missing optional dependency")


def test_timeout_is_reported_as_timeout_not_as_missing(isolated, monkeypatch):
    import subprocess

    monkeypatch.setattr(rt.shutil, "which", lambda name: "node")

    def hang(*args, **kwargs):
        raise subprocess.TimeoutExpired(args[0] if args else ["node"], rt.ENGINE_PROBE_TIMEOUT)

    monkeypatch.setattr(rt.subprocess, "run", hang)
    ok, why = rt._engine_run_once()
    assert ok is False and "秒没返回" in why


def test_engine_command_refuses_a_half_install(isolated, monkeypatch):
    from review_agent.evidence import EvidenceError

    monkeypatch.setattr(rt.shutil, "which", lambda name: "node")
    monkeypatch.setattr(rt, "engine_probe", lambda **kw: (False, "Error: Missing optional dependency @openai/codex-win32-x64"))
    with pytest.raises(EvidenceError) as caught:
        rt.engine_command()
    assert "Missing optional dependency" in str(caught.value)
    assert "npm ci --prefix runtime" in str(caught.value)   # 给出的必须是能做的动作


def test_login_verdict_is_cached_and_invalidated(tmp_path, monkeypatch):
    """登录态也进同一套指纹缓存，但登录动作一结束就必须作废。"""
    monkeypatch.setattr(rt, "LOGIN_CHECK_FILE", tmp_path / "engine-login.json")
    monkeypatch.setattr(rt, "_engine_identity", lambda: "same-key")
    spawns = []

    class Done:
        returncode = 0
        stdout = b"ChatGPT"
        stderr = b""

    def fake_run(cmd, **kwargs):
        spawns.append(cmd)
        return Done()

    monkeypatch.setattr(rt, "engine_command", lambda **kw: ["node", "codex.js"])
    monkeypatch.setattr(rt.subprocess, "run", fake_run)
    home = tmp_path / "codex-home"
    assert rt.login_probe(home) [0] is True
    logged, age = rt.login_probe(home)
    assert logged is True and age >= 0 and len(spawns) == 1, "第二次不该再 spawn 一次 node"
    rt.clear_login_cache()
    rt.login_probe(home)
    assert len(spawns) == 2, "clear_login_cache 之后必须重测（刚登录完不能还报未登录）"


def test_login_probe_failure_is_not_cached(isolated, tmp_path, monkeypatch):
    import subprocess

    monkeypatch.setattr(rt, "LOGIN_CHECK_FILE", tmp_path / "engine-login.json")

    def boom(*args, **kwargs):
        raise subprocess.TimeoutExpired(args[0] if args else ["node"], 8)

    monkeypatch.setattr(rt, "engine_command", lambda **kw: ["node", "codex.js"])
    monkeypatch.setattr(rt.subprocess, "run", boom)
    assert rt.login_probe(tmp_path / "codex-home") == (False, 0.0)
    assert not (tmp_path / "engine-login.json").exists(), "探测失败不该被钉成两分钟的旧登录态"
