import { useCallback, useEffect, useState } from "react";
import { GlassCard } from "@/components/ui/GlassCard";
import { ExternalLink, RotateCcw, Save } from "lucide-react";
import { loadLinks, saveLink, type LinkRoute, type LinksState } from "@/lib/quoteLinkTemplates";
import { cn } from "@/lib/utils";
import { plain } from "@/lib/agent";

const DASH = "—";

/**
 * 系统配置 →「行情外链地址（按市场）」。
 *
 * 用户 2026-10-08 要的就是这张表：「除了代码以外的网址写在哪里，代码用 ****** 在网址里替换」，
 * 并点名北证要 `https://quote.eastmoney.com/bj/******.html`。三件事必须同时成立：
 *  · 默认值逐字等于代码里那份出厂表 ⇒ 不动它，全站链接与今天完全一致（真值表钉着）；
 *  · 非法值**整笔拒**并把原因原样带出来 ⇒ 存进去一个拼不出代码的串等于全站死链；
 *  · 每行给一个**真的按当前模板拼出来的样例地址**，点一下就看得见改对没有，不用猜。
 *
 * 这一格改的是"去哪儿看行情"，不改任何数据口径 —— 页面里那句说明就是为了让下一个人不纠结。
 */
export function QuoteLinksPanel() {
  const [s, setS] = useState<LinksState | null>(null);
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState("");
  const [notice, setNotice] = useState("");
  const [draft, setDraft] = useState<Record<string, string>>({});

  const reload = useCallback(() => loadLinks(true)
    .then(r => { setS(r); setErr(r ? "" : "外链配置读不到（接口没回 data）—— 链接仍按出厂地址拼"); })
    .catch(e => setErr(e instanceof Error ? e.message : "外链配置读不到")), []);
  useEffect(() => { void reload(); }, [reload]);

  const save = async (row: LinkRoute, value: string) => {
    setBusy(row.id); setNotice("");
    try {
      setNotice(await saveLink(row.id, value));
      const next = await loadLinks(true);
      if (next) setS(next);
    } catch (e) {
      // 后端拒绝要原样带出（"这一笔整条没写入，仍按原值运行"），不许折叠成"保存失败"
      setNotice(e instanceof Error ? e.message : "后端没有采纳这次修改");
    } finally { setBusy(""); }
  };

  return (
    <GlassCard>
      <div className="flex flex-wrap items-center gap-2">
        <h2 className="text-sm font-semibold">行情外链地址（按市场）</h2>
        <span className="rounded-md bg-muted px-1.5 py-0.5 font-mono text-[11px] text-muted-foreground">
          {s?.placeholder ?? "******"} = 股票代码
        </span>
        <button type="button" onClick={() => void reload()} disabled={busy !== ""}
          className="ml-auto rounded-lg border border-border px-2 py-1 text-[11px] text-muted-foreground hover:border-primary/40 hover:text-primary disabled:opacity-60">
          重新读取
        </button>
      </div>

      {err && <p className="mt-2 text-[11px] text-destructive">{err}</p>}
      {notice && <p className="mt-2 text-[11px] text-warning">{plain(notice)}</p>}

      <p className="mt-1.5 text-[11px] leading-relaxed text-muted-foreground">
        {plain(s?.explain ?? "") || "改一处全站生效（股票名、榜单、深链里的外链都读这一张表），不用重启。"}
        只改"去哪儿看行情"，不改任何数据口径。地址里要<b className="font-medium">恰好一个</b>{" "}
        <code>{s?.placeholder ?? "******"}</code> 作代码占位，且必须是 https 绝对地址 ——
        不合格的会被拒绝并说明原因，仍按原值运行。
      </p>

      {!!s?.warnings?.length && (
        <p className="mt-1 text-[11px] text-warning">⚠ {s.warnings.map(plain).join("；")}</p>
      )}

      <div className="mt-2 grid gap-1.5">
        {(s?.routes ?? []).map(row => {
          const value = draft[row.id] ?? row.template;
          const dirty = value.trim() !== row.template;
          return (
            <div key={row.id} className={cn("rounded-lg border p-2",
              row.bad ? "border-destructive/50 bg-destructive/5" : "border-border/60")}>
              <div className="flex flex-wrap items-baseline gap-2">
                <span className="text-xs font-medium">{row.label}</span>
                <code className="text-[10px] text-muted-foreground">{row.id}</code>
                {row.changed && <span className="rounded bg-primary/15 px-1 py-0.5 text-[10px] text-primary">已改</span>}
                {row.bad && <span className="rounded bg-destructive/15 px-1 py-0.5 text-[10px] text-destructive">
                  这条不合格，暂按出厂值拼</span>}
              </div>
              <div className="mt-1 flex flex-wrap items-center gap-1.5">
                <input value={value} disabled={busy !== ""} aria-label={`${row.id} 的地址模板`}
                  onChange={e => setDraft(d => ({ ...d, [row.id]: e.target.value }))}
                  className="min-w-0 flex-1 rounded-lg border border-border bg-background px-2 py-1 font-mono text-[11px]" />
                <button type="button" disabled={busy !== "" || !dirty || !value.trim()}
                  onClick={() => void save(row, value.trim())}
                  className="flex items-center gap-1 rounded-lg bg-primary/15 px-2 py-1 text-[11px] font-medium text-primary hover:bg-primary/25 disabled:opacity-60">
                  <Save className="h-3 w-3" />保存
                </button>
                <button type="button" disabled={busy !== "" || row.template === row.default}
                  onClick={() => { setDraft(d => ({ ...d, [row.id]: row.default })); void save(row, row.default); }}
                  className="flex items-center gap-1 rounded-lg border border-border px-2 py-1 text-[11px] text-muted-foreground hover:border-primary/40 hover:text-primary disabled:opacity-60">
                  <RotateCcw className="h-3 w-3" />恢复出厂
                </button>
              </div>
              <p className="mt-1 text-[11px] text-muted-foreground">
                样例（{row.sample_code}）：
                {row.sample_url
                  ? <a href={row.sample_url} target="_blank" rel="noopener noreferrer"
                    className="ml-1 inline-flex items-center gap-0.5 font-mono text-[11px] text-primary hover:underline">
                    {row.sample_url}<ExternalLink className="h-3 w-3" />
                  </a>
                  : <span className="ml-1">这一行现在拼不出地址（检查占位符）</span>}
                {row.bad ? `　⚠ ${plain(row.bad)}` : ""}
              </p>
            </div>
          );
        })}
        {s && !s.routes?.length && (
          <p className="text-[11px] text-muted-foreground">后端没有回任何一行 —— 这是配置表读不到，不是"没有市场可配"。</p>
        )}
      </div>

      {s && (s.changed_at || s.changed_by) && (
        <p className="mt-1.5 text-[11px] text-muted-foreground">
          这张表最后一次改动：{s.changed_at ?? DASH}{s.changed_by ? ` 由 ${s.changed_by}` : ""}
        </p>
      )}
      {s?.note && <p className="mt-1 text-[11px] text-muted-foreground">理由：{s.note}</p>}
    </GlassCard>
  );
}
