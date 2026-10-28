import { useEffect, useMemo, useRef, useState } from "react";
import { ChevronDown, Loader2 } from "lucide-react";
import { agentFetch, plain } from "@/lib/agent";
import { useNonce, useProbe } from "@/lib/probe";
import { postJson } from "@/lib/jsonPost";
import { dash } from "@/lib/datastate";
import { StockLink } from "@/components/ui/StockLink";
import { cn } from "@/lib/utils";

/**
 * 选一只票，按用户 2026-09-28 定的**顺序**来：
 *   ① 重点关注（持仓）里现有的 → ② 自选分组里的自选股 → ③ 自己输入代码。
 * 默认打开的是**最近一次看过的那只**，存在 PG（`ys.ui_pref`，键由 `prefsKey` 给）——
 * 不再用 localStorage 当唯一副本：那玩意义务不清（换浏览器就回到 000001，
 * 而"回到默认"在这种页面上等于悄悄换了标的）。
 *
 * 三条实现约束：
 *  · 候选项一律来自接口，页面不自己造代码、不写死 000001；
 *  · 库里的最近一次与本地那次不一致时**以库里为准**，并说出来，不静默覆盖；
 *  · 一次选中就写库；写失败照常在页面上讲（不然下次打开又是别一只）。
 */
interface Prefs { rows: Record<string, { value: string; updated_at: string | null; label: string }>; registered?: unknown[] }
interface Positions { available?: boolean; holdings?: { code?: string; name?: string }[]; note?: string | null }
interface WatchRow { security_code?: string; name?: string; group_name?: string; group_id?: number | null }
interface Watch { available?: boolean; reason?: string | null; rows?: WatchRow[] | null; count?: number | null; date?: string | null }

const LOCAL_KEY = (k: string) => `ys-last-${k}`;

export function StockPicker({ prefsKey, code, onPick }: {
  prefsKey: string; code: string; onPick: (v: string) => void;
}) {
  const [nonce, refresh] = useNonce();
  const prefs = useProbe<Prefs>("/ui-prefs", nonce);
  const watch = useProbe<Watch>("/watchlist/stocks", nonce, { limit: 2000 });
  const [pos, setPos] = useState<Positions | null>(null);
  const [posErr, setPosErr] = useState("");
  const [draft, setDraft] = useState("");
  const [openGroup, setOpenGroup] = useState<string>("");
  const [justSaved, setJustSaved] = useState<{ value: string; updated_at: string } | null>(null);
  const [saveErr, setSaveErr] = useState("");

  useEffect(() => {
    let live = true;
    agentFetch<Positions>("/api/positions")
      .then(d => { if (live) { setPos(d); setPosErr(""); } })
      .catch(e => { if (live) setPosErr(e instanceof Error ? e.message : "持仓读取失败"); });
    return () => { live = false; };
  }, [nonce]);

  // 开局顺序：库里那次 → 本地缓存（同一台机器更快的路）→ 持仓第一只 → 自选第一只 → 空
  const seeded = useSeedOnce();
  useEffect(() => {
    if (code || seeded.current.done) return;
    const fromDb = prefs.kind === "ok" ? (prefs.data.rows?.[prefsKey]?.value ?? "") : "";
    if (prefs.kind === "loading") return;
    let local = "";
    try { local = localStorage.getItem(LOCAL_KEY(prefsKey)) || ""; } catch { /* 隐私模式 */ }
    if (fromDb) { seeded.current.done = true; onPick(fromDb); return; }
    if (local) { seeded.current.done = true; onPick(local); return; }
    const first = (pos?.holdings ?? []).find(h => h.code)?.code
      || (watch.kind === "ok" ? (watch.data.rows ?? []).find(r => r.security_code)?.security_code : "")
      || "";
    if (first) { seeded.current.done = true; onPick(first); }
  }, [code, prefs, pos, watch, prefsKey, onPick, seeded]);

  const holdings = useMemo(() => dedup((pos?.holdings ?? [])
    .filter(h => h.code).map(h => ({ code: String(h.code), name: h.name || String(h.code) }))), [pos]);
  const byGroup = useMemo(() => {
    const out = new Map<string, { code: string; name: string }[]>();
    if (watch.kind !== "ok") return out;
    for (const r of watch.data.rows ?? []) {
      const c = String(r.security_code ?? "").slice(0, 6);
      if (!c) continue;
      const g = r.group_name || "（未命名组）";
      const list = out.get(g) ?? [];
      if (!list.some(x => x.code === c)) list.push({ code: c, name: r.name || c });
      out.set(g, list);
    }
    return new Map([...out.entries()].sort((a, b) => b[1].length - a[1].length));
  }, [watch]);

  async function pick(v: string, source: string) {
    const clean = v.replace(/\D/g, "").slice(0, 6);
    if (!/^\d{6}$/.test(clean)) { setSaveErr(`代码要 6 位数字，收到「${v}」`); return; }
    setSaveErr(""); onPick(clean);
    try { localStorage.setItem(LOCAL_KEY(prefsKey), clean); } catch { /* 存不住不影响 */ }
    const r = await postJson<{ value?: string; updated_at?: string | null }>("/ui-pref",
      { key: prefsKey, value: clean, note: source });
    // 只认接口读回来的那一份：写了但没读回，就不能说"已记住"
    if (!r.ok || !r.data?.value) {
      setSaveErr(`这次没记住（下次打开会回到上一次那份）：${r.ok ? "后端没读回这一行" : r.reason}`);
      return;
    }
    setJustSaved({ value: r.data.value, updated_at: r.data.updated_at ?? "" });
  }

  const lastRow = prefs.kind === "ok" ? prefs.data.rows?.[prefsKey] : undefined;
  // 刚存完先按刚存的那一份显示（不为这一句再发一次全量重取）
  const shown = (justSaved && justSaved.value !== lastRow?.value)
    ? { value: justSaved.value, updated_at: justSaved.updated_at, label: lastRow?.label ?? "" }
    : lastRow;

  return (
    <div className="glass rounded-2xl p-3">
      <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1 text-[11px]">
        <b className="text-[12px]">选一只票</b>
        <span className="text-muted-foreground">
          顺序：重点关注（持仓）→ 自选分组里的自选股 → 自己输入
        </span>
        <span className="text-muted-foreground">
          最近一次：{shown?.value
            ? <>
                <StockLink code={shown.value} className="font-mono text-primary underline" />
                {/* 值为空时不报"存于"：那一行是"没记过"，时间是历史残留（09-29 实测自己踩过：
                    屏幕上同出一句里既写"库里还没记过"又写"存于 00:08"，读的人会以为两件事）。 */}
                {!!shown.updated_at && ` · 存于 ${dash(shown.updated_at)}`}
              </>
            : "库里还没记过（第一次选完就会记住）"}
        </span>
        {prefs.kind === "loading" && <Loader2 className="h-3 w-3 animate-spin text-muted-foreground" />}
        <button type="button" onClick={refresh} className="ml-auto text-muted-foreground underline hover:text-foreground">
          重取候选
        </button>
      </div>

      {/* ① 持仓 */}
      <div className="mt-2">
        <p className="text-[11px] font-semibold text-muted-foreground">① 重点关注（持仓）</p>
        {posErr && <p className="text-[11px] text-warning">持仓没读到：{posErr} —— 下面这一档空着不代表你没仓。</p>}
        {!posErr && holdings.length === 0 && (
          <p className="text-[11px] text-muted-foreground">{plain(pos?.note) || "当前没有未平仓持仓。"}</p>
        )}
        <div className="mt-1 flex flex-wrap gap-1.5">
          {holdings.map(h => (
            <PickChip key={h.code} item={h} active={h.code === code} onPick={() => void pick(h.code, "持仓")} />
          ))}
        </div>
      </div>

      {/* ② 自选分组 */}
      <div className="mt-2">
        <p className="text-[11px] font-semibold text-muted-foreground">
          ② 自选分组里的自选股
          {watch.kind === "ok" && `（${byGroup.size} 组，行情日 ${dash(watch.data.date)}）`}
          {watch.kind === "loading" && " · 取数中…"}
        </p>
        {watch.kind !== "loading" && watch.kind !== "ok" && (
          <p className="text-[11px] text-warning">自选明细没读到（{watch.kind}）—— 这一档先空着，不拿别的日子顶。</p>
        )}
        <div className="mt-1 space-y-1">
          {[...byGroup.entries()].map(([g, items]) => (
            <div key={g}>
              <button type="button" onClick={() => setOpenGroup(openGroup === g ? "" : g)} aria-expanded={openGroup === g}
                className="flex items-center gap-1 text-[11px] text-muted-foreground hover:text-foreground">
                <ChevronDown className={cn("h-3 w-3 transition-transform", openGroup === g ? "" : "-rotate-90")} />
                {g} <span className="text-muted-foreground/70">{items.length} 只</span>
              </button>
              {openGroup === g && (
                <div className="mt-1 flex flex-wrap gap-1.5 pl-4">
                  {items.map(it => <PickChip key={it.code} item={it} active={it.code === code} onPick={() => void pick(it.code, `自选组 ${g}`)} />)}
                </div>
              )}
            </div>
          ))}
        </div>
      </div>

      {/* ③ 手输 */}
      <div className="mt-2 flex flex-wrap items-center gap-2">
        <p className="text-[11px] font-semibold text-muted-foreground">③ 自己输入代码</p>
        <input value={draft} onChange={e => setDraft(e.target.value)}
          aria-label="股票代码" placeholder="6 位代码"
          onKeyDown={e => { if (e.key === "Enter") void pick(draft, "手输"); }}
          className="w-28 rounded border border-border bg-background px-2 py-1 font-mono text-[12px]" />
        <button type="button" onClick={() => void pick(draft, "手输")}
          className="rounded-lg border border-primary/50 bg-primary/10 px-2.5 py-1 text-[12px] text-primary hover:bg-primary/20">
          看这一只
        </button>
        {!!saveErr && <span className="text-[11px] text-warning">{saveErr}</span>}
      </div>
    </div>
  );
}

function PickChip({ item, active, onPick }: { item: { code: string; name: string }; active: boolean; onPick: () => void }) {
  return (
    <button type="button" onClick={onPick} aria-pressed={active}
      title={`${item.code} ${item.name}`}
      className={cn("rounded-lg border px-2 py-0.5 text-[11px]",
        active ? "border-primary/70 bg-primary/15 text-primary" : "border-border text-muted-foreground hover:bg-muted/50")}>
      <span className="max-w-[7rem] truncate">{item.name}</span>
      <span className="ml-1 font-mono text-[10px] opacity-70">{item.code}</span>
    </button>
  );
}

function dedup(rows: { code: string; name: string }[]) {
  const seen = new Set<string>();
  return rows.filter(r => (seen.has(r.code) ? false : (seen.add(r.code), true)));
}

/** 只播种一次：不然用户手动清空代码之后，会被"恢复上次"又填回去。 */
function useSeedOnce() {
  const ref = useRef({ done: false });
  return ref;
}
