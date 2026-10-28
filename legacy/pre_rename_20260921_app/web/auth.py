# -*- coding: utf-8 -*-
"""
登录与权限。

`config.app_user` 存 argon2 口令哈希，`config.menu.need_role` 决定哪些菜单只给
admin 看。这两张表建好很久了但一直没有代码读 —— 于是 `/admin` 那一行的
`need_role='admin'` 形同注释，谁都能点。

**当前的安全模型，写清楚免得误判：**

- 默认只绑 `127.0.0.1`（`app/web/run.py` 会把 `.env` 里的 `0.0.0.0` 改回来），
  所以"不登录"在单机自用场景下是可接受的；
- 一旦 `config.app_user` 里存在启用的用户，登录门就自动生效（`login_required()`
  返回 True）—— 不需要再改配置，加用户即开启。这样默认不打扰单机使用，
  要对外开放时只需建一个账号；
- 口令永不明文落库、不进日志、不回显。`verify()` 对不存在的用户也走一遍
  哈希校验（`_DUMMY_HASH`），避免用响应时间区分"用户不存在"和"口令错误"。
"""
from __future__ import annotations

from argon2 import PasswordHasher

from app.db import engine

_ph = PasswordHasher()

# 用户不存在时也要花同样的时间算一次哈希，否则响应快慢就是"用户存不存在"
# 的旁路信道。这个串是固定的假哈希，只用来消耗时间。
_DUMMY_HASH = _ph.hash('__no_such_user__')

ROLES = ('user', 'admin')


def users() -> list[dict]:
    return engine.query(
        """
        SELECT username, role, display_name, enabled, created_at, last_login_at
          FROM config.app_user ORDER BY role DESC, username
        """)


def user_count(*, enabled_only: bool = True) -> int:
    return int(engine.scalar(
        'SELECT count(*) FROM config.app_user WHERE (%s = false OR enabled)',
        (enabled_only,)) or 0)


def login_required() -> bool:
    """库里有启用的用户就要求登录；一个都没有则放行（单机自用）。"""
    try:
        return user_count() > 0
    except Exception:  # noqa: BLE001
        # 库连不上时**要求**登录，不放行 —— 失败方向要选安全的那一边
        return True


def create_user(username: str, password: str, *, role: str = 'user',
                display_name: str | None = None) -> None:
    if role not in ROLES:
        raise ValueError(f'role 只能是 {ROLES}')
    if len(password) < 8:
        raise ValueError('口令至少 8 位')
    engine.execute(
        """
        INSERT INTO config.app_user (username, password_hash, role, display_name)
        VALUES (%s, %s, %s, %s)
        ON CONFLICT (username) DO UPDATE SET
            password_hash = EXCLUDED.password_hash, role = EXCLUDED.role,
            display_name = EXCLUDED.display_name
        """, (username.strip(), _ph.hash(password), role, display_name),
        role='admin')


def set_password(username: str, password: str) -> None:
    if len(password) < 8:
        raise ValueError('口令至少 8 位')
    engine.execute('UPDATE config.app_user SET password_hash = %s WHERE username = %s',
                   (_ph.hash(password), username), role='admin')


def set_enabled(username: str, enabled: bool) -> None:
    engine.execute('UPDATE config.app_user SET enabled = %s WHERE username = %s',
                   (enabled, username), role='admin')


def set_role(username: str, role: str) -> None:
    if role not in ROLES:
        raise ValueError(f'role 只能是 {ROLES}')
    engine.execute('UPDATE config.app_user SET role = %s WHERE username = %s',
                   (role, username), role='admin')


def delete_user(username: str) -> None:
    engine.execute('DELETE FROM config.app_user WHERE username = %s', (username,),
                   role='admin')


def verify(username: str, password: str) -> dict | None:
    """校验通过返回 {username, role, display_name}，否则 None。"""
    rows = engine.query(
        """
        SELECT username, password_hash, role, display_name, enabled
          FROM config.app_user WHERE username = %s
        """, ((username or '').strip(),))
    row = rows[0] if rows else None
    stored = row['password_hash'] if row else _DUMMY_HASH
    try:
        _ph.verify(stored, password or '')
    except Exception:  # noqa: BLE001
        # argon2 会抛 VerifyMismatchError / InvalidHashError / VerificationError，
        # 全都当"没通过"处理。分开报会把"哈希格式坏了"泄露给调用方。
        return None
    if row is None or not row['enabled']:
        return None
    engine.execute('UPDATE config.app_user SET last_login_at = now() WHERE username = %s',
                   (row['username'],), role='app')
    return {'username': row['username'], 'role': row['role'],
            'display_name': row['display_name']}


def can_see(menu_row: dict, role: str | None) -> bool:
    """
    菜单可见性。`need_role` 为空 = 谁都能看。

    没登录时（role=None）：如果登录门没开启，等同 admin —— 单机自用场景
    不能因为"没登录"就把系统管理藏掉，那会变成谁都进不去。
    """
    need = menu_row.get('need_role')
    if not need:
        return True
    if role is None:
        return not login_required()
    return role == need or role == 'admin'
