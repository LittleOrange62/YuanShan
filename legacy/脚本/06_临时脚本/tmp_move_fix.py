import os, shutil
src = r'D:\finance\脚本'
dst = r'D:\finance\data\脚本'
if os.path.isdir(src) and not os.path.exists(dst):
    shutil.move(src, dst)
    print('moved')
elif os.path.exists(dst):
    print('dst exists, merging')
    for item in os.listdir(src):
        s = os.path.join(src, item)
        d = os.path.join(dst, item)
        shutil.move(s, d)
    os.rmdir(src)
    print('merged')
else:
    print('src missing?', os.path.isdir(src), os.path.exists(dst))
print(os.path.isdir(dst))
