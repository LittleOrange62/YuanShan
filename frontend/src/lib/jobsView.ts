/**
 * 任务调度页的**纯判定层**（契约 `docs/页面展示工作指南/03-任务调度页契约.md` §4）。
 *
 * 单独成文件、且不 import 任何东西，是为了能被 `node --test` 直接钉住：
 * 判定顺序是这页的全部价值所在 —— 前后端各解释一遍，就会重演 FinanceTool
 * 那次事故（note 全写"调度器总开关未打开"、页面看不出来、三天后才发现没落库）。
 * 页面**只调 `deriveJobView`**，不允许自己再判一次。
 */

export interface SchedulerState {
  enabled?: boolean | null;
  dry_run?: boolean | null;
  host?: string | null;
  pid?: number | null;
  started_at?: string | null;
  heartbeat_at?: string | null;
  note?: string | null;
}

export interface JobCounter {
  scope?: string | null;
  since?: string | null;
  total_seen?: number | null;
  unit?: string | null;
  [k: string]: unknown;
}

export interface SourceHealth {
  source?: string | null;
  ok?: boolean | null;
  last_err?: string | null;
  last_ok_at?: string | null;
  consecutive_failures?: number | null;
}

export interface Job {
  key?: string | null;
  name?: string | null;
  entry?: string | null;
  state?: string | null;
  enabled?: boolean | null;
  available?: boolean | null;
  reason?: string | null;
  schedule_kind?: string | null;
  schedule_spec?: string | null;
  trade_day_aware?: boolean | null;
  last_run_at?: string | null;
  last_ok_at?: string | null;
  consecutive_failures?: number | null;
  last_data_date?: string | null;
  expected_data_date?: string | null;
  days_behind?: number | null;
  next_due_at?: string | null;
  overdue_seconds?: number | null;
  last_error?: string | null;
  last_error_at?: string | null;
  run_history?: { started_at?: string | null, outcome?: string | null, rows_written?: number | null, error?: string | null }[];
  counters?: JobCounter | null;
  cursor?: Record<string, unknown> | null;
  cursor_meaning?: string | null;
  source_health?: SourceHealth[];
  sink_health?: { writable?: boolean | null, last_err?: string | null, targets?: string[] } | null;
  lock_state?: string | null;
  lock_held_by?: string | null;
  lock_waited_ms?: number | null;
  run_preview?: { outbound_hosts?: string[] | null, writes?: string | null, touches_db?: boolean | null, est_seconds?: number | null } | null;
  basis?: string | null;
}

export interface JobsSnapshot {
  generated_at?: string | null;
  scheduler?: SchedulerState | null;
  scheduler_available?: boolean | null;
  scheduler_reason?: string | null;
  jobs?: Job[];
  jobs_available?: boolean | null;
  jobs_reason?: string | null;
  truncated?: boolean | null;
  total?: number | null;
}

/** tone 只分四类，具体颜色由组件走 colors.ts 决定；这里只表达"该不该显眼" */
export type Tone = "normal" | "warn" | "bad" | "quiet";

export interface JobView {
  label: string;
  tone: Tone;
  detail: string | null;
  /** 从未运行 —— 对应"当天不采就永久没有"，样式最重 */
  neverRan: boolean;
}

export const NULL_TEXT = "—";

/** 空值统一 `—`；0 是有效值，绝不吃掉（契约 §6） */
export function dash(v: unknown): string {
  if (v === null || v === undefined) return NULL_TEXT;
  if (typeof v === "string" && v.trim() === "") return NULL_TEXT;
  return String(v);
}

function has(v: unknown): boolean {
  return !(v === null || v === undefined || (typeof v === "string" && v.trim() === ""));
}

/** 页面判"字段给没给"只能用它 —— 直接用 `if (x)` 会把 0 和空串当成没给（契约 §6） */
export const hasValue = has;

/**
 * §4 判定顺序。**顺序就是语义**，改动必须同步 jobs.test.ts 与契约文档。
 *
 * 两个易错点特意处理掉：
 *  · `available=false` 时**不再显示 state** —— 否则 state 与 reason 各说一套；
 *  · `days_behind=null` 不等于"不落后"，要报"数据日期未知"。
 */
export function deriveJobView(job: Job, top: {
  jobsAvailable?: boolean | null; schedulerAvailable?: boolean | null; scheduler?: SchedulerState | null;
}): JobView {
  if (top.jobsAvailable === false) {
    return { label: "作业清单不可读", tone: "bad", detail: NULL_TEXT, neverRan: false };
  }
  if (job.available === false) {
    return {
      label: "不可用", tone: "quiet",
      detail: has(job.reason) ? String(job.reason)
        : "（缺失 —— available=false 未带 reason，违反展示规范 01 第一条）",
      neverRan: false,
    };
  }
  if (top.schedulerAvailable === false) {
    return { label: "调度器状态未知", tone: "warn", detail: has(top.scheduler?.note) ? String(top.scheduler?.note) : NULL_TEXT, neverRan: false };
  }
  if (job.lock_state === "stuck") {
    return {
      label: "锁被占住", tone: "bad",
      detail: `被 ${has(job.lock_held_by) ? job.lock_held_by : "未知持有者"} 占住${has(job.lock_waited_ms) ? `，已等待 ${job.lock_waited_ms} ms` : ""}`,
      neverRan: false,
    };
  }
  if (job.enabled === false) return { label: "已停用", tone: "quiet", detail: "作业自身开关为关", neverRan: false };
  if (top.scheduler?.enabled === false) {
    return { label: "未调度", tone: "warn", detail: "调度器总开关未打开 —— 此时「从未运行」不代表采集坏了", neverRan: false };
  }
  // 「一次都没跑过」必须在这一档就说完，两种情形分开：
  //  · 有 next_due_at：登记了、到点会跑，只是还没跑过 —— 不能盖"从未运行"那格最重的样式
  //    （那格说的是"没人登记过这项"，契约 §4-7），但也绝不能顺着 state 显示成正常。
  //  · 连 next_due_at 都没有：没人触发，这才是"从未运行"。
  // 以前只有后一种被接住，**登记了但从未跑过**的作业会一路掉到底下那句
  // "跑过但没有成功记录"，把从没跑过说成跑过（09-30 自查抓到的同类）。
  if (!has(job.last_run_at)) {
    if (has(job.next_due_at)) {
      return { label: "还没有运行记录", tone: "warn",
               detail: `到点会跑（下一次 ${job.next_due_at}），但到现在一次都没执行过`, neverRan: false };
    }
    return { label: "从未运行", tone: "bad", detail: "如果这项的数据当天不采就永久没有，现在就是永久缺失", neverRan: true };
  }
  const fails = typeof job.consecutive_failures === "number" ? job.consecutive_failures : null;
  if (fails && fails > 0) {
    return { label: `连续失败 ${fails} 次`, tone: "bad", detail: has(job.last_error) ? String(job.last_error) : NULL_TEXT, neverRan: false };
  }
  if (job.trade_day_aware === true) {
    if (job.days_behind === null || job.days_behind === undefined) {
      if (has(job.last_data_date) || has(job.expected_data_date)) {
        return { label: "数据日期未知", tone: "warn", detail: `落库到 ${dash(job.last_data_date)} / 应在 ${dash(job.expected_data_date)}`, neverRan: false };
      }
    } else if (job.days_behind > 0) {
      return { label: `数据落后 ${job.days_behind} 个交易日`, tone: "bad", detail: `落库到 ${dash(job.last_data_date)}，应在 ${dash(job.expected_data_date)}`, neverRan: false };
    }
  }
  if (job.state === "running") return { label: "运行中", tone: "normal", detail: has(job.next_due_at) ? `下次 ${job.next_due_at}` : NULL_TEXT, neverRan: false };
  if (!has(job.last_ok_at)) {
    return { label: "跑过但没有成功记录", tone: "warn", detail: has(job.last_error) ? String(job.last_error) : NULL_TEXT, neverRan: false };
  }
  return { label: has(job.state) ? String(job.state) : "已登记", tone: "normal", detail: has(job.next_due_at) ? `下次 ${job.next_due_at}` : NULL_TEXT, neverRan: false };
}

/** `dry_run` 下的成功不是成功（契约 §6：跳过写库的代码路径不是同一条） */
export function successLabel(scheduler: SchedulerState | null | undefined): string {
  return scheduler?.dry_run === true ? "试跑（已回滚）" : "成功";
}

/** 计数字段名带分母，且不发明百分比（契约 §6） */
export function counterLine(job: Job): string {
  const c = job.counters;
  if (!c) return NULL_TEXT;
  const scope = c.scope === "cumulative" ? "累计" : c.scope === "per_run" ? "本次" : "窗口未知";
  const unit = has(c.unit) ? String(c.unit) : "条目";
  const nums = Object.entries(c)
    .filter(([k, v]) => !["scope", "since", "total_seen", "unit"].includes(k) && typeof v === "number")
    .map(([k, v]) => `${k} ${v}`);
  const total = typeof c.total_seen === "number" ? ` / 共见 ${c.total_seen}` : "";
  return `${scope}（${unit}）：${nums.length ? nums.join(" · ") : NULL_TEXT}${total}`;
}
