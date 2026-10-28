"""
通达信客户端自动化。

这个包干一件事：把"人在通达信界面上点几下"变成可重复、可判定的步骤。
它不是通用 GUI 框架，只服务于一条固定链路 —— 启动登录 → 等自动同步 →
手动触发盘后日线下载 → 再触发分钟线下载 → 收尾最小化 → 数据入库。

三条硬规则（和 `D:\\finance\\工作指南` 里写的一致）：

1. **完成判据只认数据，不认时间。** 任何一步的 `verify()` 必须去看
   vipdoc 里的日期变成了目标交易日，或者进程真的退出了。
   `time.sleep(600)` 之后就宣布"下载完了"是这套东西最容易死的方式。
2. **必须跑在登录用户的桌面会话里。** Session 0（服务、计划任务勾了
   "不管用户是否登录"）看不见任何窗口，`EnumWindows` 返回空，
   全套定位会静默失败。`win32.session_ok()` 开机先自检。
3. **口令零明文。** 从环境变量 / `.env` 读，代码和日志里都不出现。
"""

from app.tdx_client.machine import Machine, Step, StepResult
from app.tdx_client.readiness import Readiness

__all__ = ['Machine', 'Step', 'StepResult', 'Readiness']
