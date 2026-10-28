import os
r=[]
for root, dirs, files in os.walk(r'D:\finance'):
    for d in dirs:
        if ('脚本' in d or d.startswith('01_') or d.startswith('02_') or
            d.startswith('03_') or d.startswith('04_') or d.startswith('05_') or
            d.startswith('06_')):
            r.append(os.path.join(root,d))
print('\n'.join(r[:100]))
print('total', len(r))
