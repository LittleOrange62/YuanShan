import pathlib
p = pathlib.Path(r'C:\Users\Administrator\.qwenpaw\workspaces\tq\打开通达信\open_tdx.ps1')
raw = p.read_bytes()
if not raw.startswith(b'\xef\xbb\xbf'):
    p.write_bytes(b'\xef\xbb\xbf' + raw)
print('BOM added, size=', p.stat().st_size)
