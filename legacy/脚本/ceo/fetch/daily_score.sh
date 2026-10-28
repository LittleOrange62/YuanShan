#!/bin/bash
# 每日15:30(CST=07:30UTC) 自选股评分计算 + 飞书推送
PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/app/venv/bin

LOG=/app/working/workspaces/manager/temp/daily_score.log
QWENPAW=/app/venv/bin/qwenpaw
PYTHON=/app/venv/bin/python3
REPORT_FILE=/app/working/workspaces/manager/temp/daily_push_msg.txt

echo "===== $(date '+%Y-%m-%d %H:%M:%S') =====" >> $LOG

# 周末跳过
DOW=$(date '+%u')
if [ "$DOW" -gt 5 ]; then
    echo "周末，跳过" >> $LOG
    exit 0
fi

# 1. 执行评分计算
$PYTHON /app/working/workspaces/manager/temp/daily_score_update.py >> $LOG 2>&1
echo "评分计算完成" >> $LOG

# 2. 生成飞书推送报告
$PYTHON /app/working/workspaces/manager/temp/gen_feishu_report.py > $REPORT_FILE 2>>$LOG

if [ $? -eq 0 ] && [ -s "$REPORT_FILE" ]; then
    # 3. 推送到飞书
    REPORT=$(cat $REPORT_FILE)
    $QWENPAW channels send \
        --agent-id manager \
        --channel feishu \
        --target-user ou_56057783def739f18b5721a28d68910c \
        --target-session 9bdb_2bdfbc55 \
        --text "$REPORT" >> $LOG 2>&1
    echo "飞书推送完成" >> $LOG
    rm -f $REPORT_FILE
else
    echo "报告生成失败" >> $LOG
fi

echo "" >> $LOG
