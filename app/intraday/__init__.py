# -*- coding: utf-8 -*-
"""盘中播报与事件流（从外部 intraday_zt 整体迁入）。

判据（纯函数）在 `pool_events.py`，落库在 `store.py`，取数在 `sources.py`，
作业入口在 `ingest/jobs/zt_watch.py` 等四个单次短跑。
"""
