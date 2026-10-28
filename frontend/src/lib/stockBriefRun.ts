import { api } from "@/lib/api";
import { chatStream, type ChatResult } from "@/lib/llm";
import { renderPrompt, unfilledPlaceholders } from "@/lib/promptRender";
import type { FactsPayload } from "@/lib/stockFacts";
import {
  briefPromptValues, labelOf, mergeMissing, type BriefMaterial, type BriefResult, type BriefRun,
} from "@/lib/stockBrief";

/**
 * 跑一趟的形状（与 `lib/stockBrief.ts` 那半份纯判读层分开）：
 * 那一半不 import 任何东西，`node --test` 能直接跑它；这一半要 `@/lib/api`、要出网，只能留在构建里。
 */

/** 「个股研究」那一屏的 17 段，逐段一个调用。键名必须与后端 `SEGMENTS[*].key` 一字不差 ——
 *  写错一个字母不会报错，只会让那一段永远"没读到"（compose 认不出形状就算空）。 */
const CALLS: [string, (numeric: string) => Promise<unknown>][] = [
  ["valuation", c => api.valuation(c)],
  ["quote", c => api.quote(c)],
  ["percentile", c => api.percentile(c)],
  ["financials", c => api.financials(c)],
  ["reports", c => api.reports(c)],
  ["news", c => api.news(c)],
  ["announcements", c => api.announcements(c)],
  ["blocks", c => api.blocks(c)],
  ["hotConcepts", c => api.hotConcepts(c)],
  ["margin", c => api.margin(c)],
  ["blockTrade", c => api.blockTrade(c)],
  ["holders", c => api.holders(c)],
  ["dividend", c => api.dividend(c)],
  ["lockup", c => api.lockup(c)],
  ["dragonTiger", c => api.dragonTiger(c)],
  ["fundFlow", c => api.fundFlow(c)],
  ["investorQa", c => api.investorQa(c)],
];

export const CALL_COUNT = CALLS.length;

/** 抓材料：一段失败不拖垮整份（失败段交 null，并和"源里没东西"分开记进缺数声明）。 */
export async function gatherSegments(numeric: string, facts: FactsPayload):
  Promise<{ segments: Record<string, unknown>; errored: string[] }> {
  const settled = await Promise.allSettled(CALLS.map(([, fn]) => fn(numeric)));
  const segments: Record<string, unknown> = {};
  const errored: string[] = [];
  settled.forEach((r, i) => {
    const key = CALLS[i][0];
    if (r.status === "fulfilled") segments[key] = r.value;
    else { segments[key] = null; errored.push(labelOf(key)); }
  });
  // 主营标签/员工数/板块这一格来自本机库（`/api/stock/facts` 已经读过一次，不另发请求）
  segments.profile = facts?.profile
    ? { ...facts.profile, board: facts.board ?? null, name: facts.name ?? null, is_st: facts.is_st ?? null }
    : null;
  return { segments, errored };
}

/** 一次完整运行：抓材料 → 后端组装 → 交给模型（红线档）→ 结果交回后端（那边重新过一次闸才入库）。
 *  失败/被拒都会把这一轮记进库（`ok:false`），屏幕因此分得清"没跑过"与"跑了没成"。 */
export async function runBrief(o: {
  code: string; numeric: string; facts: FactsPayload; template: string; templateId: string;
  templateSource: string; onStage?: (s: string) => void;
}): Promise<BriefRun> {
  const stamp = new Date().toISOString();
  o.onStage?.(`正在读「个股研究」那 ${CALL_COUNT} 段公开源…`);
  const { segments, errored } = await gatherSegments(o.numeric, o.facts);
  o.onStage?.("把各段组成一份材料（哪几段进、每段几条由后端一处定）…");
  const mat = await api.briefMaterial<BriefMaterial>({ code: o.code, segments, fetched_at: stamp });
  const missing = mergeMissing(mat.missing, errored);
  if (!mat.available) {
    const reason = mat.reason ?? "一份材料都没组出来";
    await api.briefResult<BriefResult>({
      code: o.code, ok: false, error: reason, material_chars: mat.chars,
      segments: mat.segments, missing, input_fetched_at: stamp,
      prompt_slot: o.templateId, prompt_source: o.templateSource,
    });
    return { kind: "no_material", reason };
  }
  const values = briefPromptValues(mat, o.facts, stamp.slice(0, 10));
  const unfilled = unfilledPlaceholders(o.template, values);
  if (unfilled.length) {
    // 未知占位符不静默换空串：那等于让模型收到一句缺主语的话，而屏幕上看不出为什么答得怪
    return { kind: "rejected", reason:
      `话术里有这一档没有的占位符：${unfilled.map(k => `{${k}}`).join("、")}。`
      + `这一档能给的是 ${Object.keys(values).map(k => `{${k}}`).join("、")} —— `
      + "到「系统配置 → AI 话术」改回来再跑" };
  }
  const prompt = renderPrompt(o.template, values);
  o.onStage?.(`材料 ${mat.chars} 字已就位，交给模型（这一趟出网，可能要几十秒）…`);
  let res: ChatResult;
  try {
    res = await chatStream([{ role: "user", content: prompt }], mat.material, {});
  } catch (e) {
    const reason = e instanceof Error ? e.message : "AI 调用失败（原因未交）";
    await api.briefResult<BriefResult>({
      code: o.code, ok: false, error: reason, material_chars: mat.chars,
      segments: mat.segments, missing, input_fetched_at: stamp,
      prompt_slot: o.templateId, prompt_source: o.templateSource,
    });
    throw new Error(reason);
  }
  o.onStage?.("正文已回，后端过闸入库…");
  const row = await api.briefResult<BriefResult>({
    code: o.code, ok: true, brief: res.content,
    word_limit: mat.word_limit, material_chars: mat.chars,
    segments: mat.segments, missing,
    model: res.ai_source?.model ?? null, ai_provider: res.ai_source?.provider ?? null,
    prompt_slot: o.templateId, prompt_source: o.templateSource, input_fetched_at: stamp,
  });
  if (row.saved === false) return { kind: "rejected", reason: row.reason ?? "正文未通过写入侧的闸" };
  return { kind: "saved", row };
}
