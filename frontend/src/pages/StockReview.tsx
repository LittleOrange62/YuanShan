import { useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { PageHeader } from "@/components/ui/PageHeader";
import { Disclaimer } from "@/components/ui/Disclaimer";
import { DocDrawer, DrawerSection } from "@/components/ui/DocDrawer";
import { StockSearch } from "@/components/ui/StockSearch";
import { probe } from "@/lib/probe";
import { usePageCrumb } from "@/lib/workspace/pageCrumb";
import { StockDossier } from "@/components/quant/StockDossier";
import { TrendQuant } from "@/components/quant/TrendQuant";
import { FinancialPeriodBoard } from "@/components/quant/FinancialPeriodBoard";
import { VolumeSimilarityBoard } from "@/components/quant/VolumeSimilarityBoard";
import { StockFacts } from "@/components/stock/StockFacts";
import { StockMetricsLedger } from "@/components/stock/StockMetricsLedger";
import { PriceVolumeTrend } from "@/components/stock/PriceVolumeTrend";
import { StockData } from "@/pages/StockData";
import { DeepDive } from "@/pages/DeepDive";

/**
 * 个股深度复盘（用户 2026-10-15 点名新做一屏，且要「浏览器打开新窗口」那种）：
 * 「把 分组自选 里的 个股点击分析 改成选中上面的票双击即可，然后把
 *   要分析哪一只 / 这只票的量化档案 / 该支票的趋势量化计算 / 窗口趋势
 *   这几个个股分析的功能点都整合过去……把个股研究、多空辩论这两个功能点都移动到这个页面」。
 *
 * 三条实现约束（这一晚定下来的，别改回去）：
 *  · 一只票只有一个真源 = 地址里的 `symbol`。页首那一个 `StockSearch` 写它，下面几块读它；
 *    哪一块自己再存一份「当前票」，就会出现「框里是茅台、档案是另一只」（10-14 在 /grouped 上真抓到过）。
 *  · 几块都是一份实现、多处调用：`StockDossier`/`TrendQuant` 是档案那两叠的共用组件；
 *    `StockData`/`DeepDive` 就是原「个股研究」「多空辩论」两屏的同一份取数代码 ——
 *    撤掉的只是页面外壳（大标题与各自的选票框），老地址 `/stock-data`、`/agent/deepdive` 只做跳转。
 *  · 不带传参进来就明说「还没选一只」，不自动挑一只：默认值是「随便给一只」的话，
 *    人会把那一屏的结论当成自己想看的票的结论。
 *
 * 带代码的那一路是从 `/grouped` 明细、`/my-stocks` 持仓与本机名单「双击」开新窗口；
 * 导航与主界面卡片那个入口跳的是本页「不带传参」的版本（用户明确区分了这两种）。
 *
 * 2026-10-16 用户第二次点名这一屏要「更深」，定了块序：
 *   个股信息 → 量化档案 → 趋势量化 → 财务多期与外推 → 近 30 根量价与相似样本 → 个股研究 → 多空辩论。
 * 「个股信息」提到第一块（原话「把个股信息这些挪上去」）：它只读本机库、不出网，所以选中就出数，
 * 读者先有身份与最近一场价，再看后面每一面。下面两块是这一轮新加的判读屏
 * （`FinancialPeriodBoard`／`VolumeSimilarityBoard`），判据都不在前端：
 * 三条财务外推只住 `app/compute/financial_forecast.py`，六个相似度分量与权重只住 `duanxian/volume_similarity.py`。
 *
 * 2026-10-28 用户第三次点名这一屏：「量价走势（日线·周线·月线）移动到这只票的量化档案的前面」
 *   ⇒ 现在第二块是「量价走势」，档案顺延到第三。**块序有两份真源**（这里的 JSX ＋
 *   `duanxian/stock_metrics_ledger.py::BLOCKS`），两处一起改，两条测试（前端 `stockReviewReads.test.ts`、
 *   后端 `tests/test_stock_facts_ledger.py`）各钉一遍 —— 只改一处就是屏幕上换了、总账那一屏还在按旧序讲。
 *
 * 2026-10-28 同日第二次点名（展示框）：「"现在复盘的是哪一只"这个展示框不要了。在页面最上方
 *   "工作空间 / 个股深度复盘"后面，动态的跟上个股代码和名称。整体展示即可。」
 *   ⇒ 那块 `GlassCard` 撤了，代码＋名称改由 `usePageCrumb` 登记、`Layout` 顶栏画（页面在 `Outlet` 里，
 *   拿不到顶栏，所以走的是与右侧「AI分析」栏同一套登记表）。名称现读一次证券字典：
 *   页面不再自己养第二张代码→名称的表，读不到的两种原因写进悬停。原来卡里那三段长说明
 *   没有删掉，收进了同页那只共用 `DocDrawer`；唯独"还没选一只"那句留在屏上 ——
 *   引导语收进弹窗等于没有，用户进页第一眼就该知道这一页不替他挑。
 */
export function StockReview() {
  const [query, setQuery] = useSearchParams();
  const code = query.get('symbol') || '';
  // 打字不写地址：每敲一个字就换 URL，会让下面「个股研究」那一块对着一半的代码出网取数，
  // 也会把浏览器历史塞满。地址里只在「选中 / 提交」那一刻变。
  const [draft, setDraft] = useState(code);
  useEffect(() => { setDraft(code); }, [code]);
  const commit = (value: string) => {
    const v = value.trim().toUpperCase();
    setQuery(previous => {
      const next = new URLSearchParams(previous);
      if (v) next.set('symbol', v); else next.delete('symbol');
      return next;
    }, { replace: true });
  };
  const from = query.get('from') || '';

  /**
   * 顶栏那一串「代码 名称」里的名称：地址里只有代码，名称现读一次证券字典
   * （`/quant/security-search` —— 全站那一只搜索框用的同一个后端判据，页面不再自己养一张代码→名称的表）。
   * 读不到分两种念法，都写进悬停里：**字典里没这一只**（美 / 港 / 韩股不走本机字典）与
   * **这一次根本没读到**（查询失败）是两句话，把后者说成前者，人会去翻代码而不是去查库。
   */
  const [name, setName] = useState<string | null>(null);
  const [nameWhy, setNameWhy] = useState<string | null>(null);
  useEffect(() => {
    const c = code.trim();
    if (!c) { setName(null); setNameWhy(null); return; }
    let live = true;
    setName(null); setNameWhy(null);
    void (async () => {
      const s = await probe<{ available?: boolean | null; reason?: string | null;
        rows?: { name?: string | null }[] | null }>("/quant/security-search",
        { q: c, limit: 1, kinds: "stock,etf" });
      if (!live) return;
      if (s.kind !== "ok") {
        setNameWhy(`证券字典这一次没读到（${s.kind}），顶栏先只念代码 —— 这不是「字典里没这只票」`);
        return;
      }
      const hit = String(s.data.rows?.[0]?.name ?? '').trim();
      if (hit) { setName(hit); return; }
      setNameWhy(s.data.available
        ? '本机证券字典里没有这一只（美 / 港 / 韩股不走本机字典），所以这里只有代码'
        : `证券字典读不到：${String(s.data.reason ?? '后端没交出原因')}`);
    })();
    return () => { live = false; };
  }, [code]);

  // 用户 2026-10-28：「"现在复盘的是哪一只"这个展示框不要了。在页面最上方
  // "工作空间 / 个股深度复盘"后面，动态的跟上个股代码和名称」⇒ 这一块只登记，画在 `Layout` 的顶栏上。
  usePageCrumb(code ? {
    text: `${code}${name ? ` ${name}` : ''}`,
    hint: [
      name ? `这一屏现在吃的就是 ${code} ${name}` : `${code}（名称暂时没跟上）`,
      from ? `从「${from}」双击那一行带过来的` : '由上方那只搜索框选中',
      '地址里的 symbol 是唯一真源，下面每一块都读它',
      nameWhy,
    ].filter(Boolean).join('｜'),
  } : null);

  return (
    <div className="mx-auto max-w-5xl space-y-6">
      <PageHeader title="个股深度复盘" level={1}
        subtitle="一只票的信息 · 档案 · 趋势量化 · 财务多期与外推 · 量价走势（日/周/月）· 近 30 根量价与历史最相近样本 · 公开资料 · 多空辩论，集中在这一屏（可新开窗口并排放着看）"
        actions={<div className="flex flex-wrap items-center justify-end gap-2">
          <StockSearch value={draft} ariaLabel="选一只票做深度复盘：代码、名称或拼音首字母"
            kinds="stock,etf"
            widthClass="w-72" placeholder="代码 / 名称 / 拼音首字母，如 600519、茅台、GZMT"
            onType={setDraft} onPick={c => commit(c)} onEnter={() => commit(draft)}
            submit={<button type="button" onClick={() => commit(draft)}
              className="rounded-lg border border-primary/50 bg-primary/10 px-2.5 py-1 text-[12px] text-primary hover:bg-primary/20">
              看这一只
            </button>} />
          {!!code && (
            <button type="button" onClick={() => commit('')}
              className="rounded-lg border border-border/60 px-2 py-1 text-[11px] text-muted-foreground hover:border-primary hover:text-primary">
              清掉这一只
            </button>
          )}
          {/* 撤掉那块展示框不等于把话删了：那三段（美 / 港 / 韩股怎么走、谁自动跑、块序）是说明，
              进全站那一只共用说明抽屉 `DocDrawer`（用户 2026-10-28 定的那件统一组件）。 */}
          <DocDrawer title="个股深度复盘">
            <DrawerSection heading="现在复盘的是哪一只">
              <p>
                {code
                  ? <>就是顶栏那串字：<b className="font-mono text-foreground">{code}</b>
                    {name ? <> {name}</> : <>（名称暂时没跟上：{nameWhy ?? '这一趟还没读回'}）</>}。
                    {from ? <>它是从「{from}」双击那一行带过来的。</> : <>它由上方那只搜索框选中。</>}
                    地址里的 <code>symbol</code> 是唯一真源，下面每一块吃的都是这一只，各块自己标注它的数出自哪一天。</>
                  : <>还没选一只，所以顶栏只有页面标题、没有代码。选了之后这里念的就是顶栏那串字，两处不会是两只票。</>}
              </p>
              <p className="mt-1">
                美 / 港 / 韩股不走本机字典（那里没有它们）：直接敲 AAPL、00700、005930.KS 再回车，
                下面「个股研究」那一块会按市场走公开源；顶栏那时只有代码，名称那一截本机给不出。
              </p>
            </DrawerSection>
            <DrawerSection heading="进页之后谁会自动跑、谁要你点一下（不看代码就别猜）">
              <p>
                <b className="text-foreground">个股信息 / 档案 / 趋势量化 / 窗口趋势 / 财务多期 / 量价走势 / 量能相似样本</b>
                只读本机库，选中就出数（其中「量能相似样本」第一次约 6 秒，之后走进程内缓存）；
                <b className="text-foreground">个股研究</b> 也自动跑 —— 地址里一有代码，半秒后它就并发打十来个只读接口，
                其中多数要出网抓公开源（行情 / 估值 / 研报 / 新闻 / 财务 / 公告 / 资金面），所以要等几秒，那块里另给了一个「重新取这一只的公开资料」按钮；
                <b className="text-foreground">多空辩论</b> 不自动跑（它要用已接入的 AI 来源现场生成，一次约 2-3 分钟、且要出网），
                想看得点那一块里的「开始多空辩论」。
              </p>
            </DrawerSection>
            <DrawerSection heading="这一块为什么按这个顺序排">
              <p>
                个股信息（本机库、选中就出数，先给身份与最近一场价）→ 量价走势（日／周／月三块，
                只读本机日线，形状一眼看得懂）→ 这只票的量化档案 → 该支票的趋势量化计算 → 财务多期与外推 →
                近 30 根量价形状与历史最相近样本 → 个股研究（公开源，可能整块不可用）→ 多空辩论（要点才跑）→
                指标总账。块序在后端那份总账里也登记了一遍，两处一起改、两条测试各钉一遍。
              </p>
            </DrawerSection>
          </DocDrawer>
        </div>} />

      {/* 「现在复盘的是哪一只」那一块展示框（用户 2026-10-28：不要了）⇒ 现在哪一只是在顶栏念的。
          留在这里的只有"还没选一只"那一句：它是引导、不是关于某只票的读数，收进弹窗等于没有。 */}
      {!code && (
        <p className="rounded-lg border border-dashed border-warning/50 px-2 py-1.5 text-[12px] leading-relaxed text-warning">
          还没选一只，这一页不替你挑（挑了就等于把别人的票当成你想看的那只）。
          在上方输入代码、名称或拼音首字母选一只，或从「分组自选」「重点关注（持仓）」里双击某一行开这一页。
        </p>
      )}

      <StockFacts code={code || null} />
      {/* 用户 2026-10-28 点名：量价走势（日／周／月）提到「这只票的量化档案」**前面**。
          它排在第二是有道理的：那一块只读本机日线、选中就出数，形状是三根 K 线一眼看得懂的东西；
          档案里那八分量的分与名次要先读口径才能用 —— 先给形状，再给分数。
          ⚠️ 块序有**两份真源**：这里的 JSX 顺序 ＋ `duanxian/stock_metrics_ledger.py::BLOCKS`
          （总账那一屏按它复述），两份由两条测试各钉一遍，挪一块必须同时挪两处。 */}
      <PriceVolumeTrend code={code || null} />
      <StockDossier code={code || null} date={undefined} />
      <TrendQuant code={code || null} />
      <FinancialPeriodBoard code={code || null} />
      <VolumeSimilarityBoard code={code || null} />
      <StockData />
      <DeepDive />
      <StockMetricsLedger code={code || null} />

      <Disclaimer compact />
    </div>
  );
}
