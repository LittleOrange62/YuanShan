import os, glob
d = r'D:\finance\data'
# 删除所有 .db 文件
for f in glob.glob(os.path.join(d, '*.db')):
    os.remove(f)
    print(f'Deleted: {f}')
# 删除 .db-shm 和 .db-wal
for ext in ['*.db-shm', '*.db-wal']:
    for f in glob.glob(os.path.join(d, ext)):
        os.remove(f)
        print(f'Deleted: {f}')
print('Cleanup done.')
