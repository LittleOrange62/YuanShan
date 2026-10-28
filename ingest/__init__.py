"""本地通达信数据解析层（吸收自 FinanceTool `app/ingest/`，P2.1）。

放这里的前提是它**不依赖数据库**：`tdx_binary` 只用 stdlib，所以可以整文件照搬、
两边跑同一批 vipdoc 文件逐字节对账。需要入库的部分一律不在这一层 —— 
解析与写库分开，才可能在 P0 备份验收之前先跑只读对账。

同花顺自选也照这条规矩分：`ingest/ths_local.py` 只做文件解析（stdlib、不连库），
读写 `config.watchlist_*` 的那一半在 `duanxian/watchlist_sync.py`。
边界按用户 2026-09-25 的裁定：**"采集"的分界是数据来源不是目录名** ——
读本地文件的进 `ingest/`，走网络/接口的留在 `duanxian/`（如 `newswire.py`）。
"""
