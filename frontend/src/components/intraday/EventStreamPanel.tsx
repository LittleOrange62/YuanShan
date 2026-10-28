import { RefreshCw, Waves } from "lucide-react";
import { Caliber } from "@/components/ui/Caliber";
import { ExplainBox, QuietFold } from "@/components/ui/DataPanel";
import { GlassCard } from "@/components/ui/GlassCard";
import { SortHeader } from "@/components/ui/SortHeader";
import { StockLink } from "@/components/ui/StockLink";
import { plain } from "@/lib/agent";
import { pctColor } from "@/lib/colors";
import { dash, describeState, NULL_TEXT } from "@/lib/datastate";
import { useNonce, useProbe } from "@/lib/probe";
import { useTableSort } from "@/lib/useSort";
import { cn } from "@/lib/utils";

/**
 * 盘中涨跌停**事件流**（东财涨跌停池每 5 分钟一针的逐条事件差），挂在
 * 「日间复盘及首板」（`pages/FirstBoard.tsx`）的**上半部分**。
 *
 * 数据只读 `GET /api/intraday/timeline`（`useProbe` 自己补 `/api`、自己剥 `{data}` 信封，
 * 所以这一份源码里一个 `/api` 都不写）。判据、事件差的中文标签、首封那两把尺的说明
 * 全部出自后端（写侧 `app/intraday/pool_events.py`、读侧 `duanxian/intraday_view.py`），
 * 这里只搬运只画，一个判据都不重算。
 *
 * ⚠️ 用户 2026-10-26 裁定（写在这里防止被下一个会话当缺陷改回去）：
 *   **这一屏允许逐只列出个股代码与名称。** `vr/astock.py:511` 那条「原始池含 code/name，
 *   切勿直接接成 API/UI（零标的红线）」在本屏**让一步** —— 数据出自库里的事件行、
 *   经 `/api/intraday/timeline` 这一份读侧出口，页面不直接碰 `vr/astock.py` 的原始池。
 *
 * 三条界面上不许糊掉的线：
 *  · `available:false` 时后端**不交 rows**，只交 reason —— 原样念那句，不摆一张空表
 *    冒充"今天没有涨跌停"（那是两件不同的事）；
 *  · 缺的那一格显示 `—`，不写"缺数"：跌停池**结构上没有首封那一列**，那一格永远是 null，
 *    这是常态不是坏了（为什么是常态：句子读接口交的 `first_seal_basis`，页面不自编）；
 *  · 中文类别一律用行里的 `kind_label` 与 `kind_labels`（后端 `KIND_LABELS` 那一份），
 *    这里不抄第二份标签表 —— 抄了就会有朝一日两边不同名。
 */

/** 一针（一次 5 分钟取池）的摘要：家数是那一针的整池数，事件是那一针产出的差条数。 */
interface Round {
  captured_at: string;
  zt_n: number | null;
  dt_n: number | null;
  events: number | null;
  is_baseline: boolean;
}

/** 一条事件差。`missing` 是后端点名的"这一行为什么这一格是 —"（键＝列名）。 */
interface EventRow {
  captured_at: string;
  kind: string;
  kind_label: string | null;
  side: string;
  security_code: string;
  security_name: string | null;
  pct: number | null;
  price: number | null;
  board_count: number | null;
  open_count: number | null;
  first_seal_time: string | null;
  seal_fund_yuan: number | null;
  industry: string | null;
  missing: Record<string, string> | null;
}

interface Timeline {
  available: boolean;
  reason?: string | null;
  trade_date?: string | null;
  spec_version?: string | null;
  rounds?: Round[] | null;
  rows?: EventRow[] | null;
  truncated?: boolean | null;
  kind_labels?: Record<string, string> | null;
  first_seal_basis?: string | null;
}

/** ISO 时刻只裁到秒（微秒原样切掉，不做四舍五入 —— 屏幕说 09:30:03 就是那一条的 09:30:03）。 */
function hhmmss(iso: string | null | undefined): string {
  if (!iso) return NULL_TEXT;
  const t = iso.includes("T") ? iso.slice(iso.indexOf("T") + 1) : iso;
  return t.slice(0, 8);
}

/** 封单额：后端交的是**元**，这里只换读法（≥1 亿说亿，否则说万）。null 就是 —。 */
function fundText(v: number | null | undefined): string {
  if (v == null || !Number.isFinite(v)) return NULL_TEXT;
  if (Math.abs(v) >= 1e8) return `${(v / 1e8).toFixed(2)} 亿`;
  return `${(v / 1e4).toFixed(1)} 万`;
}

const intText = (v: number | null | undefined): string =>
  v == null || !Number.isFinite(v) ? NULL_TEXT : String(Math.round(v));

export function EventStreamPanel() {
  const [nonce, refresh] = useNonce();
  const st = useProbe<Timeline>("/intraday/timeline", nonce, { limit: 400 });
  const d = st.kind === "ok" && st.data.available ? st.data : null;
  const rows = (d?.rows ?? []) as EventRow[];
  const rounds = (d?.rounds ?? []) as Round[];
  const last = rounds[rounds.length - 1] ?? null;
  const baselineN = rounds.filter(r => r.is_baseline).length;

  // 默认行序＝接口那份（captured_at DESC, kind）。列头亮在同一把尺上，标题与列头说的是同一件事。
  const srt = useTableSort({ key: "captured_at", dir: "desc" });
  const basis = plain(d?.first_seal_basis
    ?? "首封那两把尺的说明后端没交（first_seal_basis 是空的）—— 这一屏说不出「—」该怎么读。");

  const stat = (label: string, value: string, title: string, tone = "") => (
    <div key={label} title={title} className="rounded-lg border border-border/60 bg-muted/20 px-2 py-1.5">
      <div className="text-[10px] text-muted-foreground">{label}</div>
      <div className={cn("mt-0.5 font-mono text-sm font-bold", tone || "text-primary")}>{value}</div>
    </div>
  );

  return (
    <GlassCard className="mb-4">
      <div className="mb-1.5 flex flex-wrap items-center gap-2">
        <h3 className="flex items-center gap-1.5 text-sm font-semibold">
          <Waves className="h-4 w-4 text-primary" /> 盘中涨跌停事件流 · {dash(d?.trade_date)}
        </h3>
        <Caliber text={
          "母体：东财涨停池／跌停池每 5 分钟一针，每一针只播**与上一针的事件差**"
          + "（新封／回封／开板又回封／炸板／跌停那一轨四条），落库在 analytics.intraday_pool_event。\n"
          + "判据与中文类别名只住后端（app/intraday/pool_events.py 的 KIND_LABELS），页面一个都不重算、不自己抄。\n"
          + `基线针（首轮／换日重建那一针）：接口这场标了 ${intText(baselineN)} 针 —— 基线只报整池摘要，不逐条播。\n`
          + `判据版本 ${dash(d?.spec_version)}：改版后旧行留着对账，所以取数一定带这一条。`
        } />
        <button type="button" onClick={refresh} title="重新取数（发一次真的 GET /intraday/timeline）"
          className="ml-auto flex items-center gap-1 rounded-lg bg-primary/15 px-2 py-1 text-[11px] font-medium text-primary hover:bg-primary/25">
          <RefreshCw className="h-3.5 w-3.5" /> 重新取数
        </button>
      </div>

      {st.kind === "loading" && (
        <p className="text-[11px] text-muted-foreground">取数中…（读库里那一场的盘中事件行，不现抓网络、不现算判据）</p>
      )}
      {st.kind !== "ok" && st.kind !== "loading" && (
        <p className="rounded-lg border border-warning/50 bg-warning/10 p-2 text-[11px] leading-relaxed text-warning">
          {describeState(st)}
        </p>
      )}
      {/* available:false：后端只交 reason、不交 rows。原样念那句，不摆空表冒充"今天没有涨跌停"。 */}
      {st.kind === "ok" && !st.data.available && (
        <QuietFold text={plain(st.data.reason ?? "盘中事件流读不到数，后端也没交原因。")} />
      )}

      {d && (
        <>
          <div className="mb-2 grid grid-cols-2 gap-2 md:grid-cols-3 xl:grid-cols-5">
            {stat("交易日", dash(d.trade_date), "这一场由接口交（date 留空＝库里最近有事件行的那一场），页面不吃本机时钟")}
            {stat("共几针", `${intText(rounds.length)} 针`, "一针＝一次 5 分钟取池；这一格是接口给的针数（rounds 条数）")}
            {stat("最近一针", hhmmss(last?.captured_at), last ? `那一针的取池时刻：${dash(last.captured_at)}` : "接口没交任何一针")}
            {stat("最近一针涨停家数", `${intText(last?.zt_n)} 家`, "整池家数（那一针的涨停池行数），不是事件条数", "text-danger")}
            {stat("最近一针跌停家数", `${intText(last?.dt_n)} 家`, "整池家数（那一针的跌停池行数），不是事件条数", "text-success")}
          </div>

          <p className="mb-1.5 text-[11px] leading-relaxed text-muted-foreground">
            {plain(`这一场接口交出 ${intText(rows.length)} 行事件（取数上限 400 行`
              + `${d.truncated ? "，已经截断 —— 下面这张表不是全场的事件，最近的针排在最前" : "，没截断"}`
              + `）· 每针的事件条数合计 ${intText(rounds.reduce((a, r) => a + (r.events ?? 0), 0))} 条。`)}
          </p>
          <p className="mb-2 text-[10px] leading-relaxed text-muted-foreground">
            {`类别中文（接口 kind_labels 原样列出）：${Object.values(d.kind_labels ?? {}).join("、") || NULL_TEXT}。`}
          </p>
          <p className="mb-2 text-[10px] leading-relaxed text-muted-foreground">
            {basis}
          </p>

          <div className="max-h-[420px] overflow-auto">
            <table className="w-full text-[11px]">
              <thead className="sticky top-0 bg-background/95">
                <tr className="border-b border-border/50 text-left text-[10px] text-muted-foreground">
                  <SortHeader label="时间" sortKey="captured_at" dir={srt.dirOf("captured_at")} onSort={srt.toggle}
                    title="这一针的取池时刻（默认最新在前）；同分钟内多行按后端原序" />
                  <SortHeader label="类别" sortKey="kind_label" dir={srt.dirOf("kind_label")} onSort={srt.toggle}
                    title="中文标签出自接口那一份 KIND_LABELS，页面没抄第二份；按中文串排" />
                  <SortHeader label="股票" sortKey="security_name" dir={srt.dirOf("security_name")} onSort={srt.toggle}
                    title="名称 + 代码；点名称/代码在新标签开东财行情页（用户 2026-10-26 裁定：这一屏允许逐只列出个股代码与名称）" />
                  <SortHeader label="板数" sortKey="board_count" dir={srt.dirOf("board_count")} onSort={srt.toggle}
                    title="池行 lbc；缺就是 —（不拿 1 顶上：1 是合法读数，混起来就分不出「真的首板」与「没给字段」）" />
                  <SortHeader label="涨幅%" sortKey="pct" dir={srt.dirOf("pct")} onSort={srt.toggle}
                    title="池行 zdp，已是百分数，原样存" />
                  <SortHeader label="首封" sortKey="first_seal_time" dir={srt.dirOf("first_seal_time")} onSort={srt.toggle}
                    title={basis} />
                  <SortHeader label="封单" sortKey="seal_fund_yuan" dir={srt.dirOf("seal_fund_yuan")} onSort={srt.toggle}
                    title="池行 fund（元）；这一格只换读法：≥1 亿说亿，否则说万" />
                  <SortHeader label="行业" sortKey="industry" dir={srt.dirOf("industry")} onSort={srt.toggle}
                    title="东财那一列的原样值（常被截到四字，不是显示不全）；与题材不是一套" />
                </tr>
              </thead>
              <tbody>
                {srt.apply(rows).map((r, i) => (
                  <tr key={`${r.captured_at}-${r.kind}-${r.security_code}-${i}`}
                    className="border-b border-border/30">
                    <td className="whitespace-nowrap px-2 py-1 font-mono text-muted-foreground">
                      {hhmmss(r.captured_at)}
                    </td>
                    <td className={cn("whitespace-nowrap px-2 py-1",
                      r.side === "dt" ? "text-success" : "text-danger")}>
                      {dash(r.kind_label)}
                    </td>
                    <td className="whitespace-nowrap px-2 py-1">
                      <StockLink code={r.security_code} name={r.security_name}
                        className="font-medium hover:text-primary" />{" "}
                      <span className="font-mono text-[10px] text-muted-foreground/60">{dash(r.security_code)}</span>
                    </td>
                    <td className="whitespace-nowrap px-2 py-1 font-mono"
                      title={r.missing?.board_count
                        ? `这一格是 —：${r.missing.board_count}（不是 1 板，也不是一字板）`
                        : "池行 lbc 原值"}>
                      {r.board_count == null ? NULL_TEXT : `${intText(r.board_count)} 板`}
                    </td>
                    <td className={cn("whitespace-nowrap px-2 py-1 font-mono tabular-nums", pctColor(r.pct))}>
                      {r.pct == null ? NULL_TEXT : `${r.pct > 0 ? "+" : ""}${r.pct.toFixed(2)}%`}
                    </td>
                    <td className="whitespace-nowrap px-2 py-1 font-mono" title={basis}>
                      {dash(r.first_seal_time)}
                    </td>
                    <td className="whitespace-nowrap px-2 py-1 font-mono tabular-nums"
                      title={r.missing?.seal_fund_yuan ? `这一格是 —：${r.missing.seal_fund_yuan}` : "池行 fund（元）"}>
                      {fundText(r.seal_fund_yuan)}
                    </td>
                    <td className="whitespace-nowrap px-2 py-1 text-muted-foreground"
                      title={r.missing?.industry ? `这一格是 —：${r.missing.industry}` : undefined}>
                      {dash(r.industry)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <ExplainBox title="这一块怎么点">
            <p>{plain("点列头排序：点一下排（默认先降序）、再点翻向、第三次取消；没有值的那一格（—）两个方向都恒排在最后。")}</p>
            <p>{plain("默认行序是接口给的那一份（最新一针在前、同一针按事件类别），列头上的 ↓ 亮在「时间」那一格。")}</p>
            <p>{plain("「—」就是没有这一格：跌停池结构上没有首封时间（永远 —，常态不是坏了）；板数缺是池行没给 lbc。悬停在格子上念接口为这一行点名的原因。")}</p>
            <p>{plain("点名称或代码在新标签开东财行情页，不换掉这一屏。这一屏不打分、不预测、不给买卖建议：念的是「已经封住／已经炸开」这个事实。")}</p>
          </ExplainBox>
        </>
      )}
    </GlassCard>
  );
}
