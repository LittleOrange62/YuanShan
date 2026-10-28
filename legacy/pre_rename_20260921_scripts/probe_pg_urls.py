# -*- coding: utf-8 -*-
"""Probe candidate PostgreSQL Windows installer URLs and report which ones exist."""
import sys
import urllib.request

sys.stdout.reconfigure(encoding='utf-8')

CANDIDATES = [
    # PostgreSQL 17 line (tested on Windows Server 2019/2022 -> safest for Win10 22H2)
    'https://get.enterprisedb.com/postgresql/postgresql-17.11-1-windows-x64.exe',
    'https://get.enterprisedb.com/postgresql/postgresql-17.11-2-windows-x64.exe',
    'https://get.enterprisedb.com/postgresql/postgresql-17.10-1-windows-x64.exe',
    'https://get.enterprisedb.com/postgresql/postgresql-17.6-1-windows-x64.exe',
    # binaries-only zip fallback (no installer, fully scriptable)
    'https://get.enterprisedb.com/postgresql/postgresql-17.11-1-windows-x64-binaries.zip',
    'https://get.enterprisedb.com/postgresql/postgresql-17.10-1-windows-x64-binaries.zip',
    # 18 line for reference
    'https://get.enterprisedb.com/postgresql/postgresql-18.6-1-windows-x64.exe',
]


def head(url):
    req = urllib.request.Request(url, method='HEAD')
    req.add_header('User-Agent', 'Mozilla/5.0')
    try:
        with urllib.request.urlopen(req, timeout=25) as resp:
            length = resp.headers.get('Content-Length')
            mb = round(int(length) / 1024 / 1024, 1) if length else None
            return 'OK %s  size=%sMB  final=%s' % (resp.status, mb, resp.url)
    except Exception as exc:  # noqa: BLE001
        return 'FAIL %s' % exc


def main():
    for url in CANDIDATES:
        print('%-90s %s' % (url.rsplit('/', 1)[-1], head(url)))


if __name__ == '__main__':
    main()
