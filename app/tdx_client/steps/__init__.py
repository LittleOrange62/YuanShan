"""
九个步骤。顺序固定，但每一步都能单独重跑（`--only`）。

    s1_launch     启动/复用通达信
    s2_login      填账号口令并登录
    s3_popup      清弹窗
    s4_autosync   等登录后的自动同步（TPyth 退出 + 指数日线到位）
    s5_dialog     打开 TQ策略 → TQ数据设置
    s6_day        第一遍：勾"日线和实时行情数据"→ 开始下载
    s7_minute     第二遍：切分钟线 tab，勾 1/5 分钟线 → 开始下载
    s8_finish     关闭 TQ数据设置 并最小化通达信
    s9_ingest     解析入库（PG 为准，旧库为可选副本）

**为什么拆这么细。** 用户那条链路里真正会出问题的是 s5~s7 三步
（菜单自绘、Tab 页、复选框状态），其它几步基本一次过。拆开之后
s5 卡住时可以只重跑 s5，不用把登录和自动同步再等一遍。
"""

from app.tdx_client.steps.s1_launch import LaunchStep
from app.tdx_client.steps.s2_login import LoginStep
from app.tdx_client.steps.s3_popup import PopupStep
from app.tdx_client.steps.s4_autosync import AutoSyncStep
from app.tdx_client.steps.s5_dialog import OpenDialogStep
from app.tdx_client.steps.s6_day import DayDownloadStep
from app.tdx_client.steps.s7_minute import MinuteDownloadStep
from app.tdx_client.steps.s8_finish import FinishStep
from app.tdx_client.steps.s9_ingest import IngestStep

ALL_STEPS = (
    LaunchStep, LoginStep, PopupStep, AutoSyncStep, OpenDialogStep,
    DayDownloadStep, MinuteDownloadStep, FinishStep, IngestStep,
)

# 只要日线的最短链路。分钟线那一遍慢得多，日常复盘用不上就别跑。
DAY_ONLY_STEPS = (
    LaunchStep, LoginStep, PopupStep, AutoSyncStep, OpenDialogStep,
    DayDownloadStep, FinishStep, IngestStep,
)

STEP_BY_KEY = {c.key: c for c in ALL_STEPS}


def build(keys: list[str] | None = None, *, day_only: bool = False) -> list:
    if keys:
        missing = [k for k in keys if k not in STEP_BY_KEY]
        if missing:
            raise KeyError(f'没有这些步骤：{missing}；可用：{list(STEP_BY_KEY)}')
        return [STEP_BY_KEY[k]() for k in keys]
    src = DAY_ONLY_STEPS if day_only else ALL_STEPS
    return [c() for c in src]


__all__ = ['ALL_STEPS', 'DAY_ONLY_STEPS', 'STEP_BY_KEY', 'build']
