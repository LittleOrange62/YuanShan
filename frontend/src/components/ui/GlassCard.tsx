import { type ReactNode } from "react";
import { cn } from "@/lib/utils";
import { BlockTitleCtx } from "@/components/ui/DocDrawer";

interface Props {
  children: ReactNode;
  className?: string;
  glow?: boolean;
  onClick?: () => void;
  /**
   * 这一块在屏幕上的标题（首页那几块不走 `Panel`，标题是自己写的 h3）。
   * 交进来后卡里的说明按钮开抽屉时就念它 —— 抽屉标题与屏上标题分叉，
   * 读者就不知道弹窗里说的是哪一块。不交则维持原样（不套 Provider）。
   */
  docTitle?: string;
}

// 玻璃卡：半透明填充 + 发丝边框 + 柔投影 + 顶部内高光（科技玻璃暖橙风的基础容器）。
export function GlassCard({ children, className, glow, onClick, docTitle }: Props) {
  const card = (
    <div
      onClick={onClick}
      className={cn(
        "glass p-5",
        glow && "glass-glow",
        onClick && "cursor-pointer transition-transform hover:-translate-y-0.5",
        className,
      )}
    >
      {children}
    </div>
  );
  return docTitle ? <BlockTitleCtx.Provider value={docTitle}>{card}</BlockTitleCtx.Provider> : card;
}
