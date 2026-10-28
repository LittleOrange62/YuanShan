import { useEffect, useMemo, useState } from "react";
import { Loader2, RefreshCw, Save } from "lucide-react";
import { GlassCard } from "@/components/ui/GlassCard";
import { useNonce, useProbe } from "@/lib/probe";
import { dash } from "@/lib/datastate";
import { postJson } from "@/lib/jsonPost";
import { MARK_HINT, MARK_LABEL, markOf, tallyMarks, type Provenance } from "@/lib/noteProvenance";
import { cn } from "@/lib/utils";

/**
 * 复盘笔记（`ys.review_note`）。列结构、中文列名、默认值判据**全部来自后端**
 * （`duanxian/review_notes.py:FIELDS` 是唯一真相源），这里一个字都不抄。
 *
 * 三条界面规矩：
 *  · 草稿只进**空着**的格子，已经有字的格子绝不覆盖（重算也一样）；
 *  · 每格标来源（机器填入·未改 / 初始为机器填入·你已改 / 你写的），判据在 `noteProvenance.ts`；
 *  · 保存后**以接口读回的那一份为准**显示，不拿本地状态冒充"已存"。
 */

interface FieldMeta { key: string; label: string; group: string; question: string; note: string; no_default: string }
interface Draft { text: string; from: string[] }
interface Defaults {
  date: string; prev_date: string | null; is_trade_day: boolean | null; settled: boolean;
  has_data?: boolean; cacheable?: boolean; latest_data_date?: string | null;
  news_counts?: Record<string, number>; fields: Record<string, Draft>; empty: Record<string, string>;
  missing: Record<string, string>; notes: string[]; generated_at: string; elapsed_ms: number;
  network_free?: boolean; hint?: string; from_cache?: boolean; cache_written_at?: string | null;
}
interface NoteRow {
  trade_date: string; machine_baseline: Record<string, string>;
  machine_generated_at: string | null; created_at: string; updated_at: string;
  /** 13 个正文列（后端按 FIELDS 组好的一份，界面不用去非正文列里刨） */
  values: Record<string, string>;
}
interface Bundle {
  available: boolean; reason?: string; fields?: FieldMeta[]; date?: string;
  note?: NoteRow | null; saved?: boolean; defaults?: Defaults; expected_date?: string | null;
}
interface DaySummary {
  trade_date: string; chars: number; filled: number; updated_at: string | null;
}

export function ReviewNotes() {
  const [nonce, refresh] = useNonce();
  // 进页面**不预设今天**：今天是哪天写不写得了由库里有没有这一场决定。
  // 空 date = 让后端按 `latest_data_session()` 给（与复盘页、首板页同一个规则）。
  const [date, setDate] = useState("");
  const [jump, setJump] = useState<{ date: string; refresh: 0 | 1 }>({ date: "", refresh: 0 });
  const [values, setValues] = useState<Record<string, string>>({});
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  const [msg, setMsg] = useState("");
  const [saving, setSaving] = useState(false);

  const bundle = useProbe<Bundle>("/review-note", nonce,
    { date: jump.date || undefined, refresh: jump.refresh });
  const history = useProbe<{ rows: DaySummary[] }>("/review-notes", nonce, { limit: 90 });
  const data = bundle.kind === "ok" ? bundle.data : null;
  const meta = data?.fields ?? [];
  const defaults = data?.defaults;
  const note = data?.note ?? null;

  // 第一次由后端决定打开哪一天之后，把那个日子回填进日期框（用户没改过才回填）
  useEffect(() => {
    if (data?.date && date === "") setDate(data.date);
  }, [data?.date, date]);

  // 只在"换了这一天 / 重新取数 / 存完了"之后播种编辑框：
  // 不这么做的话，每次轮询回来都会把用户正在打的字冲掉。
  const seed = data ? `${data.date}|${defaults?.generated_at ?? ""}|${note?.updated_at ?? ""}` : "";
  const [seeded, setSeeded] = useState("");
  useEffect(() => {
    if (!data || !meta.length || seed === seeded) return;
    const next: Record<string, string> = {};
    const dr: Record<string, string> = {};
    for (const f of meta) {
      const saved = note?.values?.[f.key] ?? "";
      const dft = defaults?.fields?.[f.key]?.text ?? "";
      dr[f.key] = dft;
      // 已经有字的格子一律保留。**存过的日子重开时不再往空格里塞草稿**：
      // 空着可能是用户当天特意清掉的，重开又冒出来等于替他改记录（想填有点「填入草稿」那个按钮）。
      next[f.key] = saved || (data.saved ? "" : dft);
    }
    setValues(next); setDrafts(dr); setSeeded(seed);
  }, [data, meta, note, defaults, seed, seeded]);

  const marks = useMemo(() => {
    const out: Record<string, Provenance> = {};
    for (const f of meta) {
      out[f.key] = markOf({
        value: values[f.key] ?? "",
        draft: drafts[f.key] ?? "",
        baseline: note?.machine_baseline?.[f.key] ?? "",
      });
    }
    return out;
  }, [meta, values, drafts, note, data?.saved]);
  const tally = useMemo(() => tallyMarks(Object.values(marks)), [marks]);

  const groups = useMemo(() => {
    const out: { name: string; items: FieldMeta[] }[] = [];
    for (const f of meta) {
      const last = out[out.length - 1];
      if (last && last.name === f.group) last.items.push(f);
      else out.push({ name: f.group, items: [f] });
    }
    return out;
  }, [meta]);

  const goto = (d: string, refreshFlag: 0 | 1 = 0) => {
    if (!d) return;
    setJump({ date: d, refresh: refreshFlag });
    setMsg(refreshFlag ? "正在现算草稿（库里已有这一场时约 2~16 秒，取决于数据是否已入库）…" : "");
    refresh();
  };
  const shown = date || data?.date || "";

  async function save() {
    if (!data?.date) return;
    setSaving(true); setMsg("");
    // 只把"内容与草稿一字不差"的格留痕给后端；后端再只补还没有的键
    const baseline: Record<string, string> = {};
    for (const f of meta) {
      const d = (drafts[f.key] ?? "").trim();
      if (d && (values[f.key] ?? "").trim() === d) baseline[f.key] = drafts[f.key];
    }
    const r = await postJson<NoteRow>("/review-note", {
      date: data.date, values, machine_baseline: baseline,
      machine_generated_at: defaults?.generated_at ?? "",
    });
    setSaving(false);
    if (!r.ok) { setMsg(`没保存上：${r.reason}`); return; }
    const back = r.data?.trade_date === data.date ? "存完读回了同一天" : "⚠ 存完读回的行情日对不上，别继续写";
    setMsg(`已保存到 ys.review_note（${dash(r.data?.updated_at)}）· ${back}`);
    setSeeded("");   // 存完之后按库里读回的那一份重新播种，本地状态不冒充"已存"
    refresh();
  }

  const questions = meta.filter(f => f.question);

  return (
    <div className="space-y-4">
      <GlassCard>
        <div className="flex flex-wrap items-center gap-2">
          <label className="text-[12px] text-muted-foreground">行情日
            <input type="date" value={shown} onChange={e => setDate(e.target.value)}
              className="ml-2 rounded-lg border border-border bg-card px-2 py-1 text-[13px] text-foreground" />
          </label>
          <button type="button" onClick={() => goto(shown)}
            className="rounded-lg bg-primary/15 px-2.5 py-1.5 text-[12px] font-medium text-primary hover:bg-primary/25">
            打开这一天
          </button>
          {!!data?.expected_date && data.expected_date !== shown && (
            <button type="button" onClick={() => { setDate(data.expected_date!); goto(data.expected_date!); }}
              className="rounded-lg border border-border px-2.5 py-1.5 text-[12px] text-muted-foreground hover:border-primary hover:text-primary">
              跳到库里实际有数的最近一场（{data.expected_date}）
            </button>
          )}
          <button type="button" onClick={() => goto(shown, 1)}
            className="flex items-center gap-1 rounded-lg border border-border/60 px-2.5 py-1.5 text-[12px] text-muted-foreground hover:border-primary hover:text-primary">
            <RefreshCw className={cn("h-3.5 w-3.5", bundle.kind === "loading" && "animate-spin")} />
            重新取数
          </button>
        </div>
        <p className="mt-2 text-[11px] leading-relaxed text-muted-foreground">
          默认打开的是<b className="text-foreground">库里实际有数的那一场</b>，不是日历上的今天 ——
          数据没入库时写"今天"会得到一份没有事实可依的草稿。这一天不是交易日也可以写，界面会照样标出来。
        </p>
      </GlassCard>

      {history.kind === "ok" && history.data.rows.length > 0 && (
        <GlassCard>
          <p className="text-[11px] font-semibold text-foreground">
            写过笔记的日子（{history.data.rows.length} 份，点一个跳过去）
          </p>
          <div className="mt-1.5 flex flex-wrap gap-1.5">
            {history.data.rows.map(r => (
              <button key={r.trade_date} type="button" onClick={() => { setDate(r.trade_date); goto(r.trade_date); }}
                aria-pressed={r.trade_date === shown}
                title={`改于 ${dash(r.updated_at)}；${r.filled}/13 格有字，共 ${r.chars} 字`}
                className={cn("rounded-lg border px-2 py-1 text-[11px] tabular-nums",
                  r.trade_date === shown
                    ? "border-primary/70 bg-primary/10 text-primary"
                    : "border-border text-muted-foreground hover:bg-muted/50")}>
                {r.trade_date}<span className="ml-1 text-muted-foreground/70">{r.filled}/13·{r.chars}字</span>
              </button>
            ))}
          </div>
        </GlassCard>
      )}
      {bundle.kind === "loading" && (
        <p className="flex items-center gap-2 text-[12px] text-muted-foreground">
          <Loader2 className="h-3.5 w-3.5 animate-spin" /> 取这一天的笔记与草稿…（库里已有这一场时约 2~16 秒，取决于冷启动）
        </p>
      )}
      {bundle.kind !== "ok" && bundle.kind !== "loading" && (
        <GlassCard>
          <p className="text-[12px] text-warning">
            这一天的笔记没取到：{bundle.kind === "offline" ? "连不到后端进程" :
              bundle.kind === "not_wired" ? "这个接口在本项目里还没实现" : "取数报错"}。
            下面的格子先空着 —— <b className="text-foreground">不拿别的日子或示例内容顶</b>。
          </p>
        </GlassCard>
      )}

      {data?.available === false && (
        <GlassCard><p className="text-[12px] text-warning">库里现在没有任何已入库的行情日：{data.reason}</p></GlassCard>
      )}

      {data && meta.length > 0 && (
        <>
          <GlassCard>
            <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1 text-[12px]">
              <b className="text-sm">{dash(data.date)}</b>
              <span className={cn(data.saved ? "text-success" : "text-warning")}>
                {data.saved ? `已存过（改于 ${dash(note?.updated_at)}）` : "这一天还没存过"}
              </span>
              <span className="text-muted-foreground">
                13 格里：机器未改 {tally.machine_untouched} · 你已改 {tally.machine_edited} ·
                纯手写 {tally.human} · 空 {tally.empty + tally.empty_with_draft}
              </span>
              <span className="text-muted-foreground">
                交易日 {data.defaults?.is_trade_day === true ? "是" : data.defaults?.is_trade_day === false ? "不是" : "读不出"} ·
                已定稿 {data.defaults?.settled ? "是" : "否"}
              </span>
            </div>
            {defaults && (
              <div className="mt-1.5 text-[11px] leading-relaxed text-muted-foreground">
                {defaults.from_cache
                  ? <>草稿取自落盘缓存：<b className="text-foreground">这次没有重算</b>（原份算于 {dash(defaults.cache_written_at)}，当时用了 {dash(defaults.elapsed_ms)} ms）。
                      已定稿的日子才会落盘，所以第二次进来是瞬时的；要按最新入库的数据重算，点上面的「重新取数」。</>
                  : <>草稿是这一次现算的，用了 {dash(defaults.elapsed_ms)} ms（生成于 {dash(defaults.generated_at)}）。
                      已定稿的日子会落盘，下次进来是瞬时的；<b className="text-foreground">当天那份不落盘</b>，
                      因为把"数据还没进来"的样子存下来会骗到明天。</>}
                {defaults.network_free === false && <span className="text-warning"> ⚠ 这一份草稿走了联网取数，与本屏的约定不符。</span>}
                <span className="text-muted-foreground/70"> {dash(defaults.hint)}</span>
              </div>
            )}
            {!!defaults?.notes?.length && (
              <ul className="mt-1 text-[11px] text-muted-foreground">
                {defaults.notes.map((n, i) => <li key={i}>· {n}</li>)}
              </ul>
            )}
            {defaults && defaults.has_data === false && (
              <p className="mt-1 text-[11px] leading-relaxed text-warning">
                这一天库里还没有日线数据（库里实际有数的最近一场是 {dash(defaults.latest_data_date)}），
                所以凡是要用<b className="text-foreground">当天行情</b>的格子都会标成缺失 ——
                那是"还没入库"，不是"今天什么都没有"。草稿里出现的都是上一场已有的读数。
              </p>
            )}
            {questions.length > 0 && (
              <div className="mt-2 rounded-lg border border-border/60 bg-muted/20 p-2">
                <p className="text-[11px] font-semibold text-foreground">这一屏要问自己的七件事（列名由后端给出，点右侧跳到那一格）</p>
                <ul className="mt-1 grid gap-0.5 sm:grid-cols-2">
                  {questions.map(f => (
                    <li key={f.key} className="text-[11px] text-muted-foreground">
                      <a className="text-primary underline" href={`#note-${f.key}`}>{f.question}</a>
                      <span className="ml-1">→ {f.label}</span>
                    </li>
                  ))}
                </ul>
              </div>
            )}
          </GlassCard>

          {groups.map(g => (
            <section key={g.name} className="space-y-2">
              <h3 className="text-[11px] font-bold uppercase tracking-[0.2em] text-primary">{g.name}</h3>
              {g.items.map(f => {
                const value = values[f.key] ?? "";
                const draft = drafts[f.key] ?? "";
                const mark = marks[f.key] ?? "empty";
                const whyNoDefault = defaults?.empty?.[f.key] ?? "";
                const whyMissing = defaults?.missing?.[f.key] ?? "";
                const src = defaults?.fields?.[f.key]?.from ?? [];
                return (
                  <div key={f.key} id={`note-${f.key}`} className="glass rounded-2xl p-3">
                    <div className="flex flex-wrap items-baseline gap-x-2 gap-y-1">
                      <b className="text-[13px]">{f.label}</b>
                      <span className={cn("rounded px-1.5 py-0.5 text-[10px]",
                        mark === "machine_untouched" ? "bg-muted text-muted-foreground"
                          : mark === "machine_edited" ? "bg-primary/15 text-primary"
                            : mark === "human" ? "bg-success/15 text-success" : "border border-dashed border-border text-muted-foreground")}
                        title={`${MARK_HINT[mark]}${src.length ? `｜来源：${src.join("、")}` : ""}`}>
                        {MARK_LABEL[mark]}
                      </span>
                      <span className="text-[10px] text-muted-foreground/70">{value.length} 字</span>
                      {value.trim() === "" && !!draft && (
                        <button type="button" onClick={() => setValues(v => ({ ...v, [f.key]: draft }))}
                          className="rounded border border-border px-1.5 py-0.5 text-[10px] text-primary hover:bg-primary/10">
                          填入草稿
                        </button>
                      )}
                      {!!value.trim() && (
                        <button type="button" onClick={() => setValues(v => ({ ...v, [f.key]: "" }))}
                          className="rounded border border-border/60 px-1.5 py-0.5 text-[10px] text-muted-foreground hover:text-foreground">
                          清空这一格
                        </button>
                      )}
                    </div>
                    <textarea
                      value={value}
                      onChange={e => setValues(v => ({ ...v, [f.key]: e.target.value }))}
                      placeholder={f.question ? `问自己的：${f.question}` : "这一格机器给不出，只能你写"}
                      rows={value.length > 400 ? 14 : value.length > 120 ? 8 : 3}
                      className="mt-1.5 w-full resize-y rounded-lg border border-border bg-card px-2.5 py-2 text-[12px] leading-relaxed"
                    />
                    <p className="mt-1 text-[10px] leading-relaxed text-muted-foreground">
                      {!!f.note && <>这一格写什么：{f.note}。</>}
                      {!!whyNoDefault && <span className="text-muted-foreground/80"> 机器给不出：{whyNoDefault}。</span>}
                      {!!whyMissing && <span className="text-warning"> 这次没取到：{whyMissing}。</span>}
                      {!!src.length && <span className="text-muted-foreground/70"> 草稿来源：{src.join("、")}。</span>}
                    </p>
                  </div>
                );
              })}
            </section>
          ))}

          <GlassCard>
            <div className="flex flex-wrap items-center gap-2">
              <button type="button" onClick={() => void save()} disabled={saving}
                className="flex items-center gap-1.5 rounded-lg bg-primary px-4 py-2 text-[13px] font-semibold text-primary-foreground hover:opacity-90 disabled:opacity-50">
                {saving ? <Loader2 className="h-4 w-4 animate-spin" /> : <Save className="h-4 w-4" />}
                {data.saved ? "更新这一天的笔记" : "保存这一天"}
              </button>
              <span className="text-[11px] text-muted-foreground">
                存进 <code>ys.review_note</code>（一天一份，主键是行情日）。
                已经写过的格子不会被草稿盖掉，重算也只补空着的那几格。
              </span>
            </div>
            {!!msg && <p className="mt-2 text-[12px] text-muted-foreground">{msg}</p>}
            <p className="mt-2 border-t border-border/50 pt-2 text-[11px] leading-relaxed text-muted-foreground">
              这一屏的内容<b className="text-foreground">不接入任何 AI prompt</b>，也不发给任何外部服务；
              与交易日志同一条规矩（<code>duanxian/journal.py</code> 顶部写着的）。
              草稿里的题材串是来源归因、不是公司确认事实；涨跌与金额一律是<b className="text-foreground">已经发生</b>的读数。
              想"关注这只票、等它回调"请到「关注提醒」那一 tab 定一个交易日到期的提醒 ——
              这一屏只写你已经想过和已经发生的事，不放点位、不给参与倾向。
            </p>
          </GlassCard>
        </>
      )}
    </div>
  );
}
