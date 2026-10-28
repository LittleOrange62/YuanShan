import { buildContext, type Expansion } from './aiContext.ts';
import { authHeaders } from './api.ts';
import { apiUrl } from './base.ts';

/**
 * 右侧「AI分析」栏的预设问题清单。
 *
 * 两条规矩：
 *  1. **预设问题必须自带资料口径**。`sources` 列出这道题要读哪些接口，运行前现场抓、
 *     拼成上下文喂给模型 —— 不许让模型在"没数据"的情况下凭印象写结论。
 *     拼装与预算分配在 `lib/aiContext.ts`（纯逻辑、可单测；取数从这里注入）。
 *  2. **结果必须带生成时间与用的模型**（`generated_at` / `source`），
 *     读的人才能分清"这是刚才算的"还是"昨天那场的数算的"。
 *
 * 新增预设只往这个数组里加：`id` 一旦发布不要改（结果按 id+交易日 存本地）。
 */
export interface AiPreset {
  id: string;
  title: string;
  /** 用户原话给的完整提示词，一字不改地存这里，页面上可展开核对 */
  prompt: string;
  /** 这道题要读哪些只读接口（不带 `/api` 前缀，同 `lib/api.ts`） */
  sources: string[];
  /** 二级展开：先读 `from`，取前 `top` 行的 `pick` 字段，再逐个读 `then+值`。
   *  题材类的题必须有这一段 —— 热度榜只到板块一级，"题材内部个股分层"要成分明细。 */
  expand?: Expansion;
}

export const AI_PRESETS: AiPreset[] = [
  {
    id: 'theme-high-low-switch',
    title: '短题材的高低切挖掘机会',
    sources: ['/quant/sector-heat?limit=12', '/market/emotion', '/qd/board-ladder'],
    expand: { from: '/quant/sector-heat?limit=12', pick: 'sector_code', top: 3,
              then: '/quant/sector-members?sector=', limit: 40 },
    prompt: '当主流热门题材进入高位分歧、资金开始高低切换阶段，请梳理该题材内部全部个股，'
      + '按照股价位置、涨幅大小、市值高低进行分层。筛选出同一题材下股价处于低位、前期涨幅有限、'
      + '基本面无利空、具备补涨逻辑的标的，区分纯跟风补涨、有独立逻辑的低位龙头。'
      + '分析题材高低切换的启动信号、资金流向特征，给出低位标的低吸入场价位、止损位置、预期上涨空间。'
      + '同时提醒题材整体退潮时，低位补涨也会出现轮动加速、快速结束的风险，限定短线持仓周期。'
      + '回答内容精简至500字以内',
  },
];

/** 抓这道题需要的页面资料，压成模型能读的文本。抓不到的源如实写"未取到"，不编。 */
export function gatherContext(preset: AiPreset): Promise<string> {
  return buildContext(preset, getJson);
}

/** 只读接口拿原始 JSON：`vr/` 那批包了一层 `{data:...}`，这里统一剥掉。 */
export async function getJson(path: string): Promise<unknown> {
  // 前缀 `/api` 与 `lib/api.ts:request()` 一致 —— 少了它会被 SPA 兜底路由吃掉，
  // 拿回一份 HTML 还 HTTP 200，于是这道题在模型眼里变成"资料为空"。
  const r = await fetch(apiUrl(`/api${path}`), { headers: authHeaders() });
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  const body = await r.json() as Record<string, unknown>;
  return body && typeof body === 'object' && 'data' in body ? body.data : body;
}

/** 库里实际有数的那一场（与首板/复盘同一个判定），给结果当缓存键用。 */
export async function currentSession(): Promise<string> {
  try {
    const b = await getJson('/review/expected-date') as { date?: string };
    return String(b?.date || '');
  } catch {
    return '';
  }
}
