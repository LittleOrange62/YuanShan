import { authHeaders } from "./api";
import { apiUrl } from "./base";
import { reportAgentOutcome } from "./agent-sources.ts";

export * from "./agent-sources.ts";

export class AgentRequestError extends Error {
  status: number;
  constructor(message: string, status: number) { super(message); this.status = status; }
}

// 只观察本会话真正提交过的那次任务：页面加载时读到别人留下的历史失败记录，
// 不能拿去把当前来源标成不通。
const SUBMIT_PATH = /^\/(turns|chat-jobs|daily|deepdive|page-chats|backtests)(\/|$)/;
let armed = false;
function observeTask(path: string, data: unknown, posted: boolean) {
  if (posted && SUBMIT_PATH.test(path)) { armed = true; return; }
  if (!armed || !data || typeof data !== "object") return;
  const row = data as { status?: unknown; error?: unknown };
  if (row.status === "cancelled") { armed = false; return; }
  if (row.status === "failed") {
    armed = false;
    reportAgentOutcome(typeof row.error === "string" && row.error ? row.error : "AI 任务失败");
  } else if (row.status === "complete" || row.status === "incomplete") { armed = false; reportAgentOutcome(""); }
}

export async function agentRequest<T>(path: string, body?: unknown, signal?: AbortSignal): Promise<T> {
  const response = await fetch(apiUrl(`/api/review-agent${path}`), {
    method: body === undefined ? "GET" : "POST", signal,
    headers: { ...authHeaders(), ...(body === undefined ? {} : { "Content-Type": "application/json" }) },
    ...(body === undefined ? {} : { body: JSON.stringify(body) }),
  });
  const data = await response.json();
  if (!response.ok) {
    if (body !== undefined && SUBMIT_PATH.test(path)) armed = false;
    throw new AgentRequestError(response.status === 401 ? "请在「AI 探索 · 接入设置」页填写后端访问密钥（VR_API_KEY）" : (data.detail || data.error || "请求未完成"), response.status);
  }
  observeTask(path, data, body !== undefined);
  return data as T;
}
