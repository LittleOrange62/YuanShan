/**
 * 「短线复盘」（原名「首板分析」→ 10-26「日间复盘及首板」→ 10-28 用户裁定改成本名；路由 `/first-board` 不变）。
 *
 * 本屏从上到下五段（10-28 四点裁定的自洽读法，用户原话「整体上移」）：
 *   ① 盘中涨跌停事件流（`components/intraday/EventStreamPanel.tsx`，只读 `GET /api/intraday/timeline`）
 *   ② 本页交易日选择器（候选项只来自接口给的 `available_dates`）
 *   ③ 涨停原因／所属行业 · 重复出现的类别 两张榜（用户 09-28 点名要，10-28 点名上移到这里）
 *   ④ 短线情绪（大块）＝ 涨停分析 ＋ 跌停分析（`components/review/LimitUpBoard.tsx`／`LimitDownBoard.tsx`）
 *   ⑤ 首板分析（从首页搬过来，挂在短线情绪之后）
 *
 * ⚠️ 同一天的另一条裁定，写给下一个会话看：**这一屏允许在界面上逐只列出个股代码与名称。**
 *   `vr/astock.py:511` 那条「原始池含 code/name，切勿直接接成 API/UI（零标的红线）」在本屏让一步 ——
 *   事件流的母体是库里那批事件行（`analytics.intraday_pool_event`），经读侧出口
 *   `duanxian/intraday_view.py` 出来，页面不直接调 `vr/astock.py` 的原始池。
 *   这不是漏改的缺陷，别把它改回去。（2026-10-26 用户原话背景：盘中数据与事件流要展示在这一屏上半部分。）
 *
 * ⚠️ 10-29 并表那一步留下的一条线：③ 两张榜的母体仍来自**东财实时池**（`/api/market/first-board`），
 *   因为「所属行业」这一列本机只有池子给得出（`reference_data.security` 没有行业列）。
 *   ④ 那两张表读的却是**本地库**（lup-1 / stock_daily）—— 同一个屏幕上并排放着两把尺的数，
 *   所以两张榜的分母那句（「这一批 N 只首板」）与表头那句「本场封住 M 家」不许合并成一句话。
 */
import { Flame, Loader2 } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { useLocation } from "react-router-dom";
import { FirstBoardDailyPanel } from "@/components/home/FirstBoardDailyPanel";
import { FirstBoardDeep } from "@/components/review/FirstBoardDeep";
import { LazySection } from "@/components/ui/LazySection";
import { PageHeader } from "@/components/ui/PageHeader";
import { GlassCard } from "@/components/ui/GlassCard";
import { DocDrawer } from "@/components/ui/DocDrawer";
import { Disclaimer } from "@/components/ui/Disclaimer";
import { EventStreamPanel } from "@/components/intraday/EventStreamPanel";
import { LimitDownBoard } from "@/components/review/LimitDownBoard";
import { LimitUpBoard } from "@/components/review/LimitUpBoard";
import { NoticeBar } from "@/components/ui/NoticeBar";
import { api, type FirstBoardData } from "@/lib/api";
import { plain } from "@/lib/agent";
import { cn } from "@/lib/utils";

const dateLabel = (d: string) =>
  d.length === 8 ? `${d.slice(0, 4)}-${d.slice(4, 6)}-${d.slice(6)}` : d;

export function FirstBoard() {
  const [data, setData] = useState<FirstBoardData | null>(null);
  const [loaded, setLoaded] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);
  // 选择的交易日：初值为空 = 由后端定"库里最近的一场"。候选项只能来自 available_dates，
  // 页面不按 new Date()/「今天」造任何日期（判定只在 vr/firstboard.py:data_session() 一处）。
  // 这一个 day 同时喂给下面四块（两张榜、涨停分析、跌停分析），换场就一起换。
  const [day, setDay] = useState<string | null>(null);

  // 带 `?to=` 进来就滚到那一块（首页的跳转入口用的就是这条）。只滚一次，之后用户自己说了算；
  // 目标块是懒挂载的，所以滚两下：第一下让它进视口触发挂载，第二下在它长高之后对齐。
  const loc = useLocation();
  const scrolled = useRef("");
  useEffect(() => {
    const to = new URLSearchParams(loc.search).get("to") || "";
    if (!to || scrolled.current === to) return;
    scrolled.current = to;
    const go = () => document.getElementById(to)?.scrollIntoView({ behavior: "smooth", block: "start" });
    go();
    const t = window.setTimeout(go, 700);
    return () => window.clearTimeout(t);
  }, [loc.search]);

  useEffect(() => {
    api.firstBoard(day ?? undefined).then(setData).catch((error: unknown) => {
      setLoadError(error instanceof Error ? error.message : "首板样本读取失败，请刷新重试");
    }).finally(() => setLoaded(true));
  }, [day]);

  const dates = data?.available_dates ?? [];

  return (
    <div>
      <PageHeader
        title="短线复盘"
        subtitle="盘中事件流 → 重复出现的类别榜 → 短线情绪（涨停分析＋跌停分析）→ 首板分析；交易日一处选、四段一起换"
      />

      {/* ① 上半：盘中事件流（用户 2026-10-26 点名挂在这屏上半部分；
          逐只列代码与名称是同一天的裁定，见本文件顶部注释） */}
      <EventStreamPanel />

      {/* ② 交易日选择器：候选项就是库里真有的那些日子。选了没数的日子会由那两块自己说"该跑哪一步"，
          页面不猜、也不把它回落成最近的一天。 */}
      {dates.length > 0 && (
        <div className="mb-3 flex flex-wrap items-center gap-1.5">
          <span className="text-xs text-muted-foreground">本页交易日（只列库里真有的日子，下面四段共用）：</span>
          {dates.map((d) => {
            const on = (data?.date ?? "") === d;
            return (
              <button key={d} type="button" aria-pressed={on} disabled={on}
                onClick={() => { setLoaded(false); setDay(d); }}
                className={cn("rounded px-1.5 py-0.5 font-mono text-[11px]",
                  on ? "bg-primary/15 text-primary" : "bg-muted text-muted-foreground hover:text-foreground")}>
                {d}
              </button>
            );
          })}
          <span className="text-[11px] text-muted-foreground/60">
            当前这一场：{data ? dateLabel(data.date) : loaded ? "没读到" : "读取中…"}
          </span>
        </div>
      )}

      {/* ③ 原因/行业类别汇总：用户 09-28 点名要这两块，10-28 裁定**整体上移**到
          盘中事件流与短线情绪之间（原来在名单之后，要滚到底才看到）。
          计数在后端 `vr/firstboard.py:_tally()`，页面只排展示 —— 两边各拆一次串，早晚给出两个榜。 */}
      {!!data && (!!data.reason_summary?.length || !!data.industry_summary?.length
        || !!data.concept_board?.rows?.length) && (
        <div className="mb-4 grid gap-4 md:grid-cols-2 xl:grid-cols-3">
          <TallyCard title="涨停原因 · 重复出现的类别" rows={data.reason_summary ?? []}
            stockCount={data.summary_meta?.stock_count ?? data.stocks.length}
            dropped={data.summary_meta?.dropped_single_reason ?? 0}
            caliber={"把每一只首板的涨停原因串按 +/、// 拆成单个原因，数出现过的只数"
              + "（同一只票里重复的词只算一次）。只列次数≥2 的（你 09-28 点名的口径），"
              + "被滤掉的单例条数写在下面，免得看着像今天只有这几类题材。\n"
              + "题材串是来源站的编辑归因，不是公司确认事实；这里也不判好坏、不给方向。\n"
              + "这一张榜的母体仍是东财实时池的首板档整场（行业列只有池子给得出），"
              + "与下面涨停分析那张库表不是同一批票。"} />
          <TallyCard title="所属行业 · 重复出现的类别" rows={data.industry_summary ?? []}
            stockCount={data.summary_meta?.stock_count ?? data.stocks.length}
            dropped={data.summary_meta?.dropped_single_industry ?? 0}
            caliber={"同一批首板，按涨停池给的所属行业数只数（同样只列≥2 的）。"
              + "行业是东财那一列的原样值，与概念/题材不同一套，不能拿来解释「为什么涨」。"} />
          {/* 10-29 用户点名加的那张榜：概念板块按重复只数从高到低、只列前 10。
              数只数与"这个概念全市场多大""这批票占它多少"都来自 `concept_rollup` 一份实现。 */}
          <ConceptCard cb={data.concept_board} />
        </div>
      )}
      {loadError && <p role="alert" className="mb-3 text-sm text-danger">这两张榜读取失败：{loadError}。请刷新页面重试（下面两块各自取数，不受它影响）。</p>}
      {data?.caliber_note && <NoticeBar kind="warning">{data.caliber_note}</NoticeBar>}
      {!loaded && !loadError && (
        <p className="mb-3 flex items-center gap-2 text-sm text-muted-foreground">
          <Loader2 className="h-4 w-4 animate-spin" /> 两张榜读取中…（涨停分析、跌停分析与首板分析各自取数，不互相等）
        </p>
      )}

      {/* ④ 短线情绪（大块）：涨停分析与跌停分析同属这一大块（用户 10-28 裁定 ② 那句）。
          两块共用同一份档位控件 `components/ui/TierPicker.tsx`，但母体是两张底表：
          涨停读 `analytics.limit_up_pool_daily`（lup-1），跌停读 `market_data.stock_daily`。 */}
      {/* 说明按钮摆在标题这一行的最右边（用户 10-31）；那句「本场封住 M 家／张表 M 行」
          的分开写法与下方 NoticeBar 的告警都留在屏上，不进抽屉。 */}
      <h2 className="mb-2 mt-4 flex flex-wrap items-center gap-1.5 text-base font-semibold">
        <Flame className="h-5 w-5 text-primary" /> 短线情绪
        <span className="ml-auto"><DocDrawer title="短线情绪">
          <p className="whitespace-pre-line leading-relaxed">{plain(
            "这一块是两场对照：涨停那一侧的母体是本地库 lup-1 那批触板行（含北交所与 ST），\n"
            + "跌停那一侧的母体是本地日线按当日跌停档位判出来的那批票 —— 两张底表，家数不可相加。\n"
            + "「本场封住 N 家／张表 M 行」那两句话一个是全场、一个是筛后，界面上分开写。\n"
            + "档位多选只筛这一张表的名单，不动上面那些比率的分母。")}</p>
        </DocDrawer></span>
      </h2>
      <div className="space-y-4">
        <LimitUpBoard day={day} />
        <LimitDownBoard day={day} />
      </div>

      {/* ⑤ 首板分析（从首页 4d 搬过来，挂在短线情绪之后 —— 用户 10-28 裁定 ③）。
          判据在加工段 `fbdaily` 步落 `analytics.first_board_daily`，这块只读 /api/quant/first-board-daily。
          仍包在 LazySection 里：这一块实测 2,645px／1,014 个节点，自己取数自己带控件。 */}
      {/* 首页那块入口跳的是这个锚点（`/first-board?to=first-board-daily`）。id 挂在 LazySection **外面**：
          里面那一块要滚近才挂载，锚点若在里面，滚过去时它还不存在。 */}
      <div id="first-board-daily" className="scroll-mt-4">
        <LazySection label="首板分析">
          <FirstBoardDailyPanel />
        </LazySection>
      </div>

      {/* ⑥ 首板四层过滤深析（用户 10-29 点名的第 5 件：资料那套四层方法论落在本页最下方）。
          母体与「涨停分析」同一把尺（lup-1），判据只住 `duanxian/first_board_deep.py` 一份；
          自己也取数，所以同样包在 LazySection 里（它一打开要现算概念近 20 场常态，约 5 秒）。 */}
      <div id="first-board-deep" className="scroll-mt-4">
        <LazySection label="首板四层过滤">
          <FirstBoardDeep day={day} />
        </LazySection>
      </div>

      <Disclaimer />
    </div>
  );
}


/** 概念板块榜（用户 10-29 点名：按重复出现的只数从高到低，列前 10）。
 *  排序与去重都在后端那一份里，这里只画；三条不能糊过去的线：
 *   · 一只票可挂多个概念 ⇒ 各条只数加起来会大于分母，「只数」不是百分比；「集中度」才是，且分母不同；
 *   · 只列前 N 是**截断**，被截掉的条数要留在屏上（走 NoticeBar 的「上限」型，不进抽屉）；
 *   · 查询坏了（`failed`）与"这批票没有概念归属"是两句话，后者还要分"几只压根没行"。 */
function ConceptCard({ cb }: { cb?: FirstBoardData['concept_board'] }) {
  const rows = cb?.rows ?? [];
  const top = cb?.top ?? 10;
  const total = cb?.rows_total ?? 0;
  const pool = cb?.pool_size ?? 0;
  const covered = cb?.covered;
  const maxShare = Math.max(1, ...rows.map(r => r.lift_pctile ?? 0));
  return (
    <GlassCard docTitle="概念板块 · 重复出现的类别（前 10）">
      {/* 说明按钮在这一行最右边（用户 10-31）；口径文字仍然只有后端 `concept_board.caliber` 这一份，
          后端没交时念下面那句兜底原话，页面不复述规则。截断声明（NoticeBar）与榜尾的误读提醒留在屏上。 */}
      <div className="mb-1 flex flex-wrap items-center gap-1.5">
        <h3 className="text-sm font-semibold">概念板块 · 重复出现的类别（前 {top}）</h3>
        <span className="ml-auto"><DocDrawer title="概念板块 · 重复出现的类别（前 10）">
          <p className="whitespace-pre-line leading-relaxed">{plain(
            cb?.caliber || "这一张榜的口径文字后端没交（concept_board.caliber 为空），页面不自己复述规则。")}</p>
        </DocDrawer></span>
      </div>
      {cb?.failed && <NoticeBar kind="warning">概念归属那一张查询没读成功 ⇒ 这一张榜整张空着，
        这**不是**「这批首板没有题材」。倍量榜与四维象限那两屏用的是同一份实现，它们此刻也会一起空。</NoticeBar>}
      {!cb?.failed && !rows.length && (
        <p className="py-3 text-[12px] text-muted-foreground">
          这一批里没有一只票挂得上概念（本地 reference_data.sector_member 里查不到）。
          北交所与部分 ST 基本没有概念行 —— 读作"库里没有这一档"，不读作"它没有题材"。
        </p>
      )}
      {!!rows.length && (
        <ul className="space-y-1">
          {rows.map(r => (
            <li key={`concept-${r.concept}`} className="flex items-baseline gap-2 text-[12px]">
              <span className="w-28 shrink-0 truncate font-medium" title={r.concept}>{r.concept}</span>
              <span className="w-12 shrink-0 font-mono tabular-nums"
                title={`这一批 ${pool} 只首板里有 ${r.stocks ?? "—"} 只挂着它`}>{r.stocks ?? "—"} 只</span>
              <span className="w-16 shrink-0 font-mono tabular-nums text-muted-foreground"
                title={`这个概念在全市场有 ${r.members_total ?? "—"} 只成员：成员越多 ⇒ 排得越靠前未必是共性`}>
                成员 {r.members_total ?? "—"}</span>
              <span className="h-1.5 flex-1 overflow-hidden rounded bg-muted"
                title={`集中度 ${r.lift_pctile ?? "—"}%：这批票占它全部成员的比例。同样 30 只，占 800 只成员与占 120 只成员不是一回事`}>
                <span className="block h-full rounded bg-primary/60"
                  style={{ width: `${Math.min(100, ((r.lift_pctile ?? 0) / maxShare) * 100)}%` }} />
              </span>
              <span className="w-12 shrink-0 text-right font-mono tabular-nums text-muted-foreground">
                {r.lift_pctile == null ? "—" : `${r.lift_pctile}%`}</span>
            </li>
          ))}
        </ul>
      )}
      {/* 截断声明：属「有才显示、永不进抽屉」那一型 */}
      {!!rows.length && total > rows.length && (
        <NoticeBar kind="truncated">
          这一批共 {total} 个概念有票挂着，只列前 {rows.length} 个（按只数从高到低，同只数按集中度高到低）。
          没列出的那一截不是不存在，是排在这十名之后。
        </NoticeBar>
      )}
      <p className="mt-1.5 text-[10px] leading-relaxed text-muted-foreground">
        分母是页面上这一批（{pool || "—"} 只首板），其中 {covered == null ? "归属未知" : `${covered} 只` }挂得上概念 ——
        一只票可挂多个概念，所以「只数」这一列加起来会大于分母，它不是占这批票的百分比。
        最右那列「集中度」是百分数，但分母换成了这个概念在全市场的成员数，两列不是一把尺。
      </p>
    </GlassCard>
  );
}

/** 一张类别计数卡：后端给什么就排什么，页面不拆串、不补 0、不再排一次序。 */
function TallyCard({ title, rows, stockCount, dropped, caliber }: {
  title: string; rows: { name: string; count: number }[];
  stockCount: number; dropped: number; caliber: string;
}) {
  const top = rows[0]?.count ?? 1;
  return (
    <GlassCard docTitle={title}>
      {/* 说明按钮在标题这一行最右边（用户 10-31）；分母那句与单例类别计数仍然留在屏上，不进抽屉。 */}
      <div className="mb-1 flex flex-wrap items-center gap-1.5">
        <h3 className="text-sm font-semibold">{title}</h3>
        <span className="ml-auto"><DocDrawer title={title}>
          <p className="whitespace-pre-line leading-relaxed">{plain(caliber)}</p>
        </DocDrawer></span>
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
