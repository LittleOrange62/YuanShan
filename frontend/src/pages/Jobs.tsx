import { useCallback, useEffect, useState } from "react";
import { AlertTriangle, Play, RefreshCw } from "lucide-react";
import { Dialog } from "@/components/workspace/Dialog";
import { Disclaimer } from "@/components/ui/Disclaimer";
import { GlassCard } from "@/components/ui/GlassCard";
import { PageHeader } from "@/components/ui/PageHeader";
import { fetchJobs, snapshotAgeText, triggerJob, type JobsPayload, type JobsState, type TriggerOutcome } from "@/lib/jobs";
import { counterLine, dash, deriveJobView, hasValue, successLabel, type Job } from "@/lib/jobsView";
import { jobCardClass, jobLabelClass } from "@/lib/colors";
import { cn } from "@/lib/utils";

/**
 * 任务调度大全（契约 `docs/页面展示工作指南/03-任务调度页契约.md`）。
 *
 * 这页只回答一个问题：**今天的数据到位了吗？没到位卡在哪一环？**（契约 §0）
 * 所以状态标签一律来自 `deriveJobView` 那一份判定顺序，页面**不再自己判一次** ——
 * FinanceTool 那次事故（13 个作业的 note 全写"调度器总开关未打开"、页面上看不出来、
 * 跑了三天才发现一条都没落库）就是前后端各解释一遍造成的。
 */

/** 作业卡样式：tone 只在这里翻译成 class（配色收在 colors.ts，契约 §6） */
export function Jobs() {
  const [state, setState] = useState<JobsState>({ kind: "loading" });
  const [nonce, setNonce] = useState(0);
  const [confirming, setConfirming] = useState<Job | null>(null);
  const [runResult, setRunResult] = useState<Record<string, TriggerOutcome>>({});

  const load = useCallback(() => {
    let live = true;
    setState({ kind: "loading" });
    fetchJobs().then(next => { if (live) setState(next); });
    return () => { live = false; };
  }, []);

  useEffect(() => load(), [load, nonce]);

  return (
    <div className="mx-auto max-w-5xl">
      <PageHeader title="任务调度" level={1}
        subtitle="每个采集/计算作业现在到底在什么状态。判据只认数据日期，不认「上次跑过」。"
        actions={
          <button type="button" onClick={() => setNonce(n => n + 1)}
            className="flex items-center gap-1 rounded-lg bg-primary/15 px-2.5 py-1.5 text-[12px] font-medium text-primary hover:bg-primary/25">
            <RefreshCw className="h-3.5 w-3.5" /> 重新取数
          </button>
        } />

      {state.kind === "loading" && (
        <p className="py-8 text-center text-sm text-muted-foreground">正在读取作业清单…</p>
      )}
      {state.kind === "error" && (
        <GlassCard>
          <Banner tone="bad" text={state.reason} />
          <p className="mt-2 text-[11px] leading-relaxed text-muted-foreground">
            本页刻意不把"取不到"渲染成"一切正常"，也不渲染成"没有作业"。
          </p>
        </GlassCard>
      )}
      {state.kind === "ok" && <JobsBoard snapshot={state.snapshot}
        onTriggered={(key, outcome) => setRunResult(prev => ({ ...prev, [key]: outcome }))}
        results={runResult} confirming={confirming} setConfirming={setConfirming} />}

      <Disclaimer compact />
    </div>
  );
}

function JobsBoard({ snapshot, onTriggered, results, confirming, setConfirming }: {
  snapshot: JobsPayload;
  onTriggered: (key: string, outcome: TriggerOutcome) => void;
  results: Record<string, TriggerOutcome>;
  confirming: Job | null;
  setConfirming: (job: Job | null) => void;
}) {
  const top = {
    jobsAvailable: snapshot.jobs_available,
    schedulerAvailable: snapshot.scheduler_available,
    scheduler: snapshot.scheduler,
  };
  const jobs = Array.isArray(snapshot.jobs) ? snapshot.jobs : [];
  const sched = snapshot.scheduler ?? null;
  const schedulerOff = snapshot.scheduler_available !== false && sched?.enabled === false;

  return (
    <div className="space-y-4">
      <GlassCard>
        <div className="flex flex-wrap items-baseline justify-between gap-2">
          {/* 标题不叫"调度器"：那个词在本机同时指五件东西（对照表见
              docs/系统配置工作指南/05 §4b）。这张卡说的只有一件事 —— 本进程那一组常驻循环的汇总读数。 */}
          <h2 className="text-base font-bold">本进程宿主的循环读数</h2>
          <span className="font-mono text-[11px] text-muted-foreground">
            快照生成于 {dash(snapshot.generated_at)}（{snapshotAgeText(snapshot.generated_at, Date.now())}）
          </span>
        </div>
        {snapshot.scheduler_available === false && (
          <Banner tone="warn" className="mt-2"
            text={`本进程循环的读数取不到：${hasValue(snapshot.scheduler_reason) ? snapshot.scheduler_reason : "后端未给 reason（契约 §2：available=false 时 reason 必填）"}`} />
        )}
        {snapshot.scheduler_available !== false && (
          <div className="mt-2 grid gap-x-6 gap-y-1 text-[12px] sm:grid-cols-2">
            <Field label="总开关" value={sched ? (sched.enabled === true ? "开" : sched.enabled === false ? "关" : "未知（null）") : dash(sched)} />
            <Field label="模式" value={sched?.dry_run === true ? "dry_run：真执行 + 强制回滚，成功不算落库" : sched?.dry_run === false ? "正式写入" : dash(sched?.dry_run)} />
            <Field label="宿主" value={dash(sched?.host)} />
            <Field label="PID" value={dash(sched?.pid)} />
            <Field label="启动于" value={dash(sched?.started_at)} />
            <Field label="最近心跳" value={dash(sched?.heartbeat_at)} />
          </div>
        )}
        {hasValue(sched?.note) && (
          <p className="mt-2 text-[11px] leading-relaxed text-muted-foreground">
            后端原话（不美化）：<span className="text-foreground">{sched?.note}</span>
          </p>
        )}
        {schedulerOff && (
          <Banner tone="warn" className="mt-2"
            text="总开关为关：本页所有「从未运行」都应读作「未调度」，不代表采集逻辑坏了。这正是当初 FinanceTool 三天没发现没落库的那个字段。" />
        )}
      </GlassCard>

      {snapshot.jobs_available === false ? (
        <GlassCard>
          <Banner tone="bad"
            text={`读不到作业清单：${hasValue(snapshot.jobs_reason) ? snapshot.jobs_reason : "后端未给 reason（契约 §2）"} —— 这不是「当前没有作业」。`} />
        </GlassCard>
      ) : (
        <>
          {snapshot.truncated === true && (
            <Banner tone="warn" text={`作业清单被截断，此处只列出部分（总数后端报 ${dash(snapshot.total)}）。`} />
          )}
          {jobs.length === 0 && (
            <GlassCard>
              <p className="text-[13px] text-muted-foreground">
                后端交出的作业清单为空（0 项），且 <code>jobs_available</code> 不为 false —— 即"确实登记了 0 个作业"，不是读不到。
              </p>
            </GlassCard>
          )}
          {jobs.map((job, i) => (
            <JobCard key={job.key || `${job.name || "job"}-${i}`} job={job} top={top}
              result={job.key ? results[job.key] : undefined}
              onAskRun={() => setConfirming(job)} />
          ))}
        </>
      )}

      {confirming && <RunConfirm job={confirming} onClose={() => setConfirming(null)}
        onDone={outcome => {
          const key = confirming.key;
          if (key) onTriggered(key, outcome);
          setConfirming(null);
        }} />}
    </div>
  );
}

function JobCard({ job, top, result, onAskRun }: {
  job: Job;
  top: Parameters<typeof deriveJobView>[1];
  result?: TriggerOutcome;
  onAskRun: () => void;
}) {
  const view = deriveJobView(job, top);
  const unreadable = top.jobsAvailable === false;
  const disabled = job.enabled === false || job.available === false || unreadable;
  const disabledReason = job.enabled === false ? "作业自身开关为关"
    : job.available === false ? (hasValue(job.reason) ? String(job.reason) : "后端未给 reason")
    : unreadable ? "作业清单不可读" : "";

  return (
    <GlassCard className={cn(view.neverRan && "ring-1 ring-destructive/50", jobCardClass(view.tone))}>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <h3 className="text-sm font-semibold">{hasValue(job.name) ? job.name : "（缺 name）"}</h3>
            <span className={cn("rounded px-1.5 py-0.5 text-[11px] font-medium", jobLabelClass(view.tone))}>{view.label}</span>
          </div>
          <p className="mt-1 font-mono text-[11px] text-muted-foreground">{dash(job.key)}</p>
          {view.detail && <p className="mt-1 text-[11px] leading-relaxed text-muted-foreground">{view.detail}</p>}
          {hasValue(job.basis) && <p className="mt-1 text-[11px] text-muted-foreground">统计出处：{job.basis}</p>}
        </div>
        <div className="shrink-0 text-right">
          <button type="button" onClick={onAskRun} disabled={disabled}
            title={disabled ? disabledReason : "先看清这次会出网到哪里、写哪里"}
            className={cn("inline-flex items-center gap-1 rounded-lg px-2.5 py-1.5 text-[12px] font-medium",
              disabled ? "cursor-not-allowed bg-muted text-muted-foreground" : "bg-primary/15 text-primary hover:bg-primary/25")}>
            <Play className="h-3.5 w-3.5" /> 立即触发
          </button>
          {disabled && <p className="mt-1 max-w-40 text-[10px] leading-4 text-muted-foreground">{disabledReason}</p>}
          {result && <ResultLine outcome={result} />}
        </div>
      </div>

      {job.available === false || top.jobsAvailable === false ? null : (
        <div className="mt-3 space-y-2 text-[12px]">
          {hasValue(job.entry) && <p className="break-all text-[11px] text-muted-foreground">说明（仅供人读，不是可执行命令）：<span className="font-mono">{job.entry}</span></p>}

          <div className="grid gap-x-6 gap-y-1 sm:grid-cols-2">
            <Field label="调度种类" value={`${dash(job.schedule_kind)}${hasValue(job.schedule_spec) ? ` · ${job.schedule_spec}` : ""}`} />
            <Field label="非交易日不跑算正常吗" value={job.trade_day_aware === true ? "是（按交易日历判）" : job.trade_day_aware === false ? "否（每天该跑）" : "未知（后端没给，落后与否的判断会降级）"} />
            <Field label="上次跑" value={dash(job.last_run_at)} />
            <Field label="上次成功" value={dash(job.last_ok_at)} />
            <Field label="下次应在" value={`${dash(job.next_due_at)}${typeof job.overdue_seconds === "number" ? `（已逾期 ${job.overdue_seconds} 秒）` : ""}`} />
            <Field label="连续失败" value={typeof job.consecutive_failures === "number" ? `${job.consecutive_failures} 次（0 只表示没有连续失败，不代表跑成功过）` : dash(job.consecutive_failures)} />
          </div>

          {job.trade_day_aware === true && (
            <div className="rounded-lg border border-border/60 p-2">
              <p className="text-[11px] font-medium text-muted-foreground">数据日期（这页唯一算数的判据）</p>
              <div className="mt-1 grid gap-x-6 gap-y-1 sm:grid-cols-3">
                <Field label="落库到" value={dash(job.last_data_date)} />
                <Field label="应在" value={dash(job.expected_data_date)} />
                <Field label="落后" value={typeof job.days_behind === "number" ? `${job.days_behind} 个交易日` : "未知（null 不等于不落后）"} />
              </div>
            </div>
          )}

          <SinkBlock job={job} />
          <SourceBlock job={job} />
          <CountersBlock job={job} />
          <HistoryBlock job={job} scheduler={top.scheduler} />

          {hasValue(job.lock_state) && (
            <Field label="锁" value={`${job.lock_state}${hasValue(job.lock_held_by) ? ` · 持有者 ${job.lock_held_by}` : ""}${typeof job.lock_waited_ms === "number" ? ` · 已等待 ${job.lock_waited_ms} ms` : ""}`} />
          )}
          {hasValue(job.last_error) && (
            <p className="break-all rounded-lg bg-destructive/10 p-2 text-[11px] leading-relaxed text-destructive">
              末次错误{hasValue(job.last_error_at) ? `（${job.last_error_at}）` : ""}：{job.last_error}
            </p>
          )}
        </div>
      )}
    </GlassCard>
  );
}

function SinkBlock({ job }: { job: Job }) {
  const s = job.sink_health;
  if (!s) return <Field label="落到哪里" value={`${dash(null)}（后端未给 sink_health —— 写没写进去这一环显示不了）`} />;
  return (
    <div className="grid gap-x-6 gap-y-1 sm:grid-cols-2">
      <Field label="可写" value={s.writable === true ? "是" : s.writable === false ? "否" : "未知"} />
      <Field label="落到哪些表/文件" value={s.targets && s.targets.length ? s.targets.join("、") : dash(null)} />
      {hasValue(s.last_err) && <p className="break-all text-[11px] text-destructive sm:col-span-2">下游末次错误：{s.last_err}</p>}
    </div>
  );
}

function SourceBlock({ job }: { job: Job }) {
  const list = Array.isArray(job.source_health) ? job.source_health : null;
  if (!list || list.length === 0) return null;
  /** 部分源挂不能把整个作业显示成绿：逐源各说各的（契约 §3） */
  return (
    <div>
      <p className="text-[11px] font-medium text-muted-foreground">数据源健康（逐源，不合并成"作业正常"）</p>
      <ul className="mt-1 space-y-0.5">
        {list.map((h, i) => (
          <li key={`${h.source}-${i}`} className="flex flex-wrap items-baseline gap-x-2 text-[11px]">
            <span className={cn("rounded px-1", h.ok === false ? "bg-destructive/15 text-destructive" : h.ok === true ? "bg-primary/12 text-primary" : "bg-muted text-muted-foreground")}>
              {h.ok === false ? "失败" : h.ok === true ? "可用" : "未知"}
            </span>
            <span className="font-mono">{dash(h.source)}</span>
            {typeof h.consecutive_failures === "number" && h.consecutive_failures > 0 && <span className="text-muted-foreground">连续失败 {h.consecutive_failures} 次</span>}
            {hasValue(h.last_ok_at) && <span className="text-muted-foreground">最近可用 {h.last_ok_at}</span>}
            {hasValue(h.last_err) && <span className="break-all text-destructive">{h.last_err}</span>}
          </li>
        ))}
      </ul>
    </div>
  );
}

function CountersBlock({ job }: { job: Job }) {
  if (!job.counters && !job.cursor) return null;
  return (
    <div className="space-y-1">
      {job.counters && <Field label="计数" value={counterLine(job)} />}
      {job.cursor && <Field label="游标" value={`${JSON.stringify(job.cursor)}${hasValue(job.cursor_meaning) ? ` —— ${job.cursor_meaning}` : " ——（后端未给 cursor_meaning，这串数字的含义无法显示）"}`} />}
    </div>
  );
}

function HistoryBlock({ job, scheduler }: { job: Job; scheduler: JobsPayload["scheduler"] }) {
  const hist = Array.isArray(job.run_history) ? job.run_history : null;
  if (!hist) return <p className="text-[11px] text-muted-foreground">后端未给 <code>run_history</code> —— 于是「从未运行」与「跑了但 0 行」在这页面上分不开（契约 §3 把它列为 ☆ 是有原因的）。</p>;
  if (hist.length === 0) {
    // 实况（09-25 接口首次 200）：news / intraday_snapshot 这些 `last_run_at` 有值而 `run_history` 是空数组
    // —— 常驻采集循环不写历史。这时无条件断言"从没跑过"就是假话，且与同屏那行"上次跑"直接矛盾。
    const ran = hasValue(job.last_run_at) || hasValue(job.last_ok_at);
    return <p className="text-[11px] text-muted-foreground">
      <code>run_history</code> 为空数组：{ran
        ? <>但 <code>last_run_at</code> 有值（{dash(job.last_run_at)}）—— 这是<b className="text-foreground">跑了而没留历史</b>，不能读成「没跑过」。</>
        : "确实一次都没跑过。"}
    </p>;
  }
  return (
    <div>
      <p className="text-[11px] font-medium text-muted-foreground">最近运行记录（{hist.length} 条，新在前）</p>
      <ul className="mt-1 space-y-0.5">
        {hist.slice().reverse().map((r, i) => (
          <li key={`${r.started_at}-${i}`} className="flex flex-wrap items-baseline gap-x-2 text-[11px]">
            <span className="font-mono text-muted-foreground">{dash(r.started_at)}</span>
            <span className={cn("rounded px-1", r.outcome === "ok" ? "bg-primary/12 text-primary" : r.outcome === "skipped" ? "bg-muted text-muted-foreground" : "bg-destructive/15 text-destructive")}>
              {r.outcome === "ok" ? successLabel(scheduler) : dash(r.outcome)}
            </span>
            {typeof r.rows_written === "number" && <span className="text-muted-foreground">{r.rows_written} 行</span>}
            {hasValue(r.error) && <span className="break-all text-destructive">{r.error}</span>}
          </li>
        ))}
      </ul>
    </div>
  );
}

/** §5.3：二次确认必须看得见"会向哪些域名出网 + 写哪里"，光一句警告挡不住误点 */
function RunConfirm({ job, onClose, onDone }: {
  job: Job; onClose: () => void; onDone: (o: TriggerOutcome) => void;
}) {
  const [busy, setBusy] = useState(false);
  const p = job.run_preview;
  return (
    <Dialog titleId="job-run-title" close={onClose} className="max-w-lg">
      <div className="p-5">
        <h2 id="job-run-title" className="text-sm font-semibold">确认手动触发：{hasValue(job.name) ? job.name : job.key}</h2>
        {!p && (
          <p className="mt-2 rounded-lg bg-warning/15 p-2 text-[12px] leading-relaxed text-warning">
            没有 <code>run_preview</code> —— 这次会出网到哪里、写哪张表都看不见。契约 §5.3 把它列为必填，缺了就等于让人盲签。建议先取消，等这项在本项目内补上再触发。
          </p>
        )}
        {p && (
          <dl className="mt-3 space-y-1 text-[12px]">
            <Field label="出网目标" value={p.outbound_hosts && p.outbound_hosts.length ? p.outbound_hosts.join("、") : "（声明为不出网）"} />
            <Field label="写入" value={dash(p.writes)} />
            <Field label="是否碰数据库" value={p.touches_db === true ? "是" : p.touches_db === false ? "否" : "未知"} />
            <Field label="预计耗时" value={typeof p.est_seconds === "number" ? `约 ${p.est_seconds} 秒` : "未知"} />
          </dl>
        )}
        <p className="mt-3 text-[11px] leading-relaxed text-muted-foreground">
          触发是异步的：接口立刻返回 <code>run_id</code>，页面不阻塞等待。同一作业重复触发会得到 409，本次不会双跑。
        </p>
        <div className="mt-4 flex justify-end gap-2">
          <button type="button" onClick={onClose} className="rounded-lg px-3 py-1.5 text-[12px] text-muted-foreground hover:bg-muted">取消</button>
          <button type="button" disabled={busy}
            onClick={async () => { setBusy(true); onDone(await triggerJob(String(job.key ?? ""))); }}
            className="rounded-lg bg-primary/15 px-3 py-1.5 text-[12px] font-medium text-primary hover:bg-primary/25 disabled:opacity-60">
            {busy ? "已发送…" : "确认触发"}
          </button>
        </div>
      </div>
    </Dialog>
  );
}

function ResultLine({ outcome }: { outcome: TriggerOutcome }) {
  const map: Record<TriggerOutcome["kind"], { tone: "normal" | "warn" | "bad"; text: string }> = {
    queued: { tone: "normal", text: `已排队${outcome.kind === "queued" && outcome.runId ? `（run_id ${outcome.runId}）` : "（后端未给 run_id，无法单独跟踪这一次）"}` },
    conflict: { tone: "warn", text: outcome.kind === "conflict" ? outcome.reason : "" },
    refused: { tone: "warn", text: outcome.kind === "refused" ? `已拒绝：${outcome.reason}` : "" },
    error: { tone: "bad", text: outcome.kind === "error" ? `触发失败：${outcome.reason}` : "" },
  };
  const m = map[outcome.kind];
  return <p className={cn("mt-1 max-w-48 text-[10px] leading-4", m.tone === "bad" ? "text-destructive" : m.tone === "warn" ? "text-warning" : "text-primary")}>{m.text}</p>;
}

function Field({ label, value }: { label: string; value: string }) {
  return <p className="text-[12px]"><span className="text-muted-foreground">{label}：</span><span className="text-foreground">{value}</span></p>;
}

function Banner({ tone, text, className }: { tone: "warn" | "bad"; text: string; className?: string }) {
  return (
    <p className={cn("flex items-start gap-1.5 rounded-lg p-2 text-[12px] leading-relaxed",
      tone === "bad" ? "bg-destructive/10 text-destructive" : "bg-warning/10 text-warning", className)}>
      <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />{text}
    </p>
  );
}
