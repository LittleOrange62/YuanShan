import pathlib
paths = [
    r'C:\Users\Administrator\.qwenpaw\workspaces\tq\scripts\diag_login.ps1',
    r'C:\Users\Administrator\.qwenpaw\workspaces\tq\打开通达信\open_tdx.ps1',
]
for p in paths:
    fp = pathlib.Path(p)
    raw = fp.read_bytes()
    has_bom = raw.startswith(b'\xef\xbb\xbf')
    print(p, 'BOM:', has_bom, 'size:', len(raw))
    if not has_bom:
        fp.write_bytes(b'\xef\xbb\xbf' + raw)
        print('  -> BOM added')
