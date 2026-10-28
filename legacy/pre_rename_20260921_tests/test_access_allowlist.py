# -*- coding: utf-8 -*-
"""
访问白名单：闸门真的挡人，且挡不住本机。

这一条链路只有两种失败方式，都很贵：
- 挡不住 —— 白名单形同装饰，局域网任何人都能读全库；
- 挡过头 —— 本机也进不去，而唯一的修复入口（/admin）就在被挡的那个网页里。

所以测试全部围着这两件事写，并且**真的起一个 Flask 走一遍 HTTP**，
而不是只测 `allowed()` 这个纯函数 —— 钩子没挂上的话纯函数全绿也没用。
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest
from flask import Flask

from app.db.repo import registry as reg_repo
from app.web import access

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture()
def fixed_list(monkeypatch):
    """把白名单钉成 192.168.176.67，不碰库、不受缓存影响。"""
    monkeypatch.setattr(access, 'allowlist',
                        lambda **_kw: ['192.168.176.67'])
    return ['192.168.176.67']


# ------------------------------------------------------------ 条目解析
def test_normalize_accepts_ip_and_cidr():
    assert access.normalize(['192.168.176.67', '10.0.0.0/8']) == [
        '192.168.176.67', '10.0.0.0/8']


def test_normalize_splits_lines_commas_and_dedupes():
    text = '192.168.176.67\n192.168.176.67，10.0.0.1\n# 注释\n\n'
    assert access.normalize(text) == ['192.168.176.67', '10.0.0.1']


def test_normalize_single_host_cidr_becomes_bare_ip():
    # `/32` 看着像网段，容易被误读成"这一段都放行"
    assert access.normalize(['192.168.176.67/32']) == ['192.168.176.67']


def test_normalize_rejects_garbage():
    # 看不懂就抛，不猜 —— 猜错的方向是"多放行了一个网段"
    with pytest.raises(ValueError):
        access.normalize(['192.168.176.999'])
    with pytest.raises(ValueError):
        access.normalize(['整个局域网'])


def test_host_bits_are_tolerated():
    # 192.168.176.67/24 是常见手写法，当 192.168.176.0/24 收下
    assert access.normalize(['192.168.176.67/24']) == ['192.168.176.0/24']


# ------------------------------------------------------------ 判定
def test_loopback_always_allowed_even_with_empty_list(monkeypatch):
    monkeypatch.setattr(access, 'allowlist', lambda **_kw: [])
    assert access.allowed('127.0.0.1') is True
    assert access.allowed('::1') is True


def test_listed_address_allowed_and_others_denied(fixed_list):
    assert access.allowed('192.168.176.67') is True
    assert access.allowed('192.168.176.68') is False
    assert access.allowed('10.1.2.3') is False


def test_cidr_covers_range(monkeypatch):
    monkeypatch.setattr(access, 'allowlist', lambda **_kw: ['192.168.176.0/24'])
    assert access.allowed('192.168.176.200') is True
    assert access.allowed('192.168.177.1') is False


def test_ipv4_mapped_v6_is_compared_as_v4(fixed_list):
    # waitress 在双栈监听下会把来源报成 ::ffff:a.b.c.d，
    # 不还原的话白名单里写的 v4 永远匹配不上
    assert access.allowed('::ffff:192.168.176.67') is True
    assert access.allowed('::ffff:192.168.176.68') is False


def test_unparsable_or_missing_addr_denied(fixed_list):
    assert access.allowed(None) is False
    assert access.allowed('') is False
    assert access.allowed('unix-socket') is False


def test_fails_closed_when_settings_unreadable(monkeypatch):
    def boom(*_a, **_kw):
        raise RuntimeError('库连不上')

    monkeypatch.setattr(reg_repo, 'setting', boom)
    access.invalidate()
    try:
        assert access.allowlist(fresh=True) == []      # 不缓存空结果
        assert access.bind_host() == access.DEFAULT_BIND
        assert access.allowed('192.168.176.67') is False   # 只剩本机
        assert access.allowed('127.0.0.1') is True
    finally:
        access.invalidate()


# ------------------------------------------------------------ 真的走一遍 HTTP
def _gated_app() -> Flask:
    app = Flask(__name__)
    access.install(app, port=18420)

    @app.route('/')
    def _home():  # noqa: ANN202
        return 'ok'

    return app


def test_gate_blocks_unlisted_ip(fixed_list):
    cli = _gated_app().test_client()
    r = cli.get('/', environ_base={'REMOTE_ADDR': '192.168.176.99'})
    assert r.status_code == 403
    body = r.data.decode('utf-8')
    assert '192.168.176.99' in body          # 告诉对方自己的地址，便于加白
    assert '白名单' in body


def test_gate_passes_loopback_and_listed(fixed_list):
    cli = _gated_app().test_client()
    for ip in ('127.0.0.1', '192.168.176.67'):
        r = cli.get('/', environ_base={'REMOTE_ADDR': ip})
        assert r.status_code == 200, ip


def test_gate_ignores_forwarded_for_header(fixed_list):
    """`X-Forwarded-For` 可以由请求方随便填 —— 信它等于白名单可被绕过。"""
    cli = _gated_app().test_client()
    r = cli.get('/', environ_base={'REMOTE_ADDR': '192.168.176.99'},
                headers={'X-Forwarded-For': '192.168.176.67',
                         'X-Real-IP': '127.0.0.1'})
    assert r.status_code == 403


# ------------------------------------------------------------ 落库与默认值
def test_settings_exist_with_required_default():
    # 用户明确要求：默认填 192.168.176.67，且默认只允许本机连接
    assert '192.168.176.67' in access.normalize(
        reg_repo.setting(access.ALLOWLIST_KEY, []))
    assert reg_repo.setting(access.BIND_KEY) == '127.0.0.1'


def test_bind_host_only_two_choices():
    with pytest.raises(ValueError):
        access.save_bind_host('192.168.176.67')
    with pytest.raises(ValueError):
        access.save_bind_host('')


def test_save_allowlist_round_trip():
    before = access.allowlist(fresh=True)
    try:
        saved = access.save_allowlist('192.168.176.67\n10.9.9.0/24')
        assert saved == ['192.168.176.67', '10.9.9.0/24']
        # 保存后必须**立刻**生效：等 TTL 过期的话，用户会以为没保存上
        assert access.allowlist() == saved
        assert access.allowed('10.9.9.5') is True
    finally:
        access.save_allowlist(before)
    assert access.allowlist(fresh=True) == before


def test_seed_does_not_reset_user_edits():
    """这两项在 05_seed.sql 里必须是 DO NOTHING —— 重跑 seed 不能把白名单拍回默认。"""
    sql = (ROOT / 'app/db/sql/05_seed.sql').read_text(encoding='utf-8')
    block = re.search(
        r"INSERT INTO config\.setting[^;]*web_allowlist[^;]*;", sql, re.S)
    assert block, '05_seed.sql 里找不到 web_allowlist 的 INSERT'
    assert 'DO NOTHING' in block.group(0)
    assert 'web_bind_host' in block.group(0)
