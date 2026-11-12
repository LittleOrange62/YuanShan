import { RefreshCw } from "lucide-react";
import { useState } from "react";
import { GlassCard } from "@/components/ui/GlassCard";
import { Caliber } from "@/components/ui/Caliber";
import { SortHeader } from "@/components/ui/SortHeader";
import { CaliberFold, ExplainBox, Panel } from "@/components/ui/DataPanel";
import { CacheLine } from "@/components/ui/DataPanel";
import { StockLink } from "@/components/ui/StockLink";
import { DocDrawer, DrawerSection } from "@/components/ui/DocDrawer";
import { NoticeList } from "@/components/ui/NoticeBar";
import { useNonce, useProbe } from "@/lib/probe";
import { useTableSort } from "@/lib/useSort";
import { STOCK_ROW_HINT, useStockRowInteraction, useCellSelection, type CellStore } from "@/lib/useStockRowInteraction";
import { usePageAskAi } from "@/lib/workspace/pageAskAi";
import { dash } from "@/lib/datastate";
import { plain } from "@/lib/agent";
import { cn } from "@/lib/utils";
import { StockDossier } from "@/components/quant/StockDossier";
import { Link } from "react-router-dom";
import {
  charLevelText, headlineCards, hitSummary, indicatorCell, indicatorColumns, keepText,
  stockRows, trendSummary, type OutlookPayload,
} from "@/lib/tomorrowOutlook";

/**
 * 明日展望（用户 2026-11-05 点名：「在量化选股中增加一个明日展望，就是把任务推送 10 里面的内容展示出来，
 * 并针对提到的个股，把这些个股的各类指标全部展示出来」）。
 *
 * 三条界面上不许糊掉的红线：
 *  · **这一屏与 19:00 发出去的那一条是同一张榜**：两张榜的判据只住 `duanxian/tomorrow_outlook.py` 一处，
 *    那一炉 import 同一份；屏下还摆今天真正发的原文（`pushed.body`）供当场比对。
 *  · **绝对价格那 15 项不给名次**：`ma5` 排全场第 8 名只说明它贵。给名次＝让屏幕说假话。
 *  · **缺值一律 `—`**：某只票当日没有那一项的行 ⇒ 判不了，不是 0、也不是"垫底"。
 * 判读全在 `lib/tomorrowOutlook.ts`（可不打 DOM 测），这里只画。
 */

export function TomorrowOutlook() {
  const [nonce, refresh] = useNonce();
  const [date, setDate] = useState("");
  const [expand, setExpand] = useState(true);
  const cell = useStockRowInteraction("明日展望");
  // ⚠️ 两张榜各自一份排序状态：它们列名有重合（名称与代码／板块／股性）但**是两张不同的榜**，
  // 共用一份等于"点一、的板块，二、那列表头也跟着亮、行也跟着换序"（11-05 浏览器实测抓到）。
  const hitSort = useTableSort();
  const trendSort = useTableSort();
  const gridSort = useTableSort();
  const state = useProbe<OutlookPayload>("/quant/tomorrow-outlook", nonce,
    { date: date || undefined, expand: expand ? "true" : "false" });

  const cur = state.kind === "ok" && state.data.available ? state.data : null;
  usePageAskAi(cur ? {
    label: `明日展望（${dash(cur.date)}，推送 ⑩ 的那两张榜）`,
    context: [
      `交易日 ${dash(cur.date)}。一、多指标靠前 ${(cur.hits ?? []).length} 只；`
      + `二、趋势最正 ${(cur.trends ?? []).length} 只；两榜都进 ${(cur.overlap ?? []).length} 只。`,
      `口径：家族 ${(cur.params?.family_names ?? []).join("、")} 各取当日前 ${dash(cur.params?.top_n)} 名，`
      + `今日命中 ≥${dash(cur.params?.min_hits)} 项者进一、；近 ${dash(cur.params?.window_sessions)} 场重复上榜一并排。`
      + `二、取趋势管线当日 selected5、收盘在 5 日线上方、站上 ≥${dash(cur.params?.trend_streak_min)} 日者。`
      + `两榜只留股性${keepText(cur.params)}（判不了的照留并点名）。`,
      (cur.hits ?? []).map(r => `${r.name ?? ""}(${r.security_code}) ${r.board ?? ""} ${hitSummary(r)}`
        + ` 最靠前：${(r.top_inds ?? []).join("、") || "—"}${r.consec ? ` · ${r.consec === 1 ? "今封板" : `${r.consec} 连板`}` : ""}`).join("\n"),
      (cur.trends ?? []).map(r => `${r.name ?? ""}(${r.security_code}) ${trendSummary(r)}`).join("\n"),
      "只说明明天盯什么与什么算坏了，不预测涨跌、不给买卖建议。",
    ].join("\n"),
    suggestions: ["这两张榜的判据与群里那条有什么差别", "两榜都进的那只凭什么同时进"],
  } : null);

  return (
    <div>
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <h2 className="text-base font-bold">明日展望</h2>
        <Caliber text={
          "这是每天 19:00 那一炉「推送任务 ⑩」的同一条判据：一、多指标靠前 + 二、趋势最正 + 三、明日观察清单，"
          + "再把名单里每一只票当日全部技术指标摊开。\n判据只住后端一处，屏上与群里必然是一张榜；页面不重算、不改写。"
        } />
        {cur && <DocDrawer title="明日展望">
          <DrawerSection heading="这一屏答什么">
            <p>收盘链与加工链跑完（实测 17:56 落定）之后，库里今天的因子、涨跌停池、连板天梯、趋势管线、股性都齐了 ——
              这一屏读的就是这些已落地的加工结果，一个判据都不现算，也不预测涨跌。
              一、说的是「今天哪些票在很多把尺子下同时靠前」，二、说的是「哪些票的趋势还没走坏」，
              三、说的是明天盯什么、什么算坏了。</p>
          </DrawerSection>
          <DrawerSection heading="两张榜怎么进（参数唯一出处 app/intraday/broadcast_config.json）">
            <p>一、家族 {dash(cur.params?.family_size)} 项（{(cur.params?.family_names ?? []).join("、")}）
              各自取当日前 {dash(cur.params?.top_n)} 名；一只票当日命中 ≥{dash(cur.params?.min_hits)} 项者进榜，
              再按「今日命中项数 → 近 {dash(cur.params?.window_sessions)} 场里重复上榜的场数 → XY 合计名次（越小越前）」排。
              榜最多交 {dash(cur.params?.hits_n)} 只。</p>
            <p>二、趋势管线当日 selected5 且收盘在 5 日线上方、连续站上 ≥{dash(cur.params?.trend_streak_min)} 日，
              按站上天数 → 是否 20 日新高 → 是否封板排，最多 {dash(cur.params?.trend_n)} 只。</p>
            <p>两榜都只留股性{keepText(cur.params)}（用户 11-04 定的口径）；股性判不了的（上市不足 120 场或档位价缺）
              <b className="text-foreground">照留在榜上</b>并点名，那一格是 — 而不是「股性差」。</p>
            <p>家族为什么是这几项、为什么没有 MACD 柱：每一项都是<b className="text-foreground">跨票可比</b>的量纲
              （% / 0-100 位置 / 比值），「靠前」才有一把共同的尺；MACD 柱的量纲是元，按原始值取 top 会退化成「最贵榜」。</p>
          </DrawerSection>
          <DrawerSection heading="逐票全部指标：为什么有的给名次、有的只给值">
            <p>当日在册 35 项全部摊开。<b className="text-foreground">能跨票比的那 19 项</b>
              （单位 %／比值／无量纲）另给「全场第 N 名」；名次规则：{dash(cur.rank_note)}。</p>
            <p>分母是<b className="text-foreground">该项当日有值的只数</b>，每一项不一样（实测 2026-11-05：
              振幅 5,571 只、年线 5,393 只），所以每一格自己带母体只数，别拿一个分母解释所有名次。</p>
            <p><b className="text-foreground">绝对价格那 15 项（单位 元：均线族、MACD 族、均价/中价/最高/最低/ATR）只给值不给名次</b>
              —— 高价股天然靠前，给名次就是让屏幕说一句假话。「涨幅排名」本身就是名次，也不再排第二次。</p>
            <p>排序按值：同一列里名次随值单调，两者是同一个东西的两种写法；缺值那一格永远排在末尾（两个方向都是）。</p>
          </DrawerSection>
          <DrawerSection heading="与「推送任务 ⑩」的关系">
            <p>两张榜的取数原先长在 `ingest/jobs/push/t10_tomorrow_outlook.py` 里，2026-11-05 搬到
              `duanxian/tomorrow_outlook.py`，那一炉反过来 import 这一份 —— 因为同一张榜现在有两个调用方
              （发飞书、上界面），判据写两份迟早变成「屏上八只、群里七只」。
              搬家当晚两边正文逐字比过：1,207 字符一字不差。</p>
            <p>下面那一块「今天发出去的原文」读的是 `analytics.intraday_broadcast` 里已经落库的那一条，
              不重新取数 —— 那样「屏上的名单」与「群里的那条」能当场对得上。</p>
          </DrawerSection>
          <DrawerSection heading="取数口径（后端原句，页面未改写）">
            <p>{plain(cur.caliber)}</p>
          </DrawerSection>
          <DrawerSection heading="这一张表怎么点">
            <p>{STOCK_ROW_HINT}</p>
          </DrawerSection>
        </DocDrawer>}
        <button type="button" onClick={() => { cell.clearAll(); refresh(); }} title="重新取数（跳过 15 分钟缓存重扫一遍）"
          className="ml-auto flex items-center gap-1 rounded-lg bg-primary/15 px-2 py-1 text-[11px] font-medium text-primary hover:bg-primary/25">
          <RefreshCw className="h-3.5 w-3.5" /> 重新取数
        </button>
      </div>

      <Panel title="明日展望 · 两张榜与逐票全部指标" state={state} slowHint="冷跑约 3 秒（指标那张窗口函数最重），15 分钟内复用同一份。"
        controls={
          <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
            <label className="flex items-center gap-1 text-[11px] text-muted-foreground">
              日期：
              <input type="date" value={date} onChange={e => { setDate(e.target.value); cell.clearAll(); }}
                className="w-28 rounded border border-border/60 bg-background px-1 py-0.5 text-[11px]" />
            </label>
            <button type="button" aria-pressed={!expand}
              onClick={() => { setExpand(!expand); cell.clearAll(); }}
              className={cn("rounded-lg px-2 py-1 text-[11px]",
                !expand ? "bg-primary/20 text-primary" : "text-muted-foreground hover:bg-muted/40")}>
              {expand ? "只摆两张榜（不摊指标）" : "把两张榜与全部指标一起摆"}
            </button>
            {(date || !expand) && (
              <button type="button" className="text-muted-foreground hover:text-foreground"
                onClick={() => { setDate(""); setExpand(true); cell.clearAll(); }}>
                回到默认（最近一场、两张榜加指标）
              </button>
            )}
          </div>
        }>
        {d => {
          const cards = headlineCards(d);
          const hits = hitSort.apply(d.hits ?? []);
          const trends = trendSort.apply(d.trends ?? []);
          const rows = stockRows(d);
          const cols = indicatorColumns(rows, d);
          const gridRows = expand ? gridSort.apply(rows) : [];
          const hard = (d.caveats ?? []).filter(c => c.hard);
          return (
            <>
              <div className="mb-2 grid grid-cols-2 gap-2 md:grid-cols-5">
                {cards.map(c => (
                  <GlassCard key={c.label} className="py-2 text-center">
                    <div className="text-[11px] text-muted-foreground" title={c.hint}>{c.label}</div>
                    <div className="mt-0.5 font-mono text-base font-bold text-primary" title={c.hint}>{c.value}</div>
                  </GlassCard>
                ))}
              </div>

              <CacheLine generatedAt={d.generated_at}
                cache={{ saved_at: d.generated_at, age_seconds: d.cache_age_seconds,
                         is_stale: false, fresh_within_seconds: d.ttl_seconds }} />

              <NoticeList className="mt-2" items={hard.map(c => ({
                kind: c.code === "expanded_truncated" ? "truncated" as const
                  : c.code === "hits_session_missing" || c.code === "trends_session_missing" ? "missing" as const
                    : "warning" as const,
                text: `${c.label ?? c.code}：${c.text ?? ""}`,
              }))} />

              <CaliberFold caveats={d.caveats ?? []}
                notes={["家族、门槛、只数上限都是后端参数（`app/intraday/broadcast_config.json`），页面不写死；"
                  + "只数、母体、交集、缓存年龄全部由后端交出，页面不自己数。"]} />

              {/* ── 一、多指标靠前 ─────────────────────────────── */}
              <section className="mt-3">
                <h3 className="text-[12px] font-bold">
                  一、多指标靠前
                  <span className="ml-1 font-normal text-muted-foreground">
                    家族 {dash(d.params?.family_size)} 项各取当日前 {dash(d.params?.top_n)} 名 · 今日命中 ≥{dash(d.params?.min_hits)} 项
                    · 只留股性{keepText(d.params)} · {hits.length} 只
                  </span>
                </h3>
                {!hits.length ? (
                  <p className="mt-1 text-[11px] leading-relaxed text-muted-foreground">
                    今天没有一只票当日命中 ≥{dash(d.params?.min_hits)} 项 ——
                    这是实质读数（一只都没有），不是「没读到」，也不是被筛掉了。
                  </p>
                ) : (
                  <div className="mt-1 overflow-x-auto">
                    <table className="w-full text-[11px]">
                      <thead>
                        <tr className="text-left text-muted-foreground">
                          <Th sort={hitSort} k="security_code" label="名称与代码" cell={cell}
                            title="按代码排（6 位裸码在前按市场段分组）" />
                          <Th sort={hitSort} k="board" label="板块" cell={cell} title="主板／创业板／科创板／北交所" />
                          <Th sort={hitSort} k="hits_today" label="今日命中" cell={cell}
                            title={`当日命中家族里几项（满档 ${dash(d.params?.family_size)} 项）`} />
                          <Th sort={hitSort} k="days_in_top" label={`近 ${dash(d.params?.window_sessions)} 场上榜`} cell={cell}
                            title="重复出现次数多的那一维：近 N 场里有几场进过家族前列" />
                          <Th sort={hitSort} k="rank_sum" label="XY 合计名次" cell={cell}
                            title="analytics.xy_rank_daily 的 rank_sum，越小越靠前；没写名次的票是 —（不是最后一名）" />
                          <Th sort={hitSort} k="char_level" label="股性" cell={cell}
                            title="三腿分位取中位数定档（真源 app/compute/stock_character.py）；— 是判不了，不是股性差" />
                          <th className="px-2 py-1 font-medium" title="当日它最靠前的前三项（中文名读 config.indicator.name）">
                            最靠前的几项</th>
                          <th className="px-2 py-1 font-medium" title="今日涨停池（lup-1）里封住的那一行：几天几板与题材归因">
                            今日封板</th>
                        </tr>
                      </thead>
                      <tbody>
                        {hits.map((r, i) => (
                          <tr key={r.security_code} {...cell.rowProps(i, r.security_code)}>
                            <td {...cell.cellProps(i, "security_code", "whitespace-nowrap px-2 py-1")}>
                              <StockLink code={r.security_code} name={r.name} className="font-medium hover:text-primary" />{" "}
                              <span className="font-mono text-[10px] text-muted-foreground/60">{r.security_code}</span>
                            </td>
                            <td {...cell.cellProps(i, "board", "px-2 py-1")}>{dash(r.board)}</td>
                            <td {...cell.cellProps(i, "hits_today", "px-2 py-1 font-mono tabular-nums")}>
                              {dash(r.hits_today)}/{dash(r.hits_family)} 项
                            </td>
                            <td {...cell.cellProps(i, "days_in_top", "px-2 py-1 font-mono tabular-nums")}>
                              {dash(r.days_in_top)}/{dash(r.window_sessions)} 场
                            </td>
                            <td {...cell.cellProps(i, "rank_sum", "px-2 py-1 font-mono tabular-nums")}>{dash(r.rank_sum)}</td>
                            <td {...cell.cellProps(i, "char_level", "px-2 py-1")}>{charLevelText(r.char_level)}</td>
                            <td {...cell.cellProps(i, "top_inds", "px-2 py-1 text-muted-foreground")}>
                              {(r.top_inds ?? []).join("、") || dash(null)}
                            </td>
                            <td {...cell.cellProps(i, "consec", "px-2 py-1")}>
                              {r.consec ? (
                                <span className="text-[10px]">
                                  {r.consec === 1 ? "今封板" : `${r.consec} 连板`}
                                  {r.reason ? <span className="text-muted-foreground">（{r.reason}）</span> : null}
                                </span>
                              ) : dash(null)}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
              </section>

              {/* ── 二、趋势最正 ─────────────────────────────── */}
              <section className="mt-3">
                <h3 className="text-[12px] font-bold">
                  二、趋势最正
                  <span className="ml-1 font-normal text-muted-foreground">
                    趋势管线当日 selected5 且收盘在 5 日线上方 · 站上 ≥{dash(d.params?.trend_streak_min)} 日
                    · 只留股性{keepText(d.params)} · {trends.length} 只
                  </span>
                </h3>
                {!trends.length ? (
                  <p className="mt-1 text-[11px] leading-relaxed text-muted-foreground">
                    今天没有一只票站上 5 日线 ≥{dash(d.params?.trend_streak_min)} 日且仍在榜上 —— 实质读数，不是没读到。
                  </p>
                ) : (
                  <div className="mt-1 overflow-x-auto">
                    <table className="w-full text-[11px]">
                      <thead>
                        <tr className="text-left text-muted-foreground">
                          <Th sort={trendSort} k="security_code" label="名称与代码" cell={cell} title="按代码排" />
                          <Th sort={trendSort} k="board" label="板块" cell={cell} title="主板／创业板／科创板／北交所" />
                          <Th sort={trendSort} k="ma5_streak" label="站上 5 日线" cell={cell}
                            title="连续站上的天数（趋势管线当日读数，不是本屏算的）" />
                          <Th sort={trendSort} k="pos250" label="250 日位置" cell={cell}
                            title="indicator_value 的 pos250：现价在近两年高低区间的百分位（0~100）" />
                          <Th sort={trendSort} k="pct_change" label="今日涨幅" cell={cell}
                            title="当日涨幅；缺那一行就是 —（没有读数），不写 0" />
                          <Th sort={trendSort} k="nh_20" label="20 日新高" cell={cell} title="当日是否创 20 日新高" />
                          <Th sort={trendSort} k="sealed" label="今日封板" cell={cell} title="当日是否封住涨停" />
                          <Th sort={trendSort} k="char_level" label="股性" cell={cell}
                            title="三腿分位取中位数定档；— 是判不了，不是股性差" />
                        </tr>
                      </thead>
                      <tbody>
                        {trends.map((r, i) => (
                          <tr key={r.security_code} {...cell.rowProps(i + hits.length, r.security_code)}>
                            <td {...cell.cellProps(i + hits.length, "security_code", "whitespace-nowrap px-2 py-1")}>
                              <StockLink code={r.security_code} name={r.name} className="font-medium hover:text-primary" />{" "}
                              <span className="font-mono text-[10px] text-muted-foreground/60">{r.security_code}</span>
                            </td>
                            <td {...cell.cellProps(i + hits.length, "board", "px-2 py-1")}>{dash(r.board)}</td>
                            <td {...cell.cellProps(i + hits.length, "ma5_streak", "px-2 py-1 font-mono tabular-nums")}>
                              {dash(r.ma5_streak)} 日
                            </td>
                            <td {...cell.cellProps(i + hits.length, "pos250", "px-2 py-1 font-mono tabular-nums")}>
                              {r.pos250 === null || r.pos250 === undefined ? dash(null) : `${Number(r.pos250).toFixed(1)}%`}
                            </td>
                            <td {...cell.cellProps(i + hits.length, "pct_change",
                              cn("px-2 py-1 font-mono tabular-nums",
                                Number(r.pct_change ?? 0) > 0 ? "text-primary" : Number(r.pct_change ?? 0) < 0 ? "text-success" : ""))}>
                              {r.pct_change === null || r.pct_change === undefined ? "今日缺数" : `${Number(r.pct_change).toFixed(2)}%`}
                            </td>
                            <td {...cell.cellProps(i + hits.length, "nh_20", "px-2 py-1")}>{r.nh_20 ? "是" : "否"}</td>
                            <td {...cell.cellProps(i + hits.length, "sealed", "px-2 py-1")}>{r.sealed ? "是" : "否"}</td>
                            <td {...cell.cellProps(i + hits.length, "char_level", "px-2 py-1")}>{charLevelText(r.char_level)}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
              </section>

              {/* ── 三、明日观察清单 ─────────────────────────── */}
              <section className="mt-3">
                <h3 className="text-[12px] font-bold">
                  三、明日观察清单
                  <span className="ml-1 font-normal text-muted-foreground">
                    观察口径，不是预测 —— 明天盯这几样，什么算坏了写在下面
                  </span>
                </h3>
                <div className="mt-1 space-y-1 text-[11px] leading-relaxed">
                  <p>
                    <span className="text-muted-foreground">两榜都进：</span>
                    {(d.overlap ?? []).length
                      ? (d.overlap ?? []).map(o => (
                        <span key={o.code} className="mr-1">
                          <StockLink code={o.code} name={o.name} className="font-medium hover:text-primary" />
                        </span>))
                      : <span>{dash(null)}（今天一只都没有 —— 这是实质读数，不是没读到）</span>}
                  </p>
                  <p>
                    <span className="text-muted-foreground">今日最高标（封住的那几只是明天竞价要看的）：</span>
                    {(d.top_boards ?? []).length
                      ? (d.top_boards ?? []).map((b, i) => (
                        <span key={i} className="mr-1">{b.name ?? "?"}（{dash(b.consec)} 板）</span>))
                      : <span>{dash(null)}（涨停池那天没有封住的票，或那一族的表没行）</span>}
                  </p>
                  <p className="text-muted-foreground">
                    什么算坏了：趋势那一榜收盘跌破 5 日线、或指标那一榜前排次日大幅低开无承接 ⇒ 对应信号失效。
                    两榜当日都缺场次时这一屏不摆榜（后端交 available:false 与原因），也不拿上一场那张榜冒充今天。
                  </p>
                </div>
              </section>

              {cell.popHint && <p className="mt-2 rounded-lg border border-warning/50 bg-warning/10 p-2 text-[11px] leading-relaxed text-warning">{cell.popHint}</p>}

              {/* ── 逐票全部指标（用户点名那一块）──────────────── */}
              {expand && (
                <section className="mt-3">
                  <h3 className="text-[12px] font-bold">
                    名单里每一只票的全部指标
                    <span className="ml-1 font-normal text-muted-foreground">
                      {gridRows.length} 只 × {cols.length} 项（当日在册）· 上表数值，下面小字是全场名次（仅能跨票比的那 19 项）
                    </span>
                  </h3>
                  <div className="mt-1 overflow-x-auto">
                    <table className="w-full text-[11px]">
                      <thead>
                        <tr className="text-left text-muted-foreground">
                          <th className="col-pin col-pin-1 px-2 py-1 font-medium" rowSpan={2}>名称与代码</th>
                          <th className="px-2 py-1 font-medium" rowSpan={2}>为什么在榜</th>
                          <th className="px-1 py-1 text-center font-medium" colSpan={cols.length}>当日指标值（点列头按这一列排；缺值排最后）</th>
                        </tr>
                        <tr className="text-left text-muted-foreground">
                          {cols.map(c => (
                            <SortHeader key={c.key} label={c.name ?? c.key} sortKey={`ind.${c.key}.v`}
                              dir={gridSort.dirOf(`ind.${c.key}.v`)}
                              onSort={() => { cell.clearAll(); gridSort.toggle(`ind.${c.key}.v`); }}
                              title={indicatorCell(c, c.value ?? null, null, null).hint} />
                          ))}
                        </tr>
                      </thead>
                      <tbody>
                        {gridRows.map((r, i) => (
                          <tr key={r.code} {...cell.rowProps(i + hits.length + trends.length, r.code)}>
                            <td className="col-pin col-pin-1 whitespace-nowrap px-2 py-1">
                              <StockLink code={r.code} name={r.name} className="font-medium hover:text-primary" />{" "}
                              <span className="font-mono text-[10px] text-muted-foreground/60">{r.code}</span>
                            </td>
                            <td className="px-2 py-1 text-[10px] leading-tight text-muted-foreground">
                              {r.summary}
                              {r.missing_count ? ` ｜ 缺 ${r.missing_count} 项` : ""}
                            </td>
                            {cols.map(c => {
                              const v = r.ind[c.key]?.v ?? null;
                              const one = indicatorCell(c, v, r.ind[c.key]?.r ?? null, r.ind[c.key]?.n ?? null);
                              return (
                                <td key={c.key}
                                  {...cell.cellProps(i + hits.length + trends.length, `ind.${c.key}`,
                                    cn("px-1 py-1 text-right font-mono tabular-nums",
                                      v === null ? "text-muted-foreground/50" : ""))}
                                  title={one.hint}>
                                  <div>{one.text}</div>
                                  {one.rank && <div className="text-[9px] text-muted-foreground">{one.rank}</div>}
                                </td>
                              );
                            })}
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </section>
              )}

              <ExplainBox title="这一张表怎么点 · 取数口径（后端原句）">
                <p className="text-[11px] leading-relaxed text-muted-foreground">{STOCK_ROW_HINT}</p>
                {d.caliber && (
                  <p className="mt-1 text-[10px] leading-relaxed text-muted-foreground">
                    取数口径（后端原句，页面未改写）：{plain(d.caliber)}
                  </p>
                )}
              </ExplainBox>

              {/* 点中一行 → 下面这两块换标的（与「选股汇总」同一个形状：订阅圈在这两块里，
                  单击不会把上面 35 列那张表整个再协调一遍） */}
              <FocusPane store={cell.store} date={d.date}
                codeAt={i => (i < hits.length ? hits[i]?.security_code
                  : i < hits.length + trends.length ? trends[i - hits.length]?.security_code
                    : gridRows[i - hits.length - trends.length]?.code ?? null)} />

              <PushedBody pushed={d.pushed} date={d.date} />

              {/* 那份「隔日表现回溯」挪到 工作空间 → 策略回测 了（用户 11-06 点名），
                  这里只留一条指路：两处各挂一份，改天就变成两个说法。 */}
              <p className="mt-3 text-[11px] text-muted-foreground">
                这套判据往后的表现（6 个月、往后 1/2/3 个交易日、冲高率与全市场基线对照）在
                <Link className="mx-1 text-primary underline" to="/backtest-agent">历史回溯</Link>
                那一屏；读的是同一张 <code>analytics.outlook_pick_daily</code>，这里不再挂第二份。
              </p>
            </>
          );
        }}
      </Panel>
    </div>
  );
}

/** 一个列头：排序键＝这一格读到的字段，标题＝后端那句（页面不自编含义）。
 *  ⚠️ `SortHeader` 自己就渲染 `<th>`（带 aria-sort），这里**绝不能再包一层 th** ——
 *  那是非法 HTML，解析器把里层顶成兄弟，表头会比表体多一倍格子、列头与数字整排错位，
 *  而 `aria-sort` 照样翻、点击照样换序，排序测试看不出来（全站由 `test/firstBoardDeep.test.ts` 钉）。 */
function Th({ sort, k, label, title, cell }: {
  sort: ReturnType<typeof useTableSort>; k: string; label: string; title: string;
  cell: ReturnType<typeof useStockRowInteraction>;
}) {
  return (
    <SortHeader label={label} sortKey={k} dir={sort.dirOf(k)}
      onSort={() => { cell.clearAll(); sort.toggle(k); }} title={title} />
  );
}

/** 今天真正发出去的那一条原文（只读库里的留痕，不重算）。 */
function PushedBody({ pushed, date }: {
  pushed: OutlookPayload["pushed"]; date?: string | null;
}) {
  const [open, setOpen] = useState(false);
  if (!pushed) {
    return (
      <p className="mt-3 rounded-lg border border-border/60 bg-muted/20 p-2 text-[11px] leading-relaxed text-muted-foreground">
        {dash(date)} 这一场库里还没有「明日展望」的留痕 —— 那一炉（19:00）今天没跑，
        或跑了但两榜都缺场次所以按「宁缺毋假」一条都没发。上面这两张榜是现在按同一份判据算出来的，
        不等于群里收到过什么。
      </p>
    );
  }
  // 后端交的是带时区的 ISO 串，屏上只要念到秒（21:02:31）—— 整串摆出来没人读得下去
  const at = String(pushed.at ?? "").replace(/^[0-9T:+-]+?[Tt]/, "").slice(0, 8) || dash(pushed.at);
  const verdict = pushed.sent_ok === true ? "已确认送达"
    : pushed.sent_ok === false ? `未确认：${pushed.send_err ?? "渠道没回业务码"}` : "没读到发送结果";
  return (
    <div className="mt-3 rounded-lg border border-border/60 bg-muted/20 p-2">
      <button type="button" onClick={() => setOpen(!open)} aria-expanded={open}
        className="flex w-full flex-wrap items-center gap-1.5 text-left text-[11px]">
        <span className="font-medium text-foreground">今天发出去的那一条原文（{at}）</span>
        <span className={cn("rounded px-1 py-0.5 text-[10px]",
          pushed.sent_ok === true ? "bg-primary/15 text-primary" : "bg-warning/15 text-warning")}>
          {verdict}｜渠道 {dash(pushed.channels)}
        </span>
        <span className="ml-auto text-muted-foreground">{open ? "收起" : "展开看原文"}</span>
      </button>
      {open && (
        <pre className="mt-1 max-h-[40vh] overflow-auto whitespace-pre-wrap break-words rounded bg-background/60 p-2 font-sans text-[11px] leading-relaxed text-foreground">
          {pushed.body}
        </pre>
      )}
      <p className="mt-1 text-[10px] leading-relaxed text-muted-foreground">
        这一格读的是 `analytics.intraday_broadcast` 里已经落库的那一条正文，不重新取数 ——
        所以它证明的是「群里那天收到过什么」，上面那两张榜证明的是「按同一份判据现在算出什么」。
      </p>
    </div>
  );
}

/**
 * 点中某一行之后跟着换标的：量化档案（八块，共用一份实现）。
 *
 * 单独成组件只为一件事：**把「选中了哪一行」的订阅圈在这块里**。提回页面组件
 * 等于让每一次单击把上面那张 35 列的表整个再协调一遍（倍量页实测 461ms 长任务那个形状）。
 */
function FocusPane({ store, date, codeAt }: {
  store: CellStore;
  date?: string | null;
  codeAt: (row: number) => string | null;
}) {
  const sel = useCellSelection(store);
  const focus = sel ? codeAt(sel.row) : null;
  return <StockDossier code={focus} date={date ?? undefined} />;
}
