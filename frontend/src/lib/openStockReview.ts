import { useHref } from "react-router-dom";

/**
 * 「双击一行 → 另开一个浏览器窗口看这一只票的个股深度复盘」这个动作，分组自选的明细、
 * 重点关注的持仓、本机名单三处都要有，所以实现只留这一份（用户 2026-10-15 点名把
 * 「个股点击分析」改成双击开新页，并要求两边的功能统一）。
 *
 * 三条约定：
 *  · 地址里的 `symbol` 是深度复盘页唯一的"现在看哪一只"，`from` 只用来在那一屏说清
 *    这一只是从哪一叠双击带过来的（不做判据）。
 *  · 用 `window.open` 而不是 `<Link>`：要的是**新窗口**，原来那一叠明细/持仓留在原地对照着看。
 *  · 返回一句要念到屏幕上的告警（空串＝成功）。弹窗被浏览器拦掉时 `window.open` 返回 null，
 *    那时必须把地址原样念出来给人工兜底 —— 判据/探测函数在坏的时候不许装没事。
 *    告警由调用方渲染，**且不许折进默认收起的说明框**（收着就等于没说）。
 */
export function useOpenStockReview(): (code: string | null | undefined, from: string) => string {
  // basename 由 router 给（构建时带 `--base`）；自己拼 "/stock-review" 在子路径部署下会 404。
  const base = useHref("/stock-review");
  return (code, from) => {
    const c = String(code ?? "").trim();
    if (!c) return "这一行没有代码，开不了复盘页。";
    const url = `${base}?symbol=${encodeURIComponent(c)}&from=${encodeURIComponent(from)}`;
    const win = window.open(url, "_blank", "noopener");
    return win ? "" : `浏览器拦下了新窗口。请允许本站弹出窗口，或自己开这一条：${url}`;
  };
}
