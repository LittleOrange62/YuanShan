import subprocess, json

for agent in ['data_from', 'data_in', 'manager', 'strategy_engineer', 'work-check']:
    result = subprocess.run(
        ['qwenpaw', 'cron', 'list', '--agent-id', agent],
        capture_output=True, text=True, timeout=30
    )
    jobs = json.loads(result.stdout)
    print(f'{agent}: {len(jobs)} 条')
    for j in jobs:
        print(f'  [{j["schedule"]["cron"]}] {j["name"]} | enabled={j["enabled"]} | channel={j["dispatch"]["channel"]} | timeout={j["runtime"]["timeout_seconds"]}s')
