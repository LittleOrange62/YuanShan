"""局域网访问这道闸的回归。

网络边界的判断错了不会报错，只会安静地把机器敞开或对门锁死，所以每条都钉实际行为：
配置默认关、非法来源不放行、白名单外的连页面都拿不到、白名单内的通、
以及**只有回环能改这道闸**。
"""
from __future__ import annotations

import json
import pathlib

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(autouse=True)
def isolate(tmp_path, monkeypatch):
    """配置一律打到临时文件：这些测试会真开关、真加白。"""
    monkeypatch.setenv("VIBE_NET_CONFIG", str(tmp_path / "net" / "config.json"))
    monkeypatch.delenv("VIBE_ALLOW_HOSTS", raising=False)
    monkeypatch.delenv("VR_API_KEY", raising=False)
    import netconfig
    monkeypatch.setattr(netconfig, "_DENIALS", [])
    monkeypatch.setattr(netconfig, "_DENIAL_TOTAL", 0)
    return tmp_path


# ---------------------------------------------------------------- 配置层
def test_没有配置文件就是只开回环():
    import netconfig
    cfg = netconfig.load()
    assert cfg["lan_enabled"] is False and cfg["allowlist"] == []
    assert netconfig.bind_host(cfg) == "127.0.0.1"


def test_配置坏了按只开回环运行并且出声(tmp_path):
    """网络边界上不能拿"读不出来"当"允许"；但也不能静默回落，
    否则用户以为"我勾了软件不认"，实际是文件写坏了。"""
    import netconfig
    path = pathlib.Path(netconfig.config_path())
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{broken", encoding="utf-8")
    cfg = netconfig.load()
    assert cfg["lan_enabled"] is False and cfg["allowlist"] == []
    assert "读不了" in cfg["_warning"]


def test_白名单里的非法项被忽略并说明():
    import netconfig
    pathlib.Path(netconfig.config_path()).parent.mkdir(parents=True, exist_ok=True)
    pathlib.Path(netconfig.config_path()).write_text(json.dumps({
        "lan_enabled": True,
        "allowlist": ["192.168.186.7", "0.0.0.0", "192.168.186.0/24", "本机", "::1", "127.0.0.1"]}),
        encoding="utf-8")
    cfg = netconfig.load()
    assert cfg["allowlist"] == ["192.168.186.7"]
    assert "不是合法 IPv4" in cfg["_warning"] or "忽略" in cfg["_warning"]


@pytest.mark.parametrize("bad,needle", [
    ("", "非空"), ("1.2.3", "合法 IPv4"), ("256.1.1.1", "合法 IPv4"),
    ("0.0.0.0", "绑定地址"), ("127.0.0.1", "本来就永久放行"),
    ("255.255.255.255", "广播"), ("fe80::1", "IPv4"), ("192.168.186.0/24", "IPv4"),
])
def test_非法白名单项一律拒绝并给原因(bad, needle):
    import netconfig
    why = netconfig.validate_ip(bad)
    assert why and needle in why


def test_加白去重与上限():
    import netconfig
    _, why = netconfig.add_ip("192.168.186.7")
    assert why is None and netconfig.load()["allowlist"] == ["192.168.186.7"]
    _, why = netconfig.add_ip("192.168.186.7")
    assert why and "已经在白名单" in why
    _, why = netconfig.add_ip("垃圾")
    assert why and "IPv4" in why
    _, why = netconfig.remove_ip("10.1.1.1")
    assert why and "不在白名单" in why


# ---------------------------------------------------------------- 放行判定
def test_回环永远放行且不需要写进白名单():
    import netconfig
    netconfig.save(lan_enabled=False, allowlist=[])
    for ip in ("127.0.0.1", "127.9.9.9", "::1"):
        ok, why = netconfig.is_allowed_client(ip)
        assert ok and "回环" in why


def test_开关关着时任何非回环来源都不放行():
    import netconfig
    netconfig.save(lan_enabled=False, allowlist=["192.168.186.7"])
    ok, why = netconfig.is_allowed_client("192.168.186.7")
    assert not ok and "未开放局域网" in why


def test_开关开着时只有白名单内的通():
    import netconfig
    netconfig.save(lan_enabled=True, allowlist=["192.168.186.7"])
    assert netconfig.is_allowed_client("192.168.186.7")[0] is True
    ok, why = netconfig.is_allowed_client("192.168.186.8")
    assert not ok and "不在白名单" in why
    assert netconfig.is_allowed_client(None)[0] is False       # 读不到来源 = 不放行
    assert netconfig.is_allowed_client("10.0.0.1")[0] is False


def test_实际生效的白名单不是绑定地址():
    """勾了开关但没重启：`bind_host` 说的和进程实际绑的不是一回事，
    页面必须把"要重启"说出来，不然用户以为已经开放了。"""
    import netconfig
    netconfig.save(lan_enabled=True)
    assert netconfig.bind_host() == "0.0.0.0"
    running = netconfig.running_bind(["x", "-m", "uvicorn", "server:app",
                                      "--host", "127.0.0.1", "--port", "8910"])
    assert running == {"host": "127.0.0.1", "port": 8910, "lan_open": False}
    st = netconfig.state()
    assert st["needs_restart"] is True and st["lan_enabled"] is True


# ---------------------------------------------------------------- 真的那道闸
@pytest.fixture
def app_for_gate(isolate, monkeypatch):
    import server
    return server.app


@pytest.fixture
def from_local(app_for_gate):
    """本机回环那一侧的客户端。

    `base_url` 就是 Host 头 —— TestClient 默认 "testserver"，那会被既有那道
    Host 校验按"非法 Host"挡掉，与这道 IP 闸无关（真实浏览器不会这么发）。
    """
    def make(ip="127.0.0.1"):
        return TestClient(app_for_gate, base_url="http://127.0.0.1:8910", client=(ip, 1))
    return make


def test_白名单外的来源连静态页面都拿不到(app_for_gate):
    import netconfig
    netconfig.save(lan_enabled=True, allowlist=["192.168.186.7"])
    client = TestClient(app_for_gate, client=("192.168.99.99", 5555))
    resp = client.get("/")
    assert resp.status_code == 403
    assert "未对该来源地址开放" in resp.text
    assert "192.168.99.99" in resp.text          # 得告诉对面它实际是哪个地址
    data = client.get("/api/health").json()
    assert data["error"] and data["client"] == "192.168.99.99"


def test_白名单内的来源能通且Host用本机局域网地址也认(app_for_gate, monkeypatch):
    """对面敲的是**本机网卡地址**，而旧的 Host 白名单只有 127.0.0.1/localhost/::1。
    不把"这台机器自己的地址"认下来，表现就是"勾了局域网访问但对面 403"，
    而拦人的是一道与白名单无关的检查 —— 最难查的那种。

    网卡枚举在测试沙箱里不可靠（conftest 拦出站 socket），所以这里直接注入名单，
    验的是"Host 判定确实问了 netconfig"。
    """
    import netconfig
    netconfig.save(lan_enabled=True, allowlist=["192.168.186.7"])
    monkeypatch.setattr(netconfig, "local_ips", lambda ttl=30.0: ["192.168.176.68", "2.0.1.6"])
    client = TestClient(app_for_gate, client=("192.168.186.7", 5555))
    for host in ("192.168.176.68:8910", "2.0.1.6:8910", "127.0.0.1:8910"):
        resp = client.get("/api/health", headers={"host": host})
        assert resp.status_code == 200, f"{host} → {resp.status_code} {resp.text}"
    # 别人的域名仍然不认（没放松 DNS 重绑定防护）
    assert client.get("/api/health", headers={"host": "evil.example.com"}).status_code == 403


def test_本机回环在任何开关状态下都通(from_local):
    import netconfig
    netconfig.save(lan_enabled=False, allowlist=[])
    assert from_local().get("/api/health").status_code == 200
    netconfig.save(lan_enabled=True, allowlist=["192.168.186.7"])
    assert from_local().get("/api/health").status_code == 200


def test_被挡的来源会留记录供页面查(app_for_gate):
    """对面到底以哪个 IP 进来，只有本机看得见 —— 中间过一层 NAT 时用户猜不到。"""
    import netconfig
    netconfig.save(lan_enabled=True, allowlist=["192.168.186.7"])
    client = TestClient(app_for_gate, client=("192.168.50.5", 1))
    client.get("/api/health")
    rec = netconfig.denials()
    assert rec["total"] >= 1 and rec["recent"][-1]["client"] == "192.168.50.5"


def test_api_net_本机可读可改局域网侧只读(from_local):
    import netconfig
    local = from_local()
    st = local.get("/api/net").json()["data"]
    assert st["can_edit"] is True and st["allowlist"] == []
    assert local.post("/api/net", json={"add": "192.168.186.7"}).status_code == 200
    assert netconfig.load()["allowlist"] == ["192.168.186.7"]
    # 非法值必须出声，不能悄悄收下
    bad = local.post("/api/net", json={"add": "0.0.0.0"})
    assert bad.status_code == 400 and "绑定地址" in bad.json()["detail"]
    # 一次只改一件事
    two = local.post("/api/net", json={"add": "10.1.1.1", "remove": "192.168.186.7"})
    assert two.status_code == 400 and "一次只改一件事" in two.json()["detail"]


def test_api_net_写操作拒绝局域网侧(app_for_gate):
    """被放行的一方如果能自己加白，这道闸就形同没有。

    这里绕过 TestClient 直接调处理函数：中间件已经把非白名单来源挡在外面了，
    要验的是**第二道**规则 —— 已过 IP 闸的白名单机器，同样不许改边界。
    """
    import types

    import fastapi

    handler = next(r.endpoint for r in app_for_gate.routes
                   if getattr(r, "path", "") == "/api/net" and r.methods == {"POST"})
    patch = handler.__globals__["NetPatch"]
    req = types.SimpleNamespace(client=types.SimpleNamespace(host="192.168.186.7"))
    with pytest.raises(fastapi.HTTPException) as got:
        handler(patch(add="10.1.1.1"), req)
    assert got.value.status_code == 403 and "只能在主机上改" in got.value.detail


def test_改完一条还能接着改(from_local):
    """这条钉的是浏览器里实测出来的 bug：POST 的响应少带 `can_edit`，
    前端 setState 之后"加白 / 删除"控件集体消失 —— 改完一条就再也改不动，
    只能刷新页面，而界面上没有任何话说"为什么灰了"。
    GET 与 POST 的字段必须一模一样。"""
    local = from_local()
    import netconfig
    netconfig.save(lan_enabled=True, allowlist=["192.168.186.7"])
    before = local.get("/api/net").json()["data"]
    after = local.post("/api/net", json={"add": "10.1.1.1"}).json()["data"]
    assert after["can_edit"] is True
    assert set(before) - set(after) == set(), f"POST 少了字段：{set(before) - set(after)}"
    # 紧接着不刷新再改一次，必须还能改
    again = local.post("/api/net", json={"remove": "10.1.1.1"}).json()["data"]
    assert "10.1.1.1" not in again["allowlist"] and again["can_edit"] is True
    # 非法值要原样带出拒绝原因（前端把它显示在"未采纳"里）
    bad = local.post("/api/net", json={"add": "192.168.186.0/24"})
    assert bad.status_code == 400 and "IPv4" in bad.json()["detail"]
    assert local.get("/api/net").json()["data"]["allowlist"] == ["192.168.186.7"]
