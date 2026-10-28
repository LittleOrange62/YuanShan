import os, glob
d = r'D:\finance\data'
for f in sorted(glob.glob(os.path.join(d, '*.db'))):
    print(f'{os.path.basename(f)}: {os.path.getsize(f)/1024/1024:.1f} MB')
