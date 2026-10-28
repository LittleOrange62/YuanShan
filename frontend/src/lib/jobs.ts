import { apiUrl } from "./base";
import { authHeaders } from "./api";
import type { Job, JobsSnapshot } from "./jobsView";

/**
 * `/api/jobs` 的取数层（契约 `docs/页面展示工作指南/03-任务调度页契约.md` §2/§5）。
 *
 * 这里只做两件事：把 HTTP 层的不确定变成可显示的三种状态、把 409/异步这些
 * 触发语义原样交给页面。**判定逻辑一律不在这层**（那份只有 `jobsView.ts` 一份）。
 */

export type JobsState =
  | { kind: "loading" }
  | { kind: "error"; reason: string }
  | { kind: "ok"; snapshot: JobsSnapshot };

export type JobsPayload = JobsSnapshot & { jobs?: Job[] };

/** 与 qd 轨同一个纪律：404 是"接口还没上线"，不是"没有作业"，两者措辞必须不同 */
export function jobsFetchFailure(status: number, detail?: string): string {
  if (status === 404) {
    return "接口未提供（HTTP 404：/api/jobs）。这不代表没有作业 —— 本地作业登记尚未接入，页面无可显示内容。";
  }
  return detail || `HTTP ${status}`;
}

export async function fetchJobs(): Promise<JobsState> {
  let resp: Response;
  try {
    resp = await fetch(apiUrl("/api/jobs"), { headers: authHeaders() });
  } catch {
    return { kind: "error", reason: "连不到后端（127.0.0.1:8910）。先启动服务再看这一屏。" };
  }
  let body: unknown = null;
  try {
    body = await resp.json();
  } catch {
    /* 非 JSON：按状态码归类，不猜内容 */
  }
  if (!resp.ok) {
    const detail = (body as { detail?: string; error?: string; reason?: string } | null)?.detail
      || (body as { error?: string; reason?: string } | null)?.error
      || (body as { reason?: string } | null)?.reason;
    return { kind: "error", reason: jobsFetchFailure(resp.status, detail) };
  }
  const record = (body as { data?: unknown } | null)?.data ?? body;
  if (!record || typeof record !== "object" || Array.isArray(record)) {
    return { kind: "error", reason: "后端返回的形状不是对象（契约 §2 要求 {generated_at, scheduler, jobs, …}），已按不可读处理，不做任何猜测性渲染。" };
  }
  return { kind: "ok", snapshot: record as JobsPayload };
}

/** §5 的触发结果。四种情况必须长得不一样，所以这里不折叠成 boolean */
export type TriggerOutcome =
  | { kind: "queued"; runId: string | null }
  | { kind: "conflict"; reason: string }
  | { kind: "refused"; reason: string }
  | { kind: "error"; reason: string };

export async function triggerJob(key: string): Promise<TriggerOutcome> {
  let resp: Response;
  try {
    resp = await fetch(apiUrl(`/api/jobs/${encodeURIComponent(key)}/run`), {
      method: "POST",
      headers: { ...authHeaders(), "Content-Type": "application/json" },
    });
  } catch {
    return { kind: "error", reason: "连不到后端，本次未触发。" };
  }
  let body: unknown = null;
  try {
    body = await resp.json();
  } catch {
    /* 空响应体：按状态码归类 */
  }
  const rec = (body as Record<string, unknown> | null) ?? {};
  const reason = typeof rec.reason === "string" ? rec.reason
    : typeof rec.detail === "string" ? rec.detail
    : typeof rec.error === "string" ? rec.error : "";
  if (resp.status === 409) {
    return { kind: "conflict", reason: reason || "该作业正在运行，本次未触发" };
  }
  if (resp.status === 403 || resp.status === 400 || resp.status === 422) {
    return { kind: "refused", reason: reason || `后端拒绝本次触发（HTTP ${resp.status}）` };
  }
  if (!resp.ok) {
    return { kind: "error", reason: reason || `HTTP ${resp.status}` };
  }
  const runId = typeof rec.run_id === "string" ? rec.run_id : null;
  return { kind: "queued", runId };
}

/** 快照年龄：只用于"这屏数据多久了"，不参与任何判定 */
export function snapshotAgeText(generatedAt: string | null | undefined, nowMs: number): string {
  if (!generatedAt) return "后端未给 generated_at，无法判断这一屏有多新";
  const t = Date.parse(generatedAt);
  if (Number.isNaN(t)) return `generated_at 无法解析（${generatedAt}）`;
  const secs = Math.max(0, Math.round((nowMs - t) / 1000));
  if (secs < 60) return `${secs} 秒前`;
  if (secs < 3600) return `${Math.round(secs / 60)} 分钟前`;
  return `${Math.round(secs / 3600)} 小时前`;
}
