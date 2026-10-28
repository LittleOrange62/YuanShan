import type { ReactNode } from "react";
import { GlassCard } from "@/components/ui/GlassCard";
import { ageText, dash, describeState, hasData, isQuiet, type Availability, type DataState } from "@/lib/datastate";
import { cn } from "@/lib/utils";
import { plain } from "@/lib/agent";
import { splitCaveats } from "@/lib/caveatKind";

/**
 * 新页（数据链路 / 量化选股）共用的三块呈现：
 *  · `Panel` —— 把 `DataState` 的几种情况画成明显不同的样子（含 `available:false` 的 reason）；
 *  · `Contract` —— 接口还没实现时，**把缺哪些字段写在屏幕上**；
 *  · `Row` —— 一行"标签：值"，缺失一律 `—`。
 *
 * 为什么要有 `Contract`：规划 §5 第 3 条禁止占位数字，但空页面对用户没有信息量。
 * 把"这一屏需要 `last_data_date`/`expected_data_date`，现在一个都没有"摊开，
 * 既是给用户解释，也是给后端的验收单。
 */

export type PanelData = Availability;

/**
 * 四态面板。`T` **故意不加约束**：一旦写成 `T extends Availability`，
 * `DataState<ExpectedDate>` 就不允许赋给 `DataState<Availability>`（对象类型不协变），
 * 每个页面都得为类型再包一层假接口。可用性判断改用下面的显式转换。
 */
/**
 * 空态那一块该收成什么样：一处实现，`Panel` 与那些不走 `Panel` 的卡片（首页那几块、
 * 技术指标那一格）都调这一个，别各写一个"没数就变小"。判型 `isQuiet` 住在 `lib/datastate.ts`
 * （与 `hasData`／`describeState` 同一层，六种"没有"的分法只在那一处定）。
 *
 * **收起不等于藏话**：summary 里是 `describeState` 那句**完整原因原句**（不截字、不改写），
 * 收进去的只有这块自己的口径说明与字段契约；原因里没有 `reason` 时，`describeState` 那句
 * 「违反展示规范 01 第一条，不猜原因」也会原样出现在 summary 上。
 */
export function QuietFold({ title, text, note, contract }: {
  /** 调用方自己那块标题已经不在了时才交（`Panel` 那一支就是）；标题还在屏上的（首页那几块带 h3）别交，免得一句话里两个名字 */
  title?: string; text: string; note?: string; contract?: ReactNode;
}) {
  return (
    <details className="rounded-lg border border-dashed border-border/70 px-2 py-1">
      <summary className="cursor-pointer text-[11px] leading-relaxed text-warning">
        {title ? `${title} —— ` : ""}{text}
        <span className="text-muted-foreground">
          （这一块暂时没数，口径说明与字段契约已收起 · 点开看 · 取到数会自动展开）
        </span>
      </summary>
      {note && <div className="mt-1 text-[10px] leading-relaxed text-muted-foreground">{note}</div>}
      {contract && <div className="mt-2 border-t border-dashed border-border pt-2">{contract}</div>}
    </details>
  );
}

export function Panel<T>({ title, note, slowHint, state, contract, controls, children }: {
  title: string;
  note?: string;
  /**
   * 慢接口的"为什么还在转"那句话（实测 `/api/coverage` 冷跑过 53.6s、`/api/quant/sector-heat` 14.0s）。
   * 只写后端自己交出的口径或人工实测的时间，不给进度百分比 —— 进度条没有依据就是装饰。
   */
  slowHint?: string;
  state: DataState<T>;
  /** 无数据时附在提示下面的字段契约 / 静态实查说明 */
  contract?: ReactNode;
  /**
   * 筛条件/取数按钮那一行，**永远渲染**（包括取数中、available:false、被筛空）。
   *
   * 为什么不能塞进 `children`：`children` 只在有数时画，于是"用户选了一个今天为空的档"
   * 之后，下拉和日期框会跟着数据一起消失 —— 人卡在自己刚选的那个值上，只能刷新整页。
   * 控件恰恰要在"没数"的那一刻还在。
   */
  controls?: ReactNode;
  /** 接口没接上时可以不给 —— 这时屏幕上只剩标题 + 契约 */
  children?: (data: T) => ReactNode;
}) {
  const ready = hasData(state);
  const text = describeState(state);
  /**
   * 用户 2026-10-22：「以上这些没有获取到数据的，都默认把各自的板块默认收缩起来，
   * 点击重新获取如果获取到了再展开」——全站一处（这一支只管后端说 `available:false` 的那一型：
   * 本机库里真的没有这一项）。
   *
   * ⚠️ 另外三型**故意不跟着收**：`error`／`offline` 是"坏了"，`not_wired` 是"接口没接上"，
   * 那三类必须红着摊在屏上（收起来＝把故障咽下去，与全站"检测器坏了不许兜成空值"同一条）。
   * `idle`（还没选一只）是引导句，也不收。
   *
   * 收起不等于藏话：**空态那句原因原句整句摆在 summary 上**（一个字不截），
   * 收进去的只有这块自己的口径说明与字段契约。
   * 取到数那一刻这一支整个不再渲染（改走 `ready` 那支画 children），所以"重新取到了就展开"是结构决定的，
   * 不靠额外状态、也不会被用户手动点开的那次盖住。
   */
  const quiet = isQuiet(state);
  return (
    <GlassCard>
      {!quiet && <h2 className="text-sm font-bold">{title}</h2>}
      {note && !quiet && <ExplainBox title="这一屏的口径说明">{plain(note)}</ExplainBox>}
      {controls && <div className="mb-1.5">{controls}</div>}
      {quiet
        ? <QuietFold title={title} text={plain(text)} note={note ? plain(note) : undefined} contract={contract} />
        : ready
          ? (children ? children(state.data) : null)
          : <>
            <p className={cn("text-[12px] leading-relaxed", state.kind === "error" || state.kind === "offline" ? "text-destructive" : "text-warning")}>{plain(text)}</p>
            {state.kind === "loading" && slowHint && (
              <p className="mt-0.5 text-[11px] leading-relaxed text-muted-foreground">{plain(slowHint)}</p>
            )}
            {state.kind !== "loading" && contract && <div className="mt-2 border-t border-dashed border-border pt-2">{contract}</div>}
          </>}
    </GlassCard>
  );
}

/** 接口未实现时的"验收单"：这个端点必须交出哪些字段、每个字段回答什么 */
export function Contract({ path, fields, source }: {
  path: string;
  fields: [string, string][];
  source?: string;
}) {
  return (
    <div className="rounded-lg border border-dashed border-warning/50 p-2">
      <p className="text-[11px] font-semibold text-warning">
        这一屏需要的接口：<code>{path}</code>
      </p>
      <p className="mt-0.5 text-[11px] leading-relaxed text-muted-foreground">
        下列字段一个都没有时，对应格子只能显示 <code>—</code>，不会用别的数顶上。
        {source ? ` 数据出处应当是：${source}。` : ""}
      </p>
      <ul className="mt-1.5 space-y-0.5">
        {fields.map(([f, what]) => (
          <li key={f} className="text-[11px] leading-relaxed">
            <code className="text-primary">{f}</code>
            <span className="text-muted-foreground"> —— {what}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

/** 已经存在的静态实查结论（不是接口给的，必须标清楚是哪天查的） */
export function Measured({ children }: { children: ReactNode }) {
  return (
    <p className="mt-2 rounded-lg bg-muted/50 p-2 text-[11px] leading-relaxed text-muted-foreground">
      <span className="font-semibold text-foreground">以下结论来自人工实查、不是本轮接口返回：</span>{children}
    </p>
  );
}

/**
 * 后端交出的 `caveats[]` —— 原样渲染，不改写、不缩短。
 *
 * 这些句子是实测结论（"同一只票可以属多个组"、"`chg10_avg` 是日均不是区间涨幅"、
 * "当日象限与窗口入口象限本来就不一样"），改写一次就可能变成假话。
 * 而且必须**看得见**，不能塞进 hover：这类口径说明藏在图标里等于没写。
 *
 * 徽标上念的是后端那份**中文标签**（`label`，真源 `duanxian/quant.py::_CAV_LABEL`），
 * 不是 `stale`、`cached_aggregates` 这类机器码 —— 用户 2026-10-22 指着整屏说"完全看不懂"，
 * 机器码是头号原因。机器码留在 `title` 里供对账；后端**没交 label** 的（其他模块的 caveat
 * 今天还没登记）照旧念 code，页面不自己按 code 猜一个中文名 —— 猜错一次，屏上就少一句真话。
 */
export function Caveats({ items }: { items?: { code?: string | null; label?: string | null; text?: string | null }[] | null }) {
  const list = (items ?? []).filter(c => c && c.text);
  if (list.length === 0) return null;
  return (
    <ul className="mt-2 space-y-1 border-t border-dashed border-border pt-2">
      {list.map((c, i) => (
        <li key={`${c.code ?? "c"}-${i}`} className="text-[11px] leading-relaxed text-muted-foreground">
          <span className="mr-1 rounded bg-warning/15 px-1 text-[10px] text-warning"
            title={c.code ?? "后端没交 code"}>{c.label ?? c.code ?? "口径"}</span>
          {plain(c.text)}
        </li>
      ))}
    </ul>
  );
}

/**
 * 「这一屏是缓存还是现算」那两行。收在一处是因为它的语义最容易悄悄丢：
 * 慢接口普遍带 TTL 缓存（覆盖体检、数据源体检、环境体检），只印 `generated_at`
 * 等于把 2.4 小时前的结果摆成刚刚算的 —— 页面必须同时给出**距今多久**和**是否已过期**。
 */
export function CacheLine({ generatedAt, cache }: {
  generatedAt?: string | null;
  cache?: { source?: string | null; saved_at?: string | null; age_seconds?: number | null; is_stale?: boolean | null; very_stale?: boolean | null; fresh_within_seconds?: number | null } | null;
}) {
  if (!cache && !generatedAt) return null;
  const stale = cache?.is_stale === true || cache?.very_stale === true;
  return (
    <>
      <Row label="本屏生成于" value={`${dash(generatedAt ?? cache?.saved_at)}${cache?.age_seconds != null ? `（${ageText(cache.age_seconds)}前）` : "（后端未给 age_seconds，无法判断有多新）"}`}
        tone={stale ? "warn" : undefined} />
      {cache && (
        <p className="text-[11px] leading-relaxed text-muted-foreground">
          结果出处：<span className="font-mono">{dash(cache.source)}</span>
          {typeof cache.fresh_within_seconds === "number" ? ` · ${ageText(cache.fresh_within_seconds)}内算新鲜` : ""}
          {cache.is_stale === true ? <span className="text-warning"> · 已超过新鲜窗口（这是上一份结果，不是刚刚算的）</span> : null}
          {cache.very_stale === true ? <span className="text-destructive"> · 已明显过期：这一屏可能读不到今天的变化</span> : null}
        </p>
      )}
    </>
  );
}

/** 后端给一句话 caveat（字符串版，如 watchlist/groups 的 `caveat`） */
export function CaveatText({ text }: { text?: string | null }) {
  if (!text) return null;
  return <p className="mt-2 border-t border-dashed border-border pt-2 text-[11px] leading-relaxed text-muted-foreground">{plain(text)}</p>;
}

/**
 * 字段含义（后端 `definitions{}`）。
 *
 * 只显示后端写了的键 —— 没有说明的字段不编解释，宁可少列一行。
 */
export function Definitions({ map, bare = false }: { map?: Record<string, string> | null; bare?: boolean }) {
  const entries = Object.entries(map ?? {});
  if (entries.length === 0) return null;
  // `bare` 只给 `CaliberFold` 用：它自己已经是一层折叠，再套一层就是同一个内容两个摘要行，
  // 密度问题没解决反而多一次点击。字典的 markup 仍然只有这一份。
  const body = (
    <dl className="space-y-0.5">
      {entries.map(([k, v]) => (
        <div key={k}>
          <dt className="inline font-mono text-foreground/80">{k}</dt>
          <dd className="inline text-muted-foreground"> —— {plain(String(v))}</dd>
        </div>
      ))}
    </dl>
  );
  if (bare) return <div className="mt-1">{body}</div>;
  return <ExplainBox title="这些字段是什么意思" count={entries.length}>{body}</ExplainBox>;
}

/** 数据覆盖区间（后端 `coverage{}`）—— "从哪天起、共几场"决定了一个空档是历史短还是没算 */
export function CoverageNote({ map }: { map?: Record<string, unknown> | null }) {
  /** coverage 里可能混进非区间的键（如 score-trend 的 `available_windows_for_this_stock` 是个数组），
   *  只有带 from/to 的才是覆盖区间 —— 结构不对的键跳过，不硬凑成 `—~—（— 场）` 那种看着像数据的空壳 */
  const entries = Object.entries(map ?? {}).filter(([, v]) =>
    v && typeof v === "object" && !Array.isArray(v) && ("from" in (v as object) || "to" in (v as object)));
  if (entries.length === 0) return null;
  return (
    <p className="mt-1 text-[10px] leading-relaxed text-muted-foreground">
      覆盖：{entries.map(([k, raw]) => {
        const v = raw as { from?: string | null; to?: string | null; sessions?: number | null };
        return `${k} ${dash(v.from)}~${dash(v.to)}（${dash(v.sessions)} 场）`;
      }).join(" · ")}
    </p>
  );
}

/** 出处行：接口自己声明它从哪来（`source` / `labels_source` / `computed_by`） */
export function Provenance({ bits }: { bits: (string | null | undefined)[] }) {
  const shown = bits.filter(Boolean);
  if (shown.length === 0) return null;
  // 后端散文字段从这儿上屏，和 Caveats/Definitions 同一道闸：只剥成对粗体（连号星号是数据）。
  return <p className="mt-1 text-[10px] leading-relaxed text-muted-foreground/80">出处：{plain(shown.join(" · "))}</p>;
}

/**
 * 通用说明框（用户 2026-10-14 点名要的东西）：把「说明与定义」这类**不是读数的文字**
 * 收进一个**默认收起**的框，读数那一屏才留给数。
 * 用户原话：「自选明细里面直接显示内容，说明内容和定义的内容占用了太多屏幕空间，
 * 你做一个通用的说明框，把这些内容全放进去，并且这个框默认是收起来的状态。
 * 同样的检查整个程序所有相关的地方，都做这样的修改。」
 *
 * ⚠️ 有一条纪律没跟着折：**`Caveats` 仍然默认摊在屏上**。那些句子说的是"这一屏哪里不完整、
 * 被截断、缺数、查询失败"，藏进折叠框等于没写（本文件 `Caveats` 上方原注释；10-13 那次
 * 「把查询失败念成没进榜」的事故就是这么来的）。折进去的只有口径解释与字段定义。
 *
 * 用原生 `<details>`：不需要状态、不需要 effect，浏览器负责开合，测试按"有没有 `open` 属性"判默认态。
 */
export function ExplainBox({ title, count, children }: {
  /** 这一格是什么说明，例如「这一屏的口径说明」 */
  title: string;
  /** 条数（字段定义那种）；没有就别写，不硬凑「（0 条）」 */
  count?: number | null;
  children: ReactNode;
}) {
  return (
    <details className="mt-1.5 border-t border-dashed border-border pt-1.5">
      <summary className="cursor-pointer text-[11px] leading-relaxed text-muted-foreground hover:text-foreground">
        {title}
        {typeof count === "number" ? `（${count} 条，后端原样给出，点开看）` : "（点开看）"}
      </summary>
      <div className="mt-1 space-y-0.5 text-[10px] leading-relaxed text-muted-foreground">{children}</div>
    </details>
  );
}

/**
 * 「口径声明 + 母体那句话 + 字段含义 + 覆盖 + 出处」收成**一处**默认收起的折叠块。
 *
 * 用户 2026-10-19 点名四维象限那一屏：「这些内容都默认收缩起来」—— 那几块加起来比表还长，
 * 一屏读下来先撞见九条句子才知道下面有没有数。
 *
 * 三条边界（都不是随手加的，是这一屏曾经说过的谎）：
 *  · **summary 把每一类的条数摆在外面** —— 收起不等于藏掉，读者不点开也知道这里有多少东西；
 *  · **告警与截断声明不进这里**（弹窗被拦 `popHint`、自检对不上、榜被上限截断、名称列点数）：
 *    那类句子折起来就等于让屏幕说假话，规矩没变（见 `ExplainBox` 那条红线与本仓 §六 台账）；
 *  · 文字一律后端原句，只剥成对 `**`（`plain`），不许改写、不许缩短。
 */
export function CaliberFold({ caveats, definitions, coverage, provenance, notes, foldAll }: {
  caveats?: { code?: string | null; label?: string | null; text?: string | null }[] | null;
  definitions?: Record<string, string> | null;
  coverage?: Record<string, unknown> | null;
  provenance?: (string | null | undefined)[] | null;
  /** 本屏那几句"母体/读法"散句（如 `universe.note`），一起收进来，不再各占一行 */
  notes?: (string | null | undefined)[] | null;
  /**
   * 用户 10-22 点名某几屏（首页异动提醒那四条：`no_announce_date`／`not_signal`／`no_band`／
   * `severe_unknown`）也收起来。这一型里含「本场有 1 只取不到 / 1 只判不了」的缺数计数，
   * 全站默认绝不折。允许折的条件是**数字本身还在屏上**：summary 里按类别报条数
   * （口径解释 N 条 · 缺数与告警 M 条），所以"有东西没读到"这件事仍然看得见，只是原句要点开。
   * 默认 false ⇒ 不调这个开关的每一屏仍沿用那条红线。
   */
  foldAll?: boolean;
}) {
  // 只有"纯口径解释"那一份被折进去；缺数／截断／查询失败那一族留在屏上（`lib/caveatKind.ts` 一处定）
  const { visible, foldable } = splitCaveats(caveats);
  const held = foldAll ? [...foldable, ...visible] : foldable;
  const heldUnhealthy = foldAll ? visible.length : 0;
  const ds = Object.entries(definitions ?? {});
  const vs = Object.entries(coverage ?? {}).filter(([, v]) =>
    v && typeof v === "object" && !Array.isArray(v) && ("from" in (v as object) || "to" in (v as object)));
  const ps = (provenance ?? []).filter(Boolean);
  const ns = (notes ?? []).filter(Boolean);
  const foldCount = held.length + ds.length + vs.length + ps.length + ns.length;
  return (
    <>
      {!foldAll && <Caveats items={visible} />}
      {foldCount > 0 && (
        <details className="mt-1.5 border-t border-dashed border-border pt-1.5">
          <summary className="cursor-pointer text-[11px] leading-relaxed text-muted-foreground hover:text-foreground">
            口径与覆盖：{[
              held.length - heldUnhealthy ? `${held.length - heldUnhealthy} 条口径声明` : null,
              heldUnhealthy ? `${heldUnhealthy} 条缺数与告警（本场有读不到／判不了的行）` : null,
              ns.length ? `${ns.length} 句母体与读法` : null,
              ds.length ? `${ds.length} 个字段解释` : null,
              vs.length ? `${vs.length} 段覆盖区间` : null,
              ps.length ? `出处 ${ps.length} 项` : null,
            ].filter(Boolean).join(" · ")}（已收起 · 点开看全文）
            {!foldAll && visible.length > 0 && (
              <span className="ml-1 text-warning">另有 {visible.length} 条缺数／截断类声明没有折，在上方</span>
            )}
          </summary>
          <div className="mt-1 space-y-0.5 text-[10px] leading-relaxed text-muted-foreground">
            {ns.map((t, i) => <p key={`n${i}`}>{plain(String(t))}</p>)}
            <Caveats items={held} />
            {/* `bare`：这一块自己已经是一层折叠，字段字典不能再套一层摘要——
                套上之后同一份解释会有两行标题，收起时看着像两个不同的东西。 */}
            <Definitions map={definitions} bare />
            <CoverageNote map={coverage} />
            {ps.length > 0 && <Provenance bits={(provenance ?? []).map(x => x)} />}
          </div>
        </details>
      )}
    </>
  );
}

/** 一行"标签：值"，缺失一律 `—` */
export function Row({ label, value, tone }: { label: string; value: ReactNode; tone?: "warn" | "bad" }) {  return (
    <p className="text-[12px] leading-relaxed">
      <span className="text-muted-foreground">{label}：</span>
      <span className={cn(tone === "warn" && "text-warning", tone === "bad" && "text-destructive")}>{value}</span>
    </p>
  );
}
