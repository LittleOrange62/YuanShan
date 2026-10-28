import subprocess, json

def list_and_fix(agent_id):
    """List all cron jobs for an agent and return them"""
    result = subprocess.run(['qwenpaw', 'cron', 'list', '--agent-id', agent_id],
                          capture_output=True, text=True, timeout=30)
    return json.loads(result.stdout)

def delete_job(job_id, agent_id):
    result = subprocess.run(['qwenpaw', 'cron', 'delete', job_id, '--agent-id', agent_id],
                          capture_output=True, text=True, timeout=30)
    return 'true' in result.stdout

def create_job(name, agent_id, cron_expr, text, timeout=600):
    import subprocess
    cmd = [
        'qwenpaw', 'cron', 'create',
        '--type', 'agent',
        '--name', name,
        '--cron', cron_expr,
        '--timezone', 'Asia/Shanghai',
        '--channel', 'feishu',
        '--target-user', 'group',
        '--target-session', '9bdb_2bdfbc55',
        '--agent-id', agent_id,
        '--text', text,
        '--timeout', str(timeout),
        '--no-share-session',
        '--enabled',
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    if 'id' in result.stdout:
        data = json.loads(result.stdout)
        return data['id']
    else:
        print(f'  ❌ FAIL: {result.stderr or result.stdout[:200]}')
        return None

# Step 1: List all current jobs
print('=== 当前状态 ===')
for aid in ['data_from', 'data_in', 'manager', 'strategy_engineer', 'work-check']:
    jobs = list_and_fix(aid)
    for j in jobs:
        print(f'  [{aid}] {j["name"]:30s} cron={j["schedule"]["cron"]:12s} id={j["id"][:8]} enabled={j["enabled"]}')

# Step 2: Delete ALL messed-up afternoon jobs
print('\n=== 清理篡改的任务 ===')

# data_from - keep 08:30, 09:27, 21:00; delete 16:30
jobs_df = list_and_fix('data_from')
for j in jobs_df:
    if '16:30' in j['name'] or '收盘数据' in j['name']:
        if j['schedule']['cron'] != '15 15 * * *':
            delete_job(j['id'], 'data_from')
            print(f'  Deleted data_from: {j["name"]} ({j["schedule"]["cron"]})')

# data_in - delete 16:50
jobs_di = list_and_fix('data_in')
for j in jobs_di:
    if j['schedule']['cron'] != '30 15 * * *':
        delete_job(j['id'], 'data_in')
        print(f'  Deleted data_in: {j["name"]} ({j["schedule"]["cron"]})')

# manager - delete both (they're disabled and wrong times)
jobs_mgr = list_and_fix('manager')
for j in jobs_mgr:
    delete_job(j['id'], 'manager')
    print(f'  Deleted manager: {j["name"]} ({j["schedule"]["cron"]})')

# work-check - delete afternoon ones (15:10→17:18, 15:30→22:00)
jobs_wc = list_and_fix('work-check')
for j in jobs_wc:
    if j['schedule']['cron'] not in ['0 9 * * *', '27 9 * * *']:
        delete_job(j['id'], 'work-check')
        print(f'  Deleted work-check: {j["name"]} ({j["schedule"]["cron"]})')

print('\n=== 重建任务 ===')

# data_from: 15:15 收盘数据采集 (boss's original time)
BOSS_PROMPT_CLOSE = (
    '请先咨询work-check确认今日是否A股交易日，问他："今天开不开盘"。若是，执行：'
    '1. 进行收盘数据采集 交给data_in；'
    '2. 问总经理还缺啥数据。'
    '3. 若验证失败，/new 后重试3次，仍异常的话请示远山解决办法，并向远山提交《收盘数据异常报告》，含缺失表名/缺失率/最后一次重试日志。'
    '4.告诉work-check我开始定时任务了，让他每3分钟催促我一次（催促没反应就给我发 /compact 或者 /new ），直到我回复已完成。'
)
create_job('15:15 收盘数据采集', 'data_from', '15 15 * * *', BOSS_PROMPT_CLOSE, timeout=600)

# data_in: 15:30 信号增量计算 (boss's original time)
BOSS_PROMPT_SIGNAL = (
    '请先咨询work-check确认今日是否A股交易日，问他："今天开不开盘"。若是，执行：'
    '1. 执行一遍 数据计算 任务；'
    '2. 若有数据异常，先问data_from要。'
    '3. 有问题立即向远山求助。'
    '4. 数据梳理一遍告诉总经理远山，让他飞书执行一下 当日收盘总结 任务 发给老板。'
    '5.告诉work-check我开始定时任务了，让他每3分钟催促我一次（催促没反应就给我发 /compact 或者 /new ），直到我回复已完成。'
)
create_job('15:30 信号增量计算', 'data_in', '30 15 * * *', BOSS_PROMPT_SIGNAL, timeout=600)

# manager: 15:30 自选股评分 (my new job)
MY_PROMPT_SCORE = '【定时任务】自选股评分时间到。请按 tasks/self_select_score.md 执行自选股评分计算，评分完成后推送飞书报告。'
create_job('manager-自选股评分-1530', 'manager', '30 15 * * *', MY_PROMPT_SCORE, timeout=600)

# manager: 15:50 收盘总结 (my new job)
MY_PROMPT_SUMMARY = '【定时任务】收盘总结时间到。请按 tasks/daily_close_summary.md 执行当日收盘总结并推送飞书报告。'
create_job('manager-收盘总结-1550', 'manager', '50 15 * * *', MY_PROMPT_SUMMARY, timeout=600)

# work-check: 15:10 收盘监督
create_job('work-check-收盘1510监督', 'work-check', '10 15 * * *',
           '【定时任务】15:10 收盘监督。请执行 monitor.sh 检查收盘采集是否完成，汇报监督结果。',
           timeout=300)

# work-check: 15:30 信号监督
create_job('work-check-信号1530监督', 'work-check', '30 15 * * *',
           '【定时任务】15:30 信号监督。请执行 monitor.sh 检查信号计算是否完成，汇报监督结果。',
           timeout=300)

print('\n=== 最终验证 ===')
for aid in ['data_from', 'data_in', 'manager', 'strategy_engineer', 'work-check']:
    jobs = list_and_fix(aid)
    for j in jobs:
        en = '✅' if j['enabled'] else '❌'
        print(f'  {en} [{aid}] {j["name"]:30s} cron={j["schedule"]["cron"]:12s} timeout={j["runtime"]["timeout_seconds"]}s')
