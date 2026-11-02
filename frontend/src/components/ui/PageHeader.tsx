import { type ReactNode } from "react";

interface Props {
  title: string;
  level?: 1 | 2;
  subtitle?: string;
  actions?: ReactNode;
}

export function PageHeader({ title, subtitle, actions, level = 1 }: Props) {
  const Heading = level === 2 ? "h2" : "h1";
  return (
    <div className="mb-6 flex flex-wrap items-start justify-between gap-x-3 gap-y-2">
      {/* `min-w-0 flex-1`：原来标题列是按内容宽的，副标题一长就把 actions 挤到第二行、
          看着像单独占了一行（用户 2026-10-07 对首页「自动刷新」那一格的意见）。
          现在标题列吃满剩余宽度并允许换行，actions 固定在右上角。 */}
      <div className="min-w-0 flex-1 space-y-1">
        <Heading className="text-2xl font-extrabold tracking-tight text-glow">{title}</Heading>
        {subtitle && <p className="mt-1 text-sm text-muted-foreground">{subtitle}</p>}
      </div>
      {/* `shrink-0` 让右上角那一坨不被标题挤掉，但它同时把这一格的宽度钉成"内容一行排开的宽度"：
          控件多 + 那颗「这块的说明」之后，实测 `/stock-review` 在 568px 内容区里被顶到 621px
          （多出 53px 的**页面级**横向滚动条，`/volume-double` 更多，925px）。
          加 `max-w-full` 让它最多占满这一列，里面那一行本来就是 `flex-wrap`，于是自动折行、页面不再长条。 */}
      {actions && <div className="flex max-w-full shrink-0 flex-wrap items-center gap-2">{actions}</div>}
    </div>
  );
}
