import { Link } from "react-router-dom";
import { PageHeader } from "@/components/ui/PageHeader";
import { Disclaimer } from "@/components/ui/Disclaimer";
import { GlassCard } from "@/components/ui/GlassCard";
import { IntradayBroadcastPanel } from "@/components/IntradayBroadcastPanel";

/**
 * 推送任务（用户 2026-11-04 点名：「推送任务单独建一页，用于汇总推送任务的，
 * 推送配置从系统配置里面移动出来」）。
 *
 * 为什么值得单独一屏：这一块和系统配置里其它格子的**后果不一样** —— 保存它会动
 * Windows 任务计划程序（覆盖式注册 / 注销），而且发出去是别人看得见的动作（群里、你手机上）。
 * 放在系统配置那一长屏的第 ⑮ 块里，改之前后要滚动找、也看不出"这一格会动机器"。
 *
 * 仍然只有一份真源：读写口是 `app/intraday/tasks.py` → 作业配置 `jobs/config.json`
 * 的 `intraday_broadcast`，接口 `GET/POST /api/intraday/broadcast`。这一屏只是它的新入口，
 * 系统配置那一屏已经不再挂这块（原位置只留一行跳转）—— 两处都能改就是两处会不一致。
 *
 * 另外两条推送面**故意不搬**：渠道机器人本身与「本进程内按 `run_at` 发内容块」的那套定时计划在
 * 系统配置 ⑬，个人微信那条"在桌面上按键盘"的通道在 ⑭ —— 它们与本屏的差别恰恰是"谁在跑"，
 * 分开放、这里给指路，比混成一屏更容易看清。
 */
export function PushTasks() {
  return (
    <div className="space-y-4">
      <PageHeader title="推送任务" level={1}
        subtitle="到点往群里（和你手机上）发东西的那几路汇总在这一屏：什么时候跑、发到哪些渠道、这一路还开不开，以及机器上真注册成什么状态、今天库里真发出几条。保存会动 Windows 任务计划程序。" />

      <IntradayBroadcastPanel />

      <GlassCard>
        <h2 className="text-sm font-semibold">这屏不管什么，要去哪儿改</h2>
        <p className="mt-1 text-[11px] leading-relaxed text-muted-foreground">
          作业脚本在 <code>ingest/jobs/push/</code>，文件名前缀 <code>t01</code>…<code>t09</code>
          就是上面这张表从上往下的行序；判据与措辞在那些脚本与 <code>app/intraday/templates.json</code>，
          本屏只管"什么时候跑、发到哪、开不开"。
        </p>
        <p className="mt-1 text-[11px] leading-relaxed text-muted-foreground">
          渠道机器人本身（飞书/钉钉/企业微信）与"本进程内按 <code>run_at</code> 发内容块"的那套定时计划：
          <Link className="mx-1 text-primary underline" to="/system">系统配置 · 消息推送（渠道与定时计划）</Link>；
          个人微信那条在本机桌面上按键盘的通道（含发送前那六项检查）：
          <Link className="mx-1 text-primary underline" to="/system">系统配置 · 个人微信推送</Link>。
        </p>
        <p className="mt-1 text-[11px] leading-relaxed text-muted-foreground">
          今天几点真发了什么，另有一处按渠道回看：
          <Link className="mx-1 text-primary underline" to="/jobs">任务调度</Link>
          与库里 <code>analytics.intraday_broadcast</code>（上面每行末尾那格读的就是它）。
        </p>
      </GlassCard>
      <Disclaimer compact />
    </div>
  );
}
