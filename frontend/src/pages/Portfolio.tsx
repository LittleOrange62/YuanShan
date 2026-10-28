import { useCallback, useEffect, useMemo, useState, useRef } from "react";
import { Loader2, RefreshCw, AlertCircle, ArrowRight, Download } from "lucide-react";
import { Link } from "react-router-dom";
import { pctColor } from "@/lib/colors";
import { cn } from "@/lib/utils";
import { PageHeader } from "@/components/ui/PageHeader";
import { Disclaimer } from "@/components/ui/Disclaimer";
import { Caliber } from "@/components/ui/Caliber";
import { agentFetch, agentPost, safeArray, type PositionsReport, type PositionRow } from "@/lib/agent";
import { SortHeader } from "@/components/ui/SortHeader";
import { useTableSort } from "@/lib/useSort";
import { StockLink } from "@/components/ui/StockLink";
import { useNonce, useProbe } from "@/lib/probe";
import { postJson } from "@/lib/jsonPost";
import { ExplainBox } from "@/components/ui/DataPanel";
import { StockSearch } from "@/components/ui/StockSearch";
import { STOCK_ROW_HINT, useStockRowInteraction } from "@/lib/useStockRowInteraction";

/** 持仓范围存库的键（必须先在 `duanxian/ui_pref.py::PREFS` 登记，否则 POST 回 400） */
const SCOPE_KEY = "positions.scope";

interface ScopeGroup { group_id?: number | null; name?: string | null; stock_count?: number | null }
interface ScopeGroups { available?: boolean | null; reason?: string | null; groups?: ScopeGroup[] | null }
interface ScopeStock { security_code?: string | null; group_id?: number | null; name?: string | null }
interface ScopeWatch { available?: boolean | null; reason?: string | null; rows?: ScopeStock[] | null }
interface ScopePrefs { rows?: Record<string, { value?: string | null; updated_at?: string | null; label?: string }> }

/** 范围取值的可读名（下拉与提示共用一份，不让两处各写一遍）。
 *  个股那一档要把代码翻成名字，名字只在调用方那张持仓表里 —— 所以多接一个可选的 `name`，
 *  而不是在这里再发一次请求：为一句提示单独取数，取不到时还得再说一句"取不到"。 */
const scopeLabel = (v: string, name?: string) => v === "all" ? "全部持仓"
  : v === "watchlist" ? "只看自选股"
  : v.startsWith("group:") ? `只看自选分组 #${v.slice(6)}`
  : v.startsWith("code:") ? `只看个股 ${v.slice(5)}${name ? `（${name}）` : ""}` : v;

/** 持仓代码可能带后缀（600519.SH），自选那边也可能带 —— 两边都取前 6 位再比 */
const code6 = (v: unknown) => String(v ?? "").slice(0, 6);

/** 持仓股 —— **交易日志的一个视图**，不是另一本账。
 *
 * 持仓 = 日志里所有成交明细折算下来还剩什么。要建仓/平仓就去改那笔交易的成交明细，
 * 这样两边永远对得上，也不用同一笔录两次。
 * ⛔ 这些数据不接入任何 AI prompt。
 */

const fmt = (v: number) => v.toLocaleString("zh-CN", { maximumFractionDigits: 2 });
const fmtPx = (v: number) => v.toLocaleString("zh-CN", { maximumFractionDigits: 4 });

/** 交叉高亮那份共用 hook 的返回值，喂给 Row 用（实现只住 `lib/useStockRowInteraction.ts`） */
type RowInteraction = ReturnType<typeof useStockRowInteraction>;

function Row({ h, i, ix, onReview }: {
  h: PositionRow;
  /** 行号（按当前排序后的序号记），与交叉高亮共用一份 */
  i: number;
  ix: RowInteraction;
  onReview: (code: string) => void;
}) {
  return (
    <tr className={ix.getRowClass(i, "cursor-pointer border-b border-border/40 last:border-0")}
      onDoubleClick={() => onReview(String(h.code ?? ""))}
      title="双击这一行：另开一个浏览器窗口看这一只的「个股深度复盘」">
      <td {...ix.cellProps(i, "stock_label", "px-2 py-2")}>
        <b><StockLink code={h.code} name={h.name || h.code} className="hover:text-primary" /></b>
        <span className="ml-1 text-[10px] text-muted-foreground"><StockLink code={h.code} className="hover:text-primary">{h.code}</StockLink></span>
        {h.playbooks.length > 0 && (
          <span className="ml-1 rounded bg-muted px-1.5 py-0.5 text-[10px]">{h.playbooks[0]}</span>
        )}
      </td>
      <td {...ix.cellProps(i, "shares", "px-2 py-2 text-right tabular-nums")}>{fmt(h.shares)}</td>
      <td {...ix.cellProps(i, "cost", "px-2 py-2 text-right tabular-nums")}>{fmtPx(h.cost)}</td>
      <td {...ix.cellProps(i, "price", "px-2 py-2 text-right tabular-nums")}>
        {/* ⚠️ 取不到行情时如实说明。显示 0 会让市值变 0、盈亏变 −100%，
            界面上就成了"持仓全部归零"——那是假的。 */}
        {h.quote_ok ? fmtPx(h.price as number)
          : <span className="text-warning">行情不可用</span>}
      </td>
      <td {...ix.cellProps(i, "market_value", "px-2 py-2 text-right tabular-nums")}>
        {h.quote_ok ? fmt(h.market_value as number) : "—"}
      </td>
      <td {...ix.cellProps(i, "pnl", cn("px-2 py-2 text-right font-semibold tabular-nums",
        h.quote_ok ? pctColor(h.pnl as number) : ""))}>
        {h.quote_ok ? `${(h.pnl as number) > 0 ? "+" : ""}${fmt(h.pnl as number)}` : "—"}
      </td>
      <td {...ix.cellProps(i, "pnl_pct", cn("px-2 py-2 text-right tabular-nums",
        h.quote_ok && h.pnl_pct != null ? pctColor(h.pnl_pct) : ""))}>
        {h.quote_ok && h.pnl_pct != null
          ? `${h.pnl_pct > 0 ? "+" : ""}${h.pnl_pct.toFixed(2)}%` : "—"}
      </td>
      <td {...ix.cellProps(i, "planned_stop", "px-2 py-2 text-right tabular-nums text-muted-foreground")}>
        {h.planned_stop != null ? fmtPx(h.planned_stop)
          : <span className="text-warning/80">未设</span>}
      </td>
    </tr>
  );
}

export function Portfolio({ embedded = false }: { embedded?: boolean }) {
  const [data, setData] = useState<PositionsReport | null>(null);
  const pSort = useTableSort();
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const importBusy = useRef(false);
  const [msg, setMsg] = useState("");

  // ⭐ 显示范围（用户 2026-10-06：「可以有下拉菜单选取自选分组，和自选股，并且默认上次的
  //    选择进行展示（存数据库）」；2026-10-14：「这里能选分组，不能选个股，你这个功能没做好」
  //    → 补上「只看某一只」那一档）。四条不能写错：
  //  ① 上次那次**存库**（`ys.ui_pref`，键 positions.scope），不是 localStorage ——
  //     换浏览器/清缓存回到"全部"，用户会以为范围没生效；
  //  ② 自选名单没读到时**不许假装筛过**：筛不出来的那一档必须说"名单没读到，现在给你的是全部"，
  //     否则一张空表会被读成"这个分组里没有持仓"；
  //  ③ 合计只在"全部"时用后端那份，选了范围就改成"表内行相加"并写明是范围内 ——
  //     拿全部持仓的合计数配一张筛过的表，等于把两个口径摆在同一屏。
  //  ④ 「只看某一只」的候选**先取当前持仓里真有的那些**，持仓为空（本机此刻就是 0 只）时靠旁边那一格
  //     手输 6 位代码 —— 只列持仓的话，账上没仓的这台机器会让这一档永远是空的，等于没做。
  //     手输出去的那一只若在持仓里找不到，表会给空，但说的是"当前持仓里没有这一只"，不是"筛子坏了"；
  //     当前范围内的代码（含刚输的那一只）也列在候选里并标明「不在当前持仓」 ——
  //     不然 select 匹配不到 option，浏览器显示成空白，用户读成"范围没生效"。
  const nonce = useNonce()[0];
  const prefsState = useProbe<ScopePrefs>("/ui-prefs", nonce);
  const groupsState = useProbe<ScopeGroups>("/watchlist/groups", nonce);
  const watchState = useProbe<ScopeWatch>("/watchlist/stocks", nonce, { limit: 2000 });
  const [scope, setScope] = useState("");
  const seeded = useRef(false);
  const [scopeErr, setScopeErr] = useState("");
  const [savedAt, setSavedAt] = useState("");
  const [draftCode, setDraftCode] = useState("");

  useEffect(() => {
    if (seeded.current || prefsState.kind === "loading") return;
    seeded.current = true;
    if (prefsState.kind === "ok") {
      setScope(prefsState.data.rows?.[SCOPE_KEY]?.value || "all");
    } else {
      setScope("all");
      setScopeErr("上次选的范围没读到（" + (prefsState.kind === "offline" ? "连不到后端" : prefsState.kind)
        + "），这一屏按「全部持仓」显示 —— 不是「没有选过」，是读不到那一次。");
    }
  }, [prefsState]);

  /** 双击持仓的一行 → 另开一窗看这一只的「个股深度复盘」＋单击一格整行整列交叉高亮：
   *  全站一份实现（`lib/useStockRowInteraction.ts`，用户 2026-10-15）。这一屏原来自己拼的
   *  `useOpenStockReview()` 加一份 `reviewHint` state 都并进来；`reviewHint` 就是 `popHint` 的别名，
   *  弹窗被拦时的告警由表下那一句念出来（不折进说明框）。 */
  const pf = useStockRowInteraction("重点关注（持仓）");
  const reviewHint = pf.popHint;
  const openReview = pf.openReview;
  const sortPos = (k: string) => { pf.clearAll(); pSort.toggle(k); };

  const pickScope = async (next: string) => {
    setScope(next);
    setScopeErr("");
    pf.clearAll();
    const r = await postJson<{ value?: string; updated_at?: string | null }>("/ui-pref",
      { key: SCOPE_KEY, value: next, note: "重点关注（持仓）页的范围选择" });
    // 只认接口读回来的那一份：写了但没读回，就不能说"已记住"
    if (!r.ok || !r.data?.value) {
      setScopeErr(`这次没记住（下次打开会回到上一次那份）：${r.ok ? "后端没读回这一行" : r.reason}`);
      return;
    }
    setSavedAt(r.data.updated_at ?? "");
  };

  // 手输要看的这一只。为什么不能只让下拉列持仓：这台机器此刻持仓 0 只，
  // 只列持仓的话「只看某一只」那一档永远是空的，等于没加这一档。
  // 判据与 `duanxian/ui_pref.py` 里那支 `code:[0-9]{6}` 一致 —— 这里先夹一次，
  // 不把注定被后端拒掉的值发出去（发了会以「这次没记住」的形式露出来，那句说的是"没存住"，
  // 而真相是"这一格填错了"，两件事混在一句里会把人往错的方向查）。
  const pickTyped = () => {
    const c = draftCode.replace(/\D/g, "").slice(0, 6);
    if (!/^\d{6}$/.test(c)) {
      setScopeErr(`这一格要 6 位代码；想按名称或拼音首字母找，就从下面弹出的建议里点一只。收到「${draftCode}」—— 当前范围没动`);
      return;
    }
    setDraftCode("");
    void pickScope(`code:${c}`);
  };

  // null = 不过滤；Set = 按名单过滤；undefined = 名单没读到，不许假装筛过
  const scopeCodes: Set<string> | null | undefined = useMemo(() => {
    if (!scope || scope === "all") return null;
    // 「只看某一只」不依赖自选名单：那一只的代码就在范围值里，读得到范围就读得到筛子。
    // 让它走下面那条"名单没读到"的路，屏幕上会说"自选名单没读到"，而这一档跟名单无关。
    if (scope.startsWith("code:")) return new Set([code6(scope.slice(5))]);
    if (watchState.kind !== "ok") return undefined;
    const out = new Set<string>();
    for (const r of watchState.data.rows ?? []) {
      const c = code6(r.security_code);
      if (!c) continue;
      if (scope === "watchlist") out.add(c);
      else if (scope.startsWith("group:") && String(r.group_id ?? "") === scope.slice(6)) out.add(c);
    }
    return out;
  }, [scope, watchState]);

  const load = useCallback(async () => {
    try {
      setData(await agentFetch<PositionsReport>("/api/positions"));
      setErr(null);
    } catch (e) {
      setErr(e instanceof Error ? e.message : "读取失败");
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  const saveLegacy = async (selected?: {holdings: unknown[]}) => {
    setMsg("");
    try {
      const r = await agentPost<{ imported: number; skipped: number; errors?: string[]; message?: string }>(
        "/api/positions/import-legacy", selected ?? {});
      setMsg(r.message
        ? r.message
        : `导入 ${r.imported} 条、跳过 ${r.skipped} 条${r.errors?.length ? `；${r.errors.length} 条有问题：${r.errors[0]}` : ""}`);
      await load();
    } catch (e) {
      setMsg(e instanceof Error ? `导入失败：${e.message}` : "导入失败");
    }
  };

  const importLegacy = async () => {
    if(importBusy.current)return;
    importBusy.current=true;setBusy(true);
    try{await saveLegacy();}finally{importBusy.current=false;setBusy(false);}
  };
  const selectLegacy = async (file?: File) => {
    if (!file || importBusy.current) return;
    importBusy.current=true;setBusy(true);
    try {
      if(file.size>500000) throw new Error('文件过大，请选择持仓 JSON 文件');
      const value:unknown=JSON.parse(await file.text());
      if(!value || typeof value!=='object' || !('holdings' in value) || !Array.isArray(value.holdings)) throw new Error('文件须包含 holdings 持仓数组');
      if(!window.confirm(`将所选文件中的 ${value.holdings.length} 条当前持仓导入本机交易日志？原文件保留；不会覆盖已有成交记录；没有成交日期的会明确注明。`))return;
      await saveLegacy({holdings:value.holdings});
    } catch(e){setMsg(e instanceof SyntaxError ? '这不是有效的 JSON 持仓文件，尚未导入任何记录；请重新选择原文件。' : e instanceof Error ? e.message : '读取文件失败');}
    finally{importBusy.current=false;setBusy(false);}
  };

  const allHoldings = safeArray<PositionRow>(data?.holdings);
  const total = data?.total;

  // 名字先查持仓、再查库里那份自选名单：手输出去的那一只多半不在持仓里，但它十有八九在自选里。
  // 查不到就用代码本身，不另发一次请求 —— 为一个标签再打一发取数，取不到时还得再说一句"取不到"。
  const nameOfCode = (c: string) => (allHoldings.find(h => code6(h.code) === c)?.name
    || (watchState.kind === "ok"
        ? (watchState.data.rows ?? []).find(r => code6(r.security_code) === c)?.name : "")
    || "").trim();

  // 「只看某一只」下拉里的候选 = **当前持仓里真有的那些**，按后端交出的顺序排（不自己重排，
  // 免得这一档的顺序和下面那张表的顺序读成两件事）。上次存库的那只（或刚手输的那一只）
  // 若不在持仓里，仍然列出来并标明「不在当前持仓」；持仓还没读回来时不标 ——
  // 那时分不清是"不在"还是"还没看到"。
  const stockOptions = useMemo(() => {
    const out = new Map<string, { code: string; name: string; gone: boolean }>();
    for (const h of allHoldings) {
      const c = code6(h.code);
      if (c && !out.has(c)) out.set(c, { code: c, name: h.name || "", gone: false });
    }
    const saved = scope.startsWith("code:") ? code6(scope.slice(5)) : "";
    if (saved && !out.has(saved)) out.set(saved, { code: saved, name: nameOfCode(saved), gone: !!data });
    return [...out.values()];
  }, [allHoldings, scope, data, watchState]);

  const scopeName = scope.startsWith("code:") ? nameOfCode(code6(scope.slice(5))) : "";
  const scopeNow = scopeLabel(scope, scopeName);

  // 上次存的那个分组可能已经被 20:40 的全量同步删掉了（这一屏自己的文档就记着这一条）。
  // 下拉里没有对应 option 时浏览器显示成空白，读起来是"范围没生效"，而真相是"那一组已经没了"——
  // 所以补一个说清来历的 option，而不是留一片空白让人去查筛子。
  // 名单没读回来（loading / 非 ok）时**不宣布它没了**：那是"还不知道"，不是"已删除"。
  const groupGone = useMemo(() => {
    if (!scope.startsWith("group:") || groupsState.kind !== "ok") return null;
    const id = scope.slice(6);
    return (groupsState.data.groups ?? []).some(g => String(g.group_id ?? "") === id) ? null : id;
  }, [scope, groupsState]);
  // Set = 按名单筛 / null = 不筛（全部持仓）/ undefined = 要筛但名单没读到
  const filtering = scopeCodes instanceof Set;
  const scopeUnread = scopeCodes === undefined && scope !== "all" && scope !== "";
  const holdings = filtering ? allHoldings.filter(h => (scopeCodes as Set<string>).has(code6(h.code)))
    : allHoldings;
  // 名单没读到却选了范围：这一档筛不了，明说并给全部，不交一张空表当"这个组没持仓"
  const shown = scopeUnread ? allHoldings : holdings;
  const watchCount = watchState.kind === "ok"
    ? new Set((watchState.data.rows ?? []).map(r => code6(r.security_code)).filter(Boolean)).size : 0;

  // 选了范围就是"表内行相加"：后端那份合计含全部持仓，配在筛过的表旁边是两个口径。
  // 取不到行情的行不进市值合计（与后端的 complete 同一条规矩），但成本要带上，
  // 否则"范围内盈亏"会因为少了一只票而被算高。
  const part = useMemo(() => {
    if (!filtering) return null;
    let cost = 0, value = 0, quoted = 0;
    for (const h of holdings) {
      cost += h.cost_amount ?? 0;
      if (h.quote_ok && h.market_value != null) { value += h.market_value; quoted += 1; }
    }
    return { count: holdings.length, quoted, missing: holdings.length - quoted,
             cost, value, pnl: value - cost,
             pct: cost > 0 ? (value - cost) / cost * 100 : null };
  }, [filtering, holdings]);

  return (
    <div className="space-y-6">
      {/* 嵌入时不出标题：这页现在只作为「重点关注（持仓）」的主体被挂进去，
          两处各一个标题会被读成两叠账。（09-28 用户裁定：持仓自选与持仓股合成一项。） */}
      {!embedded && <PageHeader title="持仓股" subtitle="由交易日志的成交明细聚合而来 · 数据只存本机" />}

      <div className="glass rounded-2xl p-5">
        <div className="mb-3 flex flex-wrap items-center gap-2">
          <h3 className="text-sm font-bold">当前持仓</h3>
          <Caliber text={
            "持仓不是单独的一本账，而是**交易日志的一个视图**：\n" +
            "把日志里所有成交明细折算下来，还剩多少就是持仓多少。\n\n" +
            "· 成本按剩余股数的加权均价算（与券商的移动加权口径一致）。\n" +
            "· 同一只票在多笔交易里都有剩余时合并成一行。\n" +
            "· 建仓 / 加仓 / 平仓都去交易日志改那笔的成交明细，这里会跟着变。\n\n" +
            "⚠️ 行情取不到时那一行显示「行情不可用」，不会显示成 0 —— " +
            "显示 0 会让市值变 0、盈亏变 −100%，看着像持仓归零，那是假的。" +
            "有这种行时，上面的合计也会标成「不完整」。"
          } />
          <label className="flex items-center gap-1.5 text-[12px] text-muted-foreground">
            显示范围
            <select aria-label="持仓显示范围" value={scope || "all"} disabled={!scope}
              onChange={e => void pickScope(e.target.value)}
              className="rounded-lg border border-border bg-background px-2 py-1 text-[12px] text-foreground">
              <option value="all">全部持仓（{allHoldings.length} 只）</option>
              <option value="watchlist">只看自选股（库里名单 {watchCount} 只）</option>
              {(groupsState.kind === "ok" ? groupsState.data.groups ?? [] : [])
                .map(g => <option key={String(g.group_id)} value={`group:${g.group_id}`}>
                  只看分组「{g.name || `#${g.group_id}`}」（{g.stock_count ?? 0} 只）
                </option>)}
              {groupGone && <option value={`group:${groupGone}`}>
                （自选分组 #{groupGone} 已不在名单里 —— 大概率是 20:40 全量同步按同花顺那份删掉的）
              </option>}
              <optgroup label={`只看某一只（当前持仓 ${allHoldings.length} 只）`}>
                {allHoldings.length === 0 && stockOptions.length === 0 && (
                  <option disabled>（账上此刻没有未平仓的票 —— 用旁边那一格搜一只，代码 / 名称 / 拼音首字母都行）</option>
                )}
                {stockOptions.map(s => <option key={s.code} value={`code:${s.code}`}>
                  {(s.name ? `${s.name} ${s.code}` : s.code) + (s.gone ? "（不在当前持仓）" : "")}
                </option>)}
              </optgroup>
              {!scope && <option value="">读取中…</option>}
            </select>
            <Caliber text={
              "「只看自选股 / 只看自选分组」筛的是**库里的自选名单**（同花顺同步过来那份，在「分组自选」页维护），" +
              "不是本机浏览器存的那份。\n" +
              "「只看某一只」的候选**先列当前持仓里真有的那些**；持仓为空（这台机器此刻就是 0 只）时" +
              "旁边那一格自己输 6 位代码 —— 只列持仓的话这一档永远是空的，等于没加。\n" +
              "筛的是持仓这一张表：分组里有的票、但你日志里没有仓，不会出现 —— 这一屏始终是持仓的视图。\n" +
              "这几档是**互斥的一档**（下拉单选），不是可叠加的筛子 —— 而且不必叠：选了「某一只」已经细到一行，" +
              "分组那一档只是换一种把持仓切开的看法。\n" +
              "选过一次就存进 PG（ys.ui_pref 的 positions.scope），下次打开、换浏览器都是这一档；" +
              "写不进去会当场说明「这次没记住」。\n" +
              "⚠️ 选了范围时上面那行合计是**表内行相加**（范围内），不是后端那份全部持仓的合计。"
            } />
          </label>
          {/* 手输那一格单独成一个 span、不塞进上面那个 <label>：label 里同时包着下拉和输入框，
              「显示范围」这四个字就成了两个控件的名字（点标签文字会跳到后注册的那个上）。
              下拉自己有 aria-label，所以拿掉 label 包裹不会让它没有可及名。
              这一格换成全站共用的 `StockSearch`（用户 10-15：「重点关注这里也有添加股票，
              请调用统一的那个筛选模块代码块，就是支持数字和拼音搜索个股的」）：
              判据与四种匹配（代码/名称/全拼/拼音首字母）只在后端那一处，页面不再自己认格式。 */}
          <span className="flex items-center gap-1.5 text-[12px] text-muted-foreground">
            只看某一只
            <StockSearch value={draftCode} kinds="stock,etf" widthClass="w-56"
              ariaLabel="只看某一只：输入代码、名称或拼音首字母"
              onType={v => setDraftCode(v.toUpperCase().slice(0, 12))}
              onPick={c => void pickScope(`code:${c}`)}
              onEnter={pickTyped}
              submit={<button type="button" onClick={pickTyped}
                className="rounded-lg border border-primary/50 bg-primary/10 px-2 py-1 text-[12px] text-primary hover:bg-primary/20">
                看这一只
              </button>} />
          </span>
          <button onClick={load}
            className="ml-auto flex items-center gap-1 rounded-lg border border-border px-2.5 py-1 text-[12px] transition-colors hover:bg-muted/50">
            <RefreshCw className="h-3.5 w-3.5" /> 刷新
          </button>
        </div>

        {scopeErr && <p role="alert" className="mb-3 text-[12px] text-warning">{scopeErr}</p>}
        {/* 弹窗被拦的告警（reviewHint＝popHint）摆在表下那句通用提示的旁边，见表格下方 */}
        {savedAt && <p className="mb-2 text-[11px] text-muted-foreground">范围已存库（{savedAt}），下次打开还是这一档。</p>}
        {scopeUnread && (
          <p className="mb-3 text-[12px] text-warning">
            自选名单这一会儿没读到，所以「{scopeNow}」这一档筛不了 ——
            现在显示的是全部持仓，不是"这个分组里没有持仓"。
          </p>
        )}

        {/* 用户 2026-10-07 定的三层包含关系：市场所有股票 ⊃ 自选分组的自选股 ⊃ 重点关注（持仓）。
            自选的唯一真源是同花顺本机那份 ini，而它每天 20:40 **全量镜像**（用户选的默认档：
            以同花顺为准）。所以持仓里出现 ini 没有的票时，系统不兜底、不改那份 ini，
            只把"明天同步后这几只就不在自选中了"点名说清 —— 要它们常驻，动作在同花顺里做一次。 */}
        {data?.containment && data.containment.available === false && (
          <p className="mb-3 text-[11px] leading-relaxed text-muted-foreground">
            自选 ⊃ 持仓 这一层今天判不了：{data.containment.reason}
          </p>
        )}
        {data?.containment && data.containment.available !== false
          && (data.containment.missing?.length ?? 0) > 0 && (
          <p className="mb-3 text-[11px] leading-relaxed text-warning">
            这几只有持仓、但不在自选清单里：{data.containment.missing!.join("、")}。
            自选现在按「{data.sync_mode === "append" ? "增量添加（只加不删）" : "全量更新（以同花顺本机资料为准）"}」同步
            {data.sync_mode ? `：${data.sync_meaning}` : ""}
            {data.sync_mode !== "append" && " —— 下一次 20:40 同步后它们不会出现在自选里；要常驻请在同花顺客户端加进自选。"}
          </p>
        )}

        {err && (
          <p className="mb-3 flex items-center gap-1.5 text-[12px] text-danger">
            <AlertCircle className="h-4 w-4" />{err}
          </p>
        )}

        {part ? (
          <div className="mb-4 flex flex-wrap items-baseline gap-6 border-b border-border pb-3">
            <div>
              <div className={cn("text-2xl font-extrabold tabular-nums", pctColor(part.pnl))}>
                {part.pnl > 0 ? "+" : ""}{fmt(part.pnl)}
              </div>
              <div className="text-[11px] text-muted-foreground">
                浮动盈亏（元）· 范围内 {part.count} 只，由表内行相加
                {part.pct != null && ` · ${part.pct > 0 ? "+" : ""}${part.pct.toFixed(2)}%`}
              </div>
            </div>
            <div>
              <div className="text-lg font-bold tabular-nums">{fmt(part.value)}</div>
              <div className="text-[11px] text-muted-foreground">市值（范围内）</div>
            </div>
            <div>
              <div className="text-lg font-bold tabular-nums">{fmt(part.cost)}</div>
              <div className="text-[11px] text-muted-foreground">成本（范围内）</div>
            </div>
            {part.missing > 0 && (
              <div className="text-[12px] text-warning">
                ⚠️ 范围内 {part.missing} 只取不到行情：市值那一格只加了 {part.quoted} 只，成本是全 {part.count} 只的
              </div>
            )}
          </div>
        ) : total && (
          <div className="mb-4 flex flex-wrap items-baseline gap-6 border-b border-border pb-3">
            <div>
              <div className={cn("text-2xl font-extrabold tabular-nums", pctColor(total.pnl))}>
                {total.pnl > 0 ? "+" : ""}{fmt(total.pnl)}
              </div>
              <div className="text-[11px] text-muted-foreground">
                浮动盈亏（元）{total.pnl_pct != null && ` · ${total.pnl_pct > 0 ? "+" : ""}${total.pnl_pct.toFixed(2)}%`}
              </div>
            </div>
            <div>
              <div className="text-lg font-bold tabular-nums">{fmt(total.market_value)}</div>
              <div className="text-[11px] text-muted-foreground">市值</div>
            </div>
            <div>
              <div className="text-lg font-bold tabular-nums">{fmt(total.cost)}</div>
              <div className="text-[11px] text-muted-foreground">成本</div>
            </div>
            {!total.complete && (
              <div className="text-[12px] text-warning">
                ⚠️ 合计不完整：{total.of - total.counted} 只取不到行情，只统计了 {total.counted}/{total.of} 只
              </div>
            )}
          </div>
        )}

        {shown.length === 0 ? (
          <div className="py-10 text-center">
            <p className="text-muted-foreground">
              {scope.startsWith("code:")
                // 这一档筛的就是一只票，空表只有一种解释：当前持仓里没有这一只的行了。
                // 不能说成"这一档里没有持仓"——那句是分组那一档的话，读的人会以为筛子坏了。
                // 也不等 allHoldings.length：账上此刻 0 只时，人刚输完代码，需要听到的是
                // "这一只没仓"，而不是一句跟他选了什么无关的"当前没有未平仓的交易"。
                ? <>当前持仓里没有 <b className="font-mono">{scope.slice(5)}</b>
                    {scopeName ? `（${scopeName}）` : ""}
                    {' 这一只 —— 这一屏是持仓的视图，没仓就筛不出行（全部持仓 ' + allHoldings.length
                      + ' 只，换一档或去交易日志记一笔就有了）。下拉里它标着「不在当前持仓」，就是这个意思。'}</>
                : allHoldings.length === 0
                  ? "当前没有未平仓的交易"
                  : `「${scopeNow}」这一档里没有持仓（全部持仓 ${allHoldings.length} 只都在，换个范围就看得到）`}
            </p>
            <p className="mt-2 text-[12px] text-muted-foreground/80">
              持仓由交易日志聚合而来 —— 去
              <Link to="/journal" className="mx-1 text-primary hover:underline">交易日志</Link>
              记一笔带成交明细的交易，这里就有了。
            </p>
            <Link to="/journal"
              className="mt-3 inline-flex items-center gap-1 rounded-lg bg-primary px-3 py-1.5 text-[12px] font-semibold text-primary-foreground transition-opacity hover:opacity-90">
              去记一笔 <ArrowRight className="h-3.5 w-3.5" />
            </Link>
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[720px] text-[12px]">
              <thead>
                <tr className="border-b border-border text-[11px] text-muted-foreground">
                  <SortHeader label="标的" sortKey="stock_label" dir={pSort.dirOf("stock_label")} onSort={sortPos}
                    className="px-2 py-2 font-semibold" title="按格子里显示的那串排（有名用名，缺名回落代码）" />
                  <SortHeader label="股数" sortKey="shares" dir={pSort.dirOf("shares")} onSort={sortPos}
                    className="px-2 py-2 font-semibold" title="剩余股数" />
                  <SortHeader label="成本" sortKey="cost" dir={pSort.dirOf("cost")} onSort={sortPos}
                    className="px-2 py-2 font-semibold" title="剩余股数的加权均价（与券商移动加权同口径）" />
                  <SortHeader label="现价" sortKey="price" dir={pSort.dirOf("price")} onSort={sortPos}
                    className="px-2 py-2 font-semibold" title="取不到行情的行没有这一档，恒排最后——那不是 0 元" />
                  <SortHeader label="市值" sortKey="market_value" dir={pSort.dirOf("market_value")} onSort={sortPos}
                    className="px-2 py-2 font-semibold" title="取不到行情的行不计市值，排最后" />
                  <SortHeader label="浮盈" sortKey="pnl" dir={pSort.dirOf("pnl")} onSort={sortPos}
                    className="px-2 py-2 font-semibold" title="浮动盈亏（元）" />
                  <SortHeader label="浮盈%" sortKey="pnl_pct" dir={pSort.dirOf("pnl_pct")} onSort={sortPos}
                    className="px-2 py-2 font-semibold" title="浮动盈亏（%）。与「浮盈」是两个口径：仓位不同结论可能相反" />
                  <SortHeader label="计划止损" sortKey="planned_stop" dir={pSort.dirOf("planned_stop")} onSort={sortPos}
                    className="px-2 py-2 font-semibold" title="没设过的行排最后，那是未设不是零" />
                </tr>
              </thead>
              <tbody>{pSort.apply(shown.map(h => ({ ...h, stock_label: h.name || h.code }))).map((h, i) => <Row key={h.code} h={h} i={i} ix={pf} onReview={openReview} />)}</tbody>
            </table>
          </div>
        )}

        {/* 怎么点这一张表：通用一句（实现全站一份），本屏特有的补充另起半句；
            弹窗被拦是告警，单独念、不折进任何默认收起的说明框 */}
        <p className="mt-3 border-t border-border pt-2 text-[11px] leading-relaxed text-muted-foreground">
          {STOCK_ROW_HINT}双击打开的那一页里有这一只的档案 · 趋势量化 · 窗口趋势 · 个股研究 · 多空辩论，代码由地址带过去，这一屏持仓留在原地对照。
        </p>
        {reviewHint && <p role="status" className="mt-1 text-[11px] leading-relaxed text-warning">{reviewHint}</p>}
        <p className="mt-1 text-[11px] leading-relaxed text-muted-foreground">
          要建仓、加仓或平仓，去
          <Link to="/journal" className="mx-1 text-primary hover:underline">交易日志</Link>
          改那笔交易的成交明细 —— 一份账本，两边永远对得上，不用同一笔录两次。
        </p>
      </div>

      <div className="glass rounded-2xl p-5">
        <h3 className="text-sm font-bold">从旧持仓导入</h3>
        {/* 说明收进通用说明框（用户 10-14：这一块的来龙去脉不是每天要读的，按钮才是）。
            下面那句「旧记录没有成交明细、日期怎么补」是会读错的账，不跟着折 —— 藏进框等于没写。 */}
        <ExplainBox title="旧持仓是怎么来的 · 文件怎么选">
          <p>
            早先版本的持仓和交易日志是两套账。Mac 客户端不会自动读取其他产品目录；可选择原来的 portfolio.json 文件。
            点一下把里面的当前持仓导进日志，之后只维护一份。
          </p>
        </ExplainBox>
        <p className="mt-1 text-[11px] leading-relaxed text-muted-foreground">
          ⚠️ 旧记录没有成交明细，建仓日期取原记录的日期或今天，会在备注里注明；
          重复点不会重复导入。
        </p>
        <div className="mt-3 flex items-center gap-3">
          <button onClick={()=>void importLegacy()} disabled={busy}
            className="flex items-center gap-1.5 rounded-lg border border-border px-3 py-1.5 text-[12px] font-semibold transition-colors hover:bg-muted/50 disabled:opacity-50">
            {busy ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Download className="h-3.5 w-3.5" />}
            导入本产品旧持仓
          </button>
          <label className="rounded-lg border border-border px-3 py-1.5 text-xs">选择旧持仓文件
            <input aria-label="选择旧持仓 JSON 文件" type="file" accept=".json,application/json" disabled={busy} className="block max-w-56 text-xs" onChange={e=>{void selectLegacy(e.target.files?.[0]);e.target.value='';}} />
          </label>
          {msg && <span className="text-[12px] text-muted-foreground">{msg}</span>}
        </div>
      </div>

      <Disclaimer />
    </div>
  );
}
