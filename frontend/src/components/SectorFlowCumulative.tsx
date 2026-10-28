import { useState } from "react";
import { RefreshCw } from "lucide-react";
import { Caliber } from "@/components/ui/Caliber";
import { SectorLink } from "@/components/ui/SectorLink";
import { CaveatText, Caveats, Contract, Definitions, ExplainBox, Provenance, Row } from "@/components/ui/DataPanel";
import { GlassCard } from "@/components/ui/GlassCard";
import { SortHeader } from "@/components/ui/SortHeader";
import { useTableSort } from "@/lib/useSort";
import { useNonce, useProbe } from "@/lib/probe";
import { dash, describeState, num } from "@/lib/datastate";
import { plain } from "@/lib/agent";
import { pctColor } from "@/lib/colors";
import { cn } from "@/lib/utils";
import type { SectorFlowStored } from "@/lib/api";

/**
 * 「2024 年以来累计净流入」（用户 2026-10-06 点名要的）。
 *
 * 原话：「把每个板块历史（2024年1月1日以来）的所有净流入净流出都计算一遍，
 * 还有每个板块的近3个月的所有净流入净流出都计算一遍，然后默认展示近3个月流入流出为正的
 * 也就是净流入最大到最小排列，显示前50个，并保留第二页第三页的翻页功能。」
 *
 * 四条里三条照做，一条**做不到并把原因写在页面上**：这张表 2026-10-06 才建，
 * 而"2024 年以来"那份历史在本机不存在 —— 不是没算，是没原料：
 *  · 同花顺行业资金流接口只有「即时」一档，报文里没有日期字段（逐日那份靠 `c22` 每天采）；
 *  · 同花顺的 3日 / 5日 / 10日 档在本机 akshare 1.18.97 已坏（实测抛异常）；
 *  · 个股主力净额那张（`market_data.fund_flow_rank`）只有 3 场，同样没有日期字段；
 *  · 有 1,253 场长历史的是**成交额份额**（`analytics.sector_flow_trend`），
 *    它回答"钱相对往哪儿走"，不回答"净流入多少亿"，不能拿来冒充这一列。
 * 所以「覆盖以来累计」的起点由**表里最早那天**决定，页面把 `coverage` 一起摆出来：
 * 只有 3 场时那格读作"3 场的和"，不是"2024 年以来的和"。
 *
 * ⚠️ 这一格刻意不用 `Panel`：`Panel` 见 `available:false` 就套
 * 「本地计算尚未覆盖该项」那句 —— 那是给 HTTP 404（接口没实现）用的措辞。
 * 这里是**端点实现了、表里还没有场次**，两种"没有"必须长得不一样。
 */

const yi = (v: number | null | undefined) => (v == null ? "—" : num(v, 2));

export function SectorFlowCumulative() {
  const [nonce, refresh] = useNonce();
  const state = useProbe<SectorFlowStored>("/market/sector-flow", nonce);
  const sort = useTableSort();
  const [page, setPage] = useState(1);
  const d: SectorFlowStored | null = state.kind === "ok" ? state.data : null;
  const banner = d ? "" : describeState(state);
  const rows = d?.rows ?? [];
  const size = d?.window?.top_default ?? 50;
  const pages = Math.max(1, Math.ceil(rows.length / size));
  const at = Math.min(page, pages);
  /** 列头悬停文字读后端那一份 `definitions`（口径句子只住在 `duanxian/sector_flow.py`，
   *  页面自己抄一遍就会漂）。后端没交时才用兜底句，且兜底句只说"怎么算"、不说新口径。 */
  const def = (k: string, fb = "") => d?.definitions?.[k] ?? fb;

  return (
    <GlassCard>
      <div className="mb-2 flex flex-wrap items-center gap-2">
        {/* 标题不许承诺一个还没有的窗口。这张表 2026-10-06 才建、10-08 才第一次写通，
            库里只有 1 场时「2024 年以来累计」读起来就是假数（用户 10-08 当场指出"累计数肯定是错的"）。
            所以场数与区间进标题，日期由后端 `coverage` 给，不写死。 */}
        <h2 className="text-sm font-bold">
          覆盖以来累计净流入（库里 {d ? dash(d.coverage?.sessions) : "—"} 场
          {d?.coverage?.available ? `：${dash(d.coverage?.from)} ~ ${dash(d.coverage?.to)}` : ""}
          · 窗口累计降序 · 前 {size} · 可翻页）
        </h2>
        <Caliber text={Object.values(d?.calibers ?? {}).join("\n")
          || "净流入 = 同花顺行业榜的「净额」原样落库（流入 − 流出），单位亿元；是大单方向推断口径，不是交易所实测。"} />
        <button type="button" onClick={refresh}
          className="ml-auto flex items-center gap-1 rounded border border-border px-2 py-0.5 text-[11px] hover:bg-muted/50">
          <RefreshCw className="h-3 w-3" /> 重新取数
        </button>
      </div>
      {/* 只有"库里的场数撑不起这个窗"这一句留在外面常驻（缺料声明不许折进收起的框），
          其余"怎么读、怎么排、字段是什么"都收进下面的通用说明框（用户 2026-10-15：默认收起，点开才看）。 */}
      {d?.coverage?.available && (d.coverage.sessions ?? 0) < 60 && (
        <p className="mb-2 text-[11px] leading-relaxed text-warning">
          库里只有 {dash(d.coverage.sessions)} 场，远不满 3 个月的窗 —— 两列累计是这几场的和，
          读的不是「这三个月」。场数在列名与列里都写着，窗口满没满看标题那格。
        </p>
      )}

      {banner && <p className="mb-2 text-[12px] leading-relaxed text-warning">{banner}</p>}

      {d && (
        <>
          <Row label="库里覆盖" value={d.coverage?.available
            ? `${dash(d.coverage.sessions)} 场（${dash(d.coverage.from)} ~ ${dash(d.coverage.to)}）—— 表 2026-10-06 才建，起点由表里最早那天决定`
            : "0 场（这张表还没有任何一场）"} />
          <Row label="两个窗口" value={`「覆盖以来」自 ${dash(d.window?.since)} 起 · 「近 3 个月」${dash(d.window?.recent_from)} ~ ${dash(d.window?.recent_to)}`} />
          <Row label="窗口累计为正" value={d.available === true
            ? `${dash(d.positive_recent)} 个行业（共 ${rows.length} 个有行）` : "—"} />
          {/* 加权那一维的来源与覆盖：不写这行，"占流通市值 %"就成了一个不知道分母从哪来的比。 */}
          <Row label="加权分母" value={d.market_value?.as_of
            ? `${dash(d.market_value.covered)}/${dash(d.market_value.industries)} 个行业拼得出市值，取自 ${dash(d.market_value.as_of)} 那一场快照（${dash(d.market_value.basis)}）`
            : "市值这一维没读回来 —— 加权那两列会是 —，不是这些行业没有流入"}
            tone={d.market_value?.as_of ? undefined : "warn"} />
          {/* 板块名点不点得开：这一批（同花顺榜单当日那 90 个）与整张对表两个分母分开念，
              只念全场那个会让人以为这张表缺了一大片链接 */}
          <Row label="东财板块页" value={d.board_link?.available
            ? `这一榜 ${dash(d.board_link.rows_linked)}/${dash(d.board_link.rows_total)} 个行业名能点开；`
              + `整张对表 ${dash(d.board_link.dict_linked)}/${dash(d.board_link.dict_total)}（对表 ${dash(d.board_link.tried_at)}）`
            : "对表读不到，行业名一律纯文本（不猜地址）"}
            tone={d.board_link?.available ? undefined : "warn"} />
          {/* 告警类一律摆在收起的说明框外面（折进去等于没写） */}
          <Caveats items={d.caveats} />

          {d.available === false && (
            <p className="mt-2 whitespace-pre-line rounded-lg border border-warning/40 bg-warning/10 p-2.5 text-[11px] leading-relaxed text-warning">
              {`${plain(d.reason ?? "这一格读不到（不是没有资金流，是聚合那一步没返回内容）。")}\n`}
              今天也补不上：非交易日采集步自己拒写（10-01~10-07 休市），下一格要等开市那天收盘后加工链跑一次。
              现在唯一有长历史的是份额口径（「份额轮动 · 本地逐日」那张榜，读 analytics.sector_flow_trend，
              1,253 场、2021-08 起）—— 它回答「钱相对往哪儿走」，不回答「净流入多少亿」，两者不许混读。
            </p>
          )}

          {rows.length > 0 && (
            <>
              <div className="mt-2 overflow-x-auto">
                <table className="w-full text-[11px]">
                  <thead><tr className="text-left text-muted-foreground">
                    <SortHeader label="行业" sortKey="sector_name" dir={sort.dirOf("sector_name")} onSort={sort.toggle}
                      title={"同花顺行业榜那一档的行业名（与上面热度榜的通达信体系是两套，不互换）。"
                        + "能对上的就是一个链接：点开是东方财富的同名行业板块页（对表只认同类精确同名，"
                        + "其次差一个级别后缀，比如「油气开采」对东财「油气开采Ⅱ」）；对不上的就是纯文本，不猜地址"} />
                    <SortHeader label="窗口累计(亿)" sortKey="net_sum_recent" dir={sort.dirOf("net_sum_recent")} onSort={sort.toggle}
                      title={`按日历往前推 3 个月的窗口（不是最近 60 场），库里真落了几场就加几场 —— 现在 ${dash(d?.coverage?.sessions)} 场（${dash(d?.window?.recent_from)} ~ ${dash(d?.window?.recent_to)}）；旁边那列是窗口里落库的场数`} />
                    {/* 加权那一列排在"和"后面：用户 10-15 点名要按体量折算。两列并排才看得出差在 ——
                        10-15 实测 银行 +58.55 亿只占流通市值 0.053%，影视院线 +29.22 亿是 1.50%。 */}
                    <SortHeader label="占流通市值 %" sortKey="net_pct_float_recent" dir={sort.dirOf("net_pct_float_recent")} onSort={sort.toggle}
                      title={def("net_pct_float_recent", "窗口累计净流入 ÷ 行业流通市值")
                        + `。${d.calibers?.weight_denominator ?? ""}`} />
                    <SortHeader label="日均(亿/天)" sortKey="net_avg_recent" dir={sort.dirOf("net_avg_recent")} onSort={sort.toggle}
                      title={def("net_avg_recent", "窗口累计 ÷ 落库天数")} />
                    <SortHeader label="为正天数" sortKey="pos_days_recent" dir={sort.dirOf("pos_days_recent")} onSort={sort.toggle}
                      title={def("pos_days_recent", "窗口内净流入为正的天数")
                        + `；分母是右边的落库场数。${d.calibers?.pos_days ?? ""}`} />
                    <SortHeader label="窗口累计涨跌 %" sortKey="chg_recent_pct" dir={sort.dirOf("chg_recent_pct")} onSort={sort.toggle}
                      title={def("chg_recent_pct", "窗口内逐日涨跌幅复利累计")
                        + `；复利用了多少天：读这一行右边的「涨跌天数」`} />
                    <SortHeader label="涨跌天数" sortKey="chg_days" dir={sort.dirOf("chg_days")} onSort={sort.toggle}
                      title={def("chg_days", "上面那个复利真用了几天 —— 小于落库场数说明有些天源里没交涨跌幅，缺的天不按 0 算")} />
                    <SortHeader label="末场涨跌 %" sortKey="pct_last" dir={sort.dirOf("pct_last")} onSort={sort.toggle}
                      title={`末场（${dash(d?.coverage?.to)}）当日行业涨跌幅，不是此刻现抓`} />
                    <SortHeader label="流入合计(亿)" sortKey="inflow_sum_recent" dir={sort.dirOf("inflow_sum_recent")} onSort={sort.toggle}
                      title={def("inflow_sum_recent", "窗口内各天流入相加，不抵掉流出")} />
                    <SortHeader label="流出合计(亿)" sortKey="outflow_sum_recent" dir={sort.dirOf("outflow_sum_recent")} onSort={sort.toggle}
                      title={def("outflow_sum_recent", "窗口内各天流出相加（正数表示流出的量）")} />
                    <SortHeader label="流通市值(亿)" sortKey="float_mv_yi" dir={sort.dirOf("float_mv_yi")} onSort={sort.toggle}
                      title={`行业成分流通市值合计（亿）。${d.calibers?.weight_denominator ?? ""}`} />
                    <SortHeader label="成分(有价/在册)" sortKey="mv_priced" dir={sort.dirOf("mv_priced")} onSort={sort.toggle}
                      title={def("mv_members / mv_priced", "在册成分数 / 其中今天有市值快照的只数 —— 只数差得多时市值分母偏小")} />
                    <SortHeader label="覆盖以来累计(亿)" sortKey="net_sum_since" dir={sort.dirOf("net_sum_since")} onSort={sort.toggle}
                      title={`自 ${d.window?.since ?? "—"} 起 —— 能算多前取决于表里最早那天，不是 2024 年以来的全量；`
                        + `同一行折算成流通市值是 ${def("net_pct_float_since", "把分子换成「覆盖以来」的累计再除一次")}`} />
                    <SortHeader label="近3月场数" sortKey="days_recent" dir={sort.dirOf("days_recent")} onSort={sort.toggle} />
                    <SortHeader label="覆盖场数" sortKey="days_since" dir={sort.dirOf("days_since")} onSort={sort.toggle}
                      title="不满窗的那批不与满窗的混读：两个累计的分母不同" />
                    <SortHeader label="末场净流入(亿)" sortKey="net_last" dir={sort.dirOf("net_last")} onSort={sort.toggle} />
                    <SortHeader label="末场日期" sortKey="net_last_day" dir={sort.dirOf("net_last_day")} onSort={sort.toggle} />
                  </tr></thead>
                  <tbody>
                    {sort.apply(rows).slice((at - 1) * size, at * size).map(r => (
                      <tr key={r.sector_name} className="border-t border-border/40">
                        <td className="px-1.5 py-1 whitespace-nowrap">
                          <SectorLink name={r.sector_name} code={r.em_board_code} emName={r.em_board_name}
                            sectorType="行业" />
                        </td>
                        <td className={cn("px-1.5 py-1 font-mono tabular-nums", pctColor(r.net_sum_recent))}>
                          {r.net_sum_recent == null ? "—" : `${r.net_sum_recent > 0 ? "+" : ""}${yi(r.net_sum_recent)}`}
                        </td>
                        <td className={cn("px-1.5 py-1 font-mono font-semibold tabular-nums", pctColor(r.net_pct_float_recent))}
                          title={`累计 ${yi(r.net_sum_recent)} 亿 ÷ 流通市值 ${yi(r.float_mv_yi)} 亿`}>
                          {r.net_pct_float_recent == null ? "—" : `${r.net_pct_float_recent > 0 ? "+" : ""}${num(r.net_pct_float_recent, 3)}%`}
                        </td>
                        <td className={cn("px-1.5 py-1 font-mono tabular-nums", pctColor(r.net_avg_recent))}>{yi(r.net_avg_recent)}</td>
                        <td className="px-1.5 py-1 font-mono tabular-nums">
                          {dash(r.pos_days_recent)}<span className="text-muted-foreground">/{dash(r.days_recent)}</span>
                        </td>
                        <td className={cn("px-1.5 py-1 font-mono tabular-nums", pctColor(r.chg_recent_pct))}>
                          {r.chg_recent_pct == null ? "—" : `${r.chg_recent_pct > 0 ? "+" : ""}${num(r.chg_recent_pct, 2)}%`}
                        </td>
                        <td className="px-1.5 py-1 font-mono tabular-nums text-muted-foreground">{dash(r.chg_days)}</td>
                        <td className={cn("px-1.5 py-1 font-mono tabular-nums", pctColor(r.pct_last))}>
                          {r.pct_last == null ? "—" : `${r.pct_last > 0 ? "+" : ""}${num(r.pct_last, 2)}%`}
                        </td>
                        <td className="px-1.5 py-1 font-mono tabular-nums text-emerald-600 dark:text-emerald-400">{yi(r.inflow_sum_recent)}</td>
                        <td className="px-1.5 py-1 font-mono tabular-nums text-destructive">{yi(r.outflow_sum_recent)}</td>
                        <td className="px-1.5 py-1 font-mono tabular-nums text-muted-foreground">{yi(r.float_mv_yi)}</td>
                        <td className="px-1.5 py-1 font-mono tabular-nums text-muted-foreground">
                          {dash(r.mv_priced)}<span>/{dash(r.mv_members ?? r.firms_last)}</span>
                        </td>
                        <td className={cn("px-1.5 py-1 font-mono tabular-nums", pctColor(r.net_sum_since))}>
                          {r.net_sum_since == null ? "—" : `${r.net_sum_since > 0 ? "+" : ""}${yi(r.net_sum_since)}`}
                        </td>
                        <td className="px-1.5 py-1 font-mono tabular-nums text-muted-foreground">{dash(r.days_recent)}</td>
                        <td className="px-1.5 py-1 font-mono tabular-nums text-muted-foreground">{dash(r.days_since)}</td>
                        <td className={cn("px-1.5 py-1 font-mono tabular-nums", pctColor(r.net_last))}>
                          {r.net_last == null ? "—" : `${r.net_last > 0 ? "+" : ""}${yi(r.net_last)}`}
                        </td>
                        <td className="px-1.5 py-1 font-mono whitespace-nowrap text-muted-foreground">{dash(r.net_last_day)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>

              <div className="mt-2 flex flex-wrap items-center gap-2 text-[11px]">
                <button type="button" disabled={at <= 1} onClick={() => setPage(at - 1)}
                  className="rounded border border-border px-2 py-0.5 disabled:opacity-40">上一页</button>
                <span className="text-muted-foreground">第 {at} / {pages} 页 · 每页 {size} 个行业（共 {rows.length} 个）</span>
                <button type="button" disabled={at >= pages} onClick={() => setPage(at + 1)}
                  className="rounded border border-border px-2 py-0.5 disabled:opacity-40">下一页</button>
                {pages > 1 && (
                  <span className="flex flex-wrap gap-1">
                    {Array.from({ length: pages }, (_, i) => i + 1).map(p => (
                      <button key={p} type="button" onClick={() => setPage(p)} aria-pressed={p === at}
                        className={cn("rounded px-1.5 py-0.5", p === at ? "bg-primary/15 text-primary" : "bg-muted/50 text-muted-foreground hover:text-foreground")}>
                        {p}
                      </button>
                    ))}
                  </span>
                )}
              </div>
              <p className="mt-1 text-[10px] leading-relaxed text-muted-foreground">
                列很多，横向要滚动；没点列头时是后端榜序（窗口累计由大到小）。
              </p>
            </>
          )}

          {/* 说明与字段契约收进通用说明框（用户 2026-10-15：默认收起、点开才看）。
              ⚠️ 三类东西**刻意留在框外**：不满窗的告警、`CaveatText`（覆盖以来只有 N 场）、
              后端的 `caveats`（市值分母时点／拼不出市值／复利用了几天）—— 收进折叠框等于没写。 */}
          <ExplainBox title="这一张表怎么读 · 每一列是什么 · 字段契约">
            <p>默认序 = 窗口累计由大到小（后端给的序，负数排在后面，翻到最后一页是被抽干的那些）。
              翻页翻的是后端交出的全量（接口不按页截断），所以点列头排序是排全量再取这一页 ——
              不是只在前若干行里换顺序。</p>
            <p className="mt-2">加权与涨跌幅这两族是 10-15 加的。一句话版：
              <b>「占流通市值 %」把"多少亿"折成"相对这个盘子有多大"</b>——10-15 实测银行窗口累计 +58.55 亿只占流通市值
              0.053%，影视院线 +29.22 亿是 1.50%；只看"和"会把大板块天然排在前面，那不是它更强。</p>
            <p className="mt-2">{d.calibers?.weight ?? ""}</p>
            <p className="mt-2">{d.calibers?.chg ?? ""}</p>
            <p className="mt-2">{d.calibers?.long_history ?? ""}</p>
            <p className="mt-2">{d.calibers?.classification ?? ""}</p>
            {/* 字段契约也收在这儿（用户点名的"这些说明文字"里最长的一段）。
                真正会改变读法的三句留在框外：不满窗告警、`CaveatText`、后端 `caveats`。 */}
            <div className="mt-2">
              <Contract path="GET /api/market/sector-flow" source="duanxian/sector_flow.aggregate（两个窗口的口径只住那一份）" fields={[
                ["rows[]{sector_name, net_sum_recent, net_sum_since, days_recent, days_since, net_last, net_last_day}",
                  "两个窗口的累计与各自的分母（场数）；末场那一列不是此刻现抓"],
                ["rows[]{net_pct_float_recent, net_avg_recent, pos_days_recent, chg_recent_pct, chg_days, inflow_sum_recent, outflow_sum_recent, float_mv_yi, total_mv_yi}",
                  "10-15 新加的加权与涨跌幅族；分母/天数/缺天都随行交出，页面不补数"],
                ["coverage{sessions, from, to}", "能算多前由表里最早那天决定 —— 缺料要可见，不冒充 2024 年以来"],
                ["market_value{as_of, covered, industries, basis, missing}", "加权分母取自哪一场、90 个行业里拼得出几个、缺的是哪几个"],
                ["window{since, recent_from, recent_to, top_default}", "窗口起点与每页档位（默认 50，翻页在页面）"],
                ["calibers", "口径文字由后端交出，界面不自己抄一份"],
              ]} />
            </div>
          </ExplainBox>
          <Definitions map={d.definitions} />
          <CaveatText text={d.coverage?.available
            ? `「覆盖以来累计」现在只有 ${dash(d.coverage.sessions)} 场：那是这 ${dash(d.coverage.sessions)} 场的和，不是 2024 年以来的和。每天收盘后由加工链的 sectorflow 步续一格，攒到什么时候就读到什么时候。`
            : undefined} />
          <Provenance bits={["pg market_data.sector_fund_flow_daily（同花顺行业榜逐日落库，c22 采集）",
                            d.market_value?.basis && `市值：${d.market_value.basis} @ ${dash(d.market_value.as_of)}`]} />
        </>
      )}
    </GlassCard>
  );
}
