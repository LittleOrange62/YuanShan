import { Layers, RefreshCw } from "lucide-react";
import { useState } from "react";
import { Caveats, CoverageNote, Definitions, Provenance, QuietFold } from "@/components/ui/DataPanel";
import { DocDrawer, DrawerSection } from "@/components/ui/DocDrawer";
import { Disclaimer } from "@/components/ui/Disclaimer";
import { GlassCard } from "@/components/ui/GlassCard";
import { SortHeader } from "@/components/ui/SortHeader";
import { StockLink } from "@/components/ui/StockLink";
import { plain } from "@/lib/agent";
import { splitCaveats } from "@/lib/caveatKind";
import { dash, describeState, NULL_TEXT } from "@/lib/datastate";
import {
  LIMIT_OPTIONS, PAGE_SIZES, boardsWindowCell, boardsWindowTitle, dblCell, dblTitle, breakCountText,
  industrySide, intText, limitCountCell, limitCountTitle, maStackCell, maStackTitle, num, pctText,
  prevSealedHits, quadCell, quadTitle, ratioText, reasonCell, reasonCoverage,
  consecCell, consecTitle, pos2yCell, pos2yTitle, datedColLabel, sameWindow, xyCell, xyTitle,
  repeatedWords, sealText, shapeButtonLabel, shapeQuery, spikeTitle, thresholdsText, toggleShapeSel,
  truncationSentence, unjudgedShape, wanText,
  type FbdRow, type FirstBoardDaily,
} from "@/lib/firstBoardDaily";
import { useNonce, useProbe } from "@/lib/probe";
import { rankText, rankTitle, trimNum } from "@/lib/ranking";
import { STOCK_ROW_HINT, useStockRowInteraction } from "@/lib/useStockRowInteraction";
import { useTableSort } from "@/lib/useSort";
import { cn } from "@/lib/utils";
import { NoticeBar } from "@/components/ui/NoticeBar";

/**
 * 首页「首板分析」（用户 2026-10-21 原话：「全市场换手率 TOP20 上面加一个首板分析，分析首板个股
 * 的情况，已经近期的走势，分析涨停原因，**特别是前一天的走势**」）——所以前一日那一组是主体，
 * 表头单独成组并上色，不是散在表里的附加列。
 *
 * 判据、形态档名字、阈值、口径句子只住后端（写侧 `app/compute/first_board_daily.py`、
 * 读数侧 `duanxian/first_board_daily.py`，端点 `vr/app.py::quant_first_board_daily`）；
 * 读法只住 `lib/firstBoardDaily.ts`，这里只画。
 *
 * 六条界面上不许糊掉的线：
 *  · **缺数与 0 是两种读数**：`break_count` 空＝源站这一行没给这个字段（不是 0 次没炸）、
 *    `prev_pct` 空＝没有前一交易日那一行（不是平盘）、`boards` 空＝算不出几天几板
 *    （不拿源站那句「几天几板」顶上）——一律 `—`，绝不 `?? 0`；
 *  · **涨停原因空着分两种说法**（`reason_missing`）：`source_row_absent`＝源站没收这只
 *    （ST／北交所基本不收），`source_no_records`＝本场整池没取到（这才是本机漏采）；
 *  · **阈值只读 `thresholds`**：界面上一个数字都不写死（写死就是第二份真相源）；
 *  · **全场聚合不随筛参与上限变**：`shape_tally` 每档那个数是本场全部首板的数，
 *    筛完剩下多少行是另一件事，两句都摆在屏上；
 *  · **母体自检**：首板定义下「前一日收盘封住」应恒假，行里真冒出 `prev_sealed` 时出声，不静默；
 *  · `available:false` 时后端不交 `rows`，只交 `reason`（里面就是该跑哪条补跑命令）——
 *    原样念出来，不摆空表。
 */

/** 列的一份清单：表头与格子从同一个数组走（两处各写一份就会错位，全站栽过一次）。
 *  第 3 个元素是分组键；第 4 个（`false`）表示这一格**不给排序** ——
 *  近 5 场象限与 XY 合计名次交的都是一段序列、倍量那一格是两个数，都没有「更大／更小」这一把单一尺，
 *  硬给个 sortKey 只会静默不排序还显示已排序。
 *  10-27 用户点名：删掉「几天几板」「当日涨幅」与整组「前一交易日那一根」（与短线情绪那块重复），
 *  换成近 20 场几板、近 5 场四维象限名次、近 10/20 场倍量分布与盘中信号三列。
 *  ⚠️ 10-28 用户点名三件事（改这三件前先看，别改回旧形状）：
 *   ① 「近 5 场四维象限」与新增的「XY 合计名次」格子里**不写日期**（日期写在列名上，见 `datedColLabel`）；
 *   ② 抢跑比／近 10 场倍量／近 20 场倍量／倍量加权分（＋它的名次那一列）**整族移到最后**——
 *      用户原话「这几个是什么意思，看不懂」，放在中间会把前面几列挤走；同族第五列 `dbl_rank`
 *      是「加权分的名次」，留在中间会读成另一件事，所以跟着一起挪；
 *   ③ 前两列（代码／名称）**横向滚动时钉住**（`col-pin-1`／`col-pin-2`，样式在 `src/index.css`，
 *      底色必须是实色：卡片是半透明玻璃，透明钉列会让滚过去的内容从底下透出来）。 */
type Col = [key: string, label: string, group: "day" | "trend" | "history" | "energy", sortable?: boolean];
const COLS: Col[] = [
  ["code", "代码", "day"],
  ["name", "名称", "day"],
  ["board", "板别", "day"],
  ["boards_20d", "近 20 场几板", "day"],
  // 列名上的那串日期由接口 `recent.quad_dates` 现填（页面不自己按本机日历推）
  ["quad", "四维象限", "day", false],
  ["xy", "XY 合计名次", "day", false],
  ["launch_turnover", "换手", "day"],
  ["first_seal_at", "首封时间", "day"],
  ["last_seal_at", "最后封板", "day"],
  ["break_count", "炸板次数", "day"],
  ["order_amount_wan", "封单额", "day"],
  ["reason", "涨停原因", "day"],
  ["chg5", "近 5 场", "trend"],
  ["chg10", "近 10 场", "trend"],
  ["chg15", "近 15 场", "trend"],
  // 急拉档那一格显示中文档位，排序用的是后端交的 `spike_tier_rank`（1 慢拉 / 2 快拉 / 3 急拉）——
  // 显示值与排序键不是一个字段，所以这一列必须逐列点击实测（sortKey 写错不报错、也不换序）。
  ["spike_tier_rank", "急拉档", "trend"],
  ["spike_first5_at", "+5% 首现", "trend"],
  ["spike_speed15_pct", "15 分钟最大拉升", "trend"],
  // 这只票自己的历史位置（10-27 用户点名）。涨/跌停那一格是「封／触」两个数，
  // 所以拆成两列各排各的（按封住那个数排，触那个数只在格子里）——
  // 合成一列就没法排序：sortKey 必须是格子里读到的那一个数，两个数没有大小可比。
  ["lu_sealed_3m", "近 3 月涨停", "history"],
  ["ld_sealed_3m", "近 3 月跌停", "history"],
  ["max_consec_6m", "近 6 月连板最高", "history"],
  ["pos_2y", "2 年位置", "history"],
  // 均线排列：格子念中文档名，排序走后端交的 `ma_stack_rank`（与「急拉档」同一族写法，
  // 显示值与排序键不是一个字段 ⇒ 必须逐列点击实测）。
  ["ma_stack_rank", "均线排列", "history"],
  // ↓ 10-28 用户点名移到最后的「倍量与抢跑」那一族（口径句子只住后端 definitions）
  ["vol_grab_ratio", "抢跑比", "energy"],
  ["dbl10", "近 10 场倍量", "energy", false],
  ["dbl20", "近 20 场倍量", "energy", false],
  ["dbl_energy", "倍量加权分", "energy"],
  ["dbl_rank", "倍量名次（有分的那批）", "energy"],
];

const GROUPS: { key: "day" | "trend" | "history" | "energy"; label: string }[] = [
  { key: "day", label: "首板当日那一根" },
  { key: "trend", label: "近期走势与盘中信号" },
  { key: "history", label: "这只票自己的历史位置" },
  { key: "energy", label: "倍量与抢跑那一族（看不懂先翻下面折叠的口径）" },
];

/** 不给排序的那几列各自有一句「为什么不给点」：象限与名次那一格是一段序列、倍量那一格是两个数，
 *  拿同一句话套会念错（全站那句「点了不亮」的教训只在有 sortKey 时成立）。 */
const NO_SORT_WHY: Record<string, string> = {
  quad: "这一格是一段序列（近 5 场逐场，由早到晚），没有「更大／更小」这一把尺，所以不给点。",
  xy: "这一格同样是一段序列（近 5 场的 XY 合计名次，由早到晚，越小越靠前）——"
    + "整串没有单一大小，所以不给点；要看「名次稳步前进」那种判据，走「名次稳步前进榜」那一屏。",
  dbl10: "这一格是两个数（倍量次数·准倍量次数），没有单一大小，所以不给点；"
    + "要按倍量强度排，走同一族里「倍量加权分」「倍量名次」那两列（另一把尺，分母是前 5 场均量）。",
  dbl20: "与近 10 场那一列同一说法，只是窗口更长（20 场）。",
};

export function FirstBoardDailyPanel() {
  const [nonce, refresh] = useNonce();
  /** 空＝库里最新一场（不拿本机时钟冒充行情日：这一屏的日期永远由接口交） */
  const [date, setDate] = useState("");
  const [limit, setLimit] = useState(80);
  const [shapes, setShapes] = useState<string[]>([]);
  const [pageSize, setPageSize] = useState(25);
  const [page, setPage] = useState(1);

  const st = useProbe<FirstBoardDaily>("/quant/first-board-daily", nonce, {
    date: date || undefined, limit, shapes: shapeQuery(shapes),
  });
  const d = st.kind === "ok" && st.data.available ? st.data : null;
  const rows = (d?.rows ?? []) as FbdRow[];

  const srt = useTableSort({ key: "first_seal_at", dir: "asc" });
  const x = useStockRowInteraction("首板分析");
  /** 换日期／换上限／换筛档／换每页：清行选中并回第 1 页（行号记的是渲染数组）。 */
  const pick = (fn: () => void) => { x.clearAll(); setPage(1); fn(); };

  const th = (k: string) => plain(d?.definitions?.[k]
    ?? "这一列的口径文字后端没交（definitions 里没有这个键），页面不自己编");
  const defOf = (k: string) => d?.definitions?.[k] ?? null;

  const pages = Math.max(1, Math.ceil(rows.length / pageSize));
  const cur = Math.min(Math.max(1, page), pages);
  const shown = srt.apply(rows).slice((cur - 1) * pageSize, cur * pageSize);

  const cov = reasonCoverage(d?.reason_tally?.summary_meta ?? null);
  const words = repeatedWords(d?.reason_tally?.reason_summary ?? null);
  const ind = industrySide(d?.reason_tally ?? null);
  const unjudged = unjudgedShape(d?.shape_tally ?? null);
  const sealedHits = prevSealedHits(rows);
  /** 近 20 场那一列的窗口长度读接口（写死在界面就是第二份真相源）；没交就不判"根数不足" */
  const boardsWin = num(d?.recent?.boards_window);
  const recent = d?.recent ?? null;
  /** 四列历史位置的窗口场数、根数下限、均线周期与档名——全部读接口（抄第二份就会漂） */
  const hist = d?.history;
  const shapeFilter = d?.shape_filter ?? [];

  /**
   * 10-31 用户点名：这一块唯一的说明出口是标题行最右边那颗「这块的说明」。
   * 原先标题旁一枚「口径」芯片、表底卧着 Definitions 折框与 CaliberFold（foldAll＝10-22 用户
   * 点名首页这块的描述性内容全收）、卡外还有一只 ExplainBox —— 四个出口并成这一颗，
   * 内容来源一个字没换：rule/caliber/coverage/source/caveats 全是后端交出的原句，
   * 读法几段仍是页面原本就写好的那些。缺数类条目连同条数进抽屉（条数写在按钮后面，
   * 「有东西没读到」这件事仍然看得见）；截断、自检、筛后计数那几句声明照旧留在屏上。
   */
  const { visible: unhealthyCav, foldable: foldableCav } = splitCaveats(d?.caveats);
  const heldCav = [...foldableCav, ...unhealthyCav];
  const defsCount = Object.entries(d?.definitions ?? {}).length;
  const covMap = d?.coverage ? { ...d.coverage } : null;
  const covEntries = Object.entries(covMap ?? {}).filter(([, v]) =>
    v && typeof v === "object" && !Array.isArray(v) && ("from" in (v as object) || "to" in (v as object)));
  const noteBits = d ? [
    d.rule, d.caliber,
    `这一屏读的是落库判据（spec_version ${dash(d.spec_version)}），不现抓网络：`
    + `本场行算于 ${dash(d.calculated_at)}；表覆盖 ${dash(d.coverage?.from)} ~ ${dash(d.coverage?.to)}`
    + ` 共 ${intText(d.coverage?.sessions)} 场。日期空着＝库里最新的一场，不是今天一定有新数。`,
    // 题材词榜那两句取数说明（用户 10-27 点名收起）：被切掉的词数与行业那一侧的交代
    `另有 ${intText(d?.reason_tally?.summary_meta?.dropped_single_reason)} 个只出现一次的词没列进来`
    + `（接口给的词次合计 ${intText(d?.reason_tally?.summary_meta?.tag_total)}）。`,
    ind.words === null ? (ind.note ?? "行业那一侧接口这场没交说法（summary_meta.industry_note 是空的）。")
      : ind.words.length === 0 ? "行业那侧接口这场交的是空列表，页面不摆一个空的行业榜。"
        : `行业重复：${ind.words.map(w => `${w.name} ${intText(w.count)}`).join("、")}`,
  ].filter(Boolean) : [];
  const drawerTally = d ? [
    heldCav.length - unhealthyCav.length ? `${heldCav.length - unhealthyCav.length} 条口径声明` : null,
    unhealthyCav.length ? `${unhealthyCav.length} 条缺数与告警（本场有读不到／判不了的行）` : null,
    noteBits.length ? `${noteBits.length} 句母体与读法` : null,
    defsCount ? `${defsCount} 个字段解释` : null,
    covEntries.length ? `${covEntries.length} 段覆盖区间` : null,
    d.source ? `出处 1 项` : null,
  ].filter(Boolean).join(" · ") : "";

  const card = (label: string, value: string, unit: string, title: string, hi = false) => (
    <div key={label} title={title}
      className={cn("rounded-lg border px-2 py-1.5",
        hi ? "border-primary/50 bg-primary/10" : "border-border/60 bg-muted/20")}>
      <div className="text-[11px] text-muted-foreground">{label}</div>
      <div className="mt-0.5 font-mono text-base font-bold text-primary">
        {value}
        <span className="ml-0.5 text-[10px] font-normal text-muted-foreground">{unit}</span>
      </div>
    </div>
  );

  return (
    <GlassCard className="mt-4" docTitle={`首板分析 · ${dash(d?.date) || NULL_TEXT}`}>
      <div className="mb-1.5 flex flex-wrap items-center gap-2">
        <h3 className="flex items-center gap-1.5 text-sm font-semibold text-muted-foreground">
          <Layers className="h-4 w-4" /> 首板分析 · {dash(d?.date) || NULL_TEXT}
        </h3>
        <label className="flex items-center gap-1 text-[11px] text-muted-foreground"
          title={plain("交易日留空＝读库里最新的一场；改了会重发一次请求。这一屏只认库里算好判据的那些场次。")}>
          交易日
          <input type="date" value={date} onChange={e => pick(() => setDate(e.target.value))}
            aria-label="交易日（留空＝库里最新一场）"
            className="rounded border border-border/60 bg-background px-1.5 py-0.5 text-[11px] text-foreground" />
        </label>
        <span className="flex items-center gap-1 text-[11px] text-muted-foreground">
          一次取几行
          {LIMIT_OPTIONS.map(n => (
            <button key={n} type="button" aria-pressed={limit === n}
              onClick={() => pick(() => setLimit(n))}
              className={cn("rounded-lg px-2 py-1 text-[11px]",
                limit === n ? "bg-primary/20 text-primary" : "text-muted-foreground hover:bg-muted/40")}>
              {n}
            </button>
          ))}
        </span>
        <span className="flex items-center gap-1 text-[11px] text-muted-foreground">
          每页
          {PAGE_SIZES.map(n => (
            <button key={n} type="button" aria-pressed={pageSize === n}
              onClick={() => pick(() => setPageSize(n))}
              className={cn("rounded-lg px-2 py-1 text-[11px]",
                pageSize === n ? "bg-primary/20 text-primary" : "text-muted-foreground hover:bg-muted/40")}>
              {n} 行
            </button>
          ))}
        </span>
        {/* 「这块的说明」摆在标题这一行的最右边（用户 10-31），「重新取数」那颗跟在它右边。
            抽屉体 = 原先四个出口的并集：母体与读法（后端 rule/caliber 原句与那几句拼好的话）、
            口径声明（含 10-22 点名一起收的缺数那族，条数写在按钮后面）、字段字典、覆盖、出处，
            以及原本卡在外的「这一块怎么读」那几段——逐字搬入，屏上不再出现第二遍。 */}
        <span className="ml-auto flex flex-wrap items-center gap-1.5">
          <DocDrawer title={`首板分析 · ${dash(d?.date) || NULL_TEXT}`}>
            {!d?.rule && (
              <p>{plain("母体与默认行序由后端交（rule 字段）—— 后端没交时页面不自己复述判据，免得出现第二份口径。")}</p>
            )}
            {d && noteBits.length > 0 && (
              <DrawerSection heading="母体与读法（本屏那几句散句，后端原句）">
                {noteBits.map((t, i) => <p key={`n${i}`}>{plain(String(t))}</p>)}
              </DrawerSection>
            )}
            {heldCav.length > 0 && (
              <DrawerSection heading="口径声明（后端 caveats 原句，页面未改写）">
                <Caveats items={heldCav} />
              </DrawerSection>
            )}
            {defsCount > 0 && (
              <DrawerSection heading="这一列是什么（字段含义，后端 definitions）">
                <Definitions map={d?.definitions ?? null} bare />
              </DrawerSection>
            )}
            {covEntries.length > 0 && (
              <DrawerSection heading="覆盖区间（从哪天起、共几场）">
                <CoverageNote map={covMap} />
              </DrawerSection>
            )}
            {d?.source && (
              <DrawerSection heading="出处">
                <Provenance bits={[d.source]} />
              </DrawerSection>
            )}
            <DrawerSection heading="这一块怎么读 · 表分哪两段 · 这一张表怎么点">
              <p>{plain("母体是「连板段的第一天」那批票（本地日线判据），与首板页那张东财实时池不是一张名单；"
                + "两屏的只数对不上是容差与实时/收盘时点之差，谁也不许冒充谁 —— 原句在本抽屉「母体与读法」那节的 rule 与 caliber 两句里逐字摆着。")}</p>
              <p className="mt-1">{plain("四段从左到右：① 首板当日那一根（板别、近 20 场几板、近 5 场四维象限、"
                + "近 5 场 XY 合计名次、换手、首封与最后封板、炸板次数、封单额、涨停原因）；"
                + "② 近期走势与盘中信号（近 5／10／15 场涨幅、急拉档、+5% 首现、15 分钟最大拉升）；"
                + "③ 这只票自己的历史位置（近 3 月涨/跌停、近 6 月连板最高、2 年位置、均线排列）；"
                + "④ 倍量与抢跑那一族（抢跑比、近 10／20 场倍量、倍量加权分与名次）—— "
                + "第 ④ 段是用户 10-28 点名从中间移到最后的（原话「这几个是什么意思，看不懂」），"
                + "口径原句各列悬停里都在，一列都没撤。"
                + "前两列（代码／名称）横向滚动时钉在左边不动。"
                + "10-27 起「前一交易日那一根」整组不再在这屏上摆 —— 与首页短线情绪那块是同一批数，接口仍交出，只是不重复呈现。")}</p>
              <p className="mt-1">{plain("「四维象限」与「XY 合计名次」这两列的格子里不写日期（用户 10-28 点名），"
                + "日期就写在列名上，逐场的名次与分值在悬停里；两列都由早到晚排，最后一场就是本场。"
                + "XY 合计名次是 X 轴名次 + Y 轴名次相加（两个已入库名次之和，越小越靠前），"
                + "库住在 `analytics.xy_rank_daily`（加工段第 37 步 `xyrank` 每天落），屏上一个加法都不做；"
                + "它与「oper_score 名次」（合成分再排一次）不是同一把尺，谁也不许替谁。")}</p>
              <p className="mt-1">{plain("近 5／10／15 场是后复权区间涨幅（与全站同一把尺）；抢跑比是启动前 5 根均量 ÷ 前 25 根均量，"
                + "一个量能比，不是涨幅；倍量名次排的是当日「有分的那批」，不是全部股票。"
                + "「近 10 / 近 20 场倍量」的分母是对昨日量（vr-1），与「倍量加权分」那把 ≥2×前 5 场均量不是一把尺 —— 两列各带自己的分母。")}</p>
              <p className="mt-1">{plain("盘中那三列（急拉档、+5% 首现、15 分钟最大拉升）读的是筛过的异动榜：没进榜只说明没过入表线，不等于盘中没冲高；"
                + "分钟线只有 2026-08-14 起，10-20 与 10-21 两天整场没入库，那几天的 — 是「没有分时读数」。"
                + "急拉档那一格显示中文档位，排序用的是后端那个等级数。")}</p>
              <p className="mt-1">{STOCK_ROW_HINT}</p>
              <p className="mt-1">{plain("这一屏不打分、不预测、不给买卖建议：首板只是「已经涨停」这个事实的形状与归因读数。")}</p>
            </DrawerSection>
          </DocDrawer>
          {drawerTally && <span className="text-[10px] text-muted-foreground">{drawerTally}</span>}
        </span>
        <button type="button" onClick={() => { x.clearAll(); refresh(); }} title="重新取数"
          className="flex items-center gap-1 rounded-lg bg-primary/15 px-2 py-1 text-[11px] font-medium text-primary hover:bg-primary/25">
          <RefreshCw className="h-3.5 w-3.5" /> 重新取数
        </button>
      </div>

      {/* 形态档多选：点一档就重发一次请求（在库里筛、先于取数上限）。
          `key` 为 null 那一档是「判不了」，不是七档之一，筛不了它（后端只认那七个档键）。 */}
      <div className="mb-2 flex flex-wrap items-center gap-1.5 text-[11px]">
        <span className="text-muted-foreground">前一日形态档（点一下即筛，可多选）</span>
        {(d?.shape_tally ?? []).filter(t => t.key).map(t => (
          <button key={t.key as string} type="button" aria-pressed={shapes.includes(t.key as string)}
            onClick={() => pick(() => setShapes(s => toggleShapeSel(s, t.key as string)))}
            className={cn("rounded-lg border px-2 py-0.5",
              shapes.includes(t.key as string)
                ? "border-primary/70 bg-primary/10 font-medium text-primary"
                : "border-border hover:bg-muted/50")}>
            {shapeButtonLabel(t)}
          </button>
        ))}
        {shapes.length > 0 && (
          <button type="button" onClick={() => pick(() => setShapes([]))}
            className="rounded-lg border border-warning/60 px-2 py-0.5 text-warning hover:bg-warning/10">
            {plain("清除筛选")}
          </button>
        )}
        {unjudged && (
          <span className="text-muted-foreground">
            {plain(`判不了 ${intText(unjudged.n)} 只（形态档算不出来，不是落在某一档，这一档筛不了）`)}
          </span>
        )}
      </div>

      {st.kind === "loading" && (
        <p className="text-[11px] text-muted-foreground">取数中…（读库里落好的这一场首板行，不现算判据、不现抓网络）</p>
      )}
      {st.kind !== "ok" && st.kind !== "loading" && (
        <p className="rounded-lg border border-warning/50 bg-warning/10 p-2 text-[11px] leading-relaxed text-warning">
          {describeState(st)}
        </p>
      )}
      {/* available:false 时后端**不交 rows**，只交 reason（里面写着该跑哪条补跑命令）。
          用户 2026-10-22：这种"本机还没覆盖这一项"的块默认收成一行，原因整句留在 summary 上
          （补跑命令就在那句里，不是被折掉）；点「重新取数」真取到数时这一支不再渲染 ⇒ 自动展开。 */}
      {st.kind === "ok" && !st.data.available && (
        <QuietFold text={plain(st.data.reason ?? "首板分析读不到数，后端也没交原因。")} />
      )}

      {d && (
        <>
          <div className="mb-2 grid grid-cols-2 gap-2 md:grid-cols-3 xl:grid-cols-6">
            {card("本场首板", intText(d.first_count), "只",
              plain(`母体＝${defOf("universe") ?? "首板母体那句（universe）后端没交，页面不自己补"}`))}
            {card("当日收盘封住", intText(d.universe), "只", plain(d.caliber ?? "覆盖与分母那句（caliber）后端没交"))}
            {card("一字板（全天最高=最低）", intText(d.one_word), "只", th("launch"))}
            {card("前一日盘中触过板", intText(d.prev_touched), "只", th("prev_day"))}
            {card("前一日涨幅中位", pctText(d.prev_pct_med, 2), "",
              plain("中位是这一场全部首板的前一日涨幅算出来的（后端 percentile_cont），"
                + "不随形态筛与取数上限变；它是 — 时说的是这一场没有可数的读数，不是 0%。"))}
            {card("涨停原因覆盖", cov.covered, "只",
              plain(`${cov.note} 分母＝接口给的 ${cov.stockCount} 只首板。`))}
          </div>

          <p className="mb-1.5 text-[11px] leading-relaxed text-muted-foreground">
            {plain(thresholdsText(d.thresholds)) || plain("阈值后端没交（thresholds 是空的）—— 页面不写死一个数字，"
              + "前一日那七档到底卡在哪一线，这一屏说不出。")}
          </p>
          {/* 三句"当下一批"的计数：截断、母体自检、两把几天几板尺对不上 —— 都不许折进折叠框 */}
          <p className="mb-0.5 text-[11px] leading-relaxed text-muted-foreground">
            {plain(truncationSentence(rows.length, d))}
          </p>
          {sealedHits > 0 && (
            <p className="mb-0.5 rounded-lg border border-warning/50 bg-warning/10 p-2 text-[11px] leading-relaxed text-warning">
              {plain(`已交出的 ${rows.length} 行里有 ${sealedHits} 行的「前一日收盘封住」为真 —— 首板定义下这一档应恒假`
                + `（写侧那条自检），这说明母体或判据分叉了。页面不替它挑一种解释，原样报出来。`)}
            </p>
          )}
          {/* 10-27 用户点名撤掉「几天几板」那一列后，这里原来的那句「N 行与源站那句对不上」
              就成了没有指对象的数字（屏上再没有那一列可对照），整块随列一起撤；
              封住判据与容差仍只住 `duanxian/board_span.py` 一份，近 20 场那一列用的就是它。 */}
          {shapeFilter.length > 0 && (
            <p className="mb-0.5 text-[11px] leading-relaxed text-warning">
              {plain(`现在只列前一日形态属于 ${shapeFilter.join("、")} 的首板（在库里筛、先于取数上限）——`
                + `上面每档那个数仍是本场全部首板的全场数，不是筛完剩下的数。`)}
            </p>
          )}
          {x.popHint && <NoticeBar kind="warning">{x.popHint}</NoticeBar>}

          {/* 两列「近 5 场」读的是两张不同的表（score 的 oper_score / xy_rank_daily 的 xyr-1），
              补跑进度不一致时那五天会分叉 —— 同一批才敢说"同一批"，分叉了后端会另发一条 hard 告警。 */}
          {recent && (recent.xy_dates ?? []).length > 0 && (
            <p className="mb-0.5 text-[11px] leading-relaxed text-muted-foreground">
              {sameWindow(recent.quad_dates, recent.xy_dates)
                ? plain(`「四维象限」与「XY 合计名次」这两列读的是同一批场次（`
                  + `${(recent.xy_dates ?? []).join("、")}），越小越靠前的都是名次，不是分数。`)
                : plain(`注意：这两列各自查自己那张表的最近 ${recent.xy_sessions ?? "—"} 场，`
                  + `现在不是同一批（XY：${(recent.xy_dates ?? []).join("、")}；象限：`
                  + `${(recent.quad_dates ?? []).join("、") || "—"}）—— 各列只按自己列名上那串日期读。`)}
            </p>
          )}

          <div className="overflow-x-auto">
            <table className="w-full text-[11px]">
              <thead>
                <tr className="text-left text-[10px] text-muted-foreground">
                  {GROUPS.map(g => {
                    const span = COLS.filter(c => c[2] === g.key).length;
                    return <th key={g.key} colSpan={span}
                      className="whitespace-nowrap border-b border-border/50 px-2 py-0.5 font-medium">
                      {g.label}
                    </th>;
                  })}
                </tr>
                <tr className="text-left text-muted-foreground">
                  {COLS.map(([k, rawLabel, grp, sortable = true]) => {
                    // 日期写在列名上（用户 10-28 点名）：quad／xy 这两列的名字由接口那两份日期现填，
                    // 两批日期真同一批时才说"同一批"，否则只写自己那份日期。
                    const label = k === "quad"
                      ? datedColLabel(rawLabel, recent?.quad_dates, recent?.quad_sessions ?? null)
                      : k === "xy"
                        ? datedColLabel(rawLabel, recent?.xy_dates, recent?.xy_sessions ?? null)
                        : rawLabel;
                    const pin = k === "code" ? "col-pin col-pin-1"
                      : k === "name" ? "col-pin col-pin-2" : "";
                    return sortable
                      ? <SortHeader key={k} label={label} sortKey={k} dir={srt.dirOf(k)}
                        onSort={() => { x.clearAll(); srt.toggle(k); }}
                        title={`${colTitle(k, th)}｜${plain(grp === "day"
                          ? "首板当日那一根；点列头排序用的是接口交出的那一份数，缺数恒排最后"
                          : grp === "energy"
                            ? "倍量与抢跑那一族（用户 10-28 点名移到最后）：分母各不相同，悬停里各带自己的口径原句"
                            : "近期与盘中那一组：排序按接口交出的原值，缺数（—）恒排最后，不按 0 参与比较")}`}
                        className={cn("whitespace-nowrap", k === "reason" && "min-w-[15rem] max-w-[24rem]", pin)} />
                      : <th key={k} className={cn("whitespace-nowrap px-2 py-2 font-medium", pin)}
                        title={`${colTitle(k, th)}｜${plain(NO_SORT_WHY[k] ?? "这一格没有一把可比的大小，所以不给点")}`}>
                        {label}
                      </th>;
                  })}
                </tr>
              </thead>
              <tbody>
                {shown.map((r, i) => (
                  <tr key={`${r.code}-${i}`} {...x.rowProps(i, r.code, undefined,
                    plain(`这一行是 ${dash(r.name || r.code)} 的首板当日（场次 ${dash(d.date)}）；`
                      + `左边钉住的是代码与名称（横滚不动），中间是当日那一根与近几场走势，`
                      + `最右是倍量与抢跑那一族`))}>
                    <td {...x.cellProps(i, "code", "whitespace-nowrap px-2 py-1 font-mono col-pin col-pin-1")}>
                      <StockLink code={r.code} className="font-mono hover:text-primary">{dash(r.code)}</StockLink>
                    </td>
                    <td {...x.cellProps(i, "name", "whitespace-nowrap px-2 py-1 col-pin col-pin-2")}>
                      <StockLink code={r.code} name={r.name} className="px-2 py-1 font-medium hover:text-primary" />
                      {r.is_one_word === true && (
                        <span className="ml-1 rounded bg-warning/15 px-1 text-[10px] text-warning"
                          title={th("launch")}>一字</span>
                      )}
                      {r.market && <span className="ml-1 text-[10px] text-muted-foreground/60">{r.market}</span>}
                    </td>
                    <td {...x.cellProps(i, "board", "whitespace-nowrap px-2 py-1 text-muted-foreground")}>
                      {dash(r.board)}
                    </td>
                    <td {...x.cellProps(i, "boards_20d", "whitespace-nowrap px-2 py-1 font-mono tabular-nums")}
                      title={boardsWindowTitle(r, boardsWin, th("boards_20d"))}>
                      {boardsWindowCell(r, boardsWin)}
                    </td>
                    <td {...x.cellProps(i, "quad", "px-2 py-1 font-mono text-[10px] whitespace-nowrap")}
                      title={quadTitle(r, th("quad"))}>
                      {quadCell(r)}
                    </td>
                    {/* XY 合计名次：数读 `analytics.xy_rank_daily`（加工段第 37 步），屏上一个加法都不做。
                        越小越靠前那句方向只用后端 `ranking.DIRECTION_NOTE` 一份（在 definitions.xy 里带出）。 */}
                    <td {...x.cellProps(i, "xy", "px-2 py-1 font-mono text-[10px] whitespace-nowrap tabular-nums")}
                      title={xyTitle(r, th("xy"))}>
                      {xyCell(r)}
                    </td>
                    <td {...x.cellProps(i, "launch_turnover", "whitespace-nowrap px-2 py-1 font-mono tabular-nums")}
                      title={th("launch")}>
                      {r.launch_turnover == null ? NULL_TEXT : `${trimNum(r.launch_turnover, 4)}%`}
                    </td>
                    <td {...x.cellProps(i, "first_seal_at", "whitespace-nowrap px-2 py-1 font-mono")}
                      title={r.first_seal_at ? th("seal_time")
                        : plain("这一格是 —：源站这一行没给首封时间戳，不是「封得晚」。后端把它排在最后（nulls last）。")}>
                      {sealText(r.first_seal_at)}
                    </td>
                    <td {...x.cellProps(i, "last_seal_at", "whitespace-nowrap px-2 py-1 font-mono")}
                      title={th("seal_time")}>
                      {sealText(r.last_seal_at)}
                    </td>
                    <td {...x.cellProps(i, "break_count", "whitespace-nowrap px-2 py-1 font-mono tabular-nums")}
                      title={r.break_count == null
                        ? plain("源站这一行没给这个字段，不是 0 次没炸（这一格是 —）。")
                        : th("break_count")}>
                      {breakCountText(r.break_count)}
                    </td>
                    <td {...x.cellProps(i, "order_amount_wan", "whitespace-nowrap px-2 py-1 font-mono tabular-nums")}
                      title={th("launch")}>
                      {wanText(r.order_amount_wan)}
                    </td>
                    {/* 用户 10-27 点名：涨停原因这一列宽一点 —— 那是一句话，被压成两三个词就白写了 */}
                    <td {...x.cellProps(i, "reason", "min-w-[16rem] max-w-[30rem] whitespace-normal px-2 py-1")}>
                      <ReasonCell r={r} />
                    </td>

                    {/* 10-27：整组「前一交易日那一根」八列删掉了（与首页短线情绪那块是同一批数）。
                        接口仍交出 `prev_*`，只是这一屏不再摆。 */}
                    <td {...x.cellProps(i, "chg5", "whitespace-nowrap px-2 py-1 font-mono tabular-nums")}
                      title={th("trend")}>
                      {pctText(r.chg5)}
                    </td>
                    <td {...x.cellProps(i, "chg10", "whitespace-nowrap px-2 py-1 font-mono tabular-nums")}
                      title={th("trend")}>
                      {pctText(r.chg10)}
                    </td>
                    <td {...x.cellProps(i, "chg15", "whitespace-nowrap px-2 py-1 font-mono tabular-nums")}
                      title={th("trend")}>
                      {pctText(r.chg15)}
                    </td>
                    {/* 盘中三列：档位显示中文、排序走后端交的 `spike_tier_rank`；
                        「没进榜」与「这一族没读到」是两件事，一个写字面、一个写 —。 */}
                    <td {...x.cellProps(i, "spike_tier_rank", "whitespace-nowrap px-2 py-1")}
                      title={spikeTitle(r, th("spike"))}>
                      {r.spike_tier ?? (r.spike_checked ? "没进异动榜" : NULL_TEXT)}
                    </td>
                    <td {...x.cellProps(i, "spike_first5_at", "whitespace-nowrap px-2 py-1 font-mono")}
                      title={spikeTitle(r, th("spike"))}>
                      {dash(r.spike_first5_at)}
                    </td>
                    <td {...x.cellProps(i, "spike_speed15_pct", "whitespace-nowrap px-2 py-1 font-mono tabular-nums")}
                      title={spikeTitle(r, th("spike"))}>
                      {r.spike_speed15_pct == null ? NULL_TEXT : `${trimNum(r.spike_speed15_pct, 2)}%`}
                    </td>
                    {/* 这只票自己的历史位置五列（10-27 用户点名）。两把尺并排：封住与触及；
                        0 是真读数，— 才是读不到（后端把原因写在 missing 里）。 */}
                    <td {...x.cellProps(i, "lu_sealed_3m", "whitespace-nowrap px-2 py-1 font-mono tabular-nums")}
                      title={limitCountTitle(r, "lu", hist, th("hist_limits"))}>
                      {limitCountCell(r, "lu")}
                    </td>
                    <td {...x.cellProps(i, "ld_sealed_3m", "whitespace-nowrap px-2 py-1 font-mono tabular-nums")}
                      title={limitCountTitle(r, "ld", hist, th("hist_limits"))}>
                      {limitCountCell(r, "ld")}
                    </td>
                    <td {...x.cellProps(i, "max_consec_6m", "whitespace-nowrap px-2 py-1 font-mono tabular-nums")}
                      title={consecTitle(r, hist, th("hist_consec"))}>
                      {consecCell(r)}
                    </td>
                    <td {...x.cellProps(i, "pos_2y", "whitespace-nowrap px-2 py-1 font-mono tabular-nums")}
                      title={pos2yTitle(r, hist, th("hist_pos"))}>
                      {pos2yCell(r, hist)}
                    </td>
                    <td {...x.cellProps(i, "ma_stack_rank", "whitespace-nowrap px-2 py-1")}
                      title={maStackTitle(r, hist, th("hist_ma"))}>
                      {maStackCell(r)}
                    </td>
                    {/* ↓ 「倍量与抢跑」那一族五列（用户 10-28 点名移到最后：「这几个是什么意思，看不懂」）。
                        每列的口径原句仍各自读后端 definitions（trend／dbl_windows／energy 三份），
                        挪的只是位置 —— 一列都不许因为"看不懂"就撤掉或换成页面自己编的说法。 */}
                    <td {...x.cellProps(i, "vol_grab_ratio", "whitespace-nowrap px-2 py-1 font-mono tabular-nums")}
                      title={th("trend")}>
                      {ratioText(r.vol_grab_ratio)}
                    </td>
                    <td {...x.cellProps(i, "dbl10", "whitespace-nowrap px-2 py-1 font-mono tabular-nums")}
                      title={dblTitle(r, 10, th("dbl_windows"))}>
                      {dblCell(r, 10)}
                    </td>
                    <td {...x.cellProps(i, "dbl20", "whitespace-nowrap px-2 py-1 font-mono tabular-nums")}
                      title={dblTitle(r, 20, th("dbl_windows"))}>
                      {dblCell(r, 20)}
                    </td>
                    <td {...x.cellProps(i, "dbl_energy", "whitespace-nowrap px-2 py-1 font-mono tabular-nums")}
                      title={r.dbl_energy == null
                        ? plain("这一格是 —：这一行没有倍量加权分（没有分就没有名次，不是 0 分）。")
                        : th("energy")}>
                      {trimNum(r.dbl_energy)}
                    </td>
                    <td {...x.cellProps(i, "dbl_rank", "whitespace-nowrap px-2 py-1 font-mono tabular-nums")}
                      title={rankTitle(th("energy"), { scored: num(r.energy_universe) }, r.dbl_rank ?? null)}>
                      {rankText(r.dbl_rank)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <div className="mt-2 flex flex-wrap items-center gap-2 text-[11px]">
            <span className="text-muted-foreground">
              接口交出 {rows.length} 行（取数上限 {intText(d.limit)} 只{shapeFilter.length ? `，已按形态档筛${plain("（在库里筛、先于上限）")}` : ""}）
              · 本场首板共 {intText(d.first_count)} 只 · 每页 {pageSize} 行 · 现在是第 {cur}/{pages} 页
            </span>
            {cur > 1 && <button type="button" onClick={() => { x.clearAll(); setPage(cur - 1); }}
              className="rounded-lg bg-muted/40 px-2 py-0.5 hover:bg-muted/70">上一页</button>}
            {cur < pages && <button type="button" onClick={() => { x.clearAll(); setPage(cur + 1); }}
              className="rounded-lg bg-primary/15 px-2 py-0.5 font-medium text-primary hover:bg-primary/25">下一页</button>}
          </div>

          {/* 题材词榜：只列出现 2 次及以上的词（1 次的不叫重复出现），旁边点名被折掉的那几个 */}
          <div className="mt-3">
            <h4 className="text-[12px] font-bold text-foreground">
              涨停原因的题材词重复榜
              <span className="ml-1 font-normal text-muted-foreground">
                {plain(`本场全部首板口径 · 出现 2 次及以上 · 共 ${words.length} 个词`)}
              </span>
            </h4>
            <p className="mt-0.5 text-[11px] leading-relaxed text-muted-foreground">
              {plain(d?.reason_tally?.summary_meta?.scope
                ?? "这一格的范围那句（summary_meta.scope）后端没交 —— 页面不说这批词是从哪一批里数出来的。")}
            </p>
            {words.length === 0 ? (
              <p className="mt-1 text-[11px] leading-relaxed text-muted-foreground">
                {plain("这场没有一个词出现两次以上 —— 这是数过了（今天就是散的），与「原因没取到」是两件事；"
                  + `上面那一格「${cov.noReason} 只没有原因串」说的才是没取到。`)}
              </p>
            ) : (
              <ul className="mt-1 flex flex-wrap gap-1.5">
                {words.map(w => (
                  <li key={w.name} className="rounded-lg border border-border/60 bg-muted/20 px-2 py-0.5 text-[11px]">
                    {w.name}
                    <span className="ml-1 font-mono text-primary">{intText(w.count)}</span>
                  </li>
                ))}
              </ul>
            )}
            {/* 用户 10-27 点名这两句默认收起、10-31 点名搬进标题行那颗按钮：整段进了上面抽屉的
                「母体与读法」，「另有 N 个词没列进来」的条数也在那一节里逐字写着。 */}
          </div>

          <Disclaimer compact />

        </>
      )}

      {d && rows.length === 0 && (
        <p className="mt-1 text-[11px] leading-relaxed text-warning">
          {plain("接口这场一行都没交出来，而本场首板总数那一格是有数的 —— 「筛完剩下 0 行」与「这一场没算过」"
            + "是两件事，先清掉形态筛看一眼，再看上面那条声明。")}
        </p>
      )}
    </GlassCard>
  );
}

/** 涨停原因那一格：有串原样念（数据值不包 plain），没串按 `reason_missing` 分开念两种说法。 */
function ReasonCell({ r }: { r: FbdRow }) {
  const c = reasonCell(r);
  return c.isReason
    ? <span className="text-foreground">{c.text}</span>
    : <span className="text-[10px] leading-relaxed text-muted-foreground">{c.text}</span>;
}

/** 列头悬停：口径句子全部读后端 definitions 那一份，页面不另写第二份判据。 */
function colTitle(key: string, th: (k: string) => string): string {
  switch (key) {
    case "code":
      return plain("库里的代码原样（带市场后缀）；点它或名字开东财行情页，双击这一行开个股深度复盘");
    case "name":
      return plain("名称来自 reference_data.security，缺名字时格子退回代码 —— 空着不等于没有这只票");
    case "board":
      return plain("板别（主板／创业板／科创板／北交所），涨停幅度那一档由它定");
    case "boards_20d":
      return th("boards_20d");
    case "quad":
      return th("quad");
    case "xy":
      return th("xy");
    case "launch_turnover":
    case "order_amount_wan":
      return th("launch");
    case "first_seal_at":
    case "last_seal_at":
      return th("seal_time");
    case "break_count":
      return th("break_count");
    case "reason":
      return th("reason");
    case "chg5":
    case "chg10":
    case "chg15":
    case "vol_grab_ratio":
      return th("trend");
    case "dbl10":
    case "dbl20":
      return th("dbl_windows");
    case "dbl_energy":
    case "dbl_rank":
      return th("energy");
    case "spike_tier_rank":
    case "spike_first5_at":
    case "spike_speed15_pct":
      return th("spike");
    case "lu_sealed_3m":
    case "ld_sealed_3m":
      return th("hist_limits");
    case "max_consec_6m":
      return th("hist_consec");
    case "pos_2y":
      return th("hist_pos");
    case "ma_stack_rank":
      return th("hist_ma");
    default:
      // 新加一列忘了在这儿登记，悬停会念"这一列的口径文字后端没交"——比默默给一句别的强
      return plain("这一列的口径说明还没登记到列头（页面不猜）｜") + th("universe");
  }
}
