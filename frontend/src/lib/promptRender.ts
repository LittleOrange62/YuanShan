/**
 * 提示词模板的**渲染规则**（叶子模块：不 import 任何东西，所以能被 `node --test` 直接跑）。
 *
 * 与后端 `duanxian/prompt_slots.py:render()` 逐条对齐 —— 两边不一样就会出现
 * "配置页看着是对的、跑出来的提示词不是"。改这里必须同时改那边，并跑两边的测试。
 *
 *  1. 只替换白名单形状的占位符 `{小写标识符}`；`{"answer":"正文"}` 这种是给模型的输出约定，不动；
 *  2. 未知占位符**原样留着** —— 静默换成空串会让模型收到一句缺主语的话，
 *     比让人看见一个没换掉的 `{pice}` 难查得多；
 *  3. 值为 null/undefined/空 → `未提供`，**不给 0、不给空串**；
 *     `quote_line`/`em_line` 是"整句开关"（没报价时整句就该消失），它们允许空。
 */
export type PromptValues = Record<string, string | number | null | undefined>;

export const NULLABLE_FIELDS = new Set(['quote_line', 'em_line']);

const PLACEHOLDER = /\{([a-z][a-z0-9_]*)\}/g;

export function renderPrompt(template: string, values: PromptValues): string {
  return (template ?? '').replace(PLACEHOLDER, (whole, key: string) => {
    if (!(key in values)) return whole;
    const v = values[key];
    if (v === null || v === undefined) return '未提供';
    if (v === '' && !NULLABLE_FIELDS.has(key)) return '未提供';
    return String(v);
  });
}

/** 模板里出现了 values 没给的字段 → 列出来。保存时后端会拒，这里是页面侧的提前提醒。 */
export function unfilledPlaceholders(template: string, values: PromptValues): string[] {
  const out = new Set<string>();
  (template ?? '').replace(PLACEHOLDER, (whole, key: string) => {
    if (!(key in values)) out.add(key);
    return whole;
  });
  return [...out].sort();
}
