import os

files = [
    r'D:\new_tdx64\T0002\hq_cache\csiblock.dat',
    r'D:\new_tdx64\T0002\hq_cache\spblock.dat',
    r'D:\new_tdx64\T0002\hq_cache\infoharbor_block.dat',
    r'D:\new_tdx64\T0002\hq_cache\mgblock.dat',
    r'D:\new_tdx64\T0002\hq_cache\hkblock.dat',
    r'D:\new_tdx64\T0002\hq_cache\jjblock.dat',
    r'D:\new_tdx64\T0002\hq_cache\sbblock.dat',
    r'D:\new_tdx64\BlockMap\BlockMapXML.dat',
    r'D:\new_tdx64\BlockMap\BlockMapRes.dat',
    r'D:\new_tdx64\T0002\blocknew\zxgmore.dat',
]
for f in files:
    print('='*60)
    print('FILE:', f, 'exists:', os.path.exists(f))
    if not os.path.exists(f):
        continue
    size = os.path.getsize(f)
    print('size:', size)
    with open(f, 'rb') as fh:
        head = fh.read(200)
    print('hex head:', head[:64].hex())
    # try decode as text
    for enc in ['utf-8', 'gbk', 'latin-1']:
        try:
            txt = head.decode(enc)
            print(f'[{enc}] text sample:', repr(txt[:150]))
            break
        except Exception:
            pass

# check 880 daily files
for sub in [r'D:\new_tdx64\vipdoc\sh\lday', r'D:\new_tdx64\vipdoc\sz\lday']:
    if os.path.exists(sub):
        files880 = [f for f in os.listdir(sub) if f.startswith('sh880') or f.startswith('sz880')]
        print('='*60)
        print('DIR:', sub, '880 files count:', len(files880), 'sample:', files880[:5])
