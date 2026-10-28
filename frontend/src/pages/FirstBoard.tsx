/**
 * 「日间复盘及首板」（原名「首板分析」，用户 2026-10-26 裁定改名；路由 `/first-board` 不变）。
 *
 * 上半部分＝盘中涨跌停事件流（`components/intraday/EventStreamPanel.tsx`，只读
 * `GET /api/intraday/timeline`）；下半部分＝按连板档位分的首板样本。
 *
 * ⚠️ 同一天的另一条裁定，写给下一个会话看：**这一屏允许在界面上逐只列出个股代码与名称。**
 *   `vr/astock.py:511` 那条「原始池含 code/name，切勿直接接成 API/UI（零标的红线）」在本屏让一步 ——
 *   事件流的母体是库里那批事件行（`analytics.intraday_pool_event`），经读侧出口
 *   `duanxian/intraday_view.py` 出来，页面不直接调 `vr/astock.py` 的原始池。
 *   这不是漏改的缺陷，别把它改回去。（2026-10-26 用户原话背景：盘中数据与事件流要展示在这一屏上半部分。）
 */
import { Fragment, useEffect, useState } from "react";
import { SortHeader } from "@/components/ui/SortHeader";
import { useTableSort } from "@/lib/useSort";
import { STOCK_ROW_HINT, useStockRowInteraction } from "@/lib/useStockRowInteraction";
import { Flame, Loader2, Sparkles, AlertCircle, X, Copy, Check } from "lucide-react";
import { PageHeader } from "@/components/ui/PageHeader";
import { ExplainBox } from "@/components/ui/DataPanel";
import { GlassCard } from "@/components/ui/GlassCard";
import { Caliber } from "@/components/ui/Caliber";
import { Disclaimer } from "@/components/ui/Disclaimer";
import { useDeepDive, DeepDivePanel, RunAllButton, type DiveItem } from "@/components/ui/DeepDive";
import { renderPrompt, useTemplatePicker, type PromptValues } from "@/lib/prompts";
import { api, type FirstBoardData, type FirstBoardStock } from "@/lib/api";
import { cn } from "@/lib/utils";
import { StockLink } from "@/components/ui/StockLink";
import { TemplatePicker } from "@/components/ui/TemplatePicker";
import { NodeCell, NodeWarn } from "@/components/ui/NodeCell";
import { EventStreamPanel } from "@/components/intraday/EventStreamPanel";

const fmt = (v: number) => v.toLocaleString("zh-CN", { maximumFractionDigits: 2 });
const yi = (v: number | null) => (v == null ? "—" : `${fmt(v / 1e8)} 亿`); // 元 → 亿

const dateLabel = (d: string) =>
  d.length === 8 ? `${d.slice(0, 4)}-${d.slice(4, 6)}-${d.slice(6)}` : d;

/** 四档（连板数 = 1 / 2 / 3 / ≥4）。行序由后端按各档规则排好，页面不再排第二次。 */
const TIER_KEY = "first-board-tier";
const TIER_FALLBACK = "1";
function initialTier(): string {
  try { return sessionStorage.getItem(TIER_KEY) || TIER_FALLBACK; } catch { return TIER_FALLBACK; }
}

export function FirstBoard() {
  const [data, setData] = useState<FirstBoardData | null>(null);
  const [loaded, setLoaded] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);
  // 选择的交易日：初值为空 = 由后端定"库里最近的一场"。候选项只能来自 available_dates，
  // 页面不按 new Date()/「今天」造任何日期（判定只在 vr/firstboard.py:data_session() 一处）
  const [day, setDay] = useState<string | null>(null);
  // 上次选的档记在本次页面会话里（用户那份说明的行为）；库里这一场没有这一档时自动回落首板。
  const [tier, setTier] = useState<string>(initialTier);
  const pickTier = (k: string) => {
    setTier(k);
    try { sessionStorage.setItem(TIER_KEY, k); } catch { /* 隐私模式下存不住，不影响这一屏 */ }
    fb.clearAll();
  };
  const fbSort = useTableSort();
  // 单击交叉高亮 / 双击开深度复盘：全站一份实现（`lib/useStockRowInteraction.ts`）。
  const fb = useStockRowInteraction("首板分析");
  // 换排序／换交易日都会改行序（高亮按行号记），必须连着告警一起清，不然漂到别的票身上。
  const fbSortBy = (k: string) => { fb.clearAll(); fbSort.toggle(k); };
  const dd = useDeepDive("firstboard", data?.date || "");

  useEffect(() => {
    api.firstBoard(day ?? undefined).then(setData).catch((error: unknown) => {
      setLoadError(error instanceof Error ? error.message : "首板名单读取失败，请刷新重试");
    }).finally(() => setLoaded(true));
  }, [day]);

  // 话术从 `config.setting` 读（系统配置页可改），这里只带数据。取不到模板 → 空 prompt →
  // DeepDive 会拦住并说明原因，不在代码里另存一份话术当兜底。
  const dive = useTemplatePicker("firstboard");
  const [copied, setCopied] = useState("");
  const divePrompt = (s: FirstBoardStock) =>
    (dive.current ? renderPrompt(dive.current.template, diveValues(s)) : null);
  const copyPrompt = async (s: FirstBoardStock) => {
    const text = divePrompt(s);
    if (!text) return;
    try { await navigator.clipboard.writeText(text); setCopied(s.code); setTimeout(() => setCopied(""), 1600); }
    catch { setCopied(`fail:${s.code}`); setTimeout(() => setCopied(""), 2400); }
  };
  const diveValues = (s: FirstBoardStock): PromptValues => ({
    date: dateLabel(data?.date || ""),
    name: s.name, code: s.code, price: s.price, pct: s.pct,
    seal_time: s.seal_time || "未知", break_count: s.break_count,
    amount: yi(s.amount), float_cap: yi(s.float_cap),
    industry: s.industry || "未知", reason: s.reason || "（暂缺，需要自查）",
  });
  const ctx = (s: FirstBoardStock) => `首板股 ${s.name}(${s.code}) 涨停原因深入分析`;
  const diveItem = (s: FirstBoardStock): DiveItem => ({
    key: s.code, prompt: divePrompt(s) ?? "", context: ctx(s),
  });

  const stocks = data?.stocks ?? [];
  // 四档名单。档位与行序都由后端定（`vr/firstboard.py`）：1~3 档按首次封板时间从早到晚，
  // 四板及以上先按连板高度从高到低、同一高度再按首封。页面不再排第二次。
  const tiers = data?.tiers ?? [];
  const current = tiers.find(t => t.key === tier)
    ?? tiers.find(t => t.key === "1")
    ?? { key: "1", label: "首板", count: 0, stocks: [] as FirstBoardStock[] };
  const shown = current.stocks;
  const tierLabel = current.label;
  const nameByCode = Object.fromEntries(stocks.map((s) => [s.code, s.name]));

  return (
    <div>
      <PageHeader
        title="日间复盘及首板"
        subtitle={`上半：盘中涨跌停事件流（每 5 分钟一针的逐条事件差，读 /intraday/timeline）· 下半：${tierLabel}样本（连板数${current.key === "4p" ? "≥4" : `=${current.key}`}）· 涨停原因题材 · 节点分析 · 每只可让 AI 深入分析`}
      />

      {/* 上半：盘中事件流（用户 2026-10-26 点名挂在这屏上半部分；
          逐只列代码与名称是同一天的裁定，见本文件顶部注释） */}
      <EventStreamPanel />

      <h2 className="mb-2 flex items-center gap-1.5 text-sm font-semibold">
        <Flame className="h-4 w-4 text-primary" /> 下半 · {tierLabel}样本（按所选交易日）
      </h2>

      {(() => {
        // 交易日选择器：候选项就是库里真有的那些日子。subtitle 以前写"按下方交易日展示"
        // 却没有选择器 —— 那是页面上的一条空头承诺，现在补齐。
        const dates = data?.available_dates ?? [];
        if (dates.length === 0) return null;
        return (
          <div className="mb-3 flex flex-wrap items-center gap-1.5">
            <span className="text-xs text-muted-foreground">交易日（只列库里真有的日子）：</span>
            {dates.map((d) => {
              const on = (data?.date ?? "") === d;
              return (
                <button key={d} type="button" aria-pressed={on} disabled={on}
                  onClick={() => { fb.clearAll(); setLoaded(false); setDay(d); }}
                  className={cn("rounded px-1.5 py-0.5 font-mono text-[11px]",
                    on ? "bg-primary/15 text-primary" : "bg-muted text-muted-foreground hover:text-foreground")}>
                  {d}
                </button>
              );
            })}
          </div>
        );
      })()}

      {data && (
        <>
          <div className="mb-3 flex flex-wrap items-center gap-1.5">
            <span className="text-xs text-muted-foreground">连板档位：</span>
            {tiers.map(t => (
              <button key={t.key} type="button" aria-pressed={current.key === t.key}
                onClick={() => pickTier(t.key)}
                className={cn("rounded px-2 py-0.5 text-[11px]",
                  current.key === t.key ? "bg-primary/15 text-primary" : "bg-muted text-muted-foreground hover:text-foreground")}>
                {t.label}
                {/* 每一档后面那个数是**全场这一档**的家数（后端算的），不是这一屏截断后的行数 */}
                　{t.count}
              </button>
            ))}
          </div>
          <div className="mb-4 grid grid-cols-3 gap-3">
            {[
              { label: "交易日", value: dateLabel(data.date) },
              { label: "该交易日涨停",
                // 池子数与库里家数不一致时两个都摆出来：判定只在后端一处，页面只陈列不解释
                value: typeof data.db_zt === "number" && data.db_zt !== data.total_zt
                  ? `${data.total_zt} 家（库里 ${data.db_zt}）`
                  : `${data.total_zt} 家` },
              { label: `其中${tierLabel}`, value: `${current.count} 家` },
            ].map((c) => (
              <GlassCard key={c.label} className="py-3 text-center">
                <div className="text-xs text-muted-foreground">{c.label}</div>
                <div className="mt-1 font-mono text-lg font-bold text-primary">{c.value}</div>
              </GlassCard>
            ))}
          </div>
        </>
      )}

      {data?.reason_note && (
        <p className="mb-1.5 flex items-center gap-1.5 text-xs text-muted-foreground">
          <AlertCircle className="h-3.5 w-3.5" /> 涨停原因：{data.reason_note}
        </p>
      )}

      {typeof data?.db_zt === "number" && data.db_zt !== data.total_zt && !data.caliber_note && (
        <p className="mb-1.5 text-[11px] leading-relaxed text-muted-foreground">
          池子 {data.total_zt} 家与库里 {data.db_zt} 家不一致，而这一场接口<b className="text-foreground">没有给差异说明</b>
          （<code>caliber_note</code> 为空）—— 这里只把两个数都摆出来，不替它编原因。
        </p>
      )}

      {/* 日期口径必须与 reason_note 同级显示：用户点名要这句，吞掉就等于把"库里缺哪一天"藏起来 */}
      {data?.caliber_note && (
        <p className="mb-3 flex items-start gap-1.5 rounded-lg bg-warning/10 p-2 text-xs leading-relaxed text-warning">
          <AlertCircle className="mt-0.5 h-3.5 w-3.5 shrink-0" /> {data.caliber_note}
          {typeof data.db_zt === "number" && (
            <span className="ml-1 font-mono">（库里 {data.db_zt} 家）</span>
          )}
        </p>
      )}

      <GlassCard>
        <div className="mb-2 flex flex-wrap items-center gap-2 text-sm font-semibold">
          <Flame className="h-4 w-4 text-primary" /> {tierLabel}名单
          <Caliber text={
            "「炸板」为数据源记录的当日开板次数；0 表示未记录开板，不证明买盘强弱或持续性。\n" +
            "榜单是取数时点仍涨停的股票，盘中还可能变化；收盘后才可作为最终涨停名单。\n" +
            "首板 / 二板 / 三板按首次封板时间从早到晚排；四板及以上先按连板高度从高到低，\n" +
            "同一高度再按首封从早到晚（顺序由后端定，页面不重排）。\n" +
            "「节点」那一格的判据、以及这一格为什么是「—」，都写在悬停与名单上方那句声明里。\n" +
            "「行业」经常只有四个字（像「互联网电」「自动化设」）——是上游把名字截到四字，\n" +
            "不是这里显示不全；怕猜错所以不替它补全称。"
          } />
          <span className="text-xs font-normal text-muted-foreground">
            {current.key === "4p" ? "按连板高度从高到低（同高度按首封）" : "按首次封板时间排序（早封在前）"}
            　·　客观公开榜单，非推荐 / 非预测
          </span>
          <span className="ml-auto font-normal">
            <TemplatePicker templates={dive.templates} currentId={dive.currentId} onPick={dive.choose} scopeLabel="首板股" />
            {dive.error && <span className="text-[11px] text-danger">话术模板没取到：{dive.error}（去系统配置 → AI 提示词看这一档的生效来源）</span>}
            <RunAllButton dd={dd} items={shown.map(diveItem)} nameOf={(k) => nameByCode[k] || k} />
          </span>
        </div>
        {/* 整场那一句「这一列的 — 要怎么读」：非空就摆在名单上方，不折进上面的说明框 */}
        <NodeWarn text={data?.node_warn} />
        {!loaded ? (
          <div className="flex items-center gap-2 py-8 text-sm text-muted-foreground">
            <Loader2 className="h-4 w-4 animate-spin" /> 加载中…
          </div>
        ) : loadError ? (
          <p role="alert" className="py-8 text-center text-sm text-danger">首板名单读取失败：{loadError}。请刷新页面重试。</p>
        ) : shown.length === 0 ? (
          <div className="py-8 text-center text-sm text-muted-foreground">
            {stocks.length ? `这一档今天没有票（${tierLabel}0 家）—— 不是名单读不到，换个档位看看。`
              : "暂无数据（数据源异常或非交易日）"}
          </div>
        ) : (<>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-border/50 text-left text-xs text-muted-foreground">
                  <SortHeader label="名称" sortKey="name" dir={fbSort.dirOf("name")} onSort={fbSortBy} />
                  {current.key === "4p" && (
                    <SortHeader label="连板高度" sortKey="boards" dir={fbSort.dirOf("boards")} onSort={fbSortBy}
                      title="池内连板数（东财涨停池 lbc，缺了按 1）。6 板及以上紫色、5 板红色、其余橙色 —— 只是把最高的那几只挑出来给眼睛，不给评分、不预测。" />
                  )}
                  <SortHeader label="首封" sortKey="seal_time" dir={fbSort.dirOf("seal_time")} onSort={fbSortBy}
                        title="首次封板时刻；没有封板时刻记录的票恒排最后，不当成「没封板」" />
                  <SortHeader label="炸板" sortKey="break_count" dir={fbSort.dirOf("break_count")} onSort={fbSortBy}
                        title="数据源记录的当日开板次数；0 表示未记录开板，不证明买盘强弱" />
                  <SortHeader label="现价" sortKey="price" dir={fbSort.dirOf("price")} onSort={fbSortBy} />
                  <SortHeader label="当日涨幅" sortKey="pct" dir={fbSort.dirOf("pct")} onSort={fbSortBy} />
                  <SortHeader label="成交额" sortKey="amount" dir={fbSort.dirOf("amount")} onSort={fbSortBy} />
                  <SortHeader label="流通市值" sortKey="float_cap" dir={fbSort.dirOf("float_cap")} onSort={fbSortBy} />
                  <SortHeader label="涨停原因" sortKey="reason" dir={fbSort.dirOf("reason")} onSort={fbSortBy}
                        title="源站编辑归因，按中文串排" />
                  <SortHeader label="节点" sortKey="node_hits" dir={fbSort.dirOf("node_hits")} onSort={fbSortBy}
                        title={data?.node_rule
                          ? `${data.node_rule}\n（判据与写入只住 app/compute/board_node.py；悬停在具体一格上能看到那一格的依据）`
                          : "后端没交 node_rule —— 这一列的判据原文这一屏说不出，页面不自己复述规则"} />
                  <SortHeader label="行业" sortKey="industry" dir={fbSort.dirOf("industry")} onSort={fbSortBy} />
                  <th className="whitespace-nowrap px-2 py-2 font-medium"></th>
                </tr>
              </thead>
              <tbody>
                {fbSort.apply(shown).map((s, i) => (
                  <Fragment key={s.code}>
                    <tr {...fb.rowProps(i, s.code, "border-b border-border/30")}>
                      <td {...fb.cellProps(i, "name", "whitespace-nowrap px-2 py-2")}>
                        <StockLink code={s.code} name={s.name} className="font-medium hover:text-primary" />{" "}
                        <StockLink code={s.code} className="text-xs text-muted-foreground/50 underline decoration-dotted underline-offset-2 hover:text-primary">{s.code}</StockLink>
                      </td>
                      {current.key === "4p" && (
                        <td {...fb.cellProps(i, "boards", "whitespace-nowrap px-2 py-2 font-mono font-semibold")}>
                          <span className={cn("rounded px-1.5 py-0.5 text-[11px]",
                            (s.boards ?? 0) >= 6 ? "bg-purple-500/15 text-purple-400"
                              : (s.boards ?? 0) >= 5 ? "bg-destructive/15 text-destructive"
                                : "bg-warning/15 text-warning")}>
                            {s.boards ?? "—"} 板
                          </span>
                        </td>
                      )}
                      <td {...fb.cellProps(i, "seal_time", "whitespace-nowrap px-2 py-2 font-mono text-muted-foreground")}>{s.seal_time || "—"}</td>
                      <td {...fb.cellProps(i, "break_count", "whitespace-nowrap px-2 py-2 font-mono")}>
                        {s.break_count > 0 ? <span className="text-primary">{s.break_count} 次</span> : <span className="text-muted-foreground/50">0</span>}
                      </td>
                      <td {...fb.cellProps(i, "price", "px-2 py-2 font-mono")}>{s.price}</td>
                      <td {...fb.cellProps(i, "pct", "px-2 py-2 font-mono text-danger")}>+{s.pct}%</td>
                      <td {...fb.cellProps(i, "amount", "whitespace-nowrap px-2 py-2 font-mono text-muted-foreground")}>{yi(s.amount)}</td>
                      <td {...fb.cellProps(i, "float_cap", "whitespace-nowrap px-2 py-2 font-mono text-muted-foreground")}>{yi(s.float_cap)}</td>
                      <td {...fb.cellProps(i, "reason", "max-w-56 px-2 py-2 text-xs")}>
                        {s.reason ? <span className="text-foreground">{s.reason}</span> : <span className="text-muted-foreground/50">—</span>}
                      </td>
                      <td {...fb.cellProps(i, "node_hits", "whitespace-nowrap px-2 py-2 text-center")}>
                        <NodeCell row={s} rule={data?.node_rule} />
                      </td>
                      <td {...fb.cellProps(i, "industry", "whitespace-nowrap px-2 py-2 text-xs text-muted-foreground")}>{s.industry}</td>
                      <td {...fb.cellProps(i, "actions", "whitespace-nowrap px-2 py-2 text-right")}>
                        <div className="inline-flex items-center gap-1">
                        <button
                          onClick={() => void copyPrompt(s)} disabled={!dive.current}
                          title={copied === s.code ? "已复制到剪贴板"
                            : copied === `fail:${s.code}` ? "浏览器拒绝了剪贴板访问，请在页面里手动复制"
                            : "按当前模板把这一行的提示词复制到剪贴板"}
                          className="inline-flex items-center gap-1 rounded-lg border border-border/60 px-1.5 py-1 text-[11px] text-muted-foreground hover:border-primary hover:text-primary disabled:opacity-40">
                          {copied === s.code ? <Check className="h-3 w-3" /> : <Copy className="h-3 w-3" />}
                          {copied === s.code ? "已复制" : copied === `fail:${s.code}` ? "复制失败" : "复制提示词"}
                        </button>
                        <button
                          onClick={() => dd.toggle(diveItem(s))}
                          className="inline-flex items-center gap-1 rounded-lg border border-primary/50 bg-primary/10 px-2.5 py-1 text-xs font-medium text-primary transition-colors hover:bg-primary/20"
                        >
                          {dd.running === s.code ? <Loader2 className="h-3 w-3 animate-spin" /> : dd.open === s.code ? <X className="h-3 w-3" /> : <Sparkles className="h-3 w-3" />}
                          {dd.open === s.code ? "收起" : dd.analysis[s.code] ? "展开" : "深入分析"}
                        </button>
                        </div>
                      </td>
                    </tr>
                    {dd.open === s.code && (
                      <DeepDivePanel
                        dd={dd}
                        stockKey={s.code}
                        colSpan={10}
                        noteTitle={`首板深析 · ${s.name}`}
                        onRerun={() => dd.rerun(diveItem(s))}
                      />
                    )}
                  </Fragment>
                ))}
              </tbody>
            </table>
          </div>
          {/* 用户 2026-10-21：逐屏那两句"怎么点"的描述统一默认收起（同 /grouped 的写法），
              告警（弹窗被拦）仍然摊在下面 */}
          <ExplainBox title="这一张表怎么点 · 双击去哪里">{STOCK_ROW_HINT}</ExplainBox>
          {/* 弹窗被拦是**告警**，摊在表下面，不折进任何默认收起的说明框 */}
          {fb.popHint && <p role="status" className="mt-1 text-[11px] leading-relaxed text-warning">{fb.popHint}</p>}
        </>
        )}
      </GlassCard>

      {/* 原因/行业类别汇总（用户 09-28 点名）：计数在后端 `vr/firstboard.py:_tally()`，
          页面只排展示 —— 两边各拆一次串，早晚给出两个榜。 */}
      {!!data && (!!data.reason_summary?.length || !!data.industry_summary?.length) && (
        <div className="grid gap-4 md:grid-cols-2">
          <TallyCard title="涨停原因 · 重复出现的类别" rows={data.reason_summary ?? []}
            stockCount={data.summary_meta?.stock_count ?? data.stocks.length}
            dropped={data.summary_meta?.dropped_single_reason ?? 0}
            caliber={"把每一只首板的涨停原因串按 +/、// 拆成单个原因，数**出现过的只数**"
              + "（同一只票里重复的词只算一次）。只列次数≥2 的（你 09-28 点名的口径），"
              + "被滤掉的单例条数写在下面，免得看着像今天只有这几类题材。\n"
              + "题材串是来源站的编辑归因，不是公司确认事实；这里也不判好坏、不给方向。"} />
          <TallyCard title="所属行业 · 重复出现的类别" rows={data.industry_summary ?? []}
            stockCount={data.summary_meta?.stock_count ?? data.stocks.length}
            dropped={data.summary_meta?.dropped_single_industry ?? 0}
            caliber={"同一批首板，按涨停池给的所属行业数只数（同样只列≥2 的）。"
              + "行业是东财那一列的原样值，与概念/题材不同一套，不能拿来解释「为什么涨」。"} />
        </div>
      )}

      <Disclaimer />
    </div>
  );
}


/** 一张类别计数卡：后端给什么就排什么，页面不拆串、不补 0、不再排一次序。 */
function TallyCard({ title, rows, stockCount, dropped, caliber }: {
  title: string; rows: { name: string; count: number }[];
  stockCount: number; dropped: number; caliber: string;
}) {
  const top = rows[0]?.count ?? 1;
  return (
    <GlassCard>
      <div className="mb-1 flex items-center gap-1.5">
        <h3 className="text-sm font-semibold">{title}</h3>
        <Caliber text={caliber} />
      </div>
      {rows.length === 0
        ? <p className="py-3 text-[12px] text-muted-foreground">
            这一批里没有重复出现的类别（每一类都只有 1 只）。
            {dropped > 0 && <> 另有 {dropped} 个单例类别未列出 —— 那是"只出现过一次"，不是不存在。</>}
          </p>
        : (
          <ul className="space-y-1">
            {rows.map(r => (
              <li key={`${title}-${r.name}`} className="flex items-baseline gap-2 text-[12px]">
                <span className="w-32 shrink-0 truncate font-medium" title={r.name}>{r.name}</span>
                <span className="font-mono tabular-nums text-muted-foreground">{r.count} 只</span>
                <span className="h-1.5 flex-1 overflow-hidden rounded bg-muted">
                  <span className="block h-full rounded bg-primary/60"
                    style={{ width: `${Math.min(100, (r.count / top) * 100)}%` }} />
                </span>
              </li>
            ))}
          </ul>
        )}
      <p className="mt-1.5 text-[10px] leading-relaxed text-muted-foreground">
        分母是页面上这一批（{stockCount} 只首板），不是全市场涨停；
        次数=出现该类别的<b className="text-foreground">只数</b>（同一只票串里重复写两遍只算一次）。
        只列≥2 的，另有 {dropped} 个单例类别未列出。
      </p>
    </GlassCard>
  );
}
