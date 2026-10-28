#!/bin/bash
# ============================================================
# 飞书推送脚本 — 总经理专用唯一推送入口
# 
# 用法:
#   ./push_feishu_now.sh                    # 自动生成评分卡并推送（交易日）
#   ./push_feishu_now.sh --text "内容"      # 推送指定文本（需预先审核）
#   ./push_feishu_now.sh --file /path/file  # 推送指定文件内容
#   ./push_feishu_now.sh --check-only       # 只生成报告，不推送（用于审核）
#
# 管控规则（详见 configs/feishu_push_governance.md）：
#   1. 所有推送必须经总经理审核确认
#   2. 标准化评分卡可直接推送（模板已固化）
#   3. 非标准化内容需人工审核后再调用 --text 推送
#   4. 身份固定为 --agent-id manager
# ============================================================
PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/app/venv/bin
REPORT_DIR=/app/working/workspaces/manager/temp
LOG=/app/working/workspaces/manager/temp/daily_score.log
SCRIPT_DIR=/app/working/workspaces/manager
mkdir -p $REPORT_DIR

cd $SCRIPT_DIR

TODAY=$(TZ=Asia/Shanghai date +%Y-%m-%d)
TODAY_RAW=$(echo $TODAY | tr -d '-')
WEEKDAY=$(TZ=Asia/Shanghai date +%u)  # 1=Mon...7=Sun

# ============================================================
# 解析参数
# ============================================================
MODE="auto"
CUSTOM_TEXT=""
CUSTOM_FILE=""

while [ $# -gt 0 ]; do
    case "$1" in
        --text)
            MODE="text"
            shift
            CUSTOM_TEXT="$1"
            ;;
        --file)
            MODE="file"
            shift
            CUSTOM_FILE="$1"
            ;;
        --check-only)
            MODE="check-only"
            ;;
        *)
            echo "未知参数: $1" >&2
            echo "用法: $0 [--text '内容'|--file /path|--check-only]" >&2
            exit 1
            ;;
    esac
    shift
done

# ============================================================
# 推送函数（实际执行推送，仅总经理可调用）
# ============================================================
do_push() {
    local content="$1"
    echo "[$(date)] 推送至飞书群聊 9bdb_2bdfbc55 ..." >> $LOG
    /app/venv/bin/qwenpaw channels send \
        --agent-id manager \
        --channel feishu \
        --target-user ou_56057783def739f18b5721a28d68910c \
        --target-session 9bdb_2bdfbc55 \
        --text "$content" 2>&1
    local rc=$?
    if [ $rc -eq 0 ]; then
        echo "[$(date)] ✅ 推送成功" >> $LOG
    else
        echo "[$(date)] ❌ 推送失败 (rc=$rc)" >> $LOG
    fi
    return $rc
}

# ============================================================
# 内容审核检查（预检）
# ============================================================
check_content() {
    local content="$1"
    local issues=""

    # 检查是否包含原始错误信息
    if echo "$content" | grep -qiE "(Traceback|Error|Exception|FileNotFoundError|KeyError|SyntaxError)"; then
        issues="${issues}[FAIL] 包含原始异常堆栈，应消化为可读信息\n"
    fi

    # 检查是否包含过程日志特征
    if echo "$content" | grep -qiE "(Step \d|开始执行|正在处理|执行完毕|DEBUG|print\(|pd\.)"; then
        issues="${issues}[FAIL] 包含过程日志/调试输出，应只发送最终结果\n"
    fi

    # 检查是否为空
    if [ -z "$content" ] || [ ${#content} -lt 20 ]; then
        issues="${issues}[FAIL] 内容为空或过短\n"
    fi

    # 输出检查结果
    if [ -n "$issues" ]; then
        echo -e "⚠️ 内容审核未通过:\n$issues" >&2
        return 1
    fi

    echo "✅ 内容审核通过" >&2
    return 0
}

# ============================================================
# 根据模式执行
# ============================================================
if [ "$MODE" = "text" ]; then
    # 推送指定文本（已预审内容）
    echo "[$(date)] ===== 推送指定文本: $TODAY =====" >> $LOG
    echo "[$(date)] 内容预览: ${CUSTOM_TEXT:0:100}..." >> $LOG

    # 审核检查
    if ! check_content "$CUSTOM_TEXT"; then
        echo "[$(date)] ❌ 内容审核不通过，拒绝推送" >> $LOG
        exit 1
    fi

    do_push "$CUSTOM_TEXT"
    exit $?

elif [ "$MODE" = "file" ]; then
    # 推送指定文件
    if [ ! -f "$CUSTOM_FILE" ]; then
        echo "❌ 文件不存在: $CUSTOM_FILE" >&2
        exit 1
    fi
    CONTENT=$(cat "$CUSTOM_FILE")
    echo "[$(date)] ===== 推送文件内容: $CUSTOM_FILE =====" >> $LOG
    echo "[$(date)] 内容预览: ${CONTENT:0:100}..." >> $LOG

    if ! check_content "$CONTENT"; then
        echo "[$(date)] ❌ 内容审核不通过，拒绝推送" >> $LOG
        exit 1
    fi

    do_push "$CONTENT"
    exit $?

elif [ "$MODE" = "check-only" ]; then
    # 只生成报告不推送（用于审核）
    echo "[$(date)] ===== 检查模式: 生成报告不推送 =====" >> $LOG
    echo "[$(date)] 交易日判断中..." >> $LOG

    IS_TRADING_DAY=$(python3 -c "
import sqlite3
try:
    c = sqlite3.connect('/app/working/workspaces/data_in/data/kline_2026.db')
    cnt = c.execute('SELECT COUNT(*) FROM daily_kline WHERE trade_date=?', ('$TODAY_RAW',)).fetchone()[0]
    c.close()
    print(1 if cnt > 5 else 0)
except:
    print(0)
")

    if [ "$IS_TRADING_DAY" = "1" ]; then
        LATEST_DATE=$(python3 -c "
import sqlite3
try:
    c = sqlite3.connect('/app/working/workspaces/data_in/data/kline_2026.db')
    r = c.execute('SELECT MAX(trade_date) FROM daily_kline').fetchone()
    d = r[0] if r and r[0] else '$TODAY_RAW'
    if d and len(d) == 8:
        d = f'{d[:4]}-{d[4:6]}-{d[6:]}'
    print(d)
    c.close()
except:
    print('$TODAY')
")
        echo "[$(date)] 最新交易日: $LATEST_DATE" >> $LOG

        RAW_FILE="$REPORT_DIR/raw_report_$(echo $LATEST_DATE | tr -d '-').txt"
        CLEAN_FILE="$REPORT_DIR/clean_report_$(echo $LATEST_DATE | tr -d '-').txt"

        python3 daily_score_update.py $LATEST_DATE 2>&1 | tee $RAW_FILE
        RESULT=${PIPESTATUS[0]}

        if [ $RESULT -eq 0 ]; then
            python3 $SCRIPT_DIR/temp/extract_report.py "$RAW_FILE" "$CLEAN_FILE"
            echo "[$(date)] ✅ 报告已生成 (未推送): $CLEAN_FILE" >> $LOG
            echo "报告路径: $CLEAN_FILE"
            echo "预览:"
            head -30 "$CLEAN_FILE"
        else
            echo "[$(date)] ❌ 评分卡生成失败" >> $LOG
        fi
    else
        echo "[$(date)] 非交易日，跳过评分卡" >> $LOG
    fi
    exit 0

else
    # ============================================================
    # 自动模式：交易日→评分卡，非交易日→财经新闻
    # ============================================================
    echo "[$(date)] ===== 每日推送启动: $TODAY (周$WEEKDAY) =====" >> $LOG

    IS_TRADING_DAY=$(python3 -c "
import sqlite3
try:
    c = sqlite3.connect('/app/working/workspaces/data_in/data/kline_2026.db')
    cnt = c.execute('SELECT COUNT(*) FROM daily_kline WHERE trade_date=?', ('$TODAY_RAW',)).fetchone()[0]
    c.close()
    print(1 if cnt > 5 else 0)
except:
    print(0)
")

    if [ "$IS_TRADING_DAY" = "1" ]; then
        # 交易日：出评分卡
        echo "[$(date)] 交易日 → 生成评分卡" >> $LOG

        LATEST_DATE=$(python3 -c "
import sqlite3
try:
    c = sqlite3.connect('/app/working/workspaces/data_in/data/kline_2026.db')
    r = c.execute('SELECT MAX(trade_date) FROM daily_kline').fetchone()
    d = r[0] if r and r[0] else '$TODAY_RAW'
    if d and len(d) == 8:
        d = f'{d[:4]}-{d[4:6]}-{d[6:]}'
    print(d)
    c.close()
except:
    print('$TODAY')
")
        echo "[$(date)] 最新完整交易日: $LATEST_DATE" >> $LOG

        RAW_FILE="$REPORT_DIR/raw_report_$(echo $LATEST_DATE | tr -d '-').txt"
        CLEAN_FILE="$REPORT_DIR/clean_report_$(echo $LATEST_DATE | tr -d '-').txt"

        python3 daily_score_update.py $LATEST_DATE 2>&1 | tee $RAW_FILE
        RESULT=${PIPESTATUS[0]}

        if [ $RESULT -eq 0 ]; then
            python3 $SCRIPT_DIR/temp/extract_report.py "$RAW_FILE" "$CLEAN_FILE"

            REPORT=$(cat "$CLEAN_FILE")
            # 评分卡是标准化模板，自动推送
            echo "[$(date)] 评分卡审核通过（标准化模板，自动推送）" >> $LOG
            do_push "$REPORT"
            echo "[$(date)] ✅ 评分卡推送完成 ($LATEST_DATE)" >> $LOG
        else
            echo "[$(date)] ❌ 评分卡生成失败 (result=$RESULT)" >> $LOG
            echo "[$(date)] 原始输出见: $RAW_FILE" >> $LOG
            # 生成失败不推送原始错误，等待总经理处理
        fi

    else
        # 非交易日：生成新闻报告（不自动推，等待审核）
        echo "[$(date)] 非交易日 → 生成财经新闻（待审核）" >> $LOG
        echo "[$(date)] ⚠️ 非交易日新闻推送需总经理审核后手动触发" >> $LOG
        echo "[$(date)]    使用: ./push_feishu_now.sh --text '已审核内容'" >> $LOG
    fi

    echo "[$(date)] ===== 每日推送完成 =====" >> $LOG
fi
