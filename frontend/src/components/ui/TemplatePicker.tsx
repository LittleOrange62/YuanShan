import type { PromptTemplate } from "@/lib/prompts";

/**
 * 话术模板选择器（内置槽位 + 用户在系统配置页自建的模板）。
 *
 * 抽成一个组件是因为两处（首页短线情绪、首板分析）都要放，
 * 复制一份就会长出不一致的措辞与不同的禁用条件 —— 这类东西一旦分叉，
 * 用户在两个页面上选中的"同一个模板"其实不是同一件事。
 *
 * `templates.length === 0` 不隐藏、照原样禁用并写明"没有可用模板"：
 * 那意味着 `/api/prompts` 没返好东西，藏起来就等于把故障咽下去。
 */
export function TemplatePicker({ templates, currentId, onPick, scopeLabel }: {
  templates: PromptTemplate[]; currentId: string; onPick: (id: string) => void; scopeLabel?: string;
}) {
  const current = templates.find(t => t.id === currentId) ?? null;
  return (
    <span className="inline-flex items-center gap-1 text-[11px] text-muted-foreground">
      <span>模板</span>
      <select value={currentId} onChange={e => onPick(e.target.value)} disabled={templates.length === 0}
        aria-label={scopeLabel ? `${scopeLabel} 的话术模板` : "话术模板"}
        className="max-w-56 rounded-md border border-border/60 bg-background/60 px-1.5 py-0.5 text-[11px]">
        {templates.map(t => (
          <option key={t.id} value={t.id}>{t.kind === "custom" ? "自建 · " : "内置 · "}{t.title}</option>
        ))}
        {templates.length === 0 && <option value="">（没有可用模板）</option>}
      </select>
      {current?.kind === "custom" && (
        <span className="text-[10px] text-muted-foreground/70">用的是自建模板「{current.title}」</span>
      )}
    </span>
  );
}
