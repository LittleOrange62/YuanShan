import { SAVED_PROVIDERS } from "./ai-access.ts";

export type AgentConnection = { provider: string; model: string; baseURL: string; apiKey: string; verifiedAt?: number };
export type TestState = 'untested' | 'passed' | 'failed';
export type SavedSource = { id: string; savedAt: number; testState: TestState; testNote: string; testedAt: number; failures: number } & AgentConnection;

// 连续失败到第 FAIL_LIMIT 次才判定"不通"并让位；默认开启，全部不通时按顺序循环再试。
export const FAIL_LIMIT = 2;

// 本机保存的多条 AI 来源；列表顺序就是自动切换的优先级。
const legacyKey = "astock-agent-connection";
const listKey = "astock-agent-sources";
const activeKey = "astock-agent-active";
const failoverKey = "astock-agent-failover";
let servedId = "";

function isConnection(value: unknown): value is AgentConnection {
  const row = value as AgentConnection;
  return !!row && typeof row === "object" && SAVED_PROVIDERS.includes(row.provider)
    && typeof row.model === "string" && typeof row.baseURL === "string" && typeof row.apiKey === "string";
}
/** 容忍旧版本缺字段：不能因为少一个 failures 就把用户已保存的来源整条丢掉。 */
function normalize(value: unknown): SavedSource | null {
  const row = value as SavedSource;
  if (!isConnection(row) || typeof row.id !== "string") return null;
  const state: TestState = ['untested', 'passed', 'failed'].includes(row.testState) ? row.testState : 'untested';
  const failures = typeof row.failures === 'number' && row.failures > 0 ? Math.floor(row.failures) : (state === 'failed' ? FAIL_LIMIT : 0);
  return { provider: row.provider, model: row.model, baseURL: row.baseURL, apiKey: row.apiKey,
    id: row.id, savedAt: typeof row.savedAt === 'number' ? row.savedAt : Date.now(),
    testState: failures >= FAIL_LIMIT ? 'failed' : (failures > 0 ? 'untested' : state),
    testNote: typeof row.testNote === 'string' ? row.testNote : '', testedAt: typeof row.testedAt === 'number' ? row.testedAt : 0,
    failures };
}
function persist(rows: SavedSource[]) {
  try { localStorage.setItem(listKey, JSON.stringify(rows)); } catch { /* 存储被禁用时本次只在内存生效 */ }
  window.dispatchEvent(new Event("astock-connection-changed"));
}
export function readSources(): SavedSource[] {
  try {
    const stored = JSON.parse(localStorage.getItem(listKey) || "null");
    if (Array.isArray(stored)) return stored.map(normalize).filter((row): row is SavedSource => row !== null);
    const legacy = JSON.parse(localStorage.getItem(legacyKey) || "null");
    if (!isConnection(legacy)) return [];
    const verified = typeof legacy.verifiedAt === 'number' && legacy.verifiedAt > 0 ? legacy.verifiedAt : 0;
    const row: SavedSource = { provider: legacy.provider, model: legacy.model, baseURL: legacy.baseURL, apiKey: legacy.apiKey,
      id: crypto.randomUUID(), savedAt: Date.now(), testState: verified ? 'passed' : 'untested', testNote: '', testedAt: verified, failures: 0 };
    localStorage.setItem(activeKey, row.id);
    persist([row]);
    localStorage.removeItem(legacyKey);
    return [row];
  } catch { return []; }
}
export function activeSourceId(): string {
  const rows = readSources();
  const stored = localStorage.getItem(activeKey) || "";
  return rows.some(row => row.id === stored) ? stored : (rows[0]?.id ?? "");
}
export function setActiveSource(id: string) {
  if (!readSources().some(row => row.id === id)) return;
  try { localStorage.setItem(activeKey, id); } catch { return; }
  window.dispatchEvent(new Event("astock-connection-changed"));
}
export function failoverEnabled(): boolean { return localStorage.getItem(failoverKey) !== "off"; }
export function setFailoverEnabled(on: boolean) {
  try { localStorage.setItem(failoverKey, on ? "on" : "off"); } catch { return; }
  window.dispatchEvent(new Event("astock-connection-changed"));
}
const identity = (row: AgentConnection) => [row.provider, row.model, row.baseURL].join("\u0000");
export const sourceIdentity = identity;

/** 保存只写入本机列表；能不能用由「测试连接」和任务结果决定。 */
export function saveAgentConnection(value: AgentConnection): string {
  const rows = readSources();
  const connection: AgentConnection = { provider: value.provider, model: value.model, baseURL: value.baseURL, apiKey: value.apiKey };
  const existing = rows.find(row => identity(row) === identity(connection));
  if (existing) {
    persist(rows.map(row => row.id === existing.id ? { ...row, ...connection, apiKey: connection.apiKey || row.apiKey } : row));
    return existing.id;
  }
  const row: SavedSource = { ...connection, id: crypto.randomUUID(), savedAt: Date.now(), testState: 'untested', testNote: '', testedAt: 0, failures: 0 };
  persist([...rows, row]);
  try { localStorage.setItem(activeKey, row.id); } catch { /* 只影响下一次读取的默认选中 */ }
  return row.id;
}
export function findSavedSourceId(value: AgentConnection): string {
  return readSources().find(row => identity(row) === identity(value))?.id ?? "";
}
export function removeAgentSource(id: string) {
  const rows = readSources().filter(row => row.id !== id);
  persist(rows);
  if (localStorage.getItem(activeKey) === id) setActiveSource(rows[0]?.id ?? "");
}
export function moveAgentSource(id: string, offset: -1 | 1) {
  const rows = readSources();
  const at = rows.findIndex(row => row.id === id);
  const to = at + offset;
  if (at < 0 || to < 0 || to >= rows.length) return;
  const next = [...rows];
  [next[at], next[to]] = [next[to], next[at]];
  persist(next);
}
/** 一次成败记在对应那条上：成功清零，连续失败到 FAIL_LIMIT 次才判定不通并让位。 */
export function noteSourceResult(id: string, ok: boolean, note = "") {
  const rows = readSources();
  if (!rows.some(row => row.id === id)) return;
  persist(rows.map(row => {
    if (row.id !== id) return row;
    const failures = ok ? 0 : row.failures + 1;
    return { ...row, failures, testState: ok ? 'passed' : (failures >= FAIL_LIMIT ? 'failed' : 'untested'),
      testNote: ok ? '' : note.slice(0, 300), testedAt: Date.now() };
  }));
}
export function clearSourceMark(id: string) {
  persist(readSources().map(row => row.id === id ? { ...row, testState: 'untested', testNote: '', testedAt: 0, failures: 0 } : row));
}

/** 本次该用哪一条：默认用户选中的；自动切换时跳过已判定不通的，按列表顺序顺位。 */
export function currentSource(): SavedSource | null {
  const rows = readSources();
  if (!rows.length) { servedId = ""; return null; }
  const active = rows.find(row => row.id === activeSourceId()) ?? rows[0];
  if (!failoverEnabled()) { servedId = active.id; return active; }
  const start = rows.indexOf(active);
  const healthy = rows.slice(start).find(row => row.failures < FAIL_LIMIT) ?? rows.find(row => row.failures < FAIL_LIMIT);
  if (healthy) { servedId = healthy.id; return healthy; }
  // 全部都不通时不猜死：先试累计失败次数最少、最久没被试的那条，读取本身不改标记。
  const oldest = [...rows].sort((a, b) => a.failures - b.failures || a.testedAt - b.testedAt || a.savedAt - b.savedAt)[0];
  servedId = oldest.id;
  return oldest;
}
// Verification metadata is opt-in for the status display, never a model request field.
export function loadAgentConnection(includeVerification = false): AgentConnection | null {
  const row = currentSource();
  if (!row) return null;
  const connection: AgentConnection = { provider: row.provider, model: row.model, baseURL: row.baseURL, apiKey: row.apiKey };
  return includeVerification && row.testState === 'passed' && row.testedAt > 0 && row.testedAt <= Date.now()
    ? { ...connection, verifiedAt: row.testedAt } : connection;
}
export function servedSourceId() { return servedId; }

// 与 review_agent/runtime.py 的 safe_engine_error()、responses_bridge.status_message()
// 同步：只有"这个来源本身没服务成功"才算故障；上下文超限、本机存储与后端访问密钥不换来源。
const SOURCE_FAULT = /(限流|额度|密钥验证失败|网络连接中断|暂时无法访问|接口拒绝了请求|AI 请求失败|渠道不可用|暂时不可用|没有该模型|未提交通过校验|未通过检查|Agent 超时)/;
const NOT_SOURCE_FAULT = /(上下文超过模型上限|后端访问密钥|浏览器禁止会话存储|释放存储空间|请求过大|任务已取消|已取消)/;

/** 任务或测试结束回报：来源故障累计一次，成功则清零。 */
export function reportAgentOutcome(error = "") {
  if (!servedId) return;
  if (!error) return noteSourceResult(servedId, true);
  if (NOT_SOURCE_FAULT.test(error) || !SOURCE_FAULT.test(error)) return;
  noteSourceResult(servedId, false, error);
}
