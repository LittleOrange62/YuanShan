"""
凭据读取。

**代码里零明文口令。** 只从环境变量 / `.env` 读，日志和异常里也不允许
出现口令原文 —— 卡住时保存的现场包（`var/stuck/`）会被人和智能体翻，
口令进了那里等于泄在磁盘上。

`D:\\finance\\脚本\\02_通达信操作\\open_tdx.ps1` 里有一行
`param([string]$Pass = "Chengzi.888")`，那是历史遗留。收录到
`scripts/tdx_client/` 时会把默认值去掉，改成从环境变量取。
"""

from __future__ import annotations

from app.config import env


class MissingCredentials(RuntimeError):
    pass


def tdx_account() -> tuple[str, str]:
    """
    返回 `(账号, 口令)`。缺哪个就报哪个，**不要在报错里回显值**。

    配置项：
        FT_TDX_ACCOUNT   通达信账号
        FT_TDX_PASSWORD  通达信口令
    """
    acct = (env('FT_TDX_ACCOUNT') or '').strip()
    pwd = env('FT_TDX_PASSWORD') or ''
    missing = [k for k, v in (('FT_TDX_ACCOUNT', acct),
                              ('FT_TDX_PASSWORD', pwd)) if not v]
    if missing:
        raise MissingCredentials(
            f'缺少配置：{"、".join(missing)}。'
            '请写到 .env 或环境变量里（不要写进代码或提交到版本库）。')
    return acct, pwd


def mask(value: str) -> str:
    """
    给日志用的脱敏形式。账号 `18969969696` → `189****9696`。

    口令一律返回长度占位，**不露任何字符** —— 露前两位对暴力破解也是帮助。
    """
    if not value:
        return '(空)'
    if len(value) <= 4:
        return '*' * len(value)
    return f'{value[:3]}{"*" * (len(value) - 7)}{value[-4:]}'


def mask_secret(value: str) -> str:
    return f'({len(value)} 位)' if value else '(空)'


def has_credentials() -> bool:
    try:
        tdx_account()
        return True
    except MissingCredentials:
        return False
