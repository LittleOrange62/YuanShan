import pathlib
for name in ['diag_login.ps1']:
    p = pathlib.Path(r'C:\Users\Administrator\.qwenpaw\workspaces\tq\scripts') / name
    raw = p.read_bytes()
    if not raw.startswith(b'\xef\xbb\xbf'):
        p.write_bytes(b'\xef\xbb\xbf' + raw)
    print('BOM added for', name)
