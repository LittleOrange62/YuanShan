import { useEffect, useState } from "react";
import { apiUrl } from "./base";
import { authHeaders } from "./api";
import { errorText, type DataState } from "./datastate";

/**
 * 一次性 GET（页面右上角"重新取数"按钮驱动）。判定语义全在 `datastate.ts`，
 * 这里只负责把它翻译成一次真实的 HTTP 往返 —— 不做缓存、不做兜底、不重试成"看起来像没有"。
 *
 * ⚠️ 必须有硬超时：`/api/coverage` 与 `/api/quant/sector-heat` 实测跑到过 53.6s / 14.0s。
 * 不掐的话页面会一直停在"取数中…"，用户看到的就是一个转圈的空屏 —— 那与"后端坏了"长得一样，
 * 而且没有任何可重试的落点。掐断之后走 `error` 分支：文字说明是**等待超时**、不是没数据，
 * 面板下面的字段契约仍然在，用户知道该重试还是该报后端。
 */
export const SLOW_TIMEOUT_MS = 45_000;

/** 连不上时补两次再下结论（2026-10-06 实测「自选组分析」报连不到后端，而接口本身 3.3 秒回 200 ——
 *  差的正是服务重启那几十秒）。有界：只补两拍就收手，不变成一个悄悄转圈的请求，
 *  也不放宽成"重试到成功为止"（那会把真停了的服务读成还在跑）。 */
const OFFLINE_RETRY_MS = [2_000, 6_000];

const sleep = (ms: number) => new Promise<void>(r => setTimeout(r, ms));

export async function probe<T>(path: string, query?: Record<string, string | number | undefined>, timeoutMs = SLOW_TIMEOUT_MS): Promise<DataState<T>> {
  const search = new URLSearchParams();
  for (const [k, v] of Object.entries(query ?? {})) if (v !== undefined && v !== "") search.set(k, String(v));
  const qs = search.toString();
  const full = `/api${path}${qs ? `?${qs}` : ""}`;
  let resp: Response;
  try {
    resp = await fetchOnce(full, timeoutMs);
  } catch (e) {
    if (e instanceof Aborted) {
      return { kind: "error", reason: `等待超过 ${Math.round(timeoutMs / 1000)} 秒未返回，已中断这一请求。这是后端这一屏跑得慢（全库聚合类接口实测到几十秒），不是页面坏了、也不是没有数据 —— 点「重新取数」可重试。` };
    }
    // 网络层失败：按 OFFLINE_RETRY_MS 补两拍（服务可能正在重启）
    let waited = 0;
    for (const ms of OFFLINE_RETRY_MS) {
      await sleep(ms); waited += ms;
      try {
        resp = await fetchOnce(full, timeoutMs);
        return await interpret(resp, full);
      } catch (e2) {
        if (e2 instanceof Aborted) {
          return { kind: "error", reason: `补等 ${waited} 秒后这一次请求超过 ${Math.round(timeoutMs / 1000)} 秒没返回，已中断（是慢，不是没有数据）。` };
        }
      }
    }
    return {
      kind: "offline",
      reason: `连不到后端（127.0.0.1:8910），已自动补试 ${OFFLINE_RETRY_MS.length} 次`
        + `（隔 ${OFFLINE_RETRY_MS.join(" 秒、")} 秒）还是连不上 —— 服务没在跑或正在重启，`
        + "不是这一屏没有数据。起好服务后点「重新取数」。",
    };
  }
  return interpret(resp, full);
}

class Aborted extends Error { }

async function fetchOnce(full: string, timeoutMs: number): Promise<Response> {
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), timeoutMs);
  try {
    const resp = await fetch(apiUrl(full), { headers: authHeaders(), signal: ctrl.signal });
    clearTimeout(timer);
    return resp;
  } catch (e) {
    clearTimeout(timer);
    if (ctrl.signal.aborted) throw new Aborted("timeout");
    throw e;
  }
}

async function interpret<T>(resp: Response, full: string): Promise<DataState<T>> {
  let body: unknown = null;
  try {
    body = await resp.json();
  } catch {
    /* 非 JSON：按状态码归类，不猜内容 */
  }
  // 报全路径（带 /api 前缀）：用户在屏幕上看到的应当是真正请求的那个地址
  if (resp.status === 404) return { kind: "not_wired", path: full };
  // 422 的 detail 是**对象数组**，必须经 errorText 压成一句话，否则会被当 React 子节点炸掉整页
  if (!resp.ok) return { kind: "error", reason: errorText(body) ?? `HTTP ${resp.status}` };
  const rec = body as { data?: unknown } | null;
  const payload = rec && typeof rec === "object" && "data" in rec ? rec.data : body;
  if (!payload || typeof payload !== "object" || Array.isArray(payload)) {
    return { kind: "error", reason: "后端返回的不是对象（契约要求 JSON 对象或 {data:{…}}），已按不可读处理，不做猜测性渲染。" };
  }
  return { kind: "ok", data: payload as T };
}

/** `nonce` 递增即重取；`path` 变化（如换了股票代码）也重取。
 *  `skip` 为真时**这一轮根本不发请求**（如页面还没选中标的）：交回 `idle`，
 *  而不是发一次注定 422 的空参数请求 —— 后端会拒，控制台一片红，而那些请求
 *  本来就不该存在。 */
export function useProbe<T>(path: string, nonce: number,
  query?: Record<string, string | number | undefined>, skip = false): DataState<T> {
  const key = JSON.stringify(query ?? {});
  const [state, setState] = useState<DataState<T>>(skip ? IDLE : { kind: "loading" });
  useEffect(() => {
    if (skip) { setState(IDLE); return; }
    let live = true;
    setState({ kind: "loading" });
    probe<T>(path, key === "{}" ? undefined : (JSON.parse(key) as Record<string, string | number>))
      .then(next => { if (live) setState(next); });
    return () => { live = false; };
  }, [path, key, nonce, skip]);
  return state;
}

const IDLE: DataState<never> = {
  kind: "idle",
  reason: "这一屏还没有可查的对象（比如没选中标的），所以没有发请求 —— "
    + "这不是接口坏了，也不是没有数据。选好对象后点「重新取数」。",
};

/** 带参数的重取（个股类页面：代码 + 日期都要能换） */
export function useProbeParams<T>(path: string, nonce: number, query: Record<string, string | number | undefined>): DataState<T> {
  return useProbe<T>(path, nonce, query);
}

export function useNonce(): [number, () => void] {
  const [nonce, setNonce] = useState(0);
  return [nonce, () => setNonce(n => n + 1)];
}
