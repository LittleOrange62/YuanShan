import os, shutil

WS = r'C:\Users\Administrator\.qwenpaw\workspaces\coder'
DEST = r'D:\finance\data\脚本'
EXCLUDE = {'.bootstrap_completed', '.mcp', '.skill.json.lock', 'agent.json', 'AGENTS.md', 'BOOTSTRAP.md', 'chats.json', 'credentials.yaml', 'HEARTBEAT.md', 'history.db', 'history.db-shm', 'history.db-wal', 'jobs.json', 'MEMORY.md', 'PROFILE.md', 'skill.json', 'SOUL.md'}

exts = ('.py', '.ps1', '.bat')
found = []
for root, dirs, files in os.walk(WS):
    # skip internal framework dirs
    rel = os.path.relpath(root, WS)
    top = rel.split(os.sep)[0] if rel != '.' else ''
    if top in ('memory','sessions','media','digest','checkpoints','drivers','skills','mem_agent','mem_metadata','mem_session','resource'):
        dirs[:] = []
        continue
    for f in files:
        if f.lower().endswith(exts) and f not in EXCLUDE:
            found.append(os.path.join(root, f))
print('found:')
for p in found:
    print(' ', p)

# mapping for known files
MAP = {
    'delete_0814.py': '04_数据清洗',
    'validate_0814.py': '03_数据校验',
    'verify_0814.py': '03_数据校验',
}
for p in found:
    name = os.path.basename(p)
    cat = MAP.get(name, '05_辅助工具')
    dst = os.path.join(DEST, cat, name)
    shutil.move(p, dst)
    print('moved', p, '->', dst)
