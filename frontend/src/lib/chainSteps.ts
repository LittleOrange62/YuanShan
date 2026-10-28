import { apiUrl } from './base.ts';
import { authHeaders } from './api.ts';

/**
 * 加工段（15:59 那条链的第二段）**最近一次的逐步结果**。
 *
 * 为什么单独立一格的读数：40 步的结果原先只存在于跑的时候那段 stdout 里，跑完就没地方查
 * —— "昨天资金流没补上"只能靠人记得去翻日志。这里把它变成看得见的账，
 * 并且给出"这一步能不能单独补一次"（后端 `--only`）。
 *
 * `status` 只有 `ok` / `fail` 两种（跳过的步骤不进账，`ran_only` 说明这次只跑了几步）；
 * 界面不许把 `fail` 一律说成"故障"：`rc=2/3` 里有一类是**按口径拒写**（今天不是交易日），
 * 那是正常收工，原因文字由后端给（`reason`），页面只转述。
 */
export interface ChainStepRow {
  name: string; label?: string | null; core?: boolean | null;
  rc?: number | null; seconds?: number | null; rounds?: number | null;
  module?: string | null; status: string; reason?: string | null; tail?: string | null;
  /** 按口径拒写那句话（只有 `status==="refused"` 才有）。页面原样转述，不自己判"是不是故障"。 */
  soft?: string | null;
}

/** 阶梯读数（后端 `step_retry.status()`）：这一场还有什么没补上、下一档什么时候。 */
export interface ChainRetry {
  available?: boolean; reason?: string | null; enabled?: boolean;
  times?: string | null; catch_up_min?: number | null;
  pending?: string[]; pending_soft?: string[];
  lanes?: { label: string; at?: string | null; broken?: boolean;
            ran_at?: string | null; outcome?: string | null }[];
  next_at?: string | null; next_label?: string | null; gave_up?: boolean;
  last_lane?: string | null;
}

export interface ChainStepsState {
  available: boolean; reason?: string | null;
  target?: string | null; verdict?: string | null; line?: string | null;
  written_at?: string | null; started_at?: string | null;
  ran_only?: string[] | null; lagging_tables?: string[] | null;
  merged?: string | null;
  steps?: ChainStepRow[]; rerunnable?: string[]; failed_steps?: string[];
  refused_steps?: string[]; retry?: ChainRetry | null;
}

export async function loadChainSteps(): Promise<ChainStepsState> {
  let resp: Response;
  try {
    resp = await fetch(apiUrl('/api/jobs/chain-steps'), { headers: authHeaders() });
  } catch {
    throw new Error('连不到后端，读不到加工段的逐步结果（不是"没失败"，是读不到）');
  }
  const body = await resp.json().catch(() => null) as { data?: ChainStepsState } | null;
  if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
  const data = body?.data;
  if (!data || typeof data !== 'object') throw new Error('后端返回的形状不是对象，按读不到处理');
  return data;
}

/** 只补跑**一步**。后端可能回 409（链正在跑，抢不到锁）/ 404（步骤名不认识）/ 503（清单读不到），
 *  三种都要原样带出原因 —— 那句"本次未触发"必须让人看见，不然点了等于没点还像成功了。 */
export async function rerunChainStep(name: string): Promise<string> {
  const resp = await fetch(apiUrl(`/api/jobs/chain-steps/${encodeURIComponent(name)}/run`), {
    method: 'POST',
    headers: { ...authHeaders(), 'Content-Type': 'application/json' },
  });
  const body = await resp.json().catch(() => null) as { reason?: string; detail?: string } | null;
  if (!resp.ok) {
    throw new Error(body?.detail || body?.reason || `后端没有接受这次补跑（HTTP ${resp.status}）`);
  }
  return body?.reason || '已排队，约一分钟后刷新看结果';
}
