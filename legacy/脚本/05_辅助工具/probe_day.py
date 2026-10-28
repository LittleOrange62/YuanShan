"""探查 sh880*.day 文件格式"""
import os

day_dir = r'D:\new_tdx64\vipdoc\sh\lday'
fname = 'sh880001.day'
fpath = os.path.join(day_dir, fname)
fsize = os.path.getsize(fpath)
print(f'FILE: {fname}  size={fsize}')

with open(fpath, 'rb') as f:
    raw = f.read(4096)

hex_str = ' '.join(f'{b:02x}' for b in raw[:256])
print(f'HEX: {hex_str}')

ascii_str = ''.join(chr(b) if 32 <= b < 127 else '.' for b in raw[:256])
print(f'ASCII: {ascii_str}')

# 尝试GBK
try:
    text = raw.decode('gbk', errors='replace')
    lines = text.split('\r\n')[:20]
    print(f'\n--- GBK lines ---')
    for i, l in enumerate(lines):
        print(f'  {i}: {l[:120]}')
except Exception as e:
    print(f'GBK error: {e}')

# 按行读取
print(f'\n--- line by line ---')
with open(fpath, 'rb') as f:
    for i, line in enumerate(f):
        if i >= 5:
            break
        # 尝试分割
        try:
            text = line.decode('gbk', errors='replace').strip()
            print(f'  line {i}: [{text}]')
        except:
            print(f'  line {i}: {line[:80]}')
