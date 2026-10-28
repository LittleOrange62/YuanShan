"""探查通达信 hq_cache/*.dat 文件格式"""
import os, struct, glob

cache_dir = r'D:\new_tdx64\T0002\hq_cache'
files = sorted(glob.glob(os.path.join(cache_dir, '*.dat')))

for fpath in files[:5]:  # 先看前5个
    fname = os.path.basename(fpath)
    fsize = os.path.getsize(fpath)
    print(f'\n{"="*60}')
    print(f'FILE: {fname}  size={fsize}')
    with open(fpath, 'rb') as f:
        raw = f.read(512)
    # 打印hex
    hex_str = ' '.join(f'{b:02x}' for b in raw[:128])
    print(f'HEX: {hex_str}')
    # 尝试打印可显示ASCII
    ascii_str = ''.join(chr(b) if 32 <= b < 127 else '.' for b in raw[:128])
    print(f'ASCII: {ascii_str}')
    
    # 尝试判断结构
    # 通达信板块文件常见格式：前面有固定长度的表头
    # 尝试以GBK解码看看
    try:
        text = raw.decode('gbk', errors='replace')
        lines = text.split('\n')[:20]
        print(f'GBK decode:\n')
        for l in lines:
            print(f'  {repr(l)[:120]}')
    except Exception as e:
        print(f'GBK error: {e}')

print('\n--- ALL files list ---')
for f in files:
    print(os.path.basename(f), os.path.getsize(f))
