# -*- coding: utf-8 -*-
"""统一修改所有 agent 配置：
1. strategy: scroll -> native（关闭滚动记忆注入）
2. ceo: 禁用大体积/低频工具（保留在配置中，需要时通过 UI/API 重新启用）
"""
import json, os, shutil, time

base = r'C:\Users\Administrator\.qwenpaw\workspaces'
ts = time.strftime('%Y%m%d_%H%M%S')
bak_dir = os.path.join(base, 'ceo', 'TOOLS', 'scripts', 'temp', 'cfg_backup_' + ts)
os.makedirs(bak_dir, exist_ok=True)

# 大体积/低频工具清单（ceo 禁用）
CEO_DISABLE = [
    'browser',            # 浏览器自动化，schema 巨大，低频
    'browser_use',        # 旧版浏览器工具
    'run_tool_batch',     # 批量执行，schema 巨大，低频
    'spawn_subagent',     # 临时子任务，低频
    'materialize_skill',  # 技能物化，低频
    'ast_search',         # 代码 AST 搜索，低频
    'desktop_screenshot', # 桌面截图，低频
    'view_video',         # 视频查看，模型不支持多模态
    'set_user_timezone',  # 极少用
]

changed = []
for f in sorted(os.listdir(base)):
    cfg = os.path.join(base, f, 'agent.json')
    if not os.path.isfile(cfg):
        continue
    with open(cfg, encoding='utf-8') as fh:
        j = json.load(fh)
    # 备份
    shutil.copy2(cfg, os.path.join(bak_dir, f + '_agent.json'))

    old_strategy = j.get('running', {}).get('light_context_config', {}).get('strategy')
    old_ml = j.get('running', {}).get('max_input_length')

    # 1. strategy scroll -> native
    if j.get('running', {}).get('light_context_config', {}).get('strategy') == 'scroll':
        j['running']['light_context_config']['strategy'] = 'native'

    # 2. ceo 禁用大工具
    disabled_here = []
    if f == 'ceo':
        tools = j.get('tools', {}).get('builtin_tools', {})
        for tname in CEO_DISABLE:
            if tname in tools and tools[tname].get('enabled'):
                tools[tname]['enabled'] = False
                disabled_here.append(tname)

    with open(cfg, 'w', encoding='utf-8') as fh:
        json.dump(j, fh, ensure_ascii=False, indent=2)

    print(f'=== {f} ===')
    print(f'  strategy: {old_strategy} -> {j["running"]["light_context_config"]["strategy"]}')
    if disabled_here:
        print(f'  ceo disabled tools: {", ".join(disabled_here)}')
    changed.append(f)

print()
print('备份目录:', bak_dir)
print('已修改:', ', '.join(changed))
