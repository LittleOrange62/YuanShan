import subprocess, json

def delete_job(job_id, agent_id):
    result = subprocess.run(
        ['qwenpaw', 'cron', 'delete', job_id, '--agent-id', agent_id],
        capture_output=True, text=True, timeout=30
    )
    status = 'OK' if 'true' in result.stdout else f'FAIL: {result.stderr}'
    print(f'  {job_id} ({agent_id}) → {status}')

# Delete boss's original console-channel jobs
print('=== 删除老板原版 console 渠道任务 ===')
delete_job('a694a976-5cac-4570-bee8-bbc55c553693', 'data_from')  # 09:27
delete_job('7732771e-f225-47c2-acbb-ba78e3bdb497', 'data_from')  # 15:15
delete_job('567960f3-7096-49b4-9022-2ef818d3fa75', 'data_from')  # 21:00
delete_job('5b607819-4c32-49b4-bd2b-ac0193211b2d', 'data_in')    # 15:30
delete_job('c44038ce-a0bf-457a-9a02-e8ea3174b90a', 'strategy_engineer')  # 21:50
delete_job('ba27f76c-da3f-432d-9554-c67b12495c3c', 'manager')    # 9:04

print()
print('=== 删除我的重复项 ===')
delete_job('718638dc-56c7-4036-afe7-2e0d261a62d6', 'data_from')  # 盘前检查-0900
delete_job('addce318-3314-4d9c-8055-ba6c24420f49', 'data_from')  # 集合竞价-0927
delete_job('ccb0f140-2ca1-4d64-b93e-815acb3138ea', 'data_from')  # 收盘采集-1510
delete_job('6bc773af-5a53-4b6f-bcf8-f797559aab83', 'data_from')  # 晚间新闻-2100
delete_job('827f68f2-2966-4f89-b6aa-ce20a634350a', 'data_in')    # 信号计算-1530

print()
print('=== 完成 ===')
