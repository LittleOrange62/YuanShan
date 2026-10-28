import { authHeaders } from "./api";
import { apiUrl } from "./base";

/**
 * 往 `/api/*` 写一次东西（VR 那一层的路由都回 `{"data": ...}`，见 `vr/app.py`）。
 *
 * ⚠️ `path` 的约定与 `probe()` 完全一致：**不带 `/api` 前缀**（`postJson("/review-note", …)`），
 * 前缀在这里统一加。09-28 实测踩过：调用点少写前缀时请求打到 `/review-note`，
 * 命中的是 SPA 静态挂载（只允许 GET）→ 界面收到一句没头没尾的 "Method Not Allowed"。
 * 读和写两种约定迟早会分叉，所以写这边直接照读的来。
 *
 * 这里最要紧的一条：**写失败必须说"没写进去"，不能含糊**。项目里已经有过教训 ——
 * 只看 `error` 字段会在这一笔根本没落库的情况下显示"已保存"。所以：
 *  · 连不到进程 → `ok:false` + 明说"这次没写进去"；
 *  · 非 2xx → 把后端 `detail`/`error` 原样带出（400 的点名校验全靠它）；
 *  · 2xx 但没交出 `data` → 也算失败：调用方无法确认生效的东西，不假定已生效。
 */
export interface PostResult<T> {
  ok: boolean;
  status: number;
  data: T | null;
  reason: string;
}

export async function postJson<T>(path: string, body?: unknown): Promise<PostResult<T>> {
  const full = `/api${path}`;
  let resp: Response;
  try {
    resp = await fetch(apiUrl(full), {
      method: "POST",
      headers: { ...authHeaders(), ...(body === undefined ? {} : { "Content-Type": "application/json" }) },
      ...(body === undefined ? {} : { body: JSON.stringify(body) }),
    });
  } catch {
    return { ok: false, status: 0, data: null, reason: `连不到后端，${full} 这一笔没写进去（不是“已经保存”）` };
  }
  const text = await resp.text().catch(() => "");
  let parsed: { data?: T; detail?: string; error?: string } | null = null;
  try { parsed = text ? JSON.parse(text) : null; } catch { parsed = null; }

  if (!resp.ok) {
    const why = parsed?.detail || parsed?.error
      || (text ? text.slice(0, 200) : "")
      || `HTTP ${resp.status}`;
    return { ok: false, status: resp.status, data: null, reason: `${full}：${why}` };
  }
  if (!parsed || parsed.data === undefined) {
    return { ok: false, status: resp.status, data: null,
             reason: "后端没交出存好的那一份，不假定已生效" };
  }
  return { ok: true, status: resp.status, data: parsed.data, reason: "" };
}
