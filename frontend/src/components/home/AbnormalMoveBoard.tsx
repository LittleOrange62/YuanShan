import { RefreshCw, TriangleAlert } from "lucide-react";
import { useState } from "react";
import { GlassCard } from "@/components/ui/GlassCard";
import { Caliber } from "@/components/ui/Caliber";
import { SortHeader } from "@/components/ui/SortHeader";
import { CaliberFold, ExplainBox, QuietFold } from "@/components/ui/DataPanel";
import { StockLink } from "@/components/ui/StockLink";
import { useNonce, useProbe } from "@/lib/probe";
import { PAGE_SIZES, canStep, pageSentence, slicePage } from "@/lib/paging";
import { useTableSort } from "@/lib/useSort";
import { STOCK_ROW_HINT, useStockRowInteraction } from "@/lib/useStockRowInteraction";
import { dash, describeState } from "@/lib/datastate";
import { cn } from "@/lib/utils";
import {
  bandText, boardSpanText, devCell, forwardHeadline, hotHeadline, moveStatusText,
  nextDayState, pctText, ptsText, priceText, recentHeadline, severeState, truncationNote,
  type AbnormalMove, type AbnForwardRow, type AbnHotRow, type AbnRecentRow,
} from "@/lib/abnormalMove";

/**
 * 首页「短线情绪」下面的异动提醒（用户 2026-10-15 点名三块）。
 *
 * 判据、落库、每日加工段都不在这一层（只住 `app/compute/abnormal_move.py`，
 * 加工段第 15 步 `abnormalmove`）；读法只住 `lib/abnormalMove.ts`，这里只画。
 *
 * 四条界面上不许糊掉的线：
 *  · `severe` 与 `hit_if_limit_up` 都是三态：`null` 是"判不了"，页面单列出来，
 *    绝不并进"没踩到 / 不会触发"的计数里 —— 那正是把缺数说成结论。
 *  · ① 那块问的是"明日涨停会不会**新**踩到一条"。10-27 起这张榜两类并存：会新踩到的、
 *    与今天已踩到某一条而离下一条还有缺口的（旧版把后者整只挡在榜外，用户点名大业股份、
 *    海天瑞声就是这么消失的）—— 哪一类只在「明日涨停会怎样」那一格里逐字念后端交的
 *    `fwd_state_label`，页面不自己拼。第三块（近期严重异动）仍然只列已经踩到的。
 *  · 三条本机做不到的事（没有交易所公告日／明日指数按 0% 假设／北交所不纳入）由后端交在
 *    `caveats`：缺数/截断那一族由 `CaliberFold` 留在屏上，纯口径那几条默认收起（用户 2026-10-21
 *    「描述性内容全部收缩」）。"这不是交易所认定"那句防线不只靠那一条 caveat —— 第三块的榜头
 *    （`recentHeadline`）就在表格上方逐字写着，收起折叠块不会把它一起收掉。
 *  · 「几天几板」复用首页连板榜那一份实现（`duanxian/board_span.py`），读不到时写"没读到"。
 */

const RECENTS = [5, 10, 20];

export function AbnormalMoveBoard() {
  const [nonce, refresh] = useNonce();
  const [recent, setRecent] = useState(10);
  const [hotLimit, setHotLimit] = useState(20);
  // ③ 那张「近期严重异动」行数最多（一场几百只里往回看 10 场），用户 10-22：内容太多 ⇒ 分页。
  // 每页几行与页码是这一块的本地状态；算法与页脚那一句走 `lib/paging.ts` 一处（全站十几块榜各写一份会漂）。
  const [recSize, setRecSize] = useState(PAGE_SIZES[0]);
  const [recPage, setRecPage] = useState(1);
  const st = useProbe<AbnormalMove>("/market/abnormal-move", nonce, { recent, hot_limit: hotLimit });
  const am = st.kind === "ok" ? st.data : null;
  const ri = useStockRowInteraction("异动提醒");
  const fwdSort = useTableSort();
  const hotSort = useTableSort();
  const recSort = useTableSort();
  const recAll = recSort.apply(am?.recent_severe?.rows ?? []);
  const recShown = slicePage(recAll, recPage, recSize);

  // 三张表共用一份交叉高亮：行号必须是跨表连续序号，否则每张表的第 1 行会一起亮。
  const idx = new Map<string, number>();
  for (const r of [...(am?.forward?.rows ?? []), ...(am?.hot?.rows ?? []),
                   ...(am?.recent_severe?.rows ?? [])]) {
    if (!idx.has(r.security_code)) idx.set(r.security_code, idx.size);
  }
  const rowNo = (code: string) => idx.get(code) ?? -1;

  const noDef = (k: string) => (am?.definitions ?? {})[k]
    ?? `后端 definitions 里没有 ${k} 这一条 —— 这一列的含义没有出处，页面不自己编`;
  const Th = (sort: ReturnType<typeof useTableSort>, label: string, key: string, tip?: string) => (
    <SortHeader label={label} sortKey={key} dir={sort.dirOf(key)}
      onSort={() => { ri.clearAll(); sort.toggle(key); }} title={noDef(tip || key)}
      className="whitespace-nowrap px-2 py-1 text-left text-[11px] font-medium" />
  );
  const cell = (i: number, k: string, cls: string, body: React.ReactNode) => (
    <td {...ri.cellProps(i, k, cls)}>{body}</td>
  );
  const nameCell = (r: { security_code: string; name?: string | null }) => cell(
    rowNo(r.security_code), "security_code", "whitespace-nowrap px-2 py-1",
    <>
      <StockLink code={r.security_code} name={r.name} className="font-medium hover:text-primary" />{" "}
      <span className="font-mono text-[10px] text-muted-foreground/60">{r.security_code}</span>
    </>);
  const boardCell = (r: { security_code: string; board?: string | null; is_st?: boolean | null }) => cell(
    rowNo(r.security_code), "board", "whitespace-nowrap px-2 py-1 text-muted-foreground",
    <>
      {dash(r.board)}
      {r.is_st && <span className="ml-1 rounded bg-warning/15 px-1 text-[10px] text-warning">ST</span>}
    </>);
  const severeCell = (i: number, r: AbnHotRow | AbnRecentRow) => cell(i, "status_label", "px-2 py-1",
    <span className={severeState(r).tone === "danger" ? "font-semibold text-primary"
      : severeState(r).tone === "warn" ? "text-warning" : "text-muted-foreground"}>
      {moveStatusText(r)}
    </span>);

  return (
    <GlassCard className="mt-3">
      <div className="mb-1.5 flex flex-wrap items-center gap-2">
        <h3 className="flex items-center gap-1.5 text-sm font-semibold text-muted-foreground">
          <TriangleAlert className="h-4 w-4" /> 异动提醒 · {dash(am?.date) || "资料日待确认"}
        </h3>
        <Caliber text={
          "单日偏离值 = 个股当日涨跌幅 − 对应分类指数当日涨跌幅；累计偏离是区间复利"
          + "（窗口内逐日复利的个股区间涨幅 − 同样复利的指数区间涨幅，10-27 起这把尺）。\n"
          + "异常波动 = 连续 3 个交易日累计偏离 ≥ +20%（主板）／≥ +30%（双创）。\n"
          + "严重异常波动 = 10 日内 4 次（主板）／3 次（双创）同向异常波动，或 10 日累计 ≥ +100%/≤ −50%，"
          + "或 30 日累计 ≥ +200%/≤ −70%。完整口径见表下那句后端原句。"
        } />
        <div className="flex items-center gap-1.5">
          <span className="text-[11px] text-muted-foreground">近期回看</span>
          {RECENTS.map(n => (
            <button key={n} type="button" aria-pressed={recent === n}
              onClick={() => { setRecent(n); ri.clearAll(); }}
              className={cn("rounded-lg px-2 py-1 text-[11px]",
                recent === n ? "bg-primary/20 text-primary" : "text-muted-foreground hover:bg-muted/40")}>
              {n} 场
            </button>
          ))}
        </div>
        <label className="flex items-center gap-1 text-[11px] text-muted-foreground">
          热门股取前
          <select value={hotLimit} onChange={e => { setHotLimit(Number(e.target.value)); ri.clearAll(); }}
            className="rounded bg-muted/40 px-1 py-0.5 text-[11px] text-foreground">
            {[10, 20, 30, 50].map(v => <option key={v} value={v}>{v} 只</option>)}
          </select>
        </label>
        <button type="button" onClick={() => { ri.clearAll(); refresh(); }} title="重新取数"
          className="ml-auto flex items-center gap-1 rounded-lg bg-primary/15 px-2 py-1 text-[11px] font-medium text-primary hover:bg-primary/25">
          <RefreshCw className="h-3.5 w-3.5" /> 重新取数
        </button>
      </div>

      {st.kind === "loading"
        && <p className="text-[11px] text-muted-foreground">取数中…（读库里算好的这一场，不现算判据）</p>}
      {st.kind !== "ok" && st.kind !== "loading" && (
        <p className="rounded-lg border border-warning/50 bg-warning/10 p-2 text-[11px] leading-relaxed text-warning">
          {describeState(st)}
        </p>
      )}

      {/* 这一块原来在"接口通了但后端说这一项本机没数"时**什么都不摆**（上面那支只管 `kind!=="ok"`）——
          现在与全站同一条：整块收成一行，原因原句（含该跑哪条补跑）留在 summary 上。用户 2026-10-22。 */}
      {am && am.available === false && (
        <QuietFold text={describeState(st)} />
      )}

      {am && am.available !== false && (
        <>
          <div className="mb-2 grid grid-cols-2 gap-2 md:grid-cols-4">
            {[
              { label: "这一场母体", value: `${dash(am.summary?.universe)} 只`, hint: "筛出来的提醒榜，不是全市场" },
              { label: "已踩严重异动", value: `${dash(am.summary?.severe)} 只`, hint: "涨向与跌向分开数" },
              { label: "明日涨停可触发", value: `${dash(am.summary?.forward)} 只`,
                hint: "明日涨停会新踩到一条的（全场聚合）。今天已踩到某一条的票也在这张榜里，"
                    + "但没并进这个数 —— 那一行会写它已踩到哪条、离下一条还差多少个点" },
              { label: "判不了", value: `${dash(am.summary?.severe_unknown)} 只`, hint: "窗口根数不够／指数缺线" },
            ].map(c => (
              <div key={c.label} className="rounded-lg border border-border/60 bg-muted/20 px-2 py-1.5" title={c.hint}>
                <div className="text-[11px] text-muted-foreground">{c.label}</div>
                <div className="mt-0.5 font-mono text-base font-bold text-primary">{c.value}</div>
              </div>
            ))}
          </div>

          {am.date_fell_back && (
            <p className="mb-1.5 rounded-lg border border-warning/50 bg-warning/10 p-2 text-[11px] leading-relaxed text-warning">
              要的那一天这一版没算过，这里退成了库里最近的一场 {dash(am.latest_date)} —— 不是你把日期选错了，
              也不是那一天真的没有异动。
            </p>
          )}
          <p className="text-[11px] leading-relaxed text-muted-foreground">
            判据版本 {dash(am.spec_version)}，这一场算于 {dash(am.summary?.calculated_at)}；
            {dash(am.summary?.scope)}。
          </p>
          {truncationNote(am) && (
            <p className="mt-1 rounded-lg border border-warning/50 bg-warning/10 p-2 text-[11px] leading-relaxed text-warning">
              {truncationNote(am)}
            </p>
          )}
          {/* 用户 2026-10-21「描述性内容全部收缩」：这一屏的字段义与取数口径原句收进同一处折叠块，
              缺数/截断那一类仍由 CaliberFold 自己留在屏上（分流只住 lib/caveatKind.ts） */}
          {/* 用户 2026-10-22（第二次点名首页密度）：这四条也收 —— 走 `foldAll`。
              收的条件仍然成立：summary 会写「2 条缺数与告警（本场有读不到／判不了的行）」，
              "有东西没读到／判不了"这件事本身还在屏上，只是原句要点开。
              上面那行「判据版本 abn-2，这一场算于 …」不动：那是新鲜度，不是描述。 */}
          <CaliberFold foldAll caveats={am.caveats} definitions={am.definitions}
            notes={[am.caliber ? `取数口径（后端原句，页面未改写）：${am.caliber}` : null]} />

          {/* ① 明日涨幅至涨停会触发严重异动 */}
          <section className="mt-3">
            <h4 className="text-[12px] font-bold text-foreground">
              ① 明日涨幅至涨停会触发严重异动
              <span className="ml-1 font-normal text-muted-foreground">
                {(am.forward?.rows ?? []).length} 只（明天涨停会新踩到一条的在前，其余是今天已踩到某一条的；
                两类各按还差多少涨幅从小到大）
              </span>
            </h4>
            <p className="mt-0.5 rounded-lg border border-border/60 bg-muted/20 px-2 py-1 text-[11px] leading-relaxed">
              {forwardHeadline(am).text}
            </p>
            {(am.forward?.rows ?? []).length === 0 ? (
              <p className="mt-1 text-[11px] leading-relaxed text-muted-foreground">
                这一场没有一只在明日涨停时被踩到 —— 这是算过了、没到线，与上面那一格「判不了」是两件事。
              </p>
            ) : (
              <div className="mt-1 overflow-x-auto">
                <table className="w-full text-[11px]">
                  <thead>
                    <tr className="text-left text-muted-foreground">
                      {Th(fwdSort, "名称与代码", "security_code")}{Th(fwdSort, "板块", "board")}
                      {Th(fwdSort, "今日涨幅", "pct_change")}{Th(fwdSort, "明日涨停幅度", "band_pct", "band_next")}
                      {Th(fwdSort, "明日涨停价", "limit_price_next")}
                      {Th(fwdSort, "触发所需涨幅", "trigger_pct")}{Th(fwdSort, "触发价", "trigger_price")}
                      {Th(fwdSort, "距 100 还差", "need_dev_to_100")}{Th(fwdSort, "距 200 还差", "need_dev_to_200")}
                      {Th(fwdSort, "涨停后 10 日累计", "dev_10_if_limit")}
                      {Th(fwdSort, "明日涨停会怎样", "fwd_state_label", "fwd_state")}
                    </tr>
                  </thead>
                  <tbody>
                    {fwdSort.apply(am.forward?.rows ?? []).map((r: AbnForwardRow) => {
                      const i = rowNo(r.security_code);
                      return (
                        <tr key={r.security_code} {...ri.rowProps(i, r.security_code)}>
                          {nameCell(r)}
                          {boardCell(r)}
                          {cell(i, "pct_change", "px-2 py-1 font-mono tabular-nums", pctText(r.pct_change))}
                          {cell(i, "band_pct", "px-2 py-1 font-mono tabular-nums", bandText(r))}
                          {cell(i, "limit_price_next", "px-2 py-1 font-mono tabular-nums",
                            priceText(r.limit_price_next))}
                          {cell(i, "trigger_pct", "px-2 py-1 font-mono font-semibold tabular-nums text-primary",
                            <span title={'明日要涨这么多个点才踩到下一条。它不等于旁边那两列的缺口：'
                              + '累计走区间复利，已经涨得多的票，明日每涨 1 个点会把那一段放大一层，'
                              + '所以要的涨幅比缺口小'}>
                              {r.trigger_pct == null ? "—" : `${r.trigger_pct.toFixed(2)} 个点`}
                            </span>)}
                          {cell(i, "trigger_price", "px-2 py-1 font-mono tabular-nums", priceText(r.trigger_price))}
                          {cell(i, "need_dev_to_100", "px-2 py-1 font-mono tabular-nums", ptsText(r.need_dev_to_100))}
                          {cell(i, "need_dev_to_200", "px-2 py-1 font-mono tabular-nums", ptsText(r.need_dev_to_200))}
                          {cell(i, "dev_10_if_limit", "px-2 py-1 font-mono tabular-nums",
                            <span title="明日指数按 0% 假设（见上方 caveat index_assumed_zero）">
                              {ptsText(r.dev_10_if_limit)}
                            </span>)}
                          {cell(i, "fwd_state_label", "px-2 py-1 text-muted-foreground",
                            dash(r.fwd_state_label))}
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            )}
          </section>

          {/* ② 热门股偏离值 */}
          <section className="mt-3">
            <h4 className="text-[12px] font-bold text-foreground">
              ② 热门股偏离值
              <span className="ml-1 font-normal text-muted-foreground">前 {dash(am.hot?.limit)} 只</span>
            </h4>
            <p className="mt-0.5 text-[11px] leading-relaxed text-muted-foreground">{hotHeadline(am)}</p>
            <div className="mt-1 overflow-x-auto">
              <table className="w-full text-[11px]">
                <thead>
                  <tr className="text-left text-muted-foreground">
                    {Th(hotSort, "名称与代码", "security_code")}{Th(hotSort, "板块", "board")}
                    {Th(hotSort, "几天几板", "board_span.days", "board_span")}{Th(hotSort, "今日涨幅", "pct_change")}
                    {Th(hotSort, "3 日累计偏离", "dev_3")}{Th(hotSort, "10 日累计偏离", "dev_10")}
                    {Th(hotSort, "30 日累计偏离", "dev_30")}
                    {Th(hotSort, "同向次数 涨/跌", "up_cnt_10")}{Th(hotSort, "严重异动", "status_label", "severe")}
                    {Th(hotSort, "凭什么进这一屏", "universe_by_label", "universe_by")}
                  </tr>
                </thead>
                <tbody>
                  {hotSort.apply(am.hot?.rows ?? []).map((r: AbnHotRow) => {
                    const i = rowNo(r.security_code);
                    return (
                      <tr key={r.security_code} {...ri.rowProps(i, r.security_code)}>
                        {nameCell(r)}
                        {boardCell(r)}
                        {cell(i, "board_span.days", "whitespace-nowrap px-2 py-1 font-mono", boardSpanText(r))}
                        {cell(i, "pct_change", "px-2 py-1 font-mono tabular-nums", pctText(r.pct_change))}
                        {cell(i, "dev_3", "px-2 py-1 font-mono tabular-nums", devCell(r.dev_3, r.dev_3_complete))}
                        {cell(i, "dev_10", "px-2 py-1 font-mono font-semibold tabular-nums",
                          devCell(r.dev_10, r.dev_10_complete))}
                        {cell(i, "dev_30", "px-2 py-1 font-mono tabular-nums", devCell(r.dev_30, r.dev_30_complete))}
                        {cell(i, "up_cnt_10", "px-2 py-1 font-mono tabular-nums",
                          <span title="近 10 场里同向异常波动出现几次：主板 4 次、双创 3 次即踩到严重异动的一条">
                            {dash(r.up_cnt_10)} / {dash(r.down_cnt_10)}
                          </span>)}
                        {severeCell(i, r)}
                        {cell(i, "universe_by_label", "px-2 py-1 text-muted-foreground",
                          dash(r.universe_by_label || r.universe_by))}
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          </section>

          {/* ③ 近期严重异动（含次日涨幅与停牌） */}
          <section className="mt-3">
            <h4 className="text-[12px] font-bold text-foreground">
              ③ 近期严重异动
              <span className="ml-1 font-normal text-muted-foreground">
                往回看 {dash(am.recent_severe?.sessions)} 场 · 次日涨幅与停牌
              </span>
            </h4>
            <p className="mt-0.5 text-[11px] leading-relaxed text-muted-foreground">{recentHeadline(am)}</p>
            {(am.recent_severe?.rows ?? []).length === 0 ? (
              <p className="mt-1 text-[11px] leading-relaxed text-muted-foreground">
                这一段里按公开判据没算出踩到的票 —— 说的是这一版判据下没到线，
                不代表交易所没有认定过（本机没有公告日那份数据）。
              </p>
            ) : (
              <div className="mt-1 overflow-x-auto">
                <table className="w-full text-[11px]">
                  <thead>
                    <tr className="text-left text-muted-foreground">
                      {Th(recSort, "异动日期", "trade_date")}{Th(recSort, "名称与代码", "security_code")}
                      {Th(recSort, "板块", "board")}{Th(recSort, "异动状态", "status_label", "severe_how")}
                      {Th(recSort, "当日涨幅", "pct_change")}{Th(recSort, "10 日累计偏离", "dev_10")}
                      {Th(recSort, "同向次数 涨/跌", "up_cnt_10")}{Th(recSort, "次日涨幅 / 停牌", "next_pct", "next_halted")}
                    </tr>
                  </thead>
                  <tbody>
                    {/* 排完序再切片（`slicePage` 一处）：先截断后排序会让每一页各自为政地换口径 */}
                    {recShown.map((r: AbnRecentRow) => {
                      const i = rowNo(r.security_code);
                      const nd = nextDayState(r);
                      return (
                        <tr key={`${r.trade_date}-${r.security_code}`} {...ri.rowProps(i, r.security_code)}>
                          {cell(i, "trade_date", "whitespace-nowrap px-2 py-1 font-mono", dash(r.trade_date))}
                          {nameCell(r)}
                          {boardCell(r)}
                          {severeCell(i, r)}
                          {cell(i, "pct_change", "px-2 py-1 font-mono tabular-nums", pctText(r.pct_change))}
                          {cell(i, "dev_10", "px-2 py-1 font-mono tabular-nums", ptsText(r.dev_10))}
                          {cell(i, "up_cnt_10", "px-2 py-1 font-mono tabular-nums",
                            `${dash(r.up_cnt_10)} / ${dash(r.down_cnt_10)}`)}
                          {cell(i, "next_pct", "whitespace-nowrap px-2 py-1 font-mono tabular-nums",
                            <span className={nd.tone === "danger" ? "text-primary"
                              : nd.tone === "warn" ? "text-warning"
                                : nd.tone === "ok" ? "text-success" : "text-muted-foreground"}>
                              {nd.text}
                            </span>)}
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            )}
            {/* 分页那一行：共多少行必须一起说（不然"这一页 25 行"会被读成"这一段只有 25 只踩过"）。
                换页清行选中 —— 行号记的是排序后的渲染数组，不跟着清会串页。 */}
            {recAll.length > 0 && (
              <div className="mt-1 flex flex-wrap items-center gap-1.5 text-[11px] text-muted-foreground">
                <span title={pageSentence(recAll.length, recSize, recPage)}>
                  {pageSentence(recAll.length, recSize, recPage)}
                </span>
                <span className="ml-1">每页</span>
                {PAGE_SIZES.map(n => (
                  <button key={n} type="button" aria-pressed={recSize === n}
                    onClick={() => { ri.clearAll(); setRecSize(n); setRecPage(1); }}
                    className={cn("rounded-lg border px-1.5 py-0.5",
                      recSize === n ? "border-primary/70 bg-primary/10 font-medium text-primary"
                        : "border-border hover:bg-muted/50")}>
                    {n} 行
                  </button>
                ))}
                {canStep(recPage, recSize, recAll.length, -1) && (
                  <button type="button" onClick={() => { ri.clearAll(); setRecPage(p => p - 1); }}
                    className="rounded-lg border border-border px-1.5 py-0.5 hover:bg-muted/50">上一页</button>
                )}
                {canStep(recPage, recSize, recAll.length, 1) && (
                  <button type="button" onClick={() => { ri.clearAll(); setRecPage(p => p + 1); }}
                    className="rounded-lg border border-border px-1.5 py-0.5 hover:bg-muted/50">下一页</button>
                )}
              </div>
            )}
          </section>

          {/* 弹窗被拦这句不许折进说明框：收着等于没说 */}
          {ri.popHint && <p role="status" className="mt-1 text-[11px] leading-relaxed text-warning">{ri.popHint}</p>}

          <ExplainBox title="这三块为什么分开摆 · 这一张表怎么点 · 双击去哪里">
            <p>
              ① 是还没发生的：它只说明涨到那个位置时累计偏离会过线，不说明它会不会涨到那里。
              这张榜里两类并存 —— 明天涨停就新踩到一条的，和今天已经踩到某一条、明天涨停还够不着
              下一条的（后一类旧版整只挡在榜外，10-27 起放回来并在「明日涨停会怎样」那一格点名它已踩到哪条）。
              ② 是正在走得远的：用 10 日累计偏离这把尺子排出来的热门股，走得多不代表踩线。
              ③ 是已经算出踩到的：连同次日涨幅与停牌一起看（严重异动之后的停牌核查，
              在库里就是"指数那天有这根线、这只票没有日线行"那种形状）。
              三块的母体都是筛出来的提醒榜（一场约几百只），不是全市场五千多只的快照 ——
              这里读不到某只票，不等于它没有偏离值。
            </p>
            <p className="mt-1">{STOCK_ROW_HINT}</p>
          </ExplainBox>
        </>
      )}
    </GlassCard>
  );
}
