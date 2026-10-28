"""探查 BlockMapXML.dat 文件格式"""
import os

fpath = r'D:\new_tdx64\BlockMap\BlockMapXML.dat'
fsize = os.path.getsize(fpath)
print(f'FILE: BlockMapXML.dat  size={fsize}')

with open(fpath, 'rb') as f:
    raw = f.read(2048)

# 尝试UTF-8
try:
    text = raw.decode('utf-8')
    print('--- UTF-8 decode ---')
    print(text[:3000])
except:
    pass

# 尝试GBK
try:
    text = raw.decode('gbk', errors='replace')
    print('\n--- GBK decode ---')
    print(text[:3000])
except:
    pass

# 尝试ASCII可显示
ascii_str = ''.join(chr(b) if 32 <= b < 127 else '.' for b in raw[:200])
print(f'\n--- ASCII ---\n{ascii_str}')
