/**
 * 板块名 → 东方财富板块页的链接（全站一份实现）。
 *
 * 用户 2026-10-15 点名：「热度榜与五要素 里面的板块也要有超链接可以点开，
 * 比如『油气开采』对应 `https://quote.eastmoney.com/bk/90.BK1276.html`」。
 *
 * 三件事只在这里做一次，两块屏幕（`pages/SectorHeat.tsx` 的「热度榜与五要素」与
 * `components/SectorFlowCumulative.tsx` 的「覆盖以来累计净流入」）都只传数据过来：
 *  1. **地址怎么拼**：`https://quote.eastmoney.com/bk/90.<BK码>.html`。东财板块的市场号是 90，
 *     实测标题为证（`BK1276` → 页眉「油气开采Ⅱ(BK1276)_行情中心_东方财富网」）。
 *  2. **给不给链接**：只认后端交出的 `em_board_code`。码来自 `reference_data.em_board_link`
 *     （对表规矩只住 `ingest/jobs/c24_em_board_link.py`：同类才对表、精确同名或 Ⅱ/Ⅲ 后缀）。
 *     **没有码就退回纯文本** —— 猜一个地址出去等于把用户送到一个不存在的板块页。
 *  3. **为什么点不开要说出来**：对不上名 / 那一档（风格、市场级统计）东财根本没有对应板块族，
 *     是两件不同的事，鼠标悬停要能分别读出。
 *
 * 与个股外链（`lib/quoteLink.ts`）是两条路：个股由代码本身就能推出路径，板块不行 ——
 * 板块码是东财自己给的名字，只能查表。所以这里不套那套模板配置，也不接 `useOpenStockReview`
 * 那套「双击看个股复盘」的行交互（板块不是一只票）。
 */
import { cn } from "@/lib/utils";

const EM_BOARD_BASE = "https://quote.eastmoney.com/bk/";

/** 东财板块页地址。没有码就返回 null —— 调用方据此退回纯文本，不拼半截 URL。 */
export function emBoardUrl(code?: string | null): string | null {
  const c = String(code ?? "").trim();
  if (!c) return null;
  // 只接受东财那一族的样子：`BK1276`（大小写都收）或已经带了市场前缀的 `90.BK1276`
  const bare = c.includes(".") ? c.split(".")[1] : c;
  if (!/^BK\d{4}$/i.test(bare)) return null;
  return `${EM_BOARD_BASE}90.${bare.toUpperCase()}.html`;
}

/** 这一档东财根本没有对应的板块族（不是"没对上名"，别混着说）。 */
export const NO_FAMILY_TYPES = ["风格", "统计", "指数"];

export function SectorLink({ name, code, emName, sectorType, detail, className }: {
  name?: string | null;
  code?: string | null;
  emName?: string | null;
  sectorType?: string | null;
  /** 调用方那行自己的补充说明（板块码、当日分位那一类），拼在悬停文字最前面 */
  detail?: string;
  className?: string;
}) {
  const label = name ? String(name) : "—";
  const url = emBoardUrl(code);
  const head = detail ? `${detail}\n` : "";

  if (!url) {
    const why = NO_FAMILY_TYPES.includes(String(sectorType ?? ""))
      ? `通达信「${sectorType}」这一档在东财没有对应的板块族，所以点不开 —— 不是没对上名，是根本没有这一族`
      : "在东财没对上同名板块（只认同类精确同名，其次差一个级别后缀 Ⅱ/Ⅲ），所以点不开 —— 猜一个地址等于送你去死页";
    return (
      <span className={cn(className)} title={`${head}${label}：${why}`}>{label}</span>
    );
  }

  // 差一个级别后缀时（屏上「油气开采」/东财「油气开采Ⅱ」），把点开后页眉那个名字先说给人看
  const renamed = emName && emName !== label
    ? `点开后页眉写的是东财那一侧的名字「${emName}」（级别后缀差一个字，说的是同一族）`
    : "点开后是东财同名板块页";
  return (
    <a href={url} target="_blank" rel="noopener noreferrer"
       className={cn("text-primary underline decoration-primary/40 underline-offset-2 hover:decoration-primary", className)}
       title={`${head}${label} → 东方财富板块页 ${code}。${renamed}。新标签打开，本页读数不动`}>
      {label}
      <span className="ml-0.5 text-[9px] opacity-70">↗</span>
    </a>
  );
}

/** 屏上那一句口径（两处共用一份措辞，别各抄一遍再各漂一次）。 */
export const SECTOR_LINK_CALIBER =
  "板块名能不能点开只看一张对表：`reference_data.em_board_link`（每日加工段 boardlink 步刷新）。"
  + "同类才对表 —— 我方行业只查东财行业榜、我方概念只查东财概念榜；"
  + "只认精确同名，其次差一个级别后缀（东财把二级行业写成「油气开采Ⅱ」）。"
  + "对不上的、以及风格 / 市场级统计这两档（东财没有对应族）一律纯文本，不猜地址。";
