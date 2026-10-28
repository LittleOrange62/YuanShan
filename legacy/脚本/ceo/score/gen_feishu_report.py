#!/usr/bin/env python3
"""
gen_feishu_report.py — 生成远山评分卡并推飞书
用法: python3 gen_feishu_report.py [YYYY-MM-DD]
不传参则用最新交易日
"""

import sys
import os
import subprocess
from datetime import datetime

# 飞书推送脚本
PUSH_SCRIPT = '/app/working/workspaces/manager/temp/push_feishu_now.sh'
# 评分读取脚本
SCORE_SCRIPT = '/app/working/workspaces/manager/daily_score_update.py'
# 报告输出目录
REPORT_DIR = '/app/working/workspaces/manager/temp'


def log(msg):
    t = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    print(f'[{t}] {msg}', flush=True)


def main():
    override = sys.argv[1] if len(sys.argv) > 1 else None

    # Step 1: 生成报告
    log('生成远山评分卡...')
    cmd = ['python3', SCORE_SCRIPT]
    if override:
        cmd.append(override)

    try:
        result = subprocess.run(cmd, capture_output=True, timeout=300, text=True)
        if result.returncode != 0:
            log(f'❌ 评分计算失败: {result.stderr[-300:]}')
            print(result.stdout)
            return
        report = result.stdout
    except subprocess.TimeoutExpired:
        log('❌ 评分计算超时')
        return
    except Exception as e:
        log(f'❌ 异常: {e}')
        return

    # Step 2: 推送到飞书
    log('推送飞书...')
    if not os.path.exists(PUSH_SCRIPT):
        log(f'❌ 推送脚本不存在: {PUSH_SCRIPT}')
        print(report)
        return

    try:
        # 将报告写入临时文件（避免shell转义问题）
        report_file = os.path.join(REPORT_DIR, 'temp_report.txt')
        with open(report_file, 'w', encoding='utf-8') as f:
            f.write(report)

        # 推送
        push_result = subprocess.run(
            ['bash', PUSH_SCRIPT, report_file],
            capture_output=True, timeout=60, text=True
        )
        if push_result.returncode == 0:
            log('✅ 飞书推送成功')
        else:
            log(f'⚠️ 飞书推送可能有误: {push_result.stderr[-200:]}')
            # fallback: 控制台输出
            print(report)
    except Exception as e:
        log(f'❌ 推送异常: {e}')
        print(report)


if __name__ == '__main__':
    main()
