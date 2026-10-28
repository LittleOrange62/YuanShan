import { RefreshCw } from "lucide-react";
import { Caliber } from "@/components/ui/Caliber";
import { Caveats, Definitions, ExplainBox, Panel } from "@/components/ui/DataPanel";
import { GlassCard } from "@/components/ui/GlassCard";
import { SectorLink } from "@/components/ui/SectorLink";
import { StockBriefBox } from "@/components/stock/StockBriefBox";
import { StockLink } from "@/components/ui/StockLink";
import { useNonce, useProbe } from "@/lib/probe";
import { plain } from "@/lib/agent";
import { usePageAskAi } from "@/lib/workspace/pageAskAi";
import { dash } from "@/lib/datastate";
import { cn } from "@/lib/utils";
import {
  groupNames, identitySentence, pct, pctTone, profileAgeSentence, sectorLabels,
  sessionProfileSameDay, yi, yuan, type FactsPayload,
} from "@/lib/stockFacts";

/**
 * 个股深度复盘最上方的「个股信息」卡（用户 2026-10-16：「把个股信息这些挪上去」）。
 *
 * 摆在第一块的理由：下面几叠都只回答某一面，读者得先知道这一只是什么、最近一场什么价、
 * 归在哪些板块、在本机被放在哪个组里。
 * 三条实现约束：
 *  · 只读本机库、不出网 ⇒ 比下面「个股研究」那张公开源即时卡**可能晚一场**，这句话上屏不折。
 *  · 每个格子各带自己的日期：行情按 `session.trade_date`，档案按 `profile.hq_date` + 几天没刷；
 *    两者不同日就不并成"今天的信息"。
 *  · 主档没有上市日/总股本、收盘快照的每股类与 ROE 本机全是 0 ⇒ 这些格子在这里不出现，
 *    要看每股类去「财务多期与外推」那一块（缺就是缺，不拿 0 摆上屏）。
 */

const toneCls = { up: "text-primary", down: "text-success", flat: "", missing: "text-muted-foreground/60" } as const;

export function StockFacts({ code }: { code: string | null }) {
  const [nonce, refresh] = useNonce();
  const state = useProbe<FactsPayload>("/stock/facts", nonce, { code: code ?? "" }, !code);
  const d = state.kind === "ok" ? state.data : null;

  // 右侧「AI分析」栏那一份材料：这一块是读者的第一屏，身份/归属/最近一场价必须先进上下文。
  // 登记放在组件顶层 —— `Panel` 那个 render-prop 只在有数时才画，把 hook 放进 if 里
  // 等于按数据有无改变 hook 调用次数，React 的「每次渲染顺序要一致」就破了。
  const askCtx = d?.available ? [
    `${d.name ?? code}（${d.code}）· ${identitySentence(d)}。`,
    `最近一场 ${dash(d.session?.trade_date)}：收 ${yuan(d.session?.close)}，当日 ${pct(d.session?.pct_change)}，`
    + `成交额 ${yi(d.session?.amount_yi)}，换手 ${dash(d.session?.turnover_rate)}%，`
    + `PE-TTM ${dash(d.session?.pe_ttm)}，PB ${dash(d.session?.pb)}，`
    + `总市值 ${yi(d.session?.total_mv_yi)}，流通市值 ${yi(d.session?.float_mv_yi)}。`,
    `主营/标签：${dash(d.profile?.main_business)}；员工 ${dash(d.profile?.staff_num)} 人；`
    + `β ${dash(d.profile?.beta)}（档案日 ${dash(d.profile?.hq_date)}，`
    + `${profileAgeSentence(d.profile?.profile_age_days)}）。`,
    `板块与概念归属：${sectorLabels(d.sectors)}。`,
    (d.caveats ?? []).map(c => `· ${c.text ?? ""}`).join("\n"),
  ].join("\n") : null;
  usePageAskAi(askCtx ? {
    label: `个股信息（${d?.name ?? code}）`,
    context: askCtx,
    suggestions: ["这一只的板块归属里哪个方向最值得先看", "把这一块和下面「财务多期」的数对一遍有没有矛盾"],
  } : null);

  return (
    <div>
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <h2 className="text-base font-bold">个股信息</h2>
        <Caliber text={
          "全部读本机库（不出网），所以休市也在；比下面「个股研究」那张公开源即时卡可能晚一场。\n"
          + "每个格子各带自己的日期：行情按最近一场交易日，档案（主营/员工数/β/历史高低）按它自己的刷新日。\n"
          + "完整口径是表下那句「取数口径（后端原句）」，页面不改写。"
        } />
        <button type="button" onClick={refresh} title="重新读本机库"
          className="ml-auto flex items-center gap-1 rounded-lg bg-primary/15 px-2 py-1 text-[11px] font-medium text-primary hover:bg-primary/25">
          <RefreshCw className="h-3.5 w-3.5" /> 重新取数
        </button>
      </div>

      <Panel title="这一只是什么 · 最近一场什么价" state={state}
        note={!code ? "还没选一只，这一块不替你挑。"
          : "读本机六张表，一次请求（五张给读数，第六张 `analytics.stock_brief` 存那份 AI 说明的正文与它的材料回执）；"
          + "格子缺的显示「—」，不补 0；那一格 AI 说明是模型写的正文，不参与任何判据 —— "
          + "它上面写着「AI 生成的正文 · 不是本机判据」，那句话不折进框。"}>
        {d => {
          const s = d.session ?? {};
          const p = d.profile ?? {};
          const same = sessionProfileSameDay(s, p);
          const cells: { k: string; v: string; title: string; cls?: string }[] = [
            { k: "收盘价", v: yuan(s.close), title: d.definitions?.close ?? "", cls: "font-mono" },
            { k: "当日涨幅", v: pct(s.pct_change), title: d.definitions?.pct_change ?? "",
              cls: cn("font-mono font-semibold", toneCls[pctTone(s.pct_change)]) },
            { k: "成交额", v: yi(s.amount_yi), title: d.definitions?.amount_yi ?? "" },
            { k: "换手率", v: s.turnover_rate == null ? "—" : `${dash(s.turnover_rate)} %`,
              title: d.definitions?.turnover_rate ?? "" },
            { k: "总市值", v: yi(s.total_mv_yi), title: d.definitions?.total_mv_yi ?? "" },
            { k: "流通市值", v: yi(s.float_mv_yi), title: d.definitions?.float_mv_yi ?? "" },
            { k: "PE-TTM", v: dash(s.pe_ttm), title: d.definitions?.pe_ttm ?? "" },
            { k: "PB", v: dash(s.pb), title: d.definitions?.pb ?? "" },
            { k: "当日涨停价", v: yuan(s.limit_up), title: d.definitions?.limit_up ?? "" },
            { k: "主营/标签", v: dash(p.main_business), title: d.definitions?.main_business ?? "" },
            { k: "员工数", v: p.staff_num == null ? "—" : `${dash(p.staff_num)} 人`,
              title: d.definitions?.staff_num ?? "" },
            { k: "β 系数", v: dash(p.beta), title: d.definitions?.beta ?? "" },
            { k: "IPO 价", v: yuan(p.ipo_price), title: d.definitions?.ipo_price ?? "" },
            { k: "历史最高", v: yuan(p.his_high), title: d.definitions?.his_high ?? "" },
            { k: "历史最低", v: yuan(p.his_low), title: d.definitions?.his_low ?? "" },
            { k: "拼音首字母", v: dash(d.pinyin_abbr), title: "本机证券字典里登记的首字母串（搜索框用它）" },
          ];
          return (
            <>
              <div className="mb-2 flex flex-wrap items-baseline gap-2">
                <span className="text-base font-bold"><StockLink code={d.code} name={d.name} /></span>
                <span className="font-mono text-[12px] text-muted-foreground">{d.code}</span>
                <span className="rounded bg-primary/15 px-1.5 py-0.5 text-[10px] text-primary">
                  {dash(d.board)}{d.is_st ? " · ST" : ""}
                </span>
                <span className="text-[11px] text-muted-foreground">{identitySentence(d)}</span>
                <span className="ml-auto text-[11px] text-muted-foreground">
                  行情 {dash(s.trade_date)}
                  {p.hq_date && <> · 档案 {p.hq_date}（{profileAgeSentence(p.profile_age_days)}）</>}
                  {!same && <><br />行情与档案不同日，各说各的日期</>}
                </span>
              </div>

              <div className="grid grid-cols-2 gap-2 sm:grid-cols-4 lg:grid-cols-8">
                {cells.map(c => (
                  <GlassCard key={c.k} className="px-2 py-1.5">
                    <div className="text-[10px] text-muted-foreground" title={c.title}>{c.k}</div>
                    <div className={cn("mt-0.5 text-[13px] font-semibold text-foreground", c.cls)}>{c.v}</div>
                  </GlassCard>
                ))}
              </div>

              <StockBriefBox facts={d} refresh={refresh} />

              <div className="mt-2 grid gap-2 lg:grid-cols-2">
                <div className="rounded-lg border border-border/60 bg-muted/20 p-2">
                  <div className="text-[11px] font-medium text-foreground"
                    title={d.definitions?.sectors ?? ""}>板块与概念归属</div>
                  <div className="mt-1 flex flex-wrap gap-1">
                    {(d.sectors ?? []).map(x => (
                      <SectorLink key={x.sector_code} name={x.name} code={x.sector_code}
                        sectorType={x.sector_type}
                        detail={`${x.sector_type ?? "板块"} · 本机字典里这个板块有 ${dash(x.members)} 只成分`} />
                    ))}
                    {!(d.sectors ?? []).length && (
                      <span className="text-[11px] text-muted-foreground">
                        本机字典里没登记它的板块归属 —— 不是"它没有板块"，是那张对表没这一行
                      </span>
                    )}
                  </div>
                </div>
                <div className="rounded-lg border border-border/60 bg-muted/20 p-2">
                  <div className="text-[11px] font-medium text-foreground"
                    title={d.definitions?.groups ?? ""}>本机自选分组</div>
                  <div className="mt-1 text-[11px] text-muted-foreground">
                    {groupNames(d.groups).length
                      ? `在这些组里：${groupNames(d.groups).join("、")}`
                      : "不在任何自选组里（同花顺那份名单同步进来后这里才会有名字）"}
                  </div>
                </div>
              </div>

              <Caveats items={d.caveats ?? []} />
              {d.caliber && (
                <p className="mt-2 border-t border-dashed border-border pt-2 text-[10px] leading-relaxed text-muted-foreground">
                  取数口径（后端原句，页面未改写）：{plain(d.caliber)}
                </p>
              )}
              <Definitions map={d.definitions} />
              <ExplainBox title="为什么这一块不显示每股收益、ROE、上市日、总股本">
                <p>
                  这四样本机库给不出能读的数：<b className="text-foreground">收盘快照</b>里的
                  <b className="text-foreground">每股收益 / 每股净资产 / ROE</b> 三列实测 21 万余行一个非零都没有，
                  <b className="text-foreground">证券主档</b>里的<b className="text-foreground">上市日期</b>与
                  <b className="text-foreground">总股本</b>两列全表为空。把它们摆上屏只会让人读到 0 与"没有上市日"，
                  所以这一块一律不出现这四格 —— 每股类与 ROE 请看下面<b className="text-foreground">「财务多期与外推」</b>
                  那一块（源＝同花顺，逐期都有），上市日请看下面<b className="text-foreground">「个股研究」</b>那块的公开源档案。
                </p>
              </ExplainBox>
            </>
          );
        }}
      </Panel>
    </div>
  );
}
