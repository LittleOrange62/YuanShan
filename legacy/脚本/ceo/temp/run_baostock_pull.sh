#!/bin/bash
# 后台拉取 Baostock 数据，不阻塞终端
cd /app/working/workspaces/data_from

echo "=== 开始拉取 Baostock 数据 ===" >> /tmp/baostock_pull.log
date >> /tmp/baostock_pull.log

# 拉 0708
echo "--- 拉取 20260708 ---" >> /tmp/baostock_pull.log
python3 data_sources/src/baostock_fetcher.py daily_full --date 20260708 --outdir /app/working/workspaces/data_from/data/ >> /tmp/baostock_pull.log 2>&1
echo "0708 完成: $?" >> /tmp/baostock_pull.log

# 拉 0709
echo "--- 拉取 20260709 ---" >> /tmp/baostock_pull.log
python3 data_sources/src/baostock_fetcher.py daily_full --date 20260709 --outdir /app/working/workspaces/data_from/data/ >> /tmp/baostock_pull.log 2>&1
echo "0709 完成: $?" >> /tmp/baostock_pull.log

# 尝试拉 0710（可能还没更新）
echo "--- 尝试 20260710 ---" >> /tmp/baostock_pull.log
python3 data_sources/src/baostock_fetcher.py daily_full --date 20260710 --outdir /app/working/workspaces/data_from/data/ >> /tmp/baostock_pull.log 2>&1
echo "0710 完成: $?" >> /tmp/baostock_pull.log

echo "=== 拉取全部完成 ===" >> /tmp/baostock_pull.log
date >> /tmp/baostock_pull.log
