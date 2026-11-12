import { type ReactNode } from 'react';
import { PageHeader } from '@/components/ui/PageHeader';
import { OutlookBacktestBoard } from '@/components/quant/OutlookBacktestBoard';
import { EnergyTrendBoard } from '@/components/quant/EnergyTrendBoard';
import { StartupVolumeBoard } from '@/components/quant/StartupVolumeBoard';
import { FirstBoardShapeBoard } from '@/components/quant/FirstBoardShapeBoard';
import { StartupQuadrantTrackBoard } from '@/components/quant/StartupQuadrantTrackBoard';
import { StartupSpikeDaysBoard } from '@/components/quant/StartupSpikeDaysBoard';
import { SystemEntryCohort } from '@/components/quant/SystemEntryCohort';
import { Backtest } from '@/pages/Backtest';
import { SectionNav } from '@/components/SectionNav';

/**
 * 历史回溯（路由 `/backtest-agent`；会话标识仍留「策略回测」—— 那个键存在服务端对话记录与浏览器恢复键里）。
 *
 * 2026-11-07 用户两次点名定了这一屏的形状：
 * ① 「主要功能点是回溯的都搬到 个股研究与回测 · 策略回测」⇒ 八块历史统计归到这里；
 * ② 「回测内容编个序号出来，然后以下内容不要了」⇒ 现在就是下面那八块、每块左边一个序号；
 *    拆掉的清单：「这块的说明 · 回测」那个说明框、费用／滑点披露句与「AI 探索 · 接入设置」链接、
 *    「想验证什么？」输入卡与「整理回测条件／确认参数并运行／恢复同一请求／取消本次任务」那一排按钮、
 *    回测条件卡与回测报告块（含净值曲线与成交明细那份 `ExecutionView`）、以及「最近回测报告」那一叠。
 *    **拆的只是界面**：`/api/review-agent/chat-jobs*` 与 `/api/review-agent/backtest-reports`
 *    两个后端入口一行没动；那份界面在 git HEAD（`9f58dc8`）与 `.local/StrategyBacktest.with-agent-half.1107.tsx` 里都留着。
 *
 * 所以这一屏从此只做一件事：把各套判据**之后**的表现按序号摆出来。宿主屏不起取数、
 * 不起行交互实例、不替任何一块代写口径（`test/retrospectiveHome.test.ts` 反向钉着这三条）。
 */

/**
 * 序号那一栏。**数字是真 DOM 文本**，不是 CSS 生成内容：这样 innerText 读得到、测试钉得住，
 * 说「第 3 块」时屏上确实写着 3。标题仍由各块自己交（中文只住后端描述符一份），这里不复制名字。
 */
function Retro({ n, children }: { n: number; children: ReactNode }) {
  return (
    <div className="grid grid-cols-[2.25rem_minmax(0,1fr)] items-start gap-1">
      <div className="pt-3 text-right font-mono text-base font-semibold text-primary/70"
        title={`本屏第 ${n} 块`}>{n}</div>
      <div className="min-w-0">{children}</div>
    </div>
  );
}

export function StrategyBacktest() {
  return <div className="space-y-6">
    <PageHeader title="历史回溯"
      subtitle="拿历史场次统计各套判据之后的表现，按序号摆在一屏。这里不逐笔模拟成交、不给买卖建议；目录在最上面，以后加一块目录自己会长。" />
    {/* 目录现扫屏上标题生成（`components/SectionNav.tsx`）—— 写死的清单第二天就会少一行。 */}
    <SectionNav />

    {/* 第三档（主功能点也是回溯、但和所在页共用一份取数的那四块）本轮没搬，理由写在
        docs/功能点说明/个股研究与回测/回测.md 的 §14.1。 */}

    {/* 1 · 昨日涨停样本在次日的历史表现 —— 原本自己占一屏（老路由 /backtest，现只剩跳转），
        屏上无一格当日读数，整块搬进来。 */}
    <Retro n={1}><Backtest /></Retro>

    {/* 2 · 近期倍量表走势分析：回看窗口里每一场各取加权分前 N 名，名次每 10 名一档，
        看各档在名次日之后 1/2/3/5/10 场的涨跌趋势（按名次日加权分配权，与全市场基准并排）。
        2026-11-08 用户推翻上一版逐票热力表后重做，判据只住 duanxian/energy_trend.py。 */}
    <Retro n={2}><EnergyTrendBoard /></Retro>

    {/* 3 · 启动前量能画像（四类母体分开摆）。 */}
    <Retro n={3}><StartupVolumeBoard /></Retro>

    {/* 4 · 首板形状预示：母体＝每个连板段的第一天，段长是结果。 */}
    <Retro n={4}><FirstBoardShapeBoard /></Retro>

    {/* 5 · 读**走向**而不是读形状：启动日之前那 N 场里七个分量的全市场名次往哪边走。 */}
    <Retro n={5}><StartupQuadrantTrackBoard /></Retro>

    {/* 6 · 读**量与价在同一天的联合**：倍量那天收涨还是收跌、突然拉高摊在哪一天（日线代理）。 */}
    <Retro n={6}><StartupSpikeDaysBoard /></Retro>

    {/* 7 · 走完三条的个股近六个月名单（当日那张交易体系榜仍留在「选股汇总」）。 */}
    <Retro n={7}><SystemEntryCohort /></Retro>

    {/* 8 · 「推送任务 ⑩」两张名单往后 1/2/3 日的表现，与同批场次全市场基线并排。 */}
    <Retro n={8}><OutlookBacktestBoard /></Retro>
  </div>;
}
