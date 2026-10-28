import { Link } from "react-router-dom";
import { RefreshCw } from "lucide-react";
import { PageHeader } from "@/components/ui/PageHeader";
import { Disclaimer } from "@/components/ui/Disclaimer";
import { GlassCard } from "@/components/ui/GlassCard";
import { Contract, Panel, Row } from "@/components/ui/DataPanel";
import { useNonce, useProbe } from "@/lib/probe";
import { dash } from "@/lib/datastate";
import { deriveJobView, type JobsSnapshot } from "@/lib/jobsView";

/**
 * 链路总览（`docs/新增页面规划.md` §2 维度一）。
 *
 * 这一屏只回答一句话：**今天的数据到位了吗？没到位卡在哪一环？**
 * 判据是**落库的数据日期**，不是"上次跑过没有" —— 后端把 `last_run_at` 交得再好看，
 * 只要 `last_data_date` 没推进，这一屏就必须是红的（`页面展示工作指南/03` §0 第 1 条）。
 */

interface ExpectedDate { date?: string | null }
interface ReviewStatus { running?: boolean | null; job_id?: string | null; date?: string | null; error?: string | null; elapsed?: number | null; finished_at?: string | null }
interface Drift {
  available?: boolean | null; reason?: string | null;
  field_drift?: Record<string, unknown>; field_changed?: unknown[];
  structure?: { available?: boolean | null; reason?: string | null };
  regime_events?: { date?: string | null; note?: string | null }[];
  regime_note?: string | null;
}

export function Pipeline() {
  const [nonce, refresh] = useNonce();
  const expected = useProbe<ExpectedDate>("/review/expected-date", nonce);
  const status = useProbe<ReviewStatus>("/review/status", nonce);
  const drift = useProbe<Drift>("/drift", nonce);
  // 作业清单走 03 号契约那个路径；四态由共用面板负责，别在这里再判一次
  const jobs = useProbe<JobsSnapshot>("/jobs", nonce);

  return (
    <div className="mx-auto max-w-4xl">
      <PageHeader title="链路总览" level={1}
        subtitle="下载 → 入库 → 校验 → 加工，每一环最后成功到哪天、卡在哪。判据只认数据日期。"
        actions={<button type="button" onClick={refresh}
          className="flex items-center gap-1 rounded-lg bg-primary/15 px-2.5 py-1.5 text-[12px] font-medium text-primary hover:bg-primary/25">
          <RefreshCw className="h-3.5 w-3.5" /> 重新取数
        </button>} />

      <div className="space-y-4">
        <Panel title="该到哪天" note="交易日历推出的、数据应当落库到的那一层。" state={expected}>
          {d => <>
            <Row label="期望数据日期" value={dash(d.date)} />
            <p className="mt-1 text-[11px] text-muted-foreground">下面各环的落库日期与它比对；比它旧就是落后，不因为"最近跑过"而判正常。</p>
          </>}
        </Panel>

        <Panel title="采集作业" note="每个作业最后落库到哪一天、开关状态如何。状态标签出自 03 号契约那一份判定顺序，本页不再判一次。" state={jobs}
          contract={<Contract path="GET /api/jobs" source="作业登记表 + 各表 max(trade_date)" fields={[
            ["jobs[].last_data_date", "该作业落库覆盖到的交易日 —— 这一屏唯一算数的判据"],
            ["jobs[].expected_data_date", "按交易日历应当到哪天"],
            ["jobs[].days_behind", "两者之差（交易日数）；算不出给 null，不给 0"],
            ["jobs[].trade_day_aware", "非交易日不跑算不算正常"],
            ["scheduler.enabled", "总开关 —— 关着的时候一切 never_ran 重新解释成未调度"],
          ]} />}>
          {d => {
            const rows = deriveJobs(d);
            return (
              <div>
                <Row label="作业数" value={rows.length} />
                {rows.length === 0
                  ? <p className="mt-1 text-[12px] text-muted-foreground">后端交出的清单为空（0 项）—— 这是"确实登记了 0 个作业"，不是读不到。</p>
                  : <ul className="mt-1 space-y-0.5">{rows.map((j, i) => (
                    <li key={`${j.key}-${i}`} className="text-[12px]">
                      <span className="font-mono text-muted-foreground">{j.key}</span> —— {j.label}
                      {j.detail && j.detail !== "—" ? <span className="text-muted-foreground">（{j.detail}）</span> : null}
                    </li>
                  ))}</ul>}
                <p className="mt-2 text-[11px] text-muted-foreground">
                  完整卡片、锁状态与手动触发在 <Link className="text-primary underline" to="/jobs">任务调度</Link>。
                </p>
              </div>
            );
          }}
        </Panel>

        <Panel title="复盘任务" note="报告生成任务的当前状态（人工触发的长任务）。" state={status}>
          {d => <>
            <Row label="正在跑" value={d.running === true ? "是" : d.running === false ? "否" : "—"} />
            <Row label="场次" value={dash(d.date)} />
            <Row label="已耗时" value={typeof d.elapsed === "number" ? `${d.elapsed} 秒` : "—"} />
            <Row label="结束于" value={dash(d.finished_at)} />
            <Row label="末次错误" value={dash(d.error)} tone={d.error ? "bad" : undefined} />
          </>}
        </Panel>

        <Panel title="字段与结构漂移" note="上游改了列、加了表时，这里先响。" state={drift}>
          {d => <>
            <Row label="字段漂移" value={d.field_changed && d.field_changed.length ? `${d.field_changed.length} 处变化` : "本轮未报出变化（不代表结构一致，只代表这次比对没差异）"} />
            <Row label="结构比较" value={dash(d.structure?.reason ?? d.structure?.available)} />
            <Row label="制度变化登记" value={`${d.regime_events?.length ?? 0} 条`} />
            {d.regime_note && <p className="mt-1 text-[11px] leading-relaxed text-muted-foreground">{d.regime_note}</p>}
          </>}
        </Panel>

        <GlassCard>
          <h2 className="text-sm font-bold">还没接上的两环</h2>
          <p className="mb-2 mt-0.5 text-[11px] text-muted-foreground">这两环的数在库里，但没有接口端出来 —— 按各自契约补齐后本页自动出数。</p>
          <Contract path="GET /api/pipeline" source="下载→入库→校验→加工 四环的逐环状态" fields={[
            ["stages[].name", "环节名（下载 / 入库 / 校验 / 加工）"],
            ["stages[].last_ok_at", "该环最后一次成功"],
            ["stages[].last_data_date", "该环交出的数据日期（判据）"],
            ["stages[].blocking", "卡在哪一步的错误原文，不截断"],
          ]} />
        </GlassCard>
      </div>

      <Disclaimer compact />
    </div>
  );
}

/** 把 03 契约的作业清单压成一列文字摘要；判定顺序仍只在 jobsView 一份 */
function deriveJobs(snapshot: JobsSnapshot) {
  const jobs = Array.isArray(snapshot.jobs) ? snapshot.jobs : [];
  return jobs.map(job => ({
    key: job.key ?? "（缺 key）",
    ...deriveJobView(job, {
      jobsAvailable: snapshot.jobs_available, schedulerAvailable: snapshot.scheduler_available, scheduler: snapshot.scheduler,
    }),
  }));
}
