import { useEffect, useState } from "react";
import { Layers, Loader2 } from "lucide-react";
import { api, type FirstBoardDeepData } from "@/lib/api";
import { DocDrawer, DrawerSection } from "@/components/ui/DocDrawer";
import { GlassCard } from "@/components/ui/GlassCard";
import { NoticeBar } from "@/components/ui/NoticeBar";
import { SortHeader } from "@/components/ui/SortHeader";
import { StockLink } from "@/components/ui/StockLink";
import { PAGE_SIZES, canStep, clampPage, pageCount, pageSentence, slicePage } from "@/lib/paging";
import { STOCK_ROW_HINT, useStockRowInteraction } from "@/lib/useStockRowInteraction";
import { pctColor } from "@/lib/colors";
import { finite, plain } from "@/lib/agent";
import { useTableSort } from "@/lib/useSort";
import { cn } from "@/lib/utils";

/**
 * 首板四层过滤深析（用户 2026-10-29 给了一套四层方法论，点名「把具体功能做在短线复盘最下方」）。
 *
 * 三条不糊过去的取舍（全部有实测依据，见 `duanxian/first_board_deep.py` 的模块文档）：
 *  · **不给合成总分**：四层的量纲互不相干（分钟／倍／%／只），加权出来的数没有读法；
 *    所以只摆每层的读数与"这一层本机算高不低"，命中几条另说；
 *  · **外部阈值与本机分位并排摆**：资料给的「封单比 >1%/>3%」「换手 10~20%」「板块 ≥2-3 只同步涨停」
 *    在本机分别落在 p70/p95、p75+/p99+、99.8% 全覆盖 —— 照抄上屏就是拿别人的经验数当本机判据，
 *    所以那一列的口径文字必须同时给出这两个数，让读者看见差多远；
 *  · **源池那一族只有约 28% 有行**（北证与部分 ST 池子不给）⇒ 首封／炸板／在板分钟这几列
 *    不参与任何排序默认与提示，覆盖率单独摆在屏上；按它们筛等于按"源站采没采"筛。
 */
const fmt = (v: number) => v.toLocaleString("zh-CN", { maximumFractionDigits: 2 });

const pct = (v: number | null | undefined, digits = 2) =>
  typeof v === "number" ? `${v > 0 ? "+" : ""}${v.toFixed(digits)}%` : "—";
const mult = (v: number | null | undefined) => (typeof v === "number" ? `${fmt(v)} 倍` : "—");
const count = (v: number | null | undefined, unit = "只") =>
  typeof v === "number" ? `${v.toFixed(0)} ${unit}` : "—";

/** 场次卡里那一格：当前值 + 它在近 20 场里的位置（不给"强/弱"的判词，那是读的人下的）。 */
function EnvCell({ label, v, unit }: {
  label: string; unit: string;
  v?: { current?: number | null; median?: number | null; percentile_in_20?: number | null;
        sessions?: number | null } | null;
}) {
  if (!v) return null;
  return (
    <div className="rounded-lg border border-border/60 bg-muted/20 px-2.5 py-1.5">
      <p className="text-[11px] text-muted-foreground">{label}</p>
      <p className="text-sm font-semibold tabular-nums">
        {typeof v.current === "number" ? `${fmt(v.current)}${unit}` : "—"}
      </p>
      <p className="text-[10px] leading-snug text-muted-foreground">
        近 20 场中位 {typeof v.median === "number" ? `${fmt(v.median)}${unit}` : "—"}
        {typeof v.percentile_in_20 === "number" ? `｜本场排在近 ${v.sessions ?? 20} 场的第 ${v.percentile_in_20} 分位` : ""}
      </p>
    </div>
  );
}

export function FirstBoardDeep({ day }: { day: string | null }) {
  const [data, setData] = useState<FirstBoardDeepData | null>(null);
  const [loaded, setLoaded] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [size, setSize] = useState(25);
  const [page, setPage] = useState(1);
  const sort = useTableSort({ key: "concept_lift", dir: "desc" });
  const row = useStockRowInteraction("first-board-deep");

  useEffect(() => {
    setLoaded(false);
    api.firstBoardDeep(day ?? undefined).then(d => { setData(d); setError(null); })
      .catch((e: unknown) => setError(e instanceof Error ? e.message : "首板四层深析读取失败，请刷新重试"))
      .finally(() => setLoaded(true));
  }, [day]);

  const rows = data?.rows ?? [];
  const bounds = data?.bounds ?? [];
  /** 列头与它的口径说明是同一条描述符：以后端 `bounds[].label` 为准，页面这个串只在后端没交时兜底。 */
  const bt = (key: string, fallback: string) =>
    bounds.find(x => x.key === key)?.label ?? fallback;
  const btitle = (key: string) => bounds.find(x => x.key === key)?.local ?? "";
  const cov = (data?.coverage ?? {}) as Record<string, { have?: number; total?: number; pct?: number }>;
  const srcMissing = finite(cov?.first_seal?.pct);
  const shown = sort.apply(rows);
  const pages = pageCount(shown.length, size);
  const cur = clampPage(page, pages);
  const view = slicePage(shown, cur, size);
  const ses = data?.session;

  if (error) return <GlassCard><p role="alert" className="text-sm text-danger">首板四层深析读取失败：{error}</p></GlassCard>;
  if (!loaded && !data) {
    return <GlassCard><p className="flex items-center gap-2 text-sm text-muted-foreground">
      <Loader2 className="h-4 w-4 animate-spin" /> 首板四层深析读取中…（约 5 秒：它要现算概念近 20 场的常态）</p></GlassCard>;
  }
  if (data && !data.available) {
    return <GlassCard docTitle="首板四层过滤"><h3 className="text-sm font-semibold">首板四层过滤</h3>
      <NoticeBar kind="missing">{data.reason ?? "接口没交原因（available:false 而 reason 为空）"}</NoticeBar></GlassCard>;
  }

  return (
    <GlassCard docTitle="首板四层过滤 · 逐只点名（题材／盘口／资金 ＋ 场次环境）">
      {/* 说明按钮在这一行最右边（用户 10-31）：这一块唯一的说明出口就是这一颗 ——
          原先标题行挂着一颗「口径」悬停芯片、正文里又卧着一只 ExplainBox，同屏两处说明都不像"这一块的说明"。
          现在母体那句与四层读法、外部阈值对照、做不出来的那几族全部搬进同一口抽屉；
          缺数声明（NoticeBar）、覆盖率计数与场次卡那几句误读提醒仍然留在屏上。 */}
      <div className="mb-1 flex flex-wrap items-center gap-1.5">
        <h3 className="flex items-center gap-1.5 text-sm font-semibold">
          <Layers className="h-4 w-4 text-primary" aria-hidden />
          首板四层过滤 · {data?.date ?? "—"}
        </h3>
        <span className="ml-auto"><DocDrawer title="首板四层过滤">
          <p className="whitespace-pre-line leading-relaxed">{plain(
            data?.definitions?.parent ?? "母体文字后端没交（definitions.parent 为空），页面不自己复述规则。")}</p>
          <DrawerSection heading="这四层各自怎么读 · 外部阈值与本机分位差多远">
            <p className="mb-1.5 text-[11px] leading-relaxed text-muted-foreground">
              {data?.definitions?.no_score}
            </p>
            <ul className="space-y-1.5">
              {bounds.map(b => (
                <li key={b.key} className="text-[11px] leading-relaxed">
                  <b className="text-foreground">{b.layer} · {b.label}</b>
                  <span className="text-muted-foreground"> ｜资料说：{b.external} ｜本机：{b.local}</span>
                </li>
              ))}
            </ul>
            <p className="mt-1.5 text-[11px] leading-relaxed text-muted-foreground">{data?.definitions?.missing}</p>
            <p className="mt-1 text-[11px] leading-relaxed text-muted-foreground">{data?.definitions?.concept}</p>
            <p className="mt-1.5 text-[11px] font-medium text-foreground">本机拼不出来、因此这一屏没有做的那几族：</p>
            <ul className="space-y-1">
              {(data?.cannot ?? []).map(c => (
                <li key={c.what} className="text-[11px] leading-relaxed">
                  <b className="text-foreground">{c.what}</b>
                  <span className="text-muted-foreground"> —— {c.why}</span></li>
              ))}
            </ul>
          </DrawerSection>
        </DocDrawer></span>
      </div>

      {/* ④ 环境：全场同一件事，所以放在表外的场次卡里，不逐票重复一列 */}
      {ses && (
        <div className="mb-2">
          <p className="mb-1 text-[11px] text-muted-foreground">
            第④层 · 市场环境与情绪周期（全场一张卡，{ses.breadth_source ?? "来源未交"}）
          </p>
          <div className="flex flex-wrap gap-1.5">
            <EnvCell label="1 进 2 晋级率" unit="%" v={ses.promotion_rate} />
            <EnvCell label="炸板率" unit="%" v={ses.broken_rate} />
            <EnvCell label="最高板" unit=" 板" v={ses.highest_board} />
            <EnvCell label="上涨家数占比" unit="%" v={ses.up_ratio} />
            <EnvCell label="全市场中位数涨幅" unit="%" v={ses.median_pct} />
          </div>
          <p className="mt-1 text-[11px] leading-relaxed text-muted-foreground">
            {ses.premium?.text ?? "昨日涨停今日溢价：接口没交。"}
            {" "}当日涨停 {count(ses.limit_up_count)}／跌停 {count(ses.limit_down_count)}。
          </p>
          <p className="mt-0.5 text-[10px] leading-relaxed text-muted-foreground">
            这几个数是"本场在哪一段"的读数，不是"该不该打板"的判词 ——
            资料里那套「正向周期多打、逆向周期少做」是判断，留给看的人（本屏不给定性词）。
          </p>
        </div>
      )}

      {srcMissing !== null && srcMissing < 60 && (
        <NoticeBar kind="missing">
          源池那一族（首封时刻／炸板次数／在板分钟数）今天只有 {cov.first_seal?.have ?? 0}/
          {cov.first_seal?.total ?? rows.length} 只有行（{srcMissing}%）—— 北证与部分 ST 东财池子不交这些字段。
          <b className="text-foreground">这几列不参与默认排序、也不许拿来当筛子</b>：
          按它们筛等于按"源站采没采"筛，与"这只票强不强"是两回事。
        </NoticeBar>
      )}

      {rows.length > 0 && (
        <div className="overflow-x-auto">
          <table className="w-full text-[12px]" data-row-interaction>
            <caption className="sr-only">首板四层过滤读数表：单击一格同时高亮整行与整列，双击另开一只票的深度复盘</caption>
            <thead>
              <tr className="border-b border-border/60 text-muted-foreground">
                <th className="py-1 pr-2 text-left">代码</th>
                <th className="py-1 pr-2 text-left">名称</th>
                <th className="py-1 pr-2 text-right">涨幅</th>
                <SortHeader label={bt("theme_lift", "概念热度抬升")} sortKey="concept_lift" dir={sort.dirOf("concept_lift")}
                  onSort={sort.toggle}
                  title={btitle("concept_lift")}
                  className="whitespace-nowrap py-1 pr-2 text-right font-normal" />
                <th className="py-1 pr-2 text-left">最强概念（当日涨停只数／隔几场）</th>
                <SortHeader label={bt("seal_ratio", "封单÷流通市值")} sortKey="seal_ratio_pct" dir={sort.dirOf("seal_ratio_pct")}
                  onSort={sort.toggle}
                  title={btitle("seal_ratio")}
                  className="whitespace-nowrap py-1 pr-2 text-right font-normal" />
                <SortHeader label={bt("vol_mult", "量能倍数")} sortKey="vol_mult" dir={sort.dirOf("vol_mult")} onSort={sort.toggle}
                  title={btitle("vol_mult")}
                  className="whitespace-nowrap py-1 pr-2 text-right font-normal" />
                <SortHeader label={bt("turnover", "换手")} sortKey="turnover" dir={sort.dirOf("turnover")} onSort={sort.toggle}
                  title={btitle("turnover")}
                  className="whitespace-nowrap py-1 pr-2 text-right font-normal" />
                <th className="py-1 pr-2 text-left">首封／炸板</th>
                <SortHeader label={bt("dt_buy_sell", "买÷卖")} sortKey="dt_buy_sell" dir={sort.dirOf("dt_buy_sell")}
                  onSort={sort.toggle}
                  title={btitle("dt_buy_sell")}
                  className="whitespace-nowrap py-1 pr-2 text-right font-normal" />
                <th className="py-1 pr-2 text-right">机构净买</th>
                <th className="py-1 pr-2 text-left">涨停原因（原样）</th>
              </tr>
            </thead>
            <tbody>
              {view.map((r, i) => (
                <tr key={`${r.code}-${i}`}
                  {...row.rowProps(i, r.code, "border-b border-border/30 hover:bg-muted/20")}>
                  <td className="py-1 pr-2 font-mono whitespace-nowrap">
                    <StockLink code={r.code}>{r.code}</StockLink></td>
                  <td className="py-1 pr-2 whitespace-nowrap">
                    {r.name ?? "—"}{r.is_st ? "ST" : ""}
                    <span className="ml-1 text-[10px] text-muted-foreground">{r.board ?? ""}</span></td>
                  <td className={cn("py-1 pr-2 text-right tabular-nums", pctColor(r.pct_change))}>{pct(r.pct_change)}</td>
                  <td className="py-1 pr-2 text-right tabular-nums"
                    title={finite(r.concept_base) ? `该概念近 20 场中位 ${fmt(r.concept_base ?? 0)} 只，本场 ${count(r.concept_count)}` : "常态样本不够，不给倍数"}>
                    {mult(r.concept_lift)}
                    {finite(r.concept_gap) ? <span className="ml-1 text-[10px] text-muted-foreground">隔 {r.concept_gap} 场</span> : null}</td>
                  <td className="max-w-[15rem] truncate py-1 pr-2" title={r.concept_top ?? ""}>
                    {r.concept_top ?? "—"}<span className="text-muted-foreground">（{count(r.concept_count)}）</span>
                    {finite(r.concept_n) && (r.concept_n ?? 0) > 1
                      ? <span className="ml-1 text-[10px] text-muted-foreground">共 {r.concept_n} 个概念</span> : null}</td>
                  <td className="py-1 pr-2 text-right tabular-nums"
                    title={r.seal_basis ? `流通市值口径：${r.seal_basis}` : ""}>
                    {typeof r.seal_ratio_pct === "number" ? `${fmt(r.seal_ratio_pct)}%` : "—"}</td>
                  <td className="py-1 pr-2 text-right tabular-nums">{mult(r.vol_mult)}</td>
                  <td className="py-1 pr-2 text-right tabular-nums">
                    {typeof r.turnover === "number" ? `${fmt(r.turnover)}%` : "—"}</td>
                  <td className="py-1 pr-2 whitespace-nowrap text-muted-foreground">
                    {r.first_seal ?? "—"}／{typeof r.reopen === "number" ? `${r.reopen} 次` : "—"}
                    {typeof r.at_limit_minutes === "number" ? `／在板 ${r.at_limit_minutes.toFixed(0)} 分` : ""}</td>
                  <td className="py-1 pr-2 text-right tabular-nums"
                    title={r.on_dt ? `上榜 ${r.dt_lists ?? 1} 条理由（前五席位合计口径）` : "当日没有龙虎榜行 —— 不等于没有资金"}>
                    {r.on_dt ? mult(r.dt_buy_sell) : <span className="text-muted-foreground">未上榜</span>}</td>
                  <td className="py-1 pr-2 text-right tabular-nums"
                    title={finite(r.inst_buy_count) ? `买方机构席位 ${r.inst_buy_count} 个、卖方 ${r.inst_sell_count ?? 0} 个` : "当日无机构行（12.9% 的首板才有）"}>
                    {typeof r.inst_net_yi === "number" ? `${r.inst_net_yi > 0 ? "+" : ""}${fmt(r.inst_net_yi)} 亿` : "—"}</td>
                  <td className="max-w-[16rem] truncate py-1 pr-2 text-muted-foreground" title={r.reason ?? ""}>
                    {r.reason ?? "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <div className="mt-1.5 flex flex-wrap items-center gap-2 text-[11px] text-muted-foreground">
        <span>{pageSentence(shown.length, size, cur)}（母体 {data?.parent?.total ?? rows.length} 只首板）</span>
        <span>{STOCK_ROW_HINT}</span>
        <select value={size} onChange={e => { setSize(Number(e.target.value)); setPage(1); }}
          className="rounded border border-border/60 bg-background px-1 py-0.5">
          {PAGE_SIZES.map(n => <option key={n} value={n}>{n} 行</option>)}
        </select>
        <button type="button" disabled={!canStep(cur, size, shown.length, -1)} onClick={() => setPage(cur - 1)}
          className="rounded border border-border/60 px-1.5 py-0.5 disabled:opacity-40">上一页</button>
        <button type="button" disabled={!canStep(cur, size, shown.length, 1)} onClick={() => setPage(cur + 1)}
          className="rounded border border-border/60 px-1.5 py-0.5 disabled:opacity-40">下一页</button>
      </div>

    </GlassCard>
  );
}
