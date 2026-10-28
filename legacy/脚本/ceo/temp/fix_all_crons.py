#!/usr/bin/env python3
"""
批量修复所有定时任务
1. 重写 work-check 4 条任务 → 覆盖全员监督 + 自检 + 会话水位
2. 修复 data_from 21:00 新闻推送 → 引用 daily_news_push.py，去掉交易日检查
3. 对齐 data_in / strategy_engineer 文本 → 明确引用 .md 文档
4. 手动触发一次 21:00 新闻推送验证
"""

import subprocess, json

BOSS_SESSION = "9bdb_2bdfbc55"
BOSS_USER = "group"
CHANNEL = "feishu"

def create_cron(name, agent_id, cron_expr, text, timeout=600):
    cmd = [
        'qwenpaw', 'cron', 'create',
        '--type', 'agent',
        '--name', name,
        '--cron', cron_expr,
        '--timezone', 'Asia/Shanghai',
        '--channel', CHANNEL,
        '--target-user', BOSS_USER,
        '--target-session', BOSS_SESSION,
        '--agent-id', agent_id,
        '--text', text,
        '--timeout', str(timeout),
        '--no-share-session',
        '--enabled',
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    if 'id' in result.stdout:
        data = json.loads(result.stdout)
        print(f'  ✅ {name} (id={data["id"][:8]}...)')
        return data['id']
    else:
        print(f'  ❌ {name}: {result.stderr or result.stdout[:200]}')
        return None

def delete_job(job_id, agent_id):
    result = subprocess.run(
        ['qwenpaw', 'cron', 'delete', job_id, '--agent-id', agent_id],
        capture_output=True, text=True, timeout=30
    )
    status = 'OK' if 'true' in result.stdout else f'FAIL: {result.stderr}'
    print(f'  🗑️  {job_id[:8]} ({agent_id}) → {status}')

def list_jobs(agent_id):
    result = subprocess.run(['qwenpaw', 'cron', 'list', '--agent-id', agent_id],
                          capture_output=True, text=True, timeout=30)
    return json.loads(result.stdout)

# ================================================================
# STEP 1: Delete ALL old work-check jobs
# ================================================================
print('\n=== Step 1: 清理旧 work-check 任务 ===')
wc_jobs = list_jobs('work-check')
for j in wc_jobs:
    delete_job(j['id'], 'work-check')

# ================================================================
# STEP 2: Create NEW work-check jobs (4 tasks)
# ================================================================
print('\n=== Step 2: 创建新 work-check 任务 ===')

create_cron(
    'work-check-早盘0900监督', 'work-check', '0 9 * * *', '''【定时任务】09:00 早盘监督 — 事前检测 + 会话健康度

执行三段式监督：

🔍 事前检测 — 覆盖全员任务就绪检查
1. 确认 data_from 的"08:30 盘前数据检查"是否已完成并提交《盘前数据验证报告》
2. 检查 data_in 昨日信号计算是否正常
3. 若某员工未完成 → 按督促节奏介入：
   T+10min 发 /status → T+15min 发 /compact → T+20min 发 /clear
   并通知远山

🔬 会话健康度检查 — 全员扫描
1. 分别检查 data_from / data_in / manager / strategy_engineer 最新 session 水位
2. 告警规则：
   - ⚠️ 文件>800KB 或 ratio>0.6 → 报告标注，次日复查
   - 🔴 文件>1.5MB 或 ratio>0.85 → 通知远山执行 /compact
   - 🚨 文件>2MB 或 ratio>1.0 → 立即通知远山+老板

🔄 自检
1. 检查 work-check 自身 session 水位（自己也要查）
2. 水位>0.6 → 给自己发 /compact

📋 输出监督报告（推飞书群）
- 各员工就绪状态：data_from=✅/⏳/❌  data_in=✅/⏳/❌
- 会话健康度汇总：正常 / ⚠️注意 / 🔴告警 / 🚨紧急
- 待处理项：/compact / /clear / 无需操作''', timeout=300)

create_cron(
    'work-check-竞价0927监督', 'work-check', '27 9 * * *', '''【定时任务】09:27 竞价监督 — 事中监督

执行事中监督：

🔍 任务进度监督 — data_from
1. 确认 data_from 的"09:27 集合竞价采集"是否已启动
2. 等待 data_from 回复"已完成"
3. 督促节奏：T+10min /status → T+15min /compact → T+20min /clear → T+25min 通知远山换模型

⚠️ 竞价异动监控
1. 检查是否有来自 data_from 的《竞价异动警报》（量比突增200%等）
2. 若有异动 → 确认远山已被通知，标注监督日志

🔬 会话健康度 — data_from 水位
1. 检查 data_from session 最新水位
2. ⚠️ ratio>0.6 → 报告 / 🔴 ratio>0.85 → 通知远山 /compact

📋 监督报告推飞书
- 竞价采集状态：✅ / ⏳ / ❌
- 竞价异动：无 / 有（已通知远山）
- data_from 会话健康度：正常 / 注意 / 告警''', timeout=300)

create_cron(
    'work-check-收盘1655监督', 'work-check', '55 16 * * *', '''【定时任务】16:55 收盘监督 — 事中监督 + 事后复盘

执行三段式监督：

🔍 事前检测
1. 确认 data_from 的"16:55 收盘数据采集"是否已触发
2. 检查 data_in 的"17:45 信号增量计算"前置数据是否就绪

🔬 事中监督
1. 监督 data_from 收盘采集进度，超15min无响应按督促节奏介入
2. data_from 完成后 → 监督 data_in 信号计算启动
3. 各环节超时阈值：data_from 15min / data_in 15min
4. 督促节奏：/status → /compact → /clear → 通知远山换模型

📋 事后复盘 — 收盘 session 巡检
1. 收盘后巡检全员 session 水位（data_from / data_in / manager）
2. 水位>0.85 → 通知远山执行 /clear
3. 水位>0.6 → 通知远山执行 /compact

🚨 告警升级
- ⚠️ 单次脚本缺失/日志少量异常 → 报告标注，次日复查
- 🔴 智能体连续2次不响应 → 飞书告警 + 通知远山
- 🚨 数据库损坏/磁盘满 → 飞书告警 + 通知远山+老板

📝 记录监督日志到 memory/YYYY-MM-DD.md''', timeout=300)

create_cron(
    'work-check-评分1730监督', 'work-check', '30 17 * * *', '''【定时任务】17:30 评分监督 — 事中监督 + 日终巡检（覆盖全员）

执行三段式监督：

🔍 事前检测 — 数据就绪检查
1. 确认 data_in 的"17:45 信号增量计算"是否已完成
2. 若未完成 → 督促 data_in 加快进度

🔬 事中监督 — 评分+收盘总结
1. 确认 manager 的"17:30 自选股评分"是否已触发
2. 监督评分执行进度，超时20分钟按督促节奏介入
3. 确认 manager 的"17:50 收盘总结"是否完成

📋 事后复盘 — 日终全员巡检
1. ✅ 巡检 manager — 评分+收盘总结是否完成
2. ✅ 巡检 strategy_engineer — "21:50 制定明日计划"是否就绪
3. ✅ 巡检 data_from — 收盘采集+新闻推送是否完成
4. ✅ 巡检 data_in — 信号计算是否完成
5. ✅ 会话水位全员扫描：data_from / data_in / manager / strategy_engineer / work-check（自检）
6. 水位>0.85 → 通知远山执行 /clear

🔄 自检
1. 检查 work-check 自身 session 水位
2. 检查 monitor.sh / review.sh 是否正常可用
3. 高水位(>0.6) → 给自己 /compact

📝 将日终巡检结果记录到 memory/YYYY-MM-DD.md''', timeout=300)

print('\n✅ Work-check 任务重建完成')

# ================================================================
# STEP 3: Fix data_from 21:00 新闻推送
# ================================================================
print('\n=== Step 3: 修复 data_from 21:00 新闻推送 ===')

df_jobs = list_jobs('data_from')
for j in df_jobs:
    if '新闻' in j['name'] or '21:00' in j['name']:
        print(f'  删除旧任务: {j["name"]} ({j["id"][:8]})')
        delete_job(j['id'], 'data_from')

create_cron(
    '21:00 新闻推送', 'data_from', '0 21 * * *', '''【定时任务】21:00 新闻推送

执行 tasks/evening_news.md 任务：
采集当日重大新闻 + 热点 Top20，推送给总经理和飞书群。

📋 执行步骤：
1. 读取 tasks/daily_news_push.py 固化脚本，直接执行：python3 /app/working/workspaces/data_from/tasks/daily_news_push.py
2. 该脚本会自动完成：采集新浪财经(5栏目) + 华尔街见闻 → 个股匹配 → 热点评分排序 → 推飞书群
3. 无需确认是否交易日，每晚均执行汇总近24小时重大财经新闻
4. 新闻简报推送到飞书群聊后，告知总经理"晚间新闻已推送"

⚠️ 注意：直接执行脚本，不需要问任何人"开不开盘"。''', timeout=300)

print('✅ 新闻推送任务已修复')

# ================================================================
# STEP 4: Align data_in text to reference task doc
# ================================================================
print('\n=== Step 4: 对齐 data_in 17:45 信号计算文本 ===')

di_jobs = list_jobs('data_in')
for j in di_jobs:
    if '信号' in j['name'] or '17:45' in j['name']:
        print(f'  删除旧任务: {j["name"]} ({j["id"][:8]})')
        delete_job(j['id'], 'data_in')

create_cron(
    '17:45 信号增量计算', 'data_in', '45 17 * * *', '''【定时任务】17:45 信号增量计算

请先咨询work-check确认今日是否A股交易日，问他："今天开不开盘"。若是，执行：

1. 按 tasks/signal_calc.md 执行信号增量计算（R001-R007 共7个技术信号）
2. 若有数据异常，先问data_from要最新数据
3. 有问题立即向远山求助
4. 信号计算完成后，梳理关键结果告诉总经理远山，让他执行飞书推送当日收盘总结
5. 告诉work-check我开始定时任务了，让他每3分钟催促我一次（催促没反应就给我发 /compact 或者 /new ），直到我回复已完成。''', timeout=600)

print('✅ data_in 信号计算文本已对齐')

# ================================================================
# STEP 5: Align strategy_engineer text
# ================================================================
print('\n=== Step 5: 对齐 strategy_engineer 21:50 明日计划文本 ===')

se_jobs = list_jobs('strategy_engineer')
for j in se_jobs:
    print(f'  删除旧任务: {j["name"]} ({j["id"][:8]})')
    delete_job(j['id'], 'strategy_engineer')

create_cron(
    '21:50 制定明日计划', 'strategy_engineer', '50 21 * * *', '''【定时任务】21:50 制定明日计划

请先咨询work-check确认今日是否A股交易日，问他："今天开不开盘"。若是，执行：

1. 按 tasks/plan_tomorrow.md 执行明日计划制定
2. 具体数据问data_in要，缺数据问data_from要，协调问题找总经理
3. 若遇到错误，按照 /compact → /new → 切换模型 的思路重试3次，仍异常的话请示远山解决办法
4. 产出《明日交易计划》报告，推送给飞书群
5. 告诉work-check我开始定时任务了，让他每3分钟催促我一次（催促没反应就给我发 /compact 或者 /new ），直到我回复已完成。''', timeout=600)

print('✅ strategy_engineer 明日计划文本已对齐')

# ================================================================
# STEP 6: Verify all jobs
# ================================================================
print('\n\n=== Step 6: 验证所有定时任务 ===')
for aid in ['data_from', 'data_in', 'manager', 'strategy_engineer', 'work-check']:
    jobs = list_jobs(aid)
    if not jobs:
        print(f'  [{aid}] ❌ 无任务')
        continue
    print(f'\n  [{aid}] {len(jobs)} 条:')
    for j in jobs:
        enabled = '✅' if j['enabled'] else '❌'
        timeout = j['runtime']['timeout_seconds']
        # get first 60 chars of text
        inputs = j.get('request', {}).get('input', [])
        text_preview = ''
        for inp in inputs:
            for content in inp.get('content', []):
                if content.get('type') == 'text':
                    text_preview = content['text'][:80].replace('\n', ' ')
        print(f'    {enabled} [{j["schedule"]["cron"]}] {j["name"]:30s} | {timeout}s')
        print(f'       📝 {text_preview}...')

print('\n🎉 全部修复完成！')
