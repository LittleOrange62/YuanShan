import { useCallback, useEffect, useState } from "react";
import { GlassCard } from "@/components/ui/GlassCard";
import { RotateCcw, Save } from "lucide-react";
import { apiUrl } from "@/lib/base";
import { authHeaders } from "@/lib/api";
import { dash, errorText } from "@/lib/datastate";
import { plain } from "@/lib/agent";
import { trimNum } from "@/lib/ranking";
import { cn } from "@/lib/utils";
import {
  bandLines, bandRange, buildPayload, draftMaxScore, finiteScore, missingBands, scoreMax,
  type EnergyBatch, type EnergyBand, type EnergyConfig, type EnergyPayload,
} from "@/lib/volumeEnergy";

/**
 * 系统配置 →「倍量加权分档」。
 *
 * 这一卡改的是**倍量榜那把尺子**（`analytics.volume_double.dbl_energy` 怎么算出来的）：
 * 18 档日量比区间各给多少分（分值＝一天落在这一档计几分）、要不要按交易所分开配。
 * 判据与档表本体只住 `app/compute/volume_energy.py` 一处，界面一律读接口交回来的那一份，
 * **不在前端抄第二份 0.1 的档表，也不在前端算占比**（同页两块用两把尺的教训见《10-每个数怎么算》§4.1）。
 *
 * 口径改于 2026-10-19 第二稿：算式 `得分 = Σ 每档 ( 这一档在窗口里出现的次数 × 这一档的分值 )`，
 * **没有系数／占比／归一化这一层**（用户原话：其实不存在系数这个参数）。所以这一卡上
 * 不再有量纲下拉，也不再有分母；算式原文直接念接口 `scale.formula`。
 *
 * 四条必须写在屏幕上的线（缺一条这一卡就会骗人）：
 *  1. **保存了≠已生效**。指纹一变后端会自动起一次后台全量重算，跑完之前库里还是旧批，
 *     倍量榜筛的还是旧数（那一侧会挂 `energy_batch_stale` 那句 caveat）。
 *     所以保存的回显必须把这句话念出来，而不是只显示「已保存」。
 *  2. **上限来自分值最大的那一档**（满窗＝窗口场数 × 那一档的分值），实测分位在
 *     `status.distribution` —— 两者并排念，别拿理论上限当分布读。
 *  3. **这张尺与名次是一套**：换尺之后名次跟着换（规则原文念接口 `rank_rule`），
 *     界面不自造那句话（实现只住 `lib/ranking.ts` 一处）。
 *  4. **后端拒了就把原因原样带出来**。校验不过时后端整笔不存（半份配置会让重算按错的档跑一遍），
 *     界面把那句 `detail` 照念，不许折叠成「保存失败」。
 */

/** POST 成功的回体：`reason` 在信封顶层（`postJson` 只读 `data`/`detail`/`error`，所以这里自己发）。 */
interface SaveData {
  saved?: { ok?: boolean | null; before?: string | null; after?: string | null;
            changed?: boolean | null; needs_rebuild?: boolean | null } | null;
  rebuild?: { started?: boolean | null; reason?: string | null } | null;
  job?: EnergyConfig["job"] | null;
  scale?: EnergyConfig["scale"] | null;
}
interface SaveResult { ok: boolean; status: number; data: SaveData | null; reason: string }

const DASH = "—";

async function loadEnergyConfig(): Promise<EnergyConfig> {
  const resp = await fetch(apiUrl("/api/config/volume-energy"), { headers: authHeaders() });
  const body = await resp.json().catch(() => null) as { data?: EnergyConfig } | null;
  if (!resp.ok) throw new Error(errorText(body) ?? `HTTP ${resp.status}`);
  if (!body?.data) throw new Error("后端没交出档表配置（返回里没有 data），这一卡显示不了生效那份");
  return body.data;
}

async function postEnergyConfig(payload: EnergyPayload): Promise<SaveResult> {
  let resp: Response;
  try {
    resp = await fetch(apiUrl("/api/config/volume-energy"), {
      method: "POST",
      headers: { ...authHeaders(), "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
  } catch {
    return { ok: false, status: 0, data: null, reason: "连不到后端，这一笔档表没写进去（不是已保存）" };
  }
  const body = await resp.text().catch(() => "");
  let parsed: (SaveData & { detail?: unknown; error?: string }) | null = null;
  try { parsed = body ? JSON.parse(body) : null; } catch { parsed = null; }
  if (!resp.ok) {
    // 422 那句原因（`这份档表没采纳：…`）是后端校验原话，整笔没存，必须一字不落地念出来
    const why = (parsed && errorText(parsed)) || (body ? body.slice(0, 300) : "") || `HTTP ${resp.status}`;
    return { ok: false, status: resp.status, data: null, reason: why };
  }
  const rec = parsed as unknown as { data?: SaveData; reason?: string } | null;
  if (!rec?.data) return { ok: false, status: resp.status, data: null,
                           reason: "后端没回存好的那一份，不假定已生效" };
  return { ok: true, status: resp.status, data: rec.data, reason: rec.reason ?? "" };
}

/** 一摊档位的输入表：档名与区间读接口 `bands`，分值与满窗上限随草稿实时重算。 */
function BandTable({ bands, scores, windowDays, onChange, title, note }: {
  bands: EnergyBand[];
  scores: Record<string, string>;
  windowDays: number | null;
  onChange: (label: string, v: string) => void;
  title: string;
  note?: string;
}) {
  const nums: Record<string, number | null> = {};
  for (const b of bands) nums[String(b.label ?? "")] = finiteScore(scores[String(b.label ?? "")]);
  const lines = bandLines(bands, nums, windowDays);
  const top = scoreMax(nums);
  const topLabels = lines.filter(l => l.isTop).map(l => l.label);
  const miss = missingBands(bands, nums);
  return (
    <div className="mt-2 rounded-lg border border-border/60 p-2">
      <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
        <p className="text-[12px] font-medium">{title}</p>
        <p className="text-[11px] text-muted-foreground">
          草稿这份：一天最多
          <code className="tabular-nums text-foreground">{trimNum(top)}</code> 分
          ｜满窗（{dash(windowDays)} 场全在顶档）最多
          <code className="tabular-nums text-foreground">{trimNum(draftMaxScore(nums, windowDays))}</code> 分
          ｜顶档（分值最大的那一档）：{topLabels.length ? topLabels.join("、") : DASH}
        </p>
      </div>
      {note && <p className="mt-0.5 text-[11px] leading-relaxed text-muted-foreground">{note}</p>}
      {/* 10-19 用户点名「这里没办法让我进行赋分啊」：那 18 格输入框本来就能改，但它的边框与底色和正文
          几乎同色，右边还挂着一列与它数值完全相同的「最多贡献」，看着就是一张只读表。
          所以两件事一起做：那一列删掉（数值与输入框重复，说不清哪个能改），输入框按界面其它输入件
          的形状给实心边框＋聚焦环，并在表上方明写"改哪一格、改完按哪个按钮"。 */}
      <p className="mt-1 text-[11px] leading-relaxed text-foreground">
        改分就动下面这张表里<b className="font-medium">「分值」那一格的那个方框</b>
        —— {dash(bands.length)} 档每一格都可以改，改完按这一卡下面的
        <b className="font-medium">「保存这份档表」</b>（保存会触发一次全量重算，跑完之前榜上还是旧批）。
      </p>
      <table className="mt-1.5 w-full text-[11px]">
        <thead>
          <tr className="text-left text-muted-foreground">
            <th className="py-0.5 font-normal">档名（日量比）</th>
            <th className="py-0.5 font-normal">区间</th>
            <th className="w-24 py-0.5 font-normal"
              title="分值＝一天落在这一档计几分。它不是系数、不是权重、也不要除以什么：得分就是每一档（出现几场 × 这一档的分值）累加。这一格里那个方框就是输入框，直接改数字。">
              分值（一天计几分）
              <span className="ml-1 rounded bg-primary/12 px-1 text-[10px] text-primary"
                title="这一列是可以输入的：点进去改数字，右边那一列会跟着算出这一档占满整窗时最多几分。">
                可改
              </span>
            </th>
            <th className="py-0.5 font-normal"
              title={`窗口 ${dash(windowDays)} 场全落在这一档时才拿得到这个数（这一档的分值 × 场数）。与上面 scale.max_score 那句满窗上限的区别：那是顶档占满整窗，这是这一档占满整窗。`}>
              这一档占满整窗最多（分值 × {dash(windowDays)}）
            </th>
          </tr>
        </thead>
        <tbody>
          {lines.map(l => (
            <tr key={l.label || DASH} className="border-t border-border/40">
              <td className="py-0.5 font-mono">
                {l.label || DASH}
                {l.isTop && (
                  <span className="ml-1 rounded bg-primary/12 px-1 text-[10px] text-primary"
                    title="这一档是当前草稿里分值最大的那一档：满窗上限（窗口场数 × 这一档的分值）就是按它算出来的。">
                    上限来源
                  </span>
                )}
              </td>
              <td className="py-0.5 font-mono tabular-nums">{bandRange(l.lo, l.hi)}</td>
              <td className="py-0.5">
                <input value={scores[l.label] ?? ""} aria-label={`${title} ${l.label} 的分值`}
                  inputMode="decimal"
                  onChange={e => onChange(l.label, e.target.value)}
                  title={l.score === null
                    ? "这一格是输入框，现在还不是一个 0 以上的数：填一个数字（比如 0.05）才会被采纳"
                    : `这一格是输入框：一天落进这一档就加 ${trimNum(l.dayMax)} 分（不除以任何东西）`}
                  className={cn("h-7 w-20 cursor-text rounded-md border bg-background px-2 py-1",
                    "font-mono text-[12px] tabular-nums outline-none transition",
                    "focus:border-primary focus:ring-2 focus:ring-primary/30",
                    l.score === null ? "border-destructive/70" : "border-border")} />
              </td>
              <td className="py-0.5 font-mono tabular-nums"
                title={`窗口 ${dash(windowDays)} 场全落在这一档时才拿得到这个数（这一档的分值 × 场数）`}>
                {trimNum(l.windowMax, 2)}
              </td>
            </tr>
          ))}
          {!lines.length && (
            <tr><td colSpan={4} className="py-1 text-[11px] text-muted-foreground">
              后端没交出任何一档（接口 `bands` 是空的）—— 这一卡显示不了档表，不是「没有档可配」。
            </td></tr>
          )}
        </tbody>
      </table>
      {miss.length > 0 && (
        <p className="mt-1 text-[11px] leading-relaxed text-warning">
          这几档现在不是一个 0 以上的数：{miss.join("、")} —— 少一档等于偷偷把这一档算成 0 分，
          后端会整笔拒收并把原因念在下面，界面不替你补一份「看着合理」的数。
        </p>
      )}
    </div>
  );
}

export function VolumeEnergyPanel() {
  const [cfg, setCfg] = useState<EnergyConfig | null>(null);
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState("");
  const [notice, setNotice] = useState("");
  const [noticeKind, setNoticeKind] = useState<"ok" | "bad">("ok");
  const [scope, setScope] = useState("global");
  const [globalDraft, setGlobalDraft] = useState<Record<string, string>>({});
  const [byMarket, setByMarket] = useState<Record<string, Record<string, string>>>({});

  /** 把后端那份填进草稿（档名一律按接口 `bands` 的 label 取，前端不补档）。 */
  const applyCfg = useCallback((next: EnergyConfig) => {
    setCfg(next);
    setScope(String(next.profile?.scope ?? "global"));
    const toText = (m?: Record<string, number | null | undefined> | null) => {
      const out: Record<string, string> = {};
      for (const b of next.bands ?? []) {
        const lab = String(b.label ?? "");
        if (lab) out[lab] = m?.[lab] === undefined || m?.[lab] === null ? "" : String(m[lab]);
      }
      return out;
    };
    setGlobalDraft(toText(next.profile?.global));
    const by: Record<string, Record<string, string>> = {};
    for (const [m, table] of Object.entries(next.profile?.by_market ?? {})) by[m] = toText(table);
    setByMarket(by);
  }, []);

  const reload = useCallback(() => loadEnergyConfig()
    .then(applyCfg)
    .catch(e => setErr(e instanceof Error ? e.message : "档表配置读不到（接口没回东西）")), [applyCfg]);
  useEffect(() => { void reload(); }, [reload]);

  const bands = cfg?.bands ?? [];
  const windowDays = typeof cfg?.scale?.window_days === "number" ? cfg.scale.window_days : null;
  const markets = cfg?.markets ?? [];
  const status = cfg?.status ?? null;
  const batches: EnergyBatch[] | null = status?.batches ?? status?.stored_batches ?? null;
  const dist = status?.distribution ?? null;

  const dirty = cfg !== null && (() => {
    const sameTable = (a: Record<string, string>, m?: Record<string, number | null | undefined> | null) =>
      bands.every(b => {
        const lab = String(b.label ?? "");
        const left = finiteScore(a[lab]);
        const right = finiteScore(m?.[lab]);
        return (left === null && right === null) || (left !== null && left === right);
      });
    if (String(cfg.profile?.scope ?? "global") !== scope) return true;
    if (!sameTable(globalDraft, cfg.profile?.global)) return true;
    const savedBy = cfg.profile?.by_market ?? {};
    for (const m of markets) if (!sameTable(byMarket[m] ?? {}, savedBy[m])) return true;
    return false;
  })();

  const send = async (payload: EnergyPayload, doing: string) => {
    setBusy(doing); setNotice("");
    try {
      const r = await postEnergyConfig(payload);
      if (!r.ok) {
        setNoticeKind("bad");
        setNotice(`${r.reason}｜这一笔整条没存，生效的还是原来那份档表。`);
        return;
      }
      const next = await loadEnergyConfig().catch(() => null);
      if (next) applyCfg(next);
      const started = r.data?.rebuild?.started === true;
      const parts = [
        `后端回的原话：${plain(r.reason) || DASH}`,
        `指纹 ${dash(r.data?.saved?.before)} → ${dash(r.data?.saved?.after)}`,
        started
          ? "正在按新档表重算历史场，跑完之前倍量榜筛的还是旧批（那一侧会挂 caveat energy_batch_stale，"
            + "下面 job 那行跑完才会换成已完成）。这一卡改的是配置，库里那一批批场次要等重算覆盖才对得上。"
          : `这一笔没有起新重算：${plain(r.data?.rebuild?.reason ?? "后端没说明原因")}。`,
      ];
      if (r.data?.job?.error) parts.push(`最近一场重算报的错：${plain(r.data.job.error)}`);
      // 成功也用警示色：这一笔的意义是「存下了、还没生效」，念成绿色的「已保存」就是那句假话本身
      setNoticeKind("ok");
      setNotice(parts.filter(Boolean).join("　｜　"));
    } catch (e) {
      setNoticeKind("bad");
      setNotice(e instanceof Error ? e.message : "后端没有回应这次保存，不假定已生效");
    } finally { setBusy(""); }
  };

  /** 存草稿那份（第二稿：payload 里没有 normalize，带了就会被后端 422 拒）。 */
  const save = () => send(buildPayload({
    scope, global: globalDraft, byMarket, bands, markets,
  }), "保存");

  /** 恢复默认：分值表用接口 `default_scores` 组，前端不另写一份默认分值。 */
  const restore = () => {
    const fallback = buildPayload({
      scope: "global", global: cfg?.default_scores ?? null, byMarket: null, bands, markets,
    });
    send(fallback, "恢复默认");
  };

  const job = cfg?.job ?? null;

  return (
    <GlassCard>
      <div className="flex flex-wrap items-center gap-2">
        <h2 className="text-sm font-semibold">倍量加权分档（倍量榜那把尺子）</h2>
        <span className="rounded-md bg-muted px-1.5 py-0.5 font-mono text-[11px] text-muted-foreground">
          {dash(cfg?.fingerprint)}
        </span>
        {status?.in_sync === true && <span className="rounded bg-primary/12 px-1.5 py-0.5 text-[10px] text-primary">库里批次与生效指纹一致</span>}
        {status?.in_sync === false && <span className="rounded bg-warning/15 px-1.5 py-0.5 text-[10px] text-warning">库里还是旧批：改完档表的重算没跑完</span>}
        {status?.in_sync == null && <span className="rounded bg-muted px-1.5 py-0.5 text-[10px] text-muted-foreground">是否同批：没读到（不是「没问题」）</span>}
        <button type="button" onClick={() => { setErr(""); void reload(); }} disabled={busy !== ""}
          className="ml-auto rounded-lg border border-border px-2 py-1 text-[11px] text-muted-foreground hover:border-primary/40 hover:text-primary disabled:opacity-60">
          重新读取
        </button>
      </div>

      {err && <p role="alert" className="mt-2 text-[11px] text-destructive">{err}</p>}
      {notice && (
        <p role="status" className={cn("mt-2 text-[11px] leading-relaxed",
          noticeKind === "bad" ? "text-destructive" : "text-warning")}>{plain(notice)}</p>
      )}

      {/* 算式与上限、实测分布、名次规则：四句都念接口原文，前端不另写一把尺 */}
      <p className="mt-1.5 text-[11px] leading-relaxed text-muted-foreground">
        算式（后端 <code>scale.formula</code> 原话）：
        <b className="text-foreground">{plain(cfg?.scale?.formula ?? "后端没交 formula —— 这一张尺的算式没有出处，界面不自己补一句")}</b>
        。改这里等于换尺子：<b className="text-foreground">保存了不等于已生效</b> —— 指纹一变后端会自动起一次后台全量重算，
        跑完之前倍量榜筛的还是旧批（那一侧的 caveat 写的是 <code>energy_batch_stale</code>）。
        单位：{plain(cfg?.scale?.unit ?? "后端没交 unit")}。
      </p>
      {cfg?.scale?.note && (
        <p className="mt-1 text-[11px] leading-relaxed text-muted-foreground">{plain(cfg.scale.note)}</p>
      )}
      <div className="mt-1 grid gap-1 text-[11px] leading-relaxed sm:grid-cols-2">
        <p className="text-muted-foreground">
          <span className="text-muted-foreground">这一张尺的上限：</span>
          一天最多 <code className="font-mono tabular-nums text-foreground">{trimNum(cfg?.scale?.max_per_day)}</code> 分、
          满窗（{dash(windowDays)} 场全在顶档）
          <code className="font-mono tabular-nums text-foreground">{trimNum(cfg?.scale?.max_score)}</code> 分
          （<code>scale.max_score</code>，是<b className="text-foreground">数学上限</b>不是实测）。
          顶档不是写死的档名：看下面那张表里标着「上限来源」的那一档。
        </p>
        {dist === null || dist?.session === undefined ? (
          <p className="text-muted-foreground">
            实测分布没读到（那条聚合失败了）—— 不拿 0 或上一个数顶上。
          </p>
        ) : (
          <p className="text-muted-foreground">
            实测分布（<code>status.distribution</code>，{dash(dist?.session)} 这场
            {dash(dist?.rows)} 行、有值 {dash(dist?.with_value)}）：
            p50 <code className="font-mono tabular-nums text-foreground">{trimNum(dist?.p50)}</code>
            · p90 <code className="font-mono tabular-nums text-foreground">{trimNum(dist?.p90)}</code>
            · p99 <code className="font-mono tabular-nums text-foreground">{trimNum(dist?.p99)}</code>
            · 最高 <code className="font-mono tabular-nums text-foreground">{trimNum(dist?.max)}</code>
            · 最低 <code className="font-mono tabular-nums text-foreground">{trimNum(dist?.min)}</code>
            ｜≥0 分的 {dash(dist?.at_or_above_zero)} 只
          </p>
        )}
      </div>
      <p className="mt-1 text-[11px] leading-relaxed text-muted-foreground">
        名次与这张尺是一套（规则原文读接口，界面不自造那一句）：
        {plain(cfg?.rank_rule ?? "后端没交 rank_rule —— 名次规则没有出处，这一卡只显示分数不显示名次规则")}
      </p>
      {(typeof cfg?.top_n_default === "number" || typeof cfg?.page_size === "number") && (
        <p className="mt-0.5 text-[11px] leading-relaxed text-muted-foreground">
          倍量形态页那块「进榜后逐日涨跌幅」的出厂档：母体＝当日前 {dash(cfg?.top_n_default)} 名、
          每页 {dash(cfg?.page_size)} 行（两个数都只住后端一处，界面读接口不写死）。
        </p>
      )}
      {status?.profile_source && (
        <p className="mt-1 text-[11px] leading-relaxed text-muted-foreground">
          生效配置出处：{plain(status.profile_source)}
          {status.broken === true ? "（已存的那份不合法，后端退回默认档表并在这一句里说明原因）" : ""}
        </p>
      )}

      <div className="mt-2 flex flex-wrap items-center gap-x-4 gap-y-1.5">
        <label className="flex items-center gap-1 text-[11px] text-muted-foreground"
          title={`作用域：global＝全部票共用一张档表；market＝按交易所各配一张（能单配的只有 ${(cfg?.markets ?? []).join("／") || "后端没交 markets"}，没单配的市场自动落回全局那张）。`
            + "键用的是 security.market，不是板块（板块有大量空值，按板块配会让那些票静默落回全局而界面却像配过了）。"
            + "换作用域＝换指纹＝必须重算历史场。"}>
          作用域
          <select value={scope} onChange={e => setScope(e.target.value)}
            className="rounded border border-border/60 bg-background px-1.5 py-0.5 text-[11px]">
            {(cfg?.scopes ?? []).map(o => (
              <option key={o} value={o}>{o === "market" ? "market（按交易所各配一张）" : o === "global" ? "global（全市场一张）" : o}</option>
            ))}
            {!(cfg?.scopes ?? []).length && <option value={scope}>{scope}（后端没交 scopes）</option>}
          </select>
        </label>
        {scope === "market" && (
          <span className="text-[11px] leading-relaxed text-warning">
            按交易所配时：<b className="text-foreground">没单配的市场落回全局那张</b>，
            下面没展开的交易所就是没单配 —— 界面不会替你补一份「等于全局」的抄本，那会被读成已经单配过。
          </span>
        )}
      </div>

      <BandTable bands={bands} scores={globalDraft} windowDays={windowDays}
        title="全局档表" onChange={(lab, v) => setGlobalDraft(d => ({ ...d, [lab]: v }))}
        note={`档名与区间都读接口 bands 那份（这一场交出 ${bands.length} 档，尾档没有上界；条数以接口为准，界面不抄）；分值与上限随下面的输入实时重算，说的是草稿会是什么样，生效那份看上面的 scale。`} />

      {scope === "market" && markets.map(m => {
        const on = Object.prototype.hasOwnProperty.call(byMarket, m);
        return (
          <div key={m} className="mt-2 rounded-lg border border-border/60 p-2">
            <div className="flex flex-wrap items-center gap-2">
              <p className="text-[12px] font-medium">{m}</p>
              <span className={cn("rounded px-1.5 py-0.5 text-[10px]",
                on ? "bg-primary/12 text-primary" : "bg-muted text-muted-foreground")}>
                {on ? "已单配这一市场" : "没单配：落回全局那张"}
              </span>
              <button type="button" disabled={busy !== ""}
                onClick={() => setByMarket(cur => {
                  const next = { ...cur };
                  if (Object.prototype.hasOwnProperty.call(next, m)) delete next[m];
                  else {
                    const seed: Record<string, string> = {};
                    for (const b of bands) seed[String(b.label ?? "")] = globalDraft[String(b.label ?? "")] ?? "";
                    next[m] = seed;
                  }
                  return next;
                })}
                className="rounded-lg border border-border px-2 py-1 text-[11px] text-muted-foreground hover:border-primary/40 hover:text-primary disabled:opacity-60">
                {on ? "取消单配（改用全局）" : "单配这一市场（从全局抄一份底子）"}
              </button>
            </div>
            {on && (
              <BandTable bands={bands} scores={byMarket[m] ?? {}} windowDays={windowDays}
                title={`${m} 档表`}
                onChange={(lab, v) => setByMarket(cur => ({ ...cur, [m]: { ...(cur[m] ?? {}), [lab]: v } }))}
                note="这一张只改这个交易所的票；取消单配就是把它交回全局那张，而不是给这一市场存一份零分。" />
            )}
          </div>
        );
      })}

      <div className="mt-2 flex flex-wrap items-center gap-2">
        <button type="button" disabled={busy !== "" || !cfg || !dirty} onClick={() => void save()}
          className="inline-flex items-center gap-1 rounded-lg bg-primary/15 px-2.5 py-1.5 text-[11px] font-medium text-primary hover:bg-primary/25 disabled:opacity-50">
          <Save className="h-3 w-3" />{busy === "保存" ? "提交中…" : "保存这份档表"}
        </button>
        <button type="button" disabled={busy !== "" || !cfg} onClick={() => void restore()}
          title="用接口交回的 default_scores 组一份 payload 再提交（前端没有另写一份默认分值表）：按交易所单配的那几张清掉。提交照旧会触发重算判定。"
          className="inline-flex items-center gap-1 rounded-lg border border-border px-2.5 py-1.5 text-[11px] text-muted-foreground hover:border-primary/40 hover:text-primary disabled:opacity-50">
          <RotateCcw className="h-3 w-3" />{busy === "恢复默认" ? "提交中…" : "恢复默认分值"}
        </button>
        {!cfg && <span className="text-[11px] text-muted-foreground">档表还没读到，两个按钮都不会发请求。</span>}
        {cfg && !dirty && <span className="text-[11px] text-muted-foreground">与生效那份一致，没有要提交的改动。</span>}
        {cfg && dirty && <span className="text-[11px] text-warning">有未保存的改动（草稿，界面上的分值与上限跟着它变）。</span>}
      </div>

      {/* ───────────── 库里批次 / 实测分布 / 后台重算 ───────────── */}
      <div className="mt-3 grid gap-2 border-t border-dashed border-border pt-2 sm:grid-cols-2">
        <div>
          <p className="text-[12px] font-medium">库里有哪几批（按指纹分组）</p>
          {(batches ?? []).length === 0 && (
            <p className="mt-0.5 text-[11px] leading-relaxed text-muted-foreground">
              一条批次都没读到 —— 这是聚合没结果，不是「库里都是同一批」。
            </p>
          )}
          <ul className="mt-0.5 space-y-0.5">
            {(batches ?? []).map((b, i) => (
              <li key={`${b.fp}-${i}`} className="text-[11px] leading-relaxed">
                <span className="font-mono">{dash(b.fp)}</span>
                {b.fp === cfg?.fingerprint
                  ? <span className="ml-1 rounded bg-primary/12 px-1 text-[10px] text-primary">当前生效</span>
                  : <span className="ml-1 rounded bg-warning/15 px-1 text-[10px] text-warning">旧批</span>}
                <span className="text-muted-foreground">
                  ｜{dash(b.rows)} 行（有值 {dash(b.with_value)}）· {dash(b.sessions)} 场 · {dash(b.lo)}~{dash(b.hi)}
                  {b.last_written ? ` · 最近写入 ${dash(b.last_written)}` : ""}
                </span>
              </li>
            ))}
          </ul>
          <p className="mt-1 text-[11px] leading-relaxed text-muted-foreground">
            生效指纹 <code className="font-mono">{dash(cfg?.fingerprint)}</code> 在库里有没有对应批次：
            {status?.in_sync === true ? "有（榜上筛的就是这一批）"
              : status?.in_sync === false ? <b className="text-foreground">没有，倍量榜现在筛的是旧批</b>
                : DASH}
          </p>
        </div>

        <div>
          <p className="text-[12px] font-medium">批次每场各有多少只落在那几档（对照）</p>
          <p className="mt-0.5 text-[11px] leading-relaxed text-muted-foreground">
            上面那句「满窗」是<b className="text-foreground">理论上限</b>：要 {dash(windowDays)} 场全落顶档才拿得到。
            真票不会那样放量，所以填筛值照实测分位来（p50／p90／p99 就在上面那一行），
            别照旧口径「倍量次」的直觉 —— 那一列现在单位是<b className="text-foreground">分</b>，不是次。
          </p>
          <p className="mt-1 text-[11px] leading-relaxed text-muted-foreground">
            榜上「最少加权分」那一格的门槛参数（<code>filter.min_energy</code>）默认仍是 0，
            也就是不筛；改档位分值不会改这个默认值。
          </p>
        </div>
      </div>

      <div className="mt-2 rounded-lg border border-border/60 p-2">
        <p className="text-[12px] font-medium">后台全量重算</p>
        {!job && <p className="mt-0.5 text-[11px] text-muted-foreground">后端没回 job —— 这一格显示不了跑到哪了。</p>}
        {job && (
          <p className="mt-0.5 text-[11px] leading-relaxed text-muted-foreground">
            {job.running === true
              ? <b className="text-foreground">正在重算</b>
              : <span>现在没在跑</span>}
            ｜起了 {job.started_at ? ` ${dash(job.started_at)}` : ""}
            {job.finished_at ? ` · 收了 ${dash(job.finished_at)}` : ""}
            {job.reason ? ` · 这一场是因为：${plain(job.reason)}` : ""}
            {job.error ? <span className="text-destructive"> · 失败：{plain(job.error)}</span> : ""}
          </p>
        )}
        {job?.last && Object.keys(job.last).length > 0 && (
          <p className="mt-0.5 text-[11px] leading-relaxed text-muted-foreground">
            最近一次重算：<code className="font-mono">{plain(String((job.last as Record<string, unknown>).ok ?? DASH))}</code>
            {" · 批次 "}{String((job.last as Record<string, unknown>).fingerprint ?? DASH)}
            {" · 该打指纹 "}{dash((job.last as Record<string, unknown>).rows_target)}
            {" · 已打上 "}{dash((job.last as Record<string, unknown>).rows_synced)}
            {" · 没打上 "}{dash((job.last as Record<string, unknown>).rows_unsynced)}
            {" · 用时 "}{dash((job.last as Record<string, unknown>).seconds)} 秒
            {" · 区间 "}{dash((job.last as Record<string, unknown>).from)}~{dash((job.last as Record<string, unknown>).to)}
          </p>
        )}
        <p className="mt-1 text-[11px] leading-relaxed text-muted-foreground">
          这一卡只管配置；重算由后端在指纹变化时自动起，界面没有单独按钮，也不需要 —— 关掉这一屏它照跑。
        </p>
      </div>
    </GlassCard>
  );
}
