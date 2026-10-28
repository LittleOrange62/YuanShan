r"""FinanceTool 摄取/运维作业的物理副本（原 `scripts/ingest/c*`、`scripts/ops/e*`、`daily_close_sync`）。

搬入理由：用户 2026-09-25 裁定终态只有一个成品项目，**不许跨文件夹调用**。
本目录是**可运行副本**，参考仓库那边从此只读、不再往里写。

运行方式：`python -m ingest.jobs.c1_day --dry-run`（`sys.path` 上溯两级即项目根，
`from app...` 命中的是本项目 `YuanShan/app/`，不是别的仓库）。

⚠️ 搬进来 ≠ 已验收：接管写库的前提是 `scripts/verify_vipdoc_daily.py` 逐条对账通过
（现状：17/18 个交易日零差异，09-02 全天差异已归因为库里那天走的是 TQ 接口而非文件）。
对账未过的表，不许由本目录往 PG 写。
"""
