/**
 * 珠峰简笔标：一座主峰 + 两侧卫峰， summit 上带雪线。
 *
 * 纯 stroke、用 `currentColor`，所以深色/浅色主题与 `text-primary` 都跟着走；
 * 侧栏 32px 与 favicon 16px 都试过不糊（线条 ≥2/64、留白靠外沿）。
 * 与 `frontend/public/favicon.svg` 是同一个几何，改一处要改两处。
 */
export function EverestLogo({ className }: { className?: string }) {
  return (
    <svg className={className} viewBox="0 0 64 64" role="img" aria-label="珠峰"
         fill="none" stroke="currentColor" strokeWidth="3"
         strokeLinecap="round" strokeLinejoin="round">
      {/* 山体：左卫峰 → 主峰（珠峰）→ 右卫峰 */}
      <path d="M4 53 L20 32 L26 39 L36 12 L47 31 L53 24 L60 53 Z" />
      {/* 雪线：主峰顶下的两道锯齿 */}
      <path d="M30.5 24 L33.5 27 L36 24.5 L38.5 27.5 L41.5 24" />
      {/* 北脊一道短促的岩痕，避免主峰看着像普通三角形 */}
      <path d="M36 12 L34.5 20" />
    </svg>
  );
}
