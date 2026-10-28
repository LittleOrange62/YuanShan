# -*- coding: utf-8 -*-
"""
配置加载。

三级优先：环境变量 > config.setting 表 > 代码默认值。
运行时可热改的配置放 config.setting（页面上能点）；连接串与路径这类
启动期就要确定的放环境变量。

口令只从 .env / 环境变量读，代码里零明文。
"""
from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import unquote, urlsplit

BASE_DIR = Path(__file__).resolve().parent.parent


def _load_dotenv(path: Path) -> None:
    """极简 .env 解析。不依赖 python-dotenv，避免安装顺序问题。"""
    if not path.exists():
        return
    for raw in path.read_text(encoding='utf-8').splitlines():
        line = raw.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        key, _, value = line.partition('=')
        key, value = key.strip(), value.strip()
        # 已存在的环境变量优先，不覆盖
        os.environ.setdefault(key, value)


_load_dotenv(BASE_DIR / '.env')


def env(key: str, default: str | None = None) -> str | None:
    return os.environ.get(key, default)


def env_int(key: str, default: int) -> int:
    try:
        return int(os.environ.get(key, default))
    except (TypeError, ValueError):
        return default


# ---------------------------------------------------------------- 数据库
PG_HOST = env('FT_PG_HOST', '127.0.0.1')
PG_PORT = env_int('FT_PG_PORT', 5432)
PG_DB = env('FT_PG_DB', 'financetool')

DSN = {
    'reader': env('FT_DSN_READER'),
    'app': env('FT_DSN_APP'),
    'etl': env('FT_DSN_ETL'),
    'admin': env('FT_DSN_ADMIN'),
}

# reader 从 4 提到 12：复盘首页每张卡片各发一个请求（2026-08-29 并入每日复盘后
# 是 16 张 + 横幅），池子只有 4 条连接时"并行加载"实际是四条一批地排队，
# 卡片再快也要等前一批还连接。app 池不动，写操作没有这个并发形态。
POOL_SIZE = {'reader': 12, 'app': 8, 'etl': 4, 'admin': 2}

# 超级用户仅供 scripts/init_db.py 建角色与建库，运行期任何模块都不该用它
PG_SUPERUSER = env('FT_PG_SUPERUSER', 'postgres')
PG_SUPERPASS = env('FT_PG_SUPERPASS')


def dsn_parts(role: str) -> dict:
    """从 DSN 里拆出 user/password/host/port/dbname，供 psql.exe 之类的外部命令用。"""
    dsn = DSN.get(role)
    if not dsn:
        raise RuntimeError(f'角色 {role!r} 的 DSN 未配置，检查 .env 的 FT_DSN_{role.upper()}')
    u = urlsplit(dsn)
    return {
        'user': unquote(u.username or ''),
        'password': unquote(u.password or ''),
        'host': u.hostname or PG_HOST,
        'port': u.port or PG_PORT,
        'dbname': (u.path or '/').lstrip('/') or PG_DB,
    }


# ---------------------------------------------------------------- 路径
PG_BIN = Path(env('FT_PG_BIN', r'D:\PostgreSQL\17\bin'))
PG_DATA = Path(env('FT_PG_DATA', r'D:\finance\TataPG'))
TDX_DIR = Path(env('FT_TDX_DIR', r'D:\new_tdx64'))
VIPDOC_DIR = TDX_DIR / 'vipdoc'
# 同花顺安装目录。**只当板块与自选分组的来源用**，K 线一律走通达信 ——
# 同花顺的 history\ 里只有"打开过的票"的缓存（实测 9 只），不是本地库。
THS_DIR = Path(env('FT_THS_DIR', r'D:\hanchen\同花顺'))
LEGACY_DATA = Path(env('FT_LEGACY_DATA', r'D:\finance\data'))
BACKUP_DIR = Path(env('FT_BACKUP_DIR', r'D:\finance\TataPG_backup'))
LOG_DIR = Path(env('FT_LOG_DIR', str(BASE_DIR / 'logs')))
VAR_DIR = BASE_DIR / 'var'
STRATEGY_DIR = BASE_DIR / 'app' / 'compute' / 'strategies'

# ---------------------------------------------------------------- 运行时
WEB_HOST = env('FT_WEB_HOST', '0.0.0.0')
WEB_PORT = env_int('FT_WEB_PORT', 18420)
LOG_KEEP_DAYS = env_int('FT_LOG_KEEP_DAYS', 30)
TIMEZONE = 'Asia/Shanghai'

# tqcenter（通达信 PYPlugins 服务），探活失败即降级，不空等
TQCENTER_HOST = '127.0.0.1'
TQCENTER_PORT = 17709
TQCENTER_TIMEOUT = 2

# 交易日切换时刻
TRADE_DAY_CUTOFF_HOUR = 15

# 板块涨跌停幅度。旧系统一刀切 pre_close*1.1，创业板/科创板/北交所全错。
BOARD_LIMIT = {
    '主板': 0.10,
    '创业板': 0.20,
    '科创板': 0.20,
    '北交所': 0.30,
}
ST_MAIN_BOARD_LIMIT = 0.05

# 代码前缀 → 市场/板块/类型 判定。市场以文件所在目录为准，前缀只定板块与类型。
A_SHARE_PREFIX = {
    'SH': ('600', '601', '603', '605', '688', '689'),
    'SZ': ('000', '001', '002', '003', '300', '301', '302'),
    'BJ': ('43', '83', '87', '92'),
}
BOARD_PREFIX = {
    '科创板': ('688', '689'),
    # 302 是创业板 2024 年启用的新代码段。302132.SZ 中航成飞（原 300114
    # 中航电测重组更名）历史上出现过 +20.04% 以及 13 个超过 19% 的交易日，
    # 数据本身证明它走 20% 涨跌幅档而不是主板 10%。漏掉这一段的后果是
    # board 判成 NULL、ref.board_limit_ratio 返回 NULL、1,218 行拿不到涨跌停价。
    '创业板': ('300', '301', '302'),
}
INDEX_PREFIX = {'SH': ('000',), 'SZ': ('399',)}
SECTOR_PREFIX = ('880', '881')
ETF_PREFIX = {
    'SH': ('510', '511', '512', '513', '515', '518', '588'),
    'SZ': ('159',),
}


def ensure_dirs() -> None:
    for d in (LOG_DIR, VAR_DIR, VAR_DIR / 'notify', VAR_DIR / 'export', BACKUP_DIR):
        d.mkdir(parents=True, exist_ok=True)
