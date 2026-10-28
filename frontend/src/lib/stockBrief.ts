import type { BriefSegment, FactsBrief, FactsPayload } from "./stockFacts";
import type { PromptValues } from "./promptRender";

/**
 * 「个股信息」那一格的 400 字公司说明 —— **纯判读层**（这一份不 import 任何有运行时代码的东西，
 * 所以 `node --test` 能直接跑它；要出网的那半份在 `lib/stockBriefRun.ts`）。
 *
 * 三件形状上的事是刻意的：
 *  · **哪几段进、每段几条，不在页面这一侧**（住后端 `duanxian/stock_brief.py` 的 `SEGMENTS` 一处）。
 *    页面只把「个股研究」那一屏的**原始返回**原样交过去，交之前不替模型挑 ——
 *    否则取舍口径就长出第二份真相，屏幕上那行「研报 200 条只喂了 8 条」也就没人能对账。
 *  · **「400 字以内」比的是实际字数**（库里存的 `brief_chars`），不是模型的自我声明：
 *    超契约就照实报"多了几字"，不截（截一半上屏会变成"公司说明就这么短"）。
 *  · **没读到 / 读到了但是空 / 旧版没记这个数**，三件事三句话，不许并成一句。
 */

export type { BriefSegment } from "./stockFacts";

/** `/api/stock/brief/material` 的回体（后端 compose 的原始形状） */
export interface BriefMaterial {
  available: boolean; code: string; spec_version: string;
  word_limit: number; material_cap: number; material: string; chars: number;
  segments: BriefSegment[]; missing: string[]; fetched_at: string | null;
  material_sha: string; caliber: string; definitions: Record<string, string>;
  reason?: string | null;
}

/** `/api/stock/brief/result` 的两种回体：成功/失败都回库里那一行，被闸拒则回 saved:false + 原因 */
export interface BriefResult extends FactsBrief { saved?: boolean; reason?: string }

export type BriefRun =
  | { kind: "saved"; row: BriefResult }
  | { kind: "rejected"; reason: string }
  | { kind: "no_material"; reason: string };

const LABELS: Record<string, string> = {
  valuation: "估值与机构预期", quote: "行情", percentile: "估值历史分位", financials: "财务摘要",
  reports: "研报标题", news: "新闻", announcements: "公告标题", blocks: "所属板块",
  conceptTags: "概念标签", hotConcepts: "热门概念", margin: "融资融券", blockTrade: "大宗交易",
  holders: "股东户数", dividend: "分红方案", lockup: "限售解禁", dragonTiger: "龙虎榜",
  fundFlow: "每日资金流", investorQa: "互动问答", profile: "本机个股档案",
};

export function labelOf(key: string): string {
  return LABELS[key] ?? key;
}

/** `600519.SH` → `600519`：「个股研究」那些公开源端点都按 6 位码取，主档键留着带后缀。
 *  认不出这个形状（字母代码、港短码、ETF 之外的写法）就交空串 —— 按钮会因此列出"还差哪几样"而不是发一个注定 422 的请求。 */
export function numericOf(code: string | null | undefined): string {
  const m = /^(\d{6})(?:\.(?:SH|SZ|BJ))?$/i.exec((code ?? "").trim().toUpperCase());
  return m ? m[1] : "";
}

/** 「400 字以内」那句契约的实说。 */
export function contractSentence(chars: number | null | undefined, limit: number | null | undefined): string {
  if (typeof chars !== "number" || typeof limit !== "number") return "";
  if (chars <= limit) return `${chars} 字（契约 ${limit} 字以内）`;
  return `实际 ${chars} 字，比 ${limit} 字的契约多了 ${chars - limit} 字 —— 这一格照实摆，不截`;
}

/** 取舍明细的每一行：源给了多少、喂了多少、被上限削过没有。形状只在这一处拼。 */
export function receiptLines(segments: BriefSegment[]): string[] {
  return segments.map(s => {
    const head = `${s.label}：喂 ${s.taken} 条`;
    if (s.dropped) return `${head}（整段因材料上限被削掉，源本轮共 ${s.total} 条）`;
    if (s.truncated) return `${head}（源共 ${s.total} 条，材料到上限后这一段落过）`;
    if (s.taken < s.total) return `${head}（源本轮共 ${s.total} 条，按预算取 ${s.taken} 条）`;
    if (!s.taken) return `${s.label}：本轮 0 条`;
    return `${head}（源本轮 ${s.total} 条，全取）`;
  });
}

/** 库里那一行的"看了哪些材料"一句：几段进了、共几字。 */
export function rowReceiptSentence(row: FactsBrief | null): string {
  const segs = row?.material_segments ?? [];
  if (segs.length === 0) return "";
  const fed = segs.filter(s => s.taken > 0).length;
  const chars = row?.material_chars ?? null;
  return `${fed}/${segs.length} 段进了材料${chars != null ? `，共 ${chars} 字` : ""}`;
}

/** 请求失败的段与"源本轮就没东西"是两件事，合进同一条缺数清单里分开说清。 */
export function mergeMissing(missing: string[], errored: string[]): string[] {
  return [...missing, ...errored.map(l => `${l}（这一段的请求失败，不是源里没东西）`)];
}

/** 库里那一行说"写它的时候哪几段没读到"。
 *  `material_missing` 是 **null**（旧版回执没记）与是 **空数组**（真的每段都读到了）两件事，不说成一件。 */
export function rowMissingSentence(row: FactsBrief | null): string {
  if (!row?.material_segments?.length) return "";
  const miss = Array.isArray(row.material_missing) ? row.material_missing : null;
  if (miss === null) return "这一份没记下「写它的时候哪几段没读到」（旧版回执）—— 重跑一次就有了";
  if (miss.length === 0) return "";
  return `写这一份时，这几段没读到：${miss.join("、")} —— 上面那段说明是在缺这些材料的前提下写的`;
}

export function ageSentence(iso: string | null | undefined, now: number = Date.now()): string {
  if (!iso) return "";
  const t = new Date(iso).getTime();
  if (!Number.isFinite(t)) return "";
  const min = Math.max(0, Math.round((now - t) / 60000));
  if (min < 60) return `距今 ${min} 分钟`;
  const hr = Math.round(min / 60);
  if (hr < 48) return `距今 ${hr} 小时`;
  return `距今 ${Math.round(hr / 24)} 天`;
}

/** 这份结论是谁写的、按哪一刻的材料写的：公开源会变，"三天前那份"与"刚写的"不是一句话。 */
export function metaSentence(row: FactsBrief, now: number = Date.now()): string {
  const bits: string[] = [];
  if (row.generated_at) bits.push(`生成于 ${String(row.generated_at).slice(0, 16)}`);
  const age = ageSentence(row.generated_at, now);
  if (age) bits.push(age);
  bits.push(`模型 ${row.model || "未交"}｜接入 ${row.ai_provider || "未交"}`);
  if (row.material_chars != null) bits.push(`材料 ${row.material_chars} 字`);
  if (row.input_fetched_at) bits.push(`材料抓于 ${String(row.input_fetched_at).slice(0, 16)}`);
  if (row.prompt_source) bits.push(`话术 ${row.prompt_source}`);
  return bits.join(" · ");
}

/** 还没配 AI 来源 / 还没选到一只票时，拦下来要说清"还差哪几样"（按钮不静默 disabled）。 */
export function missingPrerequisites(o: { hasLlm: boolean; available: boolean; numeric: string }): string[] {
  const out: string[] = [];
  if (!o.numeric) out.push("这一屏还没选到一只票（地址里没有 symbol，或这个代码不是 6 位 A 股码）");
  if (!o.available) out.push("「个股信息」这一块没读出这一只（本机主档里没有它）");
  if (!o.hasLlm) out.push("还没接入 AI：到「AI 探索 · 接入设置」保存并测通一个来源（这一趟走你自己配的模型）");
  return out;
}

/** 话术里那几个占位符能给什么 —— 与后端 `SCOPES.stock_brief.fields` 同一批名字。 */
export function briefPromptValues(m: BriefMaterial, facts: FactsPayload, date: string): PromptValues {
  return {
    date, name: facts.name ?? null, code: facts.code ?? null,
    word_limit: m.word_limit, segment_count: m.segments.filter(s => s.taken > 0).length,
  };
}

/** 按钮上那一句：正在跑到哪一步了要说出来，"生成中"三个字不够。 */
export function stageSentence(stage: string | null, busy: boolean): string {
  if (!busy) return "";
  return stage ? stage : "正在跑这一趟…";
}

/** 2026-10-27 用户点名：这一格改成**进页面就自己发起一趟**（旧规矩是「只有点按钮才发起」）。
 *  一条纪律翻转回去，最容易翻的车是「每次进页面都白烧一次额度」，所以自动跑要过四道闸，
 *  四道都在这一处判（组件里只留接线）：
 *   ① 库里已经有成功的正文 ⇒ 直接显示，不再打 —— 这条就是用户要的那句「先看数据库有没有」；
 *   ② 前置不齐（没选到票 / 本机主档没这一只 / 还没接入 AI）或话术还没从 `/api/prompts` 取到 ⇒ 不打，
 *      并把「还差哪几样」摊在屏上（不发注定被拦的请求，同全站主按钮那条纪律）；
 *   ③ 最近一次这一趟失败过、且距今不足 `AUTO_RETRY_COOLDOWN_MIN` 分钟 ⇒ 这一轮不自动重打
 *      （失败原因原样摊着）；**手动点右上角那个按钮不受这一条限** —— 要立刻重试的人按得动；
 *   ④ 有失败记录但那个时间读不出分钟 ⇒ 当作「还在冷却里」处理：判不了就不自动花钱，交给人手动决定
 *      （同全站那条「探测器不许在坏的时候交空值装成没事」的纪律）。
 *  返回值里 `why` 那句是要上屏的话，不是日志 —— 屏幕必须能说清「为什么这一轮没自动打」。 */
export const AUTO_RETRY_COOLDOWN_MIN = 30;

export function minutesSince(iso: string | null | undefined, now: number = Date.now()): number | null {
  if (!iso) return null;
  const t = new Date(String(iso)).getTime();
  return Number.isFinite(t) ? (now - t) / 60000 : null;
}

export function autoRunDecision(o: {
  hasBrief: boolean;
  error?: string | null;
  failedAt?: string | null;
  prereq: string[];
  templateReady: boolean;
  promptsLoading?: boolean;
  now?: number;
}): { fire: boolean; why: string } {
  if (o.hasBrief) return { fire: false, why: "库里已经有这一份 —— 直接显示，不再自动打这一趟" };
  if (o.prereq.length > 0) return { fire: false, why: `这一趟自动发不出去，还差：${o.prereq.join("；")}` };
  if (o.promptsLoading) return { fire: false, why: "话术还在从接口取，取到就打这一趟…" };
  if (!o.templateReady) {
    return { fire: false, why: "话术没取到（`/api/prompts` 这一条通道没交回那一份）—— 先不发，"
      + "到「系统配置 → AI 话术」看一眼是不是这条通道空了" };
  }
  const waited = minutesSince(o.failedAt, o.now ?? Date.now());
  if (o.error && waited === null) {
    return { fire: false, why: `最近一次这一趟失败（${o.error}），但库里那句失败时间读不出分钟 —— `
      + "判不了就不自动打（免得每次进页面白烧一次额度），要重试点右上角那个按钮" };
  }
  if (o.error && waited !== null && waited < AUTO_RETRY_COOLDOWN_MIN) {
    const left = Math.max(1, Math.ceil(AUTO_RETRY_COOLDOWN_MIN - waited));
    return { fire: false, why: `最近一次自动重跑在 ${Math.floor(waited)} 分钟前失败（${o.error}）—— `
      + `${left} 分钟内不再自动打；现在就想要一份的话点右上角那个按钮，那一发不受这个限制` };
  }
  return { fire: true, why: `库里还没有这一份 —— 进这一页自动打这一趟（要出网、用你配的模型额度，一次几十秒到几分钟）` };
}
