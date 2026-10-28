import { useState } from "react";
import { Link } from "react-router-dom";
import { RefreshCw, Play } from "lucide-react";
import { PageHeader } from "@/components/ui/PageHeader";
import { Disclaimer } from "@/components/ui/Disclaimer";
import { GlassCard } from "@/components/ui/GlassCard";
import { CaliberFold, CaveatText, Contract, ExplainBox, Measured, Panel, Row } from "@/components/ui/DataPanel";
import { SortHeader } from "@/components/ui/SortHeader";
import { useTableSort } from "@/lib/useSort";
import { STOCK_ROW_HINT, useStockRowInteraction } from "@/lib/useStockRowInteraction";
import { StockLink } from "@/components/ui/StockLink";
import { useNonce, useProbe } from "@/lib/probe";
import { dash, num } from "@/lib/datastate";
import { plain } from "@/lib/agent";

/**
 * 筛选器（映射表 #4：FT 的 `/screen`）。
 *
 * 2026-10-06 之前这一页只有条件清单没有"跑一次"：条件、参数、SQL 模板都在库里，
 * 执行引擎（`app/compute/screener.py`）也写好了，但**没有任何 HTTP 入口调用它** ——
 * 用户问的「这个页面是干嘛的？具体怎么用？没有应用筛选的页面呀？」就是这条断链。
 * 现在 `GET /api/screen` 会按登记的模板实跑，这一屏选条件 → 给参数 → 点「跑一次」出票。
 *
 * 2026-10-14 第二次补齐：从"只能选一条"改成"能叠若干条、并且/或者一键切换"。
 * 用户那句「250线以下，其他均线密集的这些个股」「年线以下，月线比10日线高，
 * 等等各种条件可以多个组合筛选」当时是**两条都做不出来**：
 *  · 登记表里 5 条条件没有一条读均线（均线族后来补到 6 条，见 `app/db/sql/35_ma_hfq_screen.sql`）；
 *  · 执行引擎一直收的是条件数组（`screener.build(conds, logic=…)`，参数名 `c{i}_` 前缀
 *    就是为了同一条条件带不同参数出现两次），缺的只是读层与界面这一段 —— 所以这次没动引擎。
 *
 * ⚠️ 三条仍然不做/要看得见的事：
 *  · **不外显模板 SQL、不做自由 SQL 入口**（只给 `has_sql` 表示"这条有没有命中口径"）——
 *    给一个只读成品项目开自由 SQL 等于自己放大权限；
 *  · **不摆"保存方案"按钮**：`config.screen_preset` 实测 0 行，而存/取端点还没开，
 *    摆了就是一个点了没反应的按钮；
 *  · **均线一律复权**（2026-10-14 用户裁定）：未复权价会让 133/4,933 只票的
 *    「在年线上方还是下方」判定翻转，所以这一屏读的每一根均线都是复权均线；
 *    缺复权因子的 115 只票退回未复权价算，这条降级由后端在 `caliber` 里原样说出。
 *
 * 2026-10-21 第三次补齐（用户原话「XX日线拐头的…长期下行 or 长期上行？叠加近 X 日
 * 横住了或者有所回升，回升率（降低率）低于（高于）Y%」）：登记 `ma_turn` 一条**参数化**
 * 规则（N/long_days/long_dir/recent_days/recent_shape/threshold_pct/cmp 七个参数），
 * 不是四条硬编码规则。**默认参数只住后端 `config.screen_rule.param_schema` 这一处**
 * （`app/db/sql/47_ma_turn_screen.sql`），界面从 `/api/screen/rules` 读默认并原样交回 ——
 * 组件里不抄第二份。判据原文住在登记行的 `note` 里，随清单与 `caliber` 上屏，页面不自写。
 * 这条的均线在 SQL 里用后复权收盘价**现算**（不读 `analytics.indicator_value` —— 那张表
 * 只留最近 94 场因子行，长期段一超出就静默给空名单），悬停提示把后复权口径写在行上。
 */

interface RuleParam {
  name: string; type?: string | null; format?: string | null; default?: unknown;
  required?: boolean; enum?: (string | number)[] | null; literal?: boolean; hint?: string | null;
}
interface Rule {
  rule_key?: string | null; name?: string | null;
  /** 机读参数表（`/api/screen/rules` 从 jsonb 直接交出，不再是一段 repr 字符串） */
  params?: RuleParam[] | null;
  /** 模板本身不外显（自由 SQL 入口刻意不搬），只说"这条有没有命中口径" */
  has_sql?: boolean | null;
  enabled?: boolean | null; builtin?: boolean | null; note?: string | null;
}
interface ScreenRules {
  available?: boolean | null; reason?: string | null; source?: string | null; count?: number | null;
  rules?: Rule[] | null;
  universes?: { key: string; means: string }[] | null;
  limit_choices?: number[] | null;
  caveat?: string | null;
}

interface ScreenRun {
  available?: boolean | null; reason?: string | null;
  /** 单条时的老字段（多条为 null）。新代码一律先读 `conditions`。 */
  rule?: { rule_key?: string; name?: string; note?: string | null } | null;
  /** 每一条条件实际生效的参数 —— 没有它，这一屏是按什么筛的没人知道 */
  conditions?: { index: number; rule_key: string; name: string; params: Record<string, unknown> }[] | null;
  logic?: string | null;
  date?: string | null; universe?: string | null; universe_means?: string | null;
  universe_count?: number | null;
  limit?: number | null; returned?: number | null; truncated?: boolean | null;
  columns?: string[] | null;
  rows?: Record<string, unknown>[] | null;
  params?: Record<string, unknown> | null;
  needs?: RuleParam[] | null;
  caliber?: string | null; source?: string | null;
}

/** 页面上的一条条件（还在编辑中）。`values` 只收用户真填过的，没填的按登记默认走。 */
interface CondRow { id: number; rule: string; values: Record<string, string> }

interface RunSpec {
  conds: { rule_key: string; params: Record<string, string> }[];
  logic: "and" | "or";
  universe: string; limit: number; date: string;
  /** 第几次点「跑一次」。同参数再点也必须真发一次请求：`useProbe` 是按
   *  (path, query, nonce) 三个依赖决定重取的，而结果组件自己那份 nonce 从不加一 ——
   *  少了这个 seq，"再点一次"就是一次静默的不动作（用户会以为后端查了一遍然后告诉他没变）。 */
  seq: number;
}

/** 最多叠几条。后端 `screen_view.MAX_CONDS` 也是 6，两边同一个数 ——
 *  界面放开到 7 条而后端退一句"最多 6 条"，那是一次白跑的请求。 */
const MAX_CONDS = 6;

export function Screen() {
  const [nonce, refresh] = useNonce();
  const reg = useProbe<ScreenRules>("/screen/rules", nonce);
  const rules = reg.kind === "ok" ? reg.data.rules ?? [] : [];
  const enabled = rules.filter(r => r.enabled !== false);

  const [rows, setRows] = useState<CondRow[]>([{ id: 1, rule: "", values: {} }]);
  const [logic, setLogic] = useState<"and" | "or">("and");
  const [universe, setUniverse] = useState("watchlist");
  const [limit, setLimit] = useState(200);
  const [date, setDate] = useState("");
  const [spec, setSpec] = useState<RunSpec | null>(null);
  const [err, setErr] = useState("");

  const ruleOf = (key: string) => rules.find(r => r.rule_key === key);
  const first = enabled[0]?.rule_key || "";
  const active = rows.map(r => r.rule || first);

  const setRowRule = (id: number, key: string) =>
    setRows(rs => rs.map(r => (r.id === id ? { ...r, rule: key, values: {} } : r)));
  const setRowValue = (id: number, name: string, v: string) =>
    setRows(rs => rs.map(r => (r.id === id ? { ...r, values: { ...r.values, [name]: v } } : r)));

  const apply = () => {
    const picked = rows.map((r, i) => ({ rule_key: active[i], params: r.values }));
    if (!picked.length || picked.some(p => !p.rule_key)) {
      setErr("每一条都得先选一个条件（没得选=后端一条都没登记，先看上面那张清单）");
      return;
    }
    setErr("");
    setSpec(s => ({ conds: picked, logic: picked.length > 1 ? logic : "and",
                    universe, limit, date: date.trim(), seq: (s?.seq ?? 0) + 1 }));
  };

  return (
    <div className="mx-auto max-w-5xl">
      <PageHeader title="筛选器" level={1}
        subtitle="条件是库里登记的：叠几条、按各自要的参数给值、点跑一次 —— 命中谁由库里那段模板说了算，界面不重算。"
        actions={<button type="button" onClick={refresh}
          className="flex items-center gap-1 rounded-lg bg-primary/15 px-2.5 py-1.5 text-[12px] font-medium text-primary hover:bg-primary/25">
          <RefreshCw className="h-3.5 w-3.5" /> 重新读取
        </button>} />

      <div className="space-y-4">
        <Panel title="已登记的筛选条件" state={reg}
          note="条件是登记的（可复用、可解释），不是页面上临时拼的。停用中的也列出来，但选不到 —— 隐藏它们会让人以为条件从来只有这些。">
          {d => (
            <>
              <Row label="条件" value={`${dash(d.count ?? d.rules?.length)} 种已登记（启用 ${enabled.length} 种）`} />
              <CaveatText text={d.caveat} />
              <CaliberFold provenance={[d.source]} />
              <ul className="mt-2 space-y-1.5">
                {(d.rules ?? []).map((r, i) => (
                  <li key={`${r.rule_key}-${i}`} className="rounded-lg border border-border p-2 text-[11px] leading-relaxed">
                    <div className="flex flex-wrap items-baseline gap-x-2">
                      <button type="button" onClick={() => setRows(rs => rs.map((x, j) => (j === 0 ? { ...x, rule: r.rule_key ?? "", values: {} } : x)))}
                        disabled={r.enabled === false}
                        aria-pressed={active[0] === r.rule_key}
                        className={active[0] === r.rule_key
                          ? "font-semibold text-primary underline"
                          : "font-medium hover:text-primary disabled:cursor-not-allowed disabled:text-muted-foreground"}>
                        {dash(r.name)}
                      </button>
                      <span className="font-mono text-[10px] text-muted-foreground">{dash(r.rule_key)}</span>
                      {r.builtin === true && <span className="rounded bg-muted px-1 text-[10px] text-muted-foreground">内置</span>}
                      {r.enabled === false && <span className="rounded bg-warning/15 px-1 text-[10px] text-warning">已停用·选不到</span>}
                      {r.has_sql === true && <span className="rounded bg-primary/12 px-1 text-[10px] text-primary">有命中口径</span>}
                    </div>
                    {r.note && (r.note.length > 160
                      // 长的那一份（`ma_turn` 那条 738 字）折起来：用户 2026-10-21 点名「把选项留出，
                      // 描述内容都收缩起来」，一屏十条条件每条挂一大段，读到的是墙不是选项。
                      // 判据原文仍然只有后端 `note` 这一份，只是默认收起 —— 短的那一档照旧直接可见。
                      ? <ExplainBox title={`判据原文（${r.note.length} 字 · 默认收起）`}>
                        <p className="whitespace-pre-line">{plain(r.note)}</p>
                      </ExplainBox>
                      : <p className="mt-0.5 whitespace-pre-line text-muted-foreground">{plain(r.note)}</p>)}
                    {(r.params?.length ?? 0) > 0 && (
                      <p className="mt-0.5 text-[10px] leading-relaxed text-muted-foreground">
                        参数：{r.params?.map(p => `${p.name}${p.required ? "（必填）" : ""}`
                          + `${p.enum?.length ? `[${p.enum.join("/")}]` : ""}`
                          + `${p.default != null ? ` 默认 ${String(p.default)}` : ""}`).join(" · ")}
                      </p>
                    )}
                  </li>
                ))}
                {(d.rules ?? []).length === 0 && (
                  <li className="text-[11px] text-muted-foreground">后端交出 0 种条件 —— 这是登记表里没行，不是「筛选条件全通过」。</li>
                )}
              </ul>
            </>
          )}
        </Panel>

        <GlassCard>
          <h2 className="text-sm font-bold">跑这一次筛选（条件可以叠 {MAX_CONDS} 条）</h2>
          <div className="mt-2 space-y-2">
            {rows.map((r, i) => {
              const rule = ruleOf(active[i]);
              // 悬停口径提示：凡是登记说明里写明「后复权」的均线类条件都带上——
              // 判据原文由后端 note 交出（清单与 caliber 里默认可见），这里只补一句悬停能看到的口径。
              const hfqTip = rule?.note?.includes("后复权")
                ? "这条用的是后复权均线（收盘价 × 复权因子现算；缺因子的票退回不复权，由后端在口径里说出）。"
                : undefined;
              return (
                <div key={r.id} className="rounded-lg border border-border/60 p-2" title={hfqTip}>
                  <div className="flex flex-wrap items-end gap-3 text-[12px]">
                    <label className="flex flex-col gap-1">
                      <span className="text-muted-foreground">第 {i + 1} 条条件</span>
                      <select value={active[i]} onChange={e => setRowRule(r.id, e.target.value)}
                        className="rounded border border-border bg-background px-2 py-1">
                        {enabled.map(x => (
                          <option key={`${r.id}-${x.rule_key}`} value={x.rule_key ?? ""}>{x.name}</option>
                        ))}
                      </select>
                    </label>
                    {(rule?.params ?? []).map(p => (
                      <label key={`${r.id}-${p.name}`} className="flex flex-col gap-1"
                        title={hfqTip ?? p.hint ?? ""}>
                        <span className="text-muted-foreground">
                          {p.name}{p.required ? " ＊必填" : ""}{p.default != null ? `（默认 ${String(p.default)}）` : ""}
                        </span>
                        {p.enum?.length ? (
                          <select value={r.values[p.name] ?? String(p.default ?? "")}
                            onChange={e => setRowValue(r.id, p.name, e.target.value)}
                            className="rounded border border-border bg-background px-2 py-1">
                            {p.enum.map(x => <option key={String(x)} value={String(x)}>{String(x)}</option>)}
                          </select>
                        ) : (
                          <input type={p.format === "date" ? "date" : p.type === "integer" || p.type === "number" ? "number" : "text"}
                            value={r.values[p.name] ?? ""}
                            placeholder={p.default != null ? String(p.default) : ""}
                            title={p.hint ?? ""}
                            onChange={e => setRowValue(r.id, p.name, e.target.value)}
                            className="w-32 rounded border border-border bg-background px-2 py-1" />
                        )}
                      </label>
                    ))}
                    {rows.length > 1 && (
                      <button type="button" onClick={() => setRows(rs => rs.filter(x => x.id !== r.id))}
                        className="rounded-lg border border-border/60 px-2 py-1 text-[11px] text-muted-foreground hover:border-warning hover:text-warning">
                        去掉这条
                      </button>
                    )}
                  </div>
                  {rule?.note && (
                    <p className="mt-1 line-clamp-2 text-[10px] leading-relaxed text-muted-foreground">{plain(rule.note)}</p>
                  )}
                </div>
              );
            })}
            <button type="button" disabled={rows.length >= MAX_CONDS}
              onClick={() => setRows(rs => [...rs, { id: Math.max(...rs.map(x => x.id)) + 1, rule: "", values: {} }])}
              className="rounded-lg border border-dashed border-border/70 px-2.5 py-1 text-[11px] text-muted-foreground hover:border-primary hover:text-primary disabled:opacity-50">
              ＋ 再加一条条件{rows.length >= MAX_CONDS ? `（已到上限 ${MAX_CONDS} 条）` : ""}
            </button>
          </div>

          <div className="mt-3 flex flex-wrap items-end gap-3 text-[12px]">
            <fieldset className="flex items-center gap-2">
              <legend className="sr-only">条件之间怎么合</legend>
              <span className="text-muted-foreground">条件之间</span>
              {(["and", "or"] as const).map(v => (
                <label key={v} className="flex items-center gap-1">
                  <input type="radio" name="cond-logic" value={v} checked={logic === v}
                    disabled={rows.length < 2}
                    onChange={() => setLogic(v)} />
                  {v === "and" ? "并且（每条都要过）" : "或者（过一条就算）"}
                </label>
              ))}
            </fieldset>
            <label className="flex flex-col gap-1">
              <span className="text-muted-foreground">候选集（分母）</span>
              <select value={universe} onChange={e => setUniverse(e.target.value)}
                className="rounded border border-border bg-background px-2 py-1">
                {(reg.kind === "ok" ? reg.data.universes ?? [] : []).map(u => (
                  <option key={u.key} value={u.key}>{u.key} · {u.means}</option>
                ))}
              </select>
            </label>
            <label className="flex flex-col gap-1">
              <span className="text-muted-foreground">最多交几行</span>
              <select value={String(limit)} onChange={e => setLimit(Number(e.target.value))}
                className="rounded border border-border bg-background px-2 py-1">
                {(reg.kind === "ok" ? reg.data.limit_choices ?? [50, 100, 200] : [50, 100, 200])
                  .map(n => <option key={n} value={String(n)}>前 {n} 行</option>)}
              </select>
            </label>
            <label className="flex flex-col gap-1">
              <span className="text-muted-foreground">判到哪一天（留空=库里最新那一场）</span>
              <input type="date" value={date} onChange={e => setDate(e.target.value)}
                className="rounded border border-border bg-background px-2 py-1" />
            </label>
            <button type="button" onClick={apply} disabled={!enabled.length}
              className="flex items-center gap-1 rounded-lg bg-primary px-3 py-1.5 font-semibold text-primary-foreground transition-opacity hover:opacity-90 disabled:opacity-50">
              <Play className="h-3.5 w-3.5" /> 跑一次
            </button>
          </div>
          {err && <p className="mt-1 text-[11px] text-warning">{err}</p>}
          {/* 这一屏怎么填参数的说明收进通用说明框（用户 2026-10-14），句子逐字留着。
              **下面那一句「只有一条条件时并且/或者这个开关不起作用」没跟着折** —— 它说的是控件当前
              到底生不生效，藏进默认收起的框里等于让屏幕对一个亮着的开关说假话；
              同理不折的还有上面那行红字与结果区里「命中数达到上限、这只是前 N 行」那一句。 */}
          <ExplainBox title="这些参数怎么给 · 命中口径怎么交回">
            <p className="mt-2">
              没填的那几项按库里登记的默认值走，跑完会把<b className="text-foreground">每一条</b>实际生效的参数原样交回
              （结果表上面那几行）—— 不然这一屏是按什么筛的没人知道。字面类参数（均线名、比较符）只能取登记过的枚举值，
              白名单是执行引擎那道硬门，界面不给第二个选项。
            </p>
          </ExplainBox>
          <p className="mt-2 text-[10px] leading-relaxed text-muted-foreground">
            只有一条条件时「并且/或者」这个开关不起作用（它就在那儿亮着，别说它在筛）。
          </p>
        </GlassCard>

        {spec && <RunResult key={spec.seq} spec={spec} />}

        <GlassCard>
          <h2 className="text-sm font-bold">这一屏能给什么、还差什么</h2>
          <Measured>
            条件由接口原样端出（实测 {rules.length} 种，含机读的 <code>params</code> 与 <code>has_sql</code>），
            <code>GET /api/screen</code> 按 <code>config.screen_rule</code> 登记的模板实跑，命中口径 = 那条模板 + 这里给的参数。
            均线这一族（<code>ma_position</code> / <code>ma_pair</code> / <code>dense_short5</code> /
            <code>dense_mid4</code> / <code>align_bull</code> / <code>align_bear</code>）读的是
            <code>analytics.indicator_value</code>，全部<b className="text-foreground">复权价</b>口径
            （2026-10-14 换底：未复权价会让 133/4,933 只票的年线上下判定翻转，实测记录在
            <code>app/db/sql/35_ma_hfq_screen.sql</code> 头部）。
            <code>ma_turn</code>（均线拐头，2026-10-21 新增）例外而更严：它<b className="text-foreground">不读那张表</b>，
            而是在 SQL 里用后复权收盘价<b className="text-foreground">现算任意周期 N 的均线</b> ——
            <code>indicator_value</code> 只留最近 94 场因子行（2026-10-21 实测），长期段回看一超出就静默给空名单，
            而空名单和「真筛出 0 只」在屏幕上长得一模一样；现算还顺带把 N 从库里固定的七档均线里解放出来（5..250 任意整数）。
            两处降级要看得见：<b className="text-foreground">缺复权因子的票（全表 61,295 行、115 只）按未复权价算均线</b>
            —— 不这么兜它们会从所有均线条件里静默消失；<b className="text-foreground">不足 250 根 K 线的票（当日行情里 627 只）没有年线那一行</b>，
            判不了、不进结果，也不会被当成「在年线下方」。
            还差的只有一件：<code>config.screen_preset</code> 实测 <b className="text-foreground">0 行</b> 且<b className="text-foreground">没有存/取端点</b>，
            所以这一屏不摆「保存方案」按钮 —— 摆了就是一个点了没反应的按钮。
            <b className="text-foreground">模板 SQL 刻意不外显</b>：自由 SQL 查询台（FT 的 <code>/sql</code>）被判不搬。
            不依赖筛选器、现在就能用的选票入口：
            <Link className="text-primary underline" to="/quadrant"> 四维象限</Link>（按质量×环境四档选票）、
            <Link className="text-primary underline" to="/volume-double"> 倍量形态</Link>（按形态选票）、
            <Link className="text-primary underline" to="/grouped"> 分组自选</Link>（按组看自己的票）。
          </Measured>
        </GlassCard>
      </div>

      <Disclaimer compact />
    </div>
  );
}

/** 结果单独成组件：没点过「跑一次」就不该发这个请求（挂在父层会每次渲染都打一遍）。 */
function RunResult({ spec }: { spec: RunSpec }) {
  const nonce = useNonce()[0];
  const run = useProbe<ScreenRun>("/screen", nonce, {
    conds: JSON.stringify(spec.conds), logic: spec.logic,
    universe: spec.universe, limit: spec.limit,
    ...(spec.date ? { date: spec.date } : {}),
  });
  const sort = useTableSort();
  // 单击交叉高亮 / 双击开深度复盘：全站一份实现（`lib/useStockRowInteraction.ts`）。
  // 「跑一次」会换 key 重挂本组件，选中与告警自然归零；这里只需管住页内排序。
  const x = useStockRowInteraction("筛选器");
  const sortBy = (k: string) => { x.clearAll(); sort.toggle(k); };

  return (
    <Panel title="命中结果" state={run}
      note="行是库里那些模板筛出来的，列也是它们给的（代码/名称/收盘/涨跌幅/换手率/操作分/象限 + 条件自己的列）。">
      {d => {
        const rows = d.rows ?? [];
        const cols = d.columns ?? [];
        const conds = d.conditions ?? (d.rule
          ? [{ index: 1, rule_key: d.rule.rule_key ?? "", name: d.rule.name ?? "", params: d.params ?? {} }]
          : []);
        return (
          <>
            {conds.map(c => (
              <Row key={c.index} label={`第 ${c.index} 条 · ${c.name}`}
                value={Object.entries(c.params ?? {}).map(([k, v]) => `${k}=${String(v)}`).join(" · ")
                  || "（这条没有参数，全按登记默认）"} />
            ))}
            {conds.length > 1 && (
              <Row label="怎么合" value={d.logic === "or"
                ? "或者 —— 任一条过就算（所以命中数会比分着筛任何一条都多）"
                : "并且 —— 每条都要过（命中数只会越来越少）"} />
            )}
            <Row label="判到哪一天" value={dash(d.date)} />
            <Row label="母体" value={`${dash(d.universe_count)} 只（${dash(d.universe)}：${dash(d.universe_means)}）`
              + ` —— 分母不写，「筛出 ${rows.length} 只」读不出是多还是少`} />
            <Row label="交出几行" value={`${dash(d.returned)} 行（上限 ${dash(d.limit)}）`}
              tone={d.truncated === true ? "warn" : undefined} />
            {d.truncated === true && (
              <p className="mt-1 text-[11px] leading-relaxed text-warning">
                命中数达到上限、这一屏只是<b className="text-foreground">前 {dash(d.limit)} 行</b>（按操作分降序），不是「只筛出这些」。
                要全量就把上限调大一档或去 CLI 跑同一组条件。
              </p>
            )}
            {rows.length === 0 && (
              <p className="mt-1 text-[11px] leading-relaxed text-muted-foreground">
                这几条条件在这一天、这个候选集里<b className="text-foreground">一只都没命中</b> ——
                那是筛出来空集，不是取数失败（取数失败会在上面报 reason，
                包括「那一场还没有基础因子行」这种）。
                自选池本来就窄，把候选集切成「全市场」再看一次能分开这两种「没有」；
                叠的条件越多越容易空，逐条加比一次加四条好读。
              </p>
            )}
            {rows.length > 0 && (<>
              <div className="mt-2 overflow-x-auto">
                <table className="w-full text-[11px]">
                  <thead><tr className="text-left text-muted-foreground">
                    {cols.map(c => <SortHeader key={c} label={c} sortKey={c} dir={sort.dirOf(c)} onSort={sortBy} />)}
                  </tr></thead>
                  <tbody>
                    {sort.apply(rows).map((r, i) => (
                      <tr key={`${String(r["代码"])}-${i}`} {...x.rowProps(i, String(r["代码"] ?? ""), "border-t border-border/40")}>
                        {cols.map(c => <td key={c} {...x.cellProps(i, c, "px-1.5 py-1 font-mono tabular-nums")}>
                          {c === "代码" ? <StockLink code={String(r[c] ?? "")} className="hover:text-primary">{String(r[c] ?? "—")}</StockLink>
                            : c === "名称" ? <span className="font-sans">{String(r[c] ?? "—")}</span>
                            : cellText(r[c])}
                        </td>)}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <p className="mt-1 text-[11px] leading-relaxed text-muted-foreground">{STOCK_ROW_HINT}</p>
              {/* 弹窗被拦是**告警**，摊在表下面，不折进任何默认收起的说明框 */}
              {x.popHint && <p role="status" className="mt-1 text-[11px] leading-relaxed text-warning">{x.popHint}</p>}
            </>)}
            <CaveatText text={d.caliber} />
            <CaliberFold provenance={[d.source]} />
            <div className="mt-2">
              <Contract path="GET /api/screen" source="按 config.screen_rule 的模板实跑（模板原文不外显，只给 has_sql）" fields={[
                ["conds（JSON 数组）+ logic / universe / date / limit", "多条条件拼一条 SQL；同一条条件出现两次时参数各带前缀，不会互相覆盖"],
                ["rule + 条件自己的参数", "单条的老写法，与 conds 走同一个读层"],
                ["rows[] / columns[]", "命中行与各条条件自己的列（顺序 = SQL 的 SELECT 顺序，前端不重排）"],
                ["conditions[]", "每一条实际生效的参数（含按默认值补上的那些）"],
                ["universe_count", "参与筛选的母体只数 —— 分母"],
                ["returned / limit / truncated", "截断口径：达到上限时必须说明这只是前 N 行"],
              ]} />
            </div>
          </>
        );
      }}
    </Panel>
  );
}

/** 一格的原样读法：后端给什么类型就怎么显示，不在前端换算单位（Decimal 会被 JSON 成数字） */
function cellText(v: unknown): string {
  if (v == null || v === "") return "—";
  if (typeof v === "number") return num(v, 2);
  if (typeof v === "boolean") return v ? "是" : "否";
  return String(v);
}
