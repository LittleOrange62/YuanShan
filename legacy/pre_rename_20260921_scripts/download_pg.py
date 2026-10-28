# -*- coding: utf-8 -*-
"""Download the PostgreSQL Windows installer with resume support and progress logging."""
import os
import sys
import time
import urllib.request

sys.stdout.reconfigure(encoding='utf-8')

URL = 'https://get.enterprisedb.com/postgresql/postgresql-17.11-1-windows-x64.exe'
DEST_DIR = r'D:\FinanceTool\var\download'
DEST = os.path.join(DEST_DIR, 'postgresql-17.11-1-windows-x64.exe')


def main():
    os.makedirs(DEST_DIR, exist_ok=True)
    have = os.path.getsize(DEST) if os.path.exists(DEST) else 0

    req = urllib.request.Request(URL)
    req.add_header('User-Agent', 'Mozilla/5.0')
    if have:
        req.add_header('Range', 'bytes=%d-' % have)
        print('resuming from %.1f MB' % (have / 1048576))

    mode = 'ab' if have else 'wb'
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=60) as resp:
        if have and resp.status != 206:
            print('server ignored Range (status %s), restarting from 0' % resp.status)
            have, mode = 0, 'wb'
        total = int(resp.headers.get('Content-Length', 0)) + have
        done = have
        next_mark = 0
        with open(DEST, mode) as f:
            while True:
                chunk = resp.read(1024 * 512)
                if not chunk:
                    break
                f.write(chunk)
                done += len(chunk)
                pct = done * 100 // total if total else 0
                if pct >= next_mark:
                    speed = (done - have) / max(time.time() - t0, 0.001) / 1048576
                    print('%3d%%  %.1f/%.1f MB  %.2f MB/s'
                          % (pct, done / 1048576, total / 1048576, speed), flush=True)
                    next_mark = pct + 5

    size = os.path.getsize(DEST)
    print('DONE size=%.1f MB path=%s' % (size / 1048576, DEST))
    if size < 300 * 1048576:
        print('WARNING: file smaller than expected, download may be incomplete')
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
