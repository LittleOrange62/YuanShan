import os, shutil
WS = r'C:\Users\Administrator\.qwenpaw\workspaces\tq'
DEST = r'D:\finance\data\脚本\06_临时脚本'
for f in ['tmp_find_script_dirs.py', 'tmp_move_fix.py', 'tmp_move_coder.py', 'tmp_cleanup.py']:
    p = os.path.join(WS, f)
    if os.path.isfile(p):
        shutil.move(p, os.path.join(DEST, f))
        print('moved', f)
