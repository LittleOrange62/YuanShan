# -*- coding: utf-8 -*-
"""
Unattended install of PostgreSQL 17 on Windows.

Install dir : D:\\PostgreSQL\\17
Data dir    : D:\\finance\\TataPG      (the "TataPG" directory the user asked for)
Port        : 5432
Encoding    : UTF8 (locale C, so Chinese text stores fine and sorting stays stable)
Service     : postgresql-x64-17
"""
import os
import subprocess
import sys

sys.stdout.reconfigure(encoding='utf-8')

INSTALLER = r'D:\FinanceTool\var\download\postgresql-17.11-1-windows-x64.exe'
PREFIX = r'D:\PostgreSQL\17'
DATADIR = r'D:\finance\TataPG'
PORT = '5432'
SUPERPASS = 'Tata#Pg2026!Fin'
LOGFILE = r'D:\FinanceTool\var\download\pg_install.log'


def main():
    if not os.path.exists(INSTALLER):
        print('installer missing: %s' % INSTALLER)
        return 1

    os.makedirs(DATADIR, exist_ok=True)
    if os.listdir(DATADIR):
        print('DATADIR not empty: %s -> %s' % (DATADIR, os.listdir(DATADIR)[:5]))
        return 1
    os.makedirs(os.path.dirname(LOGFILE), exist_ok=True)

    args = [
        INSTALLER,
        '--mode', 'unattended',
        '--unattendedmodeui', 'none',
        '--prefix', PREFIX,
        '--datadir', DATADIR,
        '--serverport', PORT,
        '--superpassword', SUPERPASS,
        '--locale', 'C',
        '--enable-components', 'server,commandlinetools,pgAdmin',
        '--disable-components', 'stackbuilder',
        '--install_runtimes', '1',
        '--create_shortcuts', '0',
        '--extract-only', '0',
        '--debuglevel', '2',
        '--debugtrace', LOGFILE,
    ]

    printable = list(args)
    printable[printable.index('--superpassword') + 1] = '***'
    print('running: %s' % ' '.join(printable))
    print('this takes several minutes...', flush=True)

    proc = subprocess.run(args, capture_output=True, text=True,
                          encoding='utf-8', errors='replace')
    print('exit_code=%s' % proc.returncode)
    if proc.stdout:
        print('--- stdout ---')
        print(proc.stdout[-4000:])
    if proc.stderr:
        print('--- stderr ---')
        print(proc.stderr[-4000:])

    print('\n--- post-install checks ---')
    for p in (os.path.join(PREFIX, 'bin', 'psql.exe'),
              os.path.join(DATADIR, 'PG_VERSION'),
              os.path.join(DATADIR, 'postgresql.conf')):
        print('%-50s exists=%s' % (p, os.path.exists(p)))
    return proc.returncode


if __name__ == '__main__':
    raise SystemExit(main())
