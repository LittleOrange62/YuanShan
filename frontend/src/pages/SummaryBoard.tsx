import { createContext, useContext, useEffect, useState } from "react";
import { SortHeader } from "@/components/ui/SortHeader";
import { useTableSort } from "@/lib/useSort";
import { Link } from "react-router-dom";
import { RefreshCw } from "lucide-react";
import { MarketPanorama } from "@/components/home/MarketPanorama";
import { SectorCyclePanel } from "@/components/qd/SectorCyclePanel";
import {
  ConsecPremiumCard, LadderCard, MoneyEffectCard, PromotionCard, splitMetrics,
} from "@/components/EmotionMetricsPanel";
import { Caliber } from "@/components/ui/Caliber";
import { Disclaimer } from "@/components/ui/Disclaimer";
import { ExplainBox } from "@/components/ui/DataPanel";
import { GlassCard } from "@/components/ui/GlassCard";
import { PageHeader } from "@/components/ui/PageHeader";
import { RecentReports } from "@/components/workspace/RecentReports";
import { api } from "@/lib/api";
import type { MarketOverview, ShortTermEmotion } from "@/lib/api";
import { agentFetch, plain } from "@/lib/agent";
import type { ReviewData } from "@/lib/agent";
import { dash, pct } from "@/lib/qd";
import { countCollapsed, staleVerdict } from "@/lib/staleness";
import type { LadderTier } from "@/lib/qd";
import {
  useBoardLadder, useLeaderFollow, useOwnSectorCycle, useSectorCycle,
} from "@/lib/useQd";

/**
 * **首页**（原名「汇总看板」，2026-09-26 用户裁定：取消工作台首页，这一屏改名当首页）。
 * 内容是《汇总看板优化稿》§3 的 A/B/C 三屏合一，另带原首页的「最近报告」块。
 *
 * 这一页存在的唯一理由是**跨模块一屏看完**，所以它不新算任何指标，只是把已有的面板与端点摆到一起，
 * 并守两条规矩（⚠️ 「不新建任何端点」这句到 2026-10-21 有一条例外：「风险提示 · 宽度 × 阿姆斯」那块
 * 走新建的 `GET /api/quant/risk-signal`，但它同样不在前端算 —— 宽度与阿姆斯两个数由加工链的 `breadth`
 * （d6）步每天落库，读侧 `duanxian/risk_signal.py` 只做分位与背离）：
 *
 *  1. **一个指标只有一个主数。** 涨停家数在本页只认「qd 轨 · 收盘双边判据」，
 *     东财公开池与乐咕全市场读数一律降级为「对照」，脚注写清为什么会差：
 *     库里 `analytics.market_breadth` 用的是单边判据 `close >= limit_up - 0.01`，
 *     会把「收盘高于涨停价」的行也算成涨停，而 `reference_data.security.is_st`
 *     是过期快照，摘帽票仍按 5% 档给涨停价。2026-09-23 实测：该表 56、
 *     双边重算 52、qd 轨 51。口径修正前，那张表不进本页任何格子。
 *     ⚠️ 10-21 那条例外说的仍然是**涨停家数**：风险提示那块读的是同一张表的宽度两列
 *     （`up_count`/`down_count`/`up_ratio`）与成交额两列（`up_amount`/`down_amount`/`arms_ratio`），
 *     这些列没有单边判据那件事（涨/跌按 `pct_change` 的正负分，与涨停无关），所以不冲突。
 *  2. **own / qd 双轨不合并**（《页面展示工作指南 02》）。本轨没有的能力就显示没有，
 *     不拿 qd 的值顶上。
 *
 * 全页只读（后端身份 `ft_reader`）。日期不预填「今天」—— 今天可能没收盘、也可能
 * 不是交易日；缺省时由后端各表取自己的最新 trade_date，所以不同块可能不同日。
 */

/** 每块卡片要拿去判新旧的两个事实，由这一屏一次性交给 `Block`（见 `lib/staleness.ts`） */
const NewestSession = createContext<string>("");
const LibraryBehind = createContext<boolean>(false);

/** 比率：后端给的是 0~1 的小数，null 不是 0%（01 规范第二条） */
function rate(v: number | null | undefined, digits = 1): string {
  return typeof v === "number" && Number.isFinite(v) ? `${(v * 100).toFixed(digits)}%` : "—";
}

export function SummaryBoard() {
  const [date, setDate] = useState("");
  const [nonce, setNonce] = useState(0);

  // qd 轨：PG 里的 qd.* 九张表与 analytics.*
  // 「情绪周期」那一屏 2026-10-06 起不在首页了（见下面那段注释），所以这里也不再取它 ——
  // 取而不用等于每天白打一次全库排名请求。
  const ladder = useBoardLadder(date, nonce);
  const sector = useSectorCycle(date, nonce);
  const ownSector = useOwnSectorCycle(date, nonce);
  const follow = useLeaderFollow(date, nonce);

  // 在线轨（东财公开池 / 乐咕全市场情绪）：在本页只作对照与资金面
  const [emo, setEmo] = useState<ShortTermEmotion | null>(null);
  const [emoErr, setEmoErr] = useState("");
  const [ov, setOv] = useState<MarketOverview | null>(null);
  const ovSort = useTableSort();
  const [ovErr, setOvErr] = useState("");
  // 赚钱效应/晋级率/梯队断层这几张卡是复盘报告的产物，没报告就说没报告
  const [report, setReport] = useState<ReviewData | null>(null);
  const [reportErr, setReportErr] = useState("");
  // 「库里实际有数的最近一场」+ 缺口说明（后端 `latest_data_session()` 一处判据）。
  // note 非空 = 日历上已经收盘的那一天库里没有数 —— 这时候整屏每一块都是旧数。
  const [expected, setExpected] = useState<{ date: string | null; note: string | null }>({ date: null, note: null });

  useEffect(() => {
    let live = true;
    agentFetch<{ date: string | null; note: string | null }>("/api/review/expected-date")
      .then(d => { if (live) setExpected({ date: d?.date ?? null, note: d?.note ?? null }); })
      .catch(() => { if (live) setExpected({ date: null, note: null }); });
  }, [nonce]);

  useEffect(() => {
    let live = true;
    const q = date ? `?date=${date}` : "";
    api.emotion()
      .then(d => { if (live) { setEmo(d); setEmoErr(""); } })
      .catch(e => { if (live) setEmoErr(String(e?.message ?? e)); });
    api.marketOverview()
      .then(d => { if (live) { setOv(d); setOvErr(""); } })
      .catch(e => { if (live) setOvErr(String(e?.message ?? e)); });
    // 后端「这天没有报告」返回 200 + 空对象，不会走到 catch；抛错的只有真故障
    agentFetch<ReviewData>(`/api/review/latest${q}`)
      .then(d => { if (live) { setReport(d); setReportErr(""); } })
      .catch(e => { if (live) setReportErr(String(e?.message ?? e)); });
    return () => { live = false; };
  }, [date, nonce]);

  // 主数：qd 轨天梯各档家数之和（收盘双边判据）。available:false 时不猜，直接空
  const ladderPayload = ladder.kind === "ok" && ladder.payload.available === true ? ladder.payload : null;
  const tiers = (ladderPayload?.tiers ?? []) as LadderTier[];
  const ztMain = tiers.reduce((n, t) => n + (t.stock_count ?? 0), 0);
  const ladderBlocked = ladder.kind === "error" ? `天梯取不到：${ladder.reason}`
    : ladder.kind === "loading" ? "天梯取数中…"
      : ladder.payload.available === false ? `天梯无数据：${dash(ladder.payload.reason)}` : "";

  const em = splitMetrics(report?.emotion_metrics);
  const hasReport = !!(report && (report.trade_date || report.target_date || report.emotion_metrics));
  const firstOk = [ladder, sector]
    .map(s => (s.kind === "ok" ? (s.payload.date ?? "") : ""))
    .filter(Boolean);
  const effective = date || firstOk[0] || emo?.date || ov?.sentiment?.date || "";
  // 新旧判据的两个输入：这一屏的主数 + "整库有没有跟上已收盘的那场"。
  // 手填过日期（date 非空）就不再拿"整库落后"去收起 —— 那是用户自己选的要看的那一天。
  const libraryBehind = !date && !!expected.note;

  // 每一块的 as-of 集中在这里，`Block` 只取字段 —— 原来分散在六个 JSX 属性里，
  // 父层没法知道"这一屏收起了几块"，`countCollapsed` 因此长期零调用（写了判据没人读数）。
  // ⚠️ 这张表的键必须**与页面上真正存在的 Block 一一对应**：多一个键就多算一块
  // 「不是最新就收起」的候选，收起数字就会比屏幕上的块数大（2026-10-06 摘掉情绪周期那一块时踩过）。
  const blockAsOf = {
    ladder: ladderPayload?.date ?? effective,
    overview: ov?.sentiment?.date ?? ladderPayload?.date ?? effective,
    effect: report?.emotion_metrics?.date ?? effective,
    sector: sector.kind === "ok" ? sector.payload.date ?? "" : "",
    review: report?.trade_date ?? report?.target_date ?? effective,
  };
  const newestDay = expected.date || effective;
  const collapsedBlocks = countCollapsed(
    Object.values(blockAsOf).map(a => staleVerdict(a, newestDay, libraryBehind)));

  // 在线源的场次可能比库里的新（今天收盘后在线源已有、本地加工还没跑）。
  // 这时候把两个数并排摆一起 = 诱使人横向比较，所以必须显式说明不是同一场。
  const mainDay = (ladderPayload?.date ?? effective ?? "").slice(0, 10);
  const emoDay = (emo?.date ?? "").slice(0, 10);
  const ovDay = (ov?.sentiment?.date ?? "").slice(0, 10);
  const offSession = [...new Set([emoDay, ovDay].filter(d => d && mainDay && d !== mainDay))];
  const sessionWarning = offSession.length
    ? `⚠ 下面这些对照列不是同一场：对照来自 ${offSession.join(" / ")}，本页主数是 ${mainDay}。`
      + "在线源已经收了新一天的盘，本地加工还没跑到 —— 两边的数只能各自看，不能相减、不能比大小。"
    : "";

  return (
    <NewestSession.Provider value={expected.date || effective}>
      <LibraryBehind.Provider value={libraryBehind}>
    <div className="mx-auto max-w-6xl">
      {collapsedBlocks > 0 && (
        <p role="status" className="mb-2 rounded-lg border border-warning/40 bg-warning/10 px-3 py-2 text-[12px] leading-relaxed text-warning">
          下面 <strong>{collapsedBlocks} 块</strong>不是最新一场的数，已按规矩默认收起（收起是默认，不是判决，点开照常看）。
          收起而不删掉，是因为旧数仍能回答"那一天怎么样"，但不能当作"今天怎么样"来读。
        </p>
      )}
      {/* 总闸自己坏了也要出声：基准日拿不到时没有任何一块会被收起，
          "没被收起"就绝不能被读成"都是最新的"（09-30 自查抓到的主路径）。 */}
      {!newestDay && (
        <p role="status" className="mb-2 rounded-lg border border-warning/40 bg-warning/10 px-3 py-2 text-[11px] leading-relaxed text-warning">
          判断新旧的基准日没拿到（接口没交这一场是哪天）—— 所以这一屏一块都没收起，
          这不等于下面都是最新的数，请各块自己看 as-of。
        </p>
      )}
      {!expected.date && !date && (
        <p role="status" className="mb-2 rounded-lg border border-border bg-muted/30 px-3 py-2 text-[11px] leading-relaxed text-muted-foreground">
          「日历上最近已收盘的那一场」没拿到（<code>/api/review/expected-date</code>），所以现在只能比出
          <strong>"这一块比本页主数旧"</strong>，<strong>不能证明主数本身就是最新一场</strong> ——
          这句是必要的自白：判新旧的闸自己缺输入时，页面不许反过来给每块盖"最新"的章。
        </p>
      )}
      <PageHeader title="首页" level={1}
        subtitle="盯盘、情绪梯队、板块资金并到一屏；不新算指标、不合并口径、不补数据。"
        actions={
          <div className="flex items-center gap-2">
            <label className="text-[11px] text-muted-foreground">
              交易日
              <input type="date" value={date} className="ml-1.5 rounded border border-border bg-background p-1.5 text-[12px]"
                onChange={e => setDate(e.target.value)} />
            </label>
            <button type="button" onClick={() => setNonce(n => n + 1)}
              className="flex items-center gap-1 rounded-lg bg-primary/15 px-2.5 py-1.5 text-[12px] font-medium text-primary hover:bg-primary/25">
              <RefreshCw className="h-3.5 w-3.5" /> 重新取数
            </button>
          </div>
        } />

      {/* 用户 10-22「屏幕上太多东西看着累」：日期那个数留着（它是这一屏的坐标），
          两句解释（未指定时各表怎么取数、本页只读）收进一处点开可见的框；
          刚被从副标题里移出来的那句"业务页在哪进"也一并放这里。 */}
      <p className="mb-1 text-[11px] leading-relaxed text-muted-foreground">
        当前生效交易日：<b className="text-foreground">{dash(effective)}</b>
      </p>
      <ExplainBox title="这一屏的取数口径（未指定交易日时各表各自取哪一场 · 本页只读 · 业务页入口在哪）">
        {date ? <p>交易日由上面那个框指定，本页各块吃的都是这一天。</p>
          : <p>未指定 → 各表分别取自己最新的 trade_date，不同块可能不是同一天。</p>}
        <p>本页只读（后端账号 ft_reader）。</p>
        <p>全部业务页在「系统配置 → 复盘工具，一站直达」里进。</p>
      </ExplainBox>

      {libraryBehind && (
        <p className="mb-4 rounded-lg border border-warning/50 bg-warning/10 p-3 text-[12px] leading-relaxed text-warning">
          <b>下面这些块默认被收起了：本地加工没跟上已经收盘的那一场。</b>
          {expected.note}
          {" "}收起不等于没有 —— 每一块右上角都有「还是看这一块」，点开照常看；但请按上面那个日子读它，
          不要当今天的数据用。这一屏以前一律平铺，旧数和新样子的卡片混在一起、看不出来（2026-09-28 用户裁定改这条）。
        </p>
      )}

      <div className="space-y-5">

        {/* ───────────────────── 涨停与连板：主数只有一个 ───────────────────── */}
        <Block title="涨停与连板" asOf={blockAsOf.ladder}
          channel="主数 PG qd.board_ladder（收盘双边判据）｜对照 东财公开池、乐咕全市场读数"
          caliber="主数 = 天梯各档家数之和，判据 abs(close − limit_up) ≤ 0.01。对照两列来自在线源，与本库不同源、不同判据，三个数不相等是事实，不是谁漏了。"
          warn={sessionWarning}
          noteTitle="为什么库里另一张涨停表不进这一屏 · 口径说明"
          note={`库里还有一张 analytics.market_breadth 也算涨停，但它是单边判据（收盘 ≥ 涨停价就算），会把「收盘高于涨停价」的行和摘帽后仍按 5% 档算涨停价的行一起收进来 —— 2026-09-23 实测它给 56、双边重算 52、本页主数 51。那张表在判据和 is_st 维表修好之前不进本页任何格子。`}>
          <div className="grid gap-3 md:grid-cols-3">
            <div className="rounded-xl border border-primary/30 bg-primary/5 p-4">
              <p className="text-[11px] text-muted-foreground">涨停家数（本页主数）</p>
              <p className="mt-0.5 font-mono text-3xl font-extrabold text-glow">{tiers.length ? ztMain : "—"}</p>
              <p className="mt-1.5 text-[11px] leading-relaxed text-muted-foreground">
                {tiers.length
                  ? `各档：${tiers.map(t => `${t.tier_label ?? `${t.board_tier}板`} ${t.stock_count ?? 0}`).join("｜")}`
                  : dash(ladderBlocked)}
              </p>
              {!!ladderPayload?.leader?.leader_stock_name && (
                <p className="mt-1.5 text-[11px] text-muted-foreground">
                  龙头 {ladderPayload.leader.leader_stock_name}
                  {ladderPayload.leader.leader_board_count ? ` ${ladderPayload.leader.leader_board_count}板` : ""}
                  {ladderPayload.leader.leader_status_label ? ` · ${ladderPayload.leader.leader_status_label}` : ""}
                </p>
              )}
            </div>

            <KV title={`对照 · 本地库涨停母体（${emoDay ? emoDay.slice(5) : "日期未知"}）`} hint={emoErr || (emo ? undefined : "取数中…")}>
              <Row k="涨停 / 跌停" v={emo ? `${dash(emo.zt_count)} / ${dash(emo.dt_count)}` : "—"} />
              <Row k="摸板没封住 / 上一场封住家数"
                v={emo ? `${dash(emo.zb_count)} / ${emo.prev_sealed == null ? "—" : `${dash(emo.prev_sealed)}（${emo.prev_date?.slice(5) ?? "—"}）`}` : "—"} />
              <Row k="最高连板 / 连板家数" v={emo ? `${dash(emo.max_boards)} / ${dash(emo.lianban_count)}` : "—"} />
              <Row k="封板率（封住÷尝试涨停）" v={emo ? rate(emo.seal_rate) : "—"} />
              <Row k="炸板率（炸板÷尝试涨停）" v={emo ? rate(emo.break_rate) : "—"} />
              <Row k="晋级率（上一场封住、本场又封住）" v={emo ? rate(emo.promotion_rate) : "—"} />
            </KV>

            <KV title={`对照 · 乐咕全市场读数（${ovDay ? ovDay.slice(5) : "日期未知"}）`} hint={ovErr || (ov ? undefined : "取数中…")}>
              <Row k="涨 / 跌 / 平家数" v={ov ? `${dash(ov.sentiment.up)} / ${dash(ov.sentiment.down)} / ${dash(ov.sentiment.flat)}` : "—"} />
              <Row k="涨停（真实涨停）" v={ov ? `${dash(ov.sentiment.zt)}（${dash(ov.sentiment.zt_real)}）` : "—"} />
              <Row k="跌停（真实跌停）" v={ov ? `${dash(ov.sentiment.dt)}（${dash(ov.sentiment.dt_real)}）` : "—"} />
              <Row k="宽度分档 / 投机档" v={ov ? `${dash(ov.sentiment.breadth)} / ${dash(ov.sentiment.speculation)}` : "—"} />
              <Row k="活跃度" v={ov ? dash(ov.sentiment.active) : "—"} />
              <Row k="统计日 / 更新" v={ov ? `${dash(ov.sentiment.date)} / ${dash(ov.updated)}` : "—"} />
            </KV>
          </div>

        </Block>

        {/* ───────────────────── 盘面数据：原独立页整屏搬进来，占掉「板级梯队」的位置 ─────────────────────
            用户 2026-09-27 裁定。板级天梯（含"龙头是怎么选出来的"那套 tie-break 说明）**不删**，
            移到「quantdash 口径对照」页 —— 首页这一块换成了带个股的短线情绪表。
            外面套 `Block` 只为了同一件事：让它受同一条"不是最新就默认收起"的判据管
            （这一块的场次来自同一个 overview，所以 as-of 拿得到，不必另数一套）。 */}
        <Block title="盘面数据 · 涨停与连板（含个股）"
          channel="PG qd.board_ladder / analytics.*（个股明细读 analytics.limit_up_pool_daily：家数、连板、首封都由本地日线＋分钟线自算）｜只有题材串、封单额是每日加工时从东财涨停池采一次入库，请求路径不再打在线源"
          asOf={blockAsOf.overview}
          note="这一整块就是你原先的「盘面数据」页。它和上面的天梯不同源：涨停家数以 PG 双边判据为主数，这里的家数、连板、炸板、首封一律读 analytics.limit_up_pool_daily 的落库行（本地判据），题材串与封单额只有源站给了才有 —— 两侧数字不一致时以各自口径为准，不相减。">
          <MarketPanorama />
        </Block>

        {/* ───────────────────── 风险提示 · 宽度 × 阿姆斯：2026-10-27 用户点名搬进「盘面数据」那一屏，
            摆在异动提醒上面（挂载点在 `MarketPanorama.tsx` 的 4c-0）。判据、20 期分位、档位阈值
            只住 `duanxian/risk_signal.py` 一处；它不进 `Block` 也不占 `blockAsOf` —— 窗口本来就是
            几个月，拿盘面的 as-of 去收起它反而会把"背离判不了"的原因藏掉。 */}

        {/* ───────────────────── 情绪周期：2026-10-06 用户裁定从首页拿走 ─────────────────────
            这一屏整块就是 quantdash 口径轨的三套 stage 并排，而「quantdash 口径对照」页
            已经把同一套面板（连板天梯 / 板块周期 / 快讯）摊得更开。首页留一份就够了，
            入口留在下面「今日复盘与入口」那条链接上 —— 拿掉的是重复的展示，不是可达性。 */}

        {/* ───────────────────── 温度类指标：来自复盘报告 ───────────────────── */}
        <Block title="赚钱效应 · 晋级率 · 梯队断层" asOf={blockAsOf.effect}
          channel="复盘报告产物（duanxian/emotion_metrics）"
          caliber="样本是「昨日已涨停」这个事后固定名单，不代表全市场收益；晋级率分母是上一场涨停家数，两张卡的「昨日」不同日时以卡内标注为准。"
          note="这几张卡只在生成复盘报告时产出。这天没有报告就是没有 —— 本页不单独重算，免得摆出第二套和报告不一致的数。">
          {reportErr && <p className="mb-3 rounded-lg border border-danger/40 bg-danger/10 p-2.5 text-[12px] text-danger">报告读取故障：{reportErr}</p>}
          {hasReport ? (
            <div className="grid gap-3 md:grid-cols-2">
              <MoneyEffectCard me={em.me} />
              <PromotionCard pr={em.pr} />
              <LadderCard lg={em.lg} />
              <ConsecPremiumCard cp={em.cp} />
            </div>
          ) : (
            <p className="text-[12px] leading-relaxed text-muted-foreground">
              {effective ? `${effective} 这天还没有复盘报告` : "还没取到报告日期"}。
              <Link className="mx-1 text-primary underline" to="/agent/review">去生成</Link>
              或换一个已生成的日期 —— 生成进度、取消、失败保留旧稿与版本对照都在那一页处理。
            </p>
          )}
        </Block>

        {/* ───────────────────── 板块与资金 ───────────────────── */}
        <Block title="板块周期与资金" asOf={blockAsOf.sector}
          channel="本轨 /api/market/sector-cycle ＋ qd 轨 PG qd.sector_rotation｜资金为东财行业口径"
          caliber="两套板块算法不是同一个东西：本轨给生命周期阶段，qd 轨给轮动矩阵与领涨持续性。合并会逼另一套字段留空，所以分块摆，不做综合分。"
          note="下面的板块资金是东财行业口径的净流入，与 PG 里 market_data.fund_flow_rank（新浪逐日截面，09-28 起按场入库、一天不采就永久少一天）是两个来源，不能对照着读；首页那张「份额轮动 · 本地逐日」读的又是第三样 —— analytics.sector_flow_trend 的份额统计量，既不是净流入也不是当日快照。">
          <SectorCyclePanel own={ownSector} qd={sector} follow={follow} />
          <div className="mt-4">
            <KV title={`东财行业资金净流入 · 前 8（${ovDay ? ovDay.slice(5) : "日期未知"}）`} hint={ovErr || (ov?.sectors?.length ? undefined : (ov ? "当日无板块资金数据" : "取数中…"))}>
              <div className="overflow-x-auto">
                <table className="w-full text-[12px] tabular-nums">
                  <thead>
                    <tr className="border-b border-border text-[11px] text-muted-foreground">
                      <SortHeader label="行业" sortKey="name" dir={ovSort.dirOf("name")} onSort={ovSort.toggle} />
                      <SortHeader label="涨跌幅" sortKey="pct" dir={ovSort.dirOf("pct")} onSort={ovSort.toggle} />
                      <SortHeader label="净额(亿)" sortKey="net" dir={ovSort.dirOf("net")} onSort={ovSort.toggle}
                        title="单位亿，全列一致" />
                      <SortHeader label="流入(亿)" sortKey="inflow" dir={ovSort.dirOf("inflow")} onSort={ovSort.toggle} />
                      <SortHeader label="公司家数" sortKey="firms" dir={ovSort.dirOf("firms")} onSort={ovSort.toggle} />
                    </tr>
                  </thead>
                  <tbody>
                    {ovSort.apply(ov?.sectors ?? []).slice(0, 8).map(s => (
                      <tr key={s.name} className="border-b border-border/50 last:border-0">
                        <td className="px-1.5 py-1.5">{s.name}</td>
                        <td className="px-1.5 py-1.5 text-right">{pct(s.pct, 2)}</td>
                        <td className="px-1.5 py-1.5 text-right">{dash(s.net)}</td>
                        <td className="px-1.5 py-1.5 text-right text-muted-foreground">{dash(s.inflow)} / {dash(s.outflow)}</td>
                        <td className="px-1.5 py-1.5 text-right text-muted-foreground">{dash(s.firms)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </KV>
          </div>
        </Block>

        {/* ───────────────────── 今日复盘：只报状态，不搬正文 ───────────────────── */}
        <Block title="今日复盘与入口" asOf={blockAsOf.review}
          channel="本地报告存档 review_store"
          note="报告正文、依据与 AI 解释留在「复盘报告」那一页 —— 那里处理生成进度、取消、失败保留旧稿和版本对照。本页只给一条状态，免得把「已核对的数」和「AI 的解释」压成一层。">
          <div className="flex flex-wrap items-center gap-3 text-[12px]">
            {reportErr
              ? <span className="text-danger">状态未知：读取失败</span>
              : hasReport
                ? <>
                  <span className="rounded-lg bg-success/15 px-2.5 py-1 text-success">已生成</span>
                  <span className="text-muted-foreground">
                    交易日 {dash(report?.trade_date ?? report?.target_date)} · 生成于 {dash(report?.generated_at)}
                  </span>
                  {!!report?.warnings?.length && <span className="text-warning">带 {report.warnings.length} 条数据边界提示</span>}
                </>
                : <span className="rounded-lg bg-warning/15 px-2.5 py-1 text-warning">这一天还没有报告</span>}
          </div>
          <div className="mt-3 flex flex-wrap gap-x-4 gap-y-1.5 text-[12px]">
            <Link className="text-primary underline" to="/agent/review">复盘报告</Link>
            <Link className="text-primary underline" to="/qd-compare">quantdash 口径对照（情绪周期 / 板级天梯 / 龙头选法都在那一页）</Link>
            {/* 「盘面数据」不再是另一个页面：2026-09-27 裁定整屏并入本页，就在「涨停与连板」下面这一块。 */}
            <Link className="text-primary underline" to="/stock-review">个股深度复盘</Link>
            <Link className="text-primary underline" to="/jobs">任务与调度</Link>
          </div>
        </Block>
      </div>

      <RecentReports />

      <Disclaimer compact />
      {/* 页面自写的边界说明收进通用说明框（用户 2026-10-14），句子逐字留着没改 */}
      <ExplainBox title="这一屏只做汇总的边界 · 没做的部分写在这儿">
        <p>{plain("这一屏只做汇总：所有数字都来自已有端点与已有面板，口径差异原样摆出来，不做综合、不加权、不拿上一场的数补今天。个股评分归因、筛选器、数据链路与作业状态需要新增只读查询，见 docs/汇总看板优化稿.md §3 的 D/E 屏 —— 没做就是没做，这里不放占位数据。")}</p>
      </ExplainBox>
    </div>
      </LibraryBehind.Provider>
    </NewestSession.Provider>
  );
}

/** 区块壳：标题 + 来源通道 + as-of + 口径弹层 + 边界说明，顺序全页统一。
 *  `asOf` 是**必填**：新长出来的一块如果不交自己的行情日，编译期就过不去 ——
 *  这是"陈旧收起的规矩盖住每一块"唯一能强制的办法（用户 09-28 的规矩；
 *  09-30 自查时 `Block` 的 `asOf` 还是可选的，六块里靠人记得传，等于没有规矩）。 */
function Block({ title, channel, asOf, caliber, note, noteTitle, warn, children }: {
  title: string; channel: string; asOf: string | null | undefined; caliber?: string; note?: string;
  /** 给了 `noteTitle` 就是把这段说明收进通用说明框（用户 2026-10-14：说明与定义占了太多屏幕空间）。
   *  只有纯口径解释的段可以传这个属性；缺数、截断、误读提醒那类必须摊在屏上，所以不传。 */
  noteTitle?: string;
  warn?: string; children: React.ReactNode;
}) {
  const newest = useContext(NewestSession);
  const libraryBehind = useContext(LibraryBehind);
  const verdict = staleVerdict(asOf, newest, libraryBehind);
  // 收起是默认，不是判决：点开就照常看，收起来的那一行已经把两个日子写出来了
  const [open, setOpen] = useState(!verdict.collapsed);
  useEffect(() => { setOpen(!verdict.collapsed); }, [verdict.collapsed]);

  if (verdict.collapsed && !open) {
    return (
      <GlassCard>
        <div className="flex flex-wrap items-center gap-2">
          <h2 className="text-base font-bold">{title}</h2>
          <span className="rounded-lg bg-warning/15 px-2 py-0.5 text-[11px] text-warning">旧数 · 已收起</span>
          <span className="text-[11px] text-muted-foreground">{verdict.label}</span>
          <button type="button" onClick={() => setOpen(true)}
            className="ml-auto rounded-lg border border-border px-2.5 py-1 text-[12px] text-primary hover:bg-primary/10">
            还是看这一块
          </button>
        </div>
      </GlassCard>
    );
  }
  return (
    <GlassCard>
      <div className="mb-1 flex flex-wrap items-center gap-1.5">
        <h2 className="text-base font-bold">{title}</h2>
        {caliber && <Caliber text={caliber} />}
        <span className="ml-auto text-[11px] text-muted-foreground">as-of {dash(asOf)}</span>
        {(verdict.level === "unknown" || verdict.level === "noNewest") && (
          <span className="text-[11px] text-warning">{verdict.label}</span>
        )}
        {verdict.collapsed && (
          <button type="button" onClick={() => setOpen(false)}
            className="text-[11px] text-muted-foreground underline hover:text-foreground">收起</button>
        )}
      </div>
      <p className="mb-1 text-[11px] text-muted-foreground">来源：{channel}</p>
      {note && (noteTitle
        ? <ExplainBox title={noteTitle}>{plain(note)}</ExplainBox>
        : <p className="mb-3 mt-1 text-[11px] leading-relaxed text-muted-foreground">{plain(note)}</p>)}
      {warn && <p className="mb-3 rounded-lg border border-warning/40 bg-warning/10 p-2.5 text-[11px] leading-relaxed text-warning">{warn}</p>}
      <div className="mt-2">{children}</div>
    </GlassCard>
  );
}

function KV({ title, hint, children }: { title: string; hint?: string; children: React.ReactNode }) {
  return (
    <div className="rounded-xl border border-border p-3">
      <p className="mb-2 text-[11px] font-semibold text-muted-foreground">{title}</p>
      {hint ? <p className="py-3 text-[11px] text-warning">{hint}</p> : children}
    </div>
  );
}

function Row({ k, v }: { k: string; v: string }) {
  return (
    <div className="flex items-baseline justify-between gap-2 border-b border-border/50 py-1.5 last:border-0">
      <span className="text-[11px] text-muted-foreground">{k}</span>
      <span className="text-[13px] font-semibold tabular-nums">{v}</span>
    </div>
  );
}
