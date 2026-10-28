import os

base = r'C:\Users\Administrator\.qwenpaw\workspaces\ceo'
for sub in ['', 'configs', 'tasks', 'TOOLS', 'TOOLS\\scripts', 'TOOLS\\scripts\\temp']:
    p = os.path.join(base, sub)
    if os.path.isdir(p):
        print(f'[{sub}]')
        for f in sorted(os.listdir(p)):
            print('   ', f)
