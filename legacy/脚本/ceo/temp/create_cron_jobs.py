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
    else:
        print(f'  ❌ {name}: {result.stderr or result.stdout[:200]}')

# ── data_from tasks ──
print('=== data_from (数据采集) ===')

create_cron(
    '08:30 盘前数据检查', 'data_from', '30 8 * * *',
    '请先咨询work-check确认今日是否A股交易日，问他："今天开不开盘"。若是，执行：'
    '1. 执行一次 盘前数据检查 的任务，检查上一个交易日数据的完整性；'
    '2. 若缺失超5%，自动触发补采脚本tasks/premarket_check.sh --repair；'
    '3. 向 总经理远山 提交《盘前数据验证报告》，含缺失率/补采状态。'
    '4.告诉work-check我开始定时任务了，让他每3分钟催促我一次（催促没反应就给我发 /compact 或者 /new ），直到我回复已完成。',
    timeout=300
)

create_cron(
    '09:27 集合竞价采集', 'data_from', '27 9 * * *',
    '请先咨询work-check确认今日是否A股交易日，问他："今天开不开盘"。若是，执行：'
    '1. 执行一次 集合竞价采集 的任务；'
    '2. 若有数据异常（如量比突增200%），立即重新获取最新数据并向远山推送《竞价异动警报》，含股票代码/异常字段/原始数据快照。'
    '3.告诉work-check我开始定时任务了，让他每3分钟催促我一次（催促没反应就给我发 /compact 或者 /new ），直到我回复已完成。',
    timeout=300
)

create_cron(
    '15:15 收盘数据采集', 'data_from', '15 15 * * *',
    '请先咨询work-check确认今日是否A股交易日，问他："今天开不开盘"。若是，执行：'
    '1. 进行收盘数据采集 交给data_in；'
    '2. 问总经理还缺啥数据。'
    '3. 若验证失败，/new 后重试3次，仍异常的话请示远山解决办法，并向远山提交《收盘数据异常报告》，含缺失表名/缺失率/最后一次重试日志。'
    '4.告诉work-check我开始定时任务了，让他每3分钟催促我一次（催促没反应就给我发 /compact 或者 /new ），直到我回复已完成。',
    timeout=600
)

create_cron(
    '21:00 新闻推送', 'data_from', '0 21 * * *',
    '请先咨询work-check确认今日是否A股交易日，问他："今天开不开盘"。若是，执行：'
    '1. 检查data_in.daily_news表新闻数量≥20条且含权威信源（新华社/财联社）；'
    '2. 若数据异常（如全为自媒体消息），/new 后重试3次，仍异常的话请示远山解决办法，并向远山提交《新闻数据质量报告》，标注低质量新闻占比。'
    '3.告诉work-check我开始定时任务了，让他每3分钟催促我一次（催促没反应就给我发 /compact 或者 /new ），直到我回复已完成。',
    timeout=300
)

# ── data_in tasks ──
print('=== data_in (数据计算) ===')

create_cron(
    '15:30 信号增量计算', 'data_in', '30 15 * * *',
    '请先咨询work-check确认今日是否A股交易日，问他："今天开不开盘"。若是，执行：'
    '1. 执行一遍 数据计算 任务；'
    '2. 若有数据异常，先问data_from要。'
    '3. 有问题立即向远山求助。'
    '4. 数据梳理一遍告诉总经理远山，让他飞书执行一下 当日收盘总结 任务 发给老板。'
    '5.告诉work-check我开始定时任务了，让他每3分钟催促我一次（催促没反应就给我发 /compact 或者 /new ），直到我回复已完成。',
    timeout=600
)

# ── strategy_engineer tasks ──
print('=== strategy_engineer (策略) ===')

create_cron(
    '21:50 制定明日计划', 'strategy_engineer', '50 21 * * *',
    '请先咨询work-check确认今日是否A股交易日，问他："今天开不开盘"。若是，执行：'
    '1. 执行 制定明日计划 任务；'
    '2. 具体数据问data_in要，缺数据问data_from要，协调问题找总经理。'
    '3. 若遇到错误，按照 /compact → /new → 切换模型 的思路重试3次，仍异常的话请示远山解决办法。'
    '4.告诉work-check我开始定时任务了，让他每3分钟催促我一次（催促没反应就给我发 /compact 或者 /new ），直到我回复已完成。',
    timeout=600
)

print()
print('🎉 全部重建完成！')
