# -*- coding: utf-8 -*-
"""
Web 启动入口。

**默认只绑 127.0.0.1**，因为本应用目前没有登录。旧系统把 SQL 接口以
`auth_enabled=False` 监听 0.0.0.0，等于把数据库放在局域网上任人取用；
这里不重复。

绑定地址由 `config.setting.web_bind_host` 决定（`/admin` → 「访问白名单」可改），
默认 `127.0.0.1`；`--host` 优先级最高。**即使绑了 0.0.0.0，请求也要过
IP 白名单**（`app/web/access.py`）—— 所以"对局域网可达"和"谁能进来"是两件事，
前者是这里，后者是白名单。

生产用 waitress（Windows 上验证过），调试用 Dash 自带服务器。
"""
from __future__ import annotations

import argparse
import socket

from app import config
from app.web import access
from app.web.app import get_app

SAFE_HOSTS = ('127.0.0.1', 'localhost', '::1')


def main() -> int:
    ap = argparse.ArgumentParser(description='FinanceTool Web')
    ap.add_argument('--host', default=None)
    ap.add_argument('--port', type=int, default=config.WEB_PORT)
    ap.add_argument('--debug', action='store_true')
    # 16：复盘首页一次会发 16 个卡片请求 + 1 个横幅请求（每张卡一个 MATCH 回调）。
    # 线程数少于卡片数时后面的卡片只能排队，"各自异步加载"就退化成分批加载。
    # 连接池那一侧同步提到了 12（app/config.py POOL_SIZE），两处要一起看。
    ap.add_argument('--threads', type=int, default=16)
    args = ap.parse_args()

    # 绑定地址走库里的设置，不直接采用 .env 里的 0.0.0.0：`.env` 是给部署用的，
    # 而"开不开局域网"是一个要在网页上能看见、能改、能自证的开关。
    host = args.host or access.bind_host()
    access.note_running_bind(host)
    allow = access.allowlist(fresh=True)
    if host not in SAFE_HOSTS:
        print('!! 注意：绑定到 %s，本机之外也能连到这个端口。' % host)
        print('!! 当前**没有登录**，唯一的闸门是 IP 白名单：'
              + ('、'.join(allow) if allow else '（空 —— 只有本机能进）'))
        print('!! 白名单之外的地址会收到 403。要放行更多地址：/admin → 访问白名单。')
    else:
        print('绑定 %s（仅本机可达）。白名单里的局域网地址此时连不上 —— '
              '要开局域网，去 /admin 把监听地址改成 0.0.0.0 再重启。' % host)

    dash_app = get_app()

    url = f'http://{"127.0.0.1" if host == "0.0.0.0" else host}:{args.port}/'

    # 端口先自己探一次。**这个检查是踩出来的**：端口被上一个没退干净的实例占着时，
    # waitress 起不来但外面看仍然「有页面」—— 访问到的是那个旧进程，代码改了也不生效，
    # 一路排查会怀疑到代码上去。宁可在这里直接报错退出，也不要让人对着旧页面调试。
    probe = socket.socket()
    try:
        probe.bind((host, args.port))
    except OSError:
        print('!! 端口 %d 已被占用，本次没有启动。' % args.port)
        print('!! 占用它的很可能是上一个 FinanceTool 实例 —— 那个实例跑的是老代码，')
        print('!! 浏览器打开 %s 看到的会是老页面。先结束它，或换 --port。' % url)
        print('!! 查占用：Get-NetTCPConnection -State Listen | '
              '? LocalPort -eq %d' % args.port)
        return 2
    finally:
        probe.close()

    print('FinanceTool Web 启动：%s' % url)
    # 页面清单从注册表里现取，不写死：写死过一次，加了 /screen /stock /watchlist
    # 之后这行还在报老的五个，等于启动横幅自己在骗人。
    from app.web import pages as pages_mod
    print('页面：' + '  '.join(sorted(pages_mod.PAGES)))

    if args.debug:
        dash_app.run(host=host, port=args.port, debug=True)
        return 0

    from waitress import serve
    serve(dash_app.server, host=host, port=args.port, threads=args.threads)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
