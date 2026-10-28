import { Filter } from "lucide-react";
import { cn } from "@/lib/utils";

interface FilterOption {
  value: string;
  label: string;
  /** 这一档**全场**实有几只。`undefined` = 后端没交；`null` = 读了但没读到（不是 0） */
  count?: number | null;
  /** 这一档是什么（后端那份口径，页面不自己抄） */
  meaning?: string | null;
}

interface FilterSection {
  label: string;
  type: "select" | "range" | "boolean" | "number-min" | "multi";
  key: string;
  options?: FilterOption[]; // for select / multi
  placeholder?: string;
  step?: number; // for range/number
  min?: number;
  max?: number;
  /** multi 那一格下面的一句实话：筛选发生在哪一侧、范围是什么 */
  note?: string;
}

interface TableFilterPanelProps {
  show: boolean;
  filters: Record<string, unknown>;
  sections: FilterSection[];
  onChange: (key: string, value: unknown) => void;
  onClear: () => void;
  className?: string;
}

/**
 * 通用表格筛选面板。
 *
 * 用法：
 * ```tsx
 * <TableFilterPanel
 *   show={showFilters}
 *   filters={filters}
 *   sections={[
 *     { label: "板块", type: "select", key: "board", options: [...] },
 *     { label: "倍量次", type: "range", key: "dbl_count", min: 0 },
 *   ]}
 *   onChange={(key, value) => setFilters(f => ({ ...f, [key]: value }))}
 *   onClear={() => setFilters({})}
 * />
 * ```
 */
export function TableFilterPanel({
  show,
  filters,
  sections,
  onChange,
  onClear,
  className,
}: TableFilterPanelProps) {
  if (!show) return null;

  // 空数组算"没筛"：多选框全不勾就是不限，不该点亮那个计数徽章
  const activeCount = Object.values(filters)
    .filter(v => v !== undefined && v !== "" && (!Array.isArray(v) || v.length > 0)).length;

  return (
    <div className={cn("mt-2 rounded-lg border border-border/60 bg-muted/20 p-3 grid gap-2 sm:grid-cols-2 lg:grid-cols-3", className)}>
      {sections.map(section => {
        const value = filters[section.key];

        if (section.type === "select") {
          return (
            <div key={section.key} className="flex items-center gap-2">
              <label className="text-[11px] text-muted-foreground whitespace-nowrap">{section.label}</label>
              <select
                value={(value as string) ?? ""}
                onChange={e => onChange(section.key, e.target.value || undefined)}
                className="flex-1 rounded border border-border/60 bg-background px-1 py-0.5 text-[11px]"
              >
                <option value="">全部</option>
                {section.options?.map(opt => (
                  <option key={opt.value} value={opt.value}>{opt.label}</option>
                ))}
              </select>
            </div>
          );
        }

        if (section.type === "multi") {
          const picked = Array.isArray(value) ? (value as string[]) : [];
          return (
            <div key={section.key} className="sm:col-span-2 lg:col-span-3">
              <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
                <span className="text-[11px] text-muted-foreground whitespace-nowrap">{section.label}</span>
                {(section.options ?? []).map(opt => (
                  <label key={opt.value} className="flex items-center gap-1 text-[11px]"
                    title={[opt.meaning,
                            opt.count === undefined ? "" :
                              opt.count === null ? "这一档全场实有几只：没读到"
                                : `这一档全场 ${opt.count} 只`].filter(Boolean).join("｜")}>
                    <input type="checkbox" className="h-3.5 w-3.5 accent-primary"
                      checked={picked.includes(opt.value)}
                      onChange={() => onChange(section.key,
                        picked.includes(opt.value) ? picked.filter(v => v !== opt.value)
                          : [...picked, opt.value])}
                      aria-label={`${section.label} 选 ${opt.label}`} />
                    {/* 只数来自后端那份**全场**聚合，不是"已交出的这些行里有几只" */}
                    {opt.label}
                    {opt.count !== undefined && (
                      <span className="tabular-nums text-muted-foreground">
                        {opt.count === null ? "—" : opt.count}
                      </span>
                    )}
                  </label>
                ))}
                {picked.length > 0 && (
                  <button type="button" className="text-[11px] text-muted-foreground hover:text-foreground"
                    onClick={() => onChange(section.key, [])}>
                    不限
                  </button>
                )}
              </div>
              {section.note && (
                <p className="mt-1 text-[11px] leading-4 text-muted-foreground">{section.note}</p>
              )}
            </div>
          );
        }

        if (section.type === "boolean") {
          return (
            <div key={section.key} className="flex items-center gap-2">
              <label className="text-[11px] text-muted-foreground whitespace-nowrap">{section.label}</label>
              <select
                value={value === undefined ? "" : String(value)}
                onChange={e => onChange(section.key, e.target.value === "" ? undefined : e.target.value === "true")}
                className="flex-1 rounded border border-border/60 bg-background px-1 py-0.5 text-[11px]"
              >
                <option value="">全部</option>
                <option value="true">是</option>
                <option value="false">否</option>
              </select>
            </div>
          );
        }

        if (section.type === "range") {
          const minKey = `${section.key}_min`;
          const maxKey = `${section.key}_max`;
          return (
            <div key={section.key} className="flex items-center gap-2">
              <label className="text-[11px] text-muted-foreground whitespace-nowrap">{section.label}</label>
              <input
                type="number"
                step={section.step ?? 1}
                min={section.min}
                max={section.max}
                placeholder="≥"
                value={(filters[minKey] as number) ?? ""}
                onChange={e => onChange(minKey, e.target.value ? parseFloat(e.target.value) : undefined)}
                className="w-16 rounded border border-border/60 bg-background px-1 py-0.5 text-[11px]"
              />
              <span className="text-[11px] text-muted-foreground">~</span>
              <input
                type="number"
                step={section.step ?? 1}
                min={section.min}
                max={section.max}
                placeholder="≤"
                value={(filters[maxKey] as number) ?? ""}
                onChange={e => onChange(maxKey, e.target.value ? parseFloat(e.target.value) : undefined)}
                className="w-16 rounded border border-border/60 bg-background px-1 py-0.5 text-[11px]"
              />
            </div>
          );
        }

        if (section.type === "number-min") {
          return (
            <div key={section.key} className="flex items-center gap-2">
              <label className="text-[11px] text-muted-foreground whitespace-nowrap">{section.label}≥</label>
              <input
                type="number"
                step={section.step ?? 1}
                min={section.min}
                placeholder="最小"
                value={(value as number) ?? ""}
                onChange={e => onChange(section.key, e.target.value ? parseInt(e.target.value) : undefined)}
                className="w-20 rounded border border-border/60 bg-background px-1 py-0.5 text-[11px]"
              />
            </div>
          );
        }

        return null;
      })}

      {activeCount > 0 && (
        <div className="col-span-full flex justify-end mt-1">
          <button
            type="button"
            onClick={onClear}
            className="text-[11px] text-muted-foreground hover:text-foreground"
          >
            清除筛选（{activeCount} 个条件）
          </button>
        </div>
      )}
    </div>
  );
}

/**
 * 筛选按钮组件（带激活计数徽章）。
 */
export function FilterToggleButton({
  show,
  activeCount,
  onClick,
}: {
  show: boolean;
  activeCount: number;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        "flex items-center gap-1 rounded-lg px-2 py-1 text-[11px] font-medium",
        show ? "bg-primary/20 text-primary" : "bg-muted/40 text-muted-foreground hover:bg-muted/60"
      )}
    >
      <Filter className="h-3.5 w-3.5" /> 列筛选
      {activeCount > 0 && (
        <span className="ml-1 rounded bg-primary/30 px-1 text-[10px]">{activeCount}</span>
      )}
    </button>
  );
}
