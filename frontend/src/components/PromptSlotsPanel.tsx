import { useEffect, useRef, useState } from "react";
import { GlassCard } from "@/components/ui/GlassCard";
import { ageText } from "@/lib/datastate";
import {
  deleteTemplate, loadPrompts, resetPromptSlot, savePromptSlot, saveTemplate,
  type PromptScope, type PromptTemplate,
} from "@/lib/prompts";
import { cn } from "@/lib/utils";
import { Plus, RotateCcw, Save, Sparkles, Trash2 } from "lucide-react";

/** PG 交的是 `2026-09-28 10:54:05+08:00` 这种带空格的串，浏览器不一定认；换 T 分隔再算年龄。 */
function isoAge(iso: string | null): string {
  if (!iso) return "";
  const at = Date.parse(iso.replace(" ", "T"));
  return Number.isNaN(at) ? "" : ageText((Date.now() - at) / 1000);
}

/**
 * 系统配置 →「AI 提示词」：页面上那些「一键运行」的话术，在这里加类型、写话术、当模板选。
 *
 * 两类东西放在一起管，但存法不同，界面上必须分清（不然"恢复默认"会显得像能删掉用户模板）：
 *  · 内置槽位 —— 代码注册的取数点，库里只能覆盖它的话术，「恢复默认」= 删掉这条覆盖；
 *  · 自建模板 —— 用户新建的，选一个**类型**决定能拿哪些字段，删除就是真删除。
 * 页面那边（连板股/首板的「深入分析」）下拉里两类并列，选中的记在浏览器本地。
 */
export function PromptSlotsPanel() {
  const [slots, setSlots] = useState<PromptTemplate[]>([]);
  const [scopes, setScopes] = useState<PromptScope[]>([]);
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState("");
  const [notice, setNotice] = useState<Record<string, string>>({});
  // 表单：新建或编辑一份自建模板（editingId 为空 = 新建）
  const [editingId, setEditingId] = useState("");
  const [formTitle, setFormTitle] = useState("");
  const [formScope, setFormScope] = useState("");
  const [formText, setFormText] = useState("");
  // 内置槽位的草稿
  const [draft, setDraft] = useState<Record<string, string>>({});
  const boxRef = useRef<HTMLTextAreaElement | null>(null);

  const reload = () => loadPrompts(true)
    .then(p => {
      setSlots(p.slots); setScopes(p.scopes); setErr("");
      if (!formScope && p.scopes.length) setFormScope(p.scopes[0].id);
      setDraft(d => {
        const next = { ...d };
        for (const s of p.slots) if (s.kind === "builtin" && !(s.id in next)) next[s.id] = s.template;
        return next;
      });
    })
    .catch(e => setErr(e instanceof Error ? e.message : "提示词读取失败"));
  useEffect(() => { void reload(); }, []);       // eslint-disable-line react-hooks/exhaustive-deps

  const formFields = scopes.find(s => s.id === formScope)?.fields ?? {};
  const builtins = slots.filter(s => s.kind === "builtin");
  const customs = slots.filter(s => s.kind === "custom");
  const unknownIn = (text: string, fields: Record<string, string>) => {
    const found = text.match(/\{([a-z][a-z0-9_]*)\}/g) ?? [];
    return [...new Set(found.map(f => f.slice(1, -1)).filter(k => !(k in fields)))];
  };
  const formUnknown = unknownIn(formText, formFields);

  const insert = (field: string) => {
    const token = `{${field}}`;
    setFormText(cur => {
      const box = boxRef.current;
      if (!box) return cur + token;
      const at = box.selectionStart ?? cur.length;
      return cur.slice(0, at) + token + cur.slice(box.selectionEnd ?? at);
    });
    requestAnimationFrame(() => boxRef.current?.focus());
  };

  const run = async (key: string, fn: () => Promise<unknown>, done: string) => {
    setBusy(key); setNotice(n => ({ ...n, [key]: "" }));
    try {
      await fn();
      await reload();
      setNotice(n => ({ ...n, [key]: done }));
    } catch (e) {
      setNotice(n => ({ ...n, [key]: `未保存：${e instanceof Error ? e.message : "接口无响应"}` }));
    } finally { setBusy(""); }
  };

  return (
    <GlassCard>
      <div className="flex flex-wrap items-center gap-2">
        <h3 className="flex items-center gap-1.5 text-sm font-semibold"><Sparkles className="h-4 w-4" /> AI 提示词（页面上那些「一键运行」的话术）</h3>
        <span className="text-[11px] text-muted-foreground">{builtins.length} 个内置槽位 · {customs.length} 份自建模板 · {scopes.length} 种类型</span>
        {err && <span role="alert" className="text-[11px] text-danger">{err}</span>}
      </div>
      <p className="mt-1 text-[11px] leading-5 text-muted-foreground">
        改的是话术，数据仍由页面带入。占位符只能用所选类型列出的那些 —— 写错的名字保存时会当场拒掉。
        <span className="text-foreground">唯一不能整段删掉的</span>是"不给交易动作/点位、不构成投资建议"这类约束句
        （<span className="text-foreground">按意思拦、不按措辞拦</span>：换写法可以，一句约束都不留会被拒）；其余随便改。
        服务端本来也在这档拦交易动作与点位，这条只是不让对外承诺变成配置页上一个随手可关的开关。
        要能自由给价位的通道是右侧「AI分析」的按需研究档，不是这里。
      </p>

      {/* ───────────── 新建 / 编辑自建模板 ───────────── */}
      <div className="mt-3 rounded-lg border border-primary/40 bg-primary/5 p-3">
        <div className="flex flex-wrap items-center gap-2">
          <p className="text-[13px] font-medium">{editingId ? "编辑这份自建模板" : "新建一份模板"}</p>
          {editingId && (
            <button type="button" className="text-[11px] text-muted-foreground hover:text-primary"
              onClick={() => { setEditingId(""); setFormTitle(""); setFormText(""); }}>取消编辑</button>
          )}
        </div>
        <div className="mt-2 flex flex-wrap items-center gap-2">
          <label className="text-[11px] text-muted-foreground">名字
            <input value={formTitle} onChange={e => setFormTitle(e.target.value)} placeholder="如：只看梯队结构"
              className="ml-1 w-56 rounded-md border border-border/60 bg-background/60 px-2 py-1 text-[12px] outline-none focus:border-primary" />
          </label>
          <label className="text-[11px] text-muted-foreground">类型（决定能拿到哪些数据）
            <select value={formScope} onChange={e => setFormScope(e.target.value)}
              className="ml-1 rounded-md border border-border/60 bg-background/60 px-2 py-1 text-[12px]">
              {scopes.map(s => <option key={s.id} value={s.id}>{s.label} · {s.page}</option>)}
            </select>
          </label>
        </div>
        <textarea ref={boxRef} value={formText} onChange={e => setFormText(e.target.value)} rows={6}
          aria-label="模板话术" placeholder="用下面的字段拼一句话，例如：{date} 的 {name}（{code}）已经 {boards} 板……"
          className="mt-2 w-full rounded-md border border-border/60 bg-background/60 p-2 font-mono text-[11px] leading-5 outline-none focus:border-primary" />
        <div className="mt-1 flex flex-wrap items-start gap-1.5">
          <span className="text-[11px] text-muted-foreground">可插入的数据：</span>
          {Object.entries(formFields).map(([k, label]) => (
            <button key={k} type="button" onClick={() => insert(k)} title={label}
              className="rounded border border-border/60 px-1.5 py-0.5 font-mono text-[10px] hover:border-primary hover:text-primary">{`{${k}}`}</button>
          ))}
        </div>
        {formUnknown.length > 0 && (
          <p className="mt-1 text-[11px] text-danger">这些占位符该类型没有，保存会被拒：{formUnknown.map(k => `{${k}}`).join("、")}</p>
        )}
        <div className="mt-2 flex flex-wrap items-center gap-2">
          <button type="button" disabled={busy !== "" || !formTitle.trim() || !formText.trim() || formUnknown.length > 0}
            onClick={() => void run("form", () => saveTemplate({ title: formTitle, scope: formScope, template: formText, id: editingId }),
              editingId ? "已更新，页面下拉里立刻可选" : "已保存，去页面上拉「模板」就能选到它")}
            className="inline-flex items-center gap-1 rounded-md border border-primary/50 bg-primary/10 px-2.5 py-1 text-[11px] font-medium text-primary disabled:opacity-40">
            <Save className="h-3 w-3" /> {busy === "form" ? "保存中…" : editingId ? "保存修改" : "存为模板"}
          </button>
          {notice["form"] && <span className="text-[11px] text-muted-foreground">{notice["form"]}</span>}
        </div>
      </div>

      {/* ───────────── 自建模板清单 ───────────── */}
      <div className="mt-4">
        <p className="text-[12px] font-semibold">我建的模板（{customs.length}）</p>
        {customs.length === 0 && <p className="mt-1 text-[11px] text-muted-foreground">还没有自建模板 —— 上面新建一份，页面上那排「深入分析」就会出现可选的下拉。</p>}
        <div className="mt-2 grid gap-2">
          {customs.map(t => (
            <div key={t.id} className="rounded-lg border border-border/50 px-3 py-2">
              <div className="flex flex-wrap items-baseline gap-2">
                <span className="text-[12px] font-medium">{t.title}</span>
                <span className="text-[11px] text-muted-foreground">{t.scope_label}</span>
                <span className="text-[10px] text-muted-foreground">存于 {t.updated_at}{isoAge(t.updated_at) ? `（${isoAge(t.updated_at)}前）` : ""}</span>
                <button type="button" className="ml-auto inline-flex items-center gap-1 text-[11px] text-muted-foreground hover:text-primary"
                  onClick={() => { setEditingId(t.id); setFormTitle(t.title); setFormScope(t.scope); setFormText(t.template); }}>
                  编辑</button>
                <button type="button" disabled={busy !== ""}
                  onClick={() => void run(`del:${t.id}`, () => deleteTemplate(t.id), "已删除")}
                  className="inline-flex items-center gap-1 text-[11px] text-muted-foreground hover:text-danger disabled:opacity-40">
                  <Trash2 className="h-3 w-3" /> 删除</button>
              </div>
              {t.notes && <p className="mt-0.5 text-[11px] text-warning">{t.notes}</p>}
              <p className="mt-1 line-clamp-2 whitespace-pre-wrap text-[11px] text-muted-foreground">{t.template}</p>
              {notice[`del:${t.id}`] && <p className="mt-1 text-[11px] text-muted-foreground">{notice[`del:${t.id}`]}</p>}
            </div>
          ))}
        </div>
      </div>

      {/* ───────────── 内置槽位（可覆盖话术 / 恢复默认） ───────────── */}
      <div className="mt-4 grid gap-4">
        {builtins.length === 0 && !err && <p className="text-[11px] text-muted-foreground">提示词清单读取中…</p>}
        {builtins.map(s => {
          const dirty = (draft[s.id] ?? "") !== s.template;
          const bad = unknownIn(draft[s.id] ?? "", s.fields);
          return (
            <div key={s.id} className="rounded-lg border border-border/50 p-3">
              <div className="flex flex-wrap items-baseline gap-2">
                <p className="text-[13px] font-medium">{s.title}</p>
                <span className="font-mono text-[10px] text-muted-foreground">{s.id}</span>
                <span className="text-[11px] text-muted-foreground">用在：{s.page}</span>
                <span className={cn("rounded px-1.5 py-0.5 text-[10px]", s.edited ? "bg-primary/15 text-primary" : "bg-muted text-muted-foreground")}>
                  当前生效：{s.source}
                </span>
                {s.updated_at && <span className="text-[10px] text-muted-foreground">存于 {s.updated_at}（{isoAge(s.updated_at)}前）</span>}
              </div>
              {s.notes && <p className="mt-1 text-[11px] text-muted-foreground">{s.notes}</p>}
              <textarea value={draft[s.id] ?? ""} onChange={e => setDraft(d => ({ ...d, [s.id]: e.target.value }))}
                rows={7} aria-label={`${s.title} 话术`}
                className="mt-2 w-full rounded-md border border-border/60 bg-background/60 p-2 font-mono text-[11px] leading-5 outline-none focus:border-primary" />
              <div className="mt-2 flex flex-wrap items-start gap-1.5">
                <span className="text-[11px] text-muted-foreground">可插入的数据：</span>
                {Object.entries(s.fields).map(([k, label]) => (
                  <button key={k} type="button" title={label}
                    onClick={() => setDraft(d => ({ ...d, [s.id]: `${d[s.id] ?? ""}{${k}}` }))}
                    className="rounded border border-border/60 px-1.5 py-0.5 font-mono text-[10px] hover:border-primary hover:text-primary">{`{${k}}`}</button>
                ))}
              </div>
              {bad.length > 0 && <p className="mt-1 text-[11px] text-danger">保存会被拒：{bad.map(k => `{${k}}`).join("、")} 这个类型没有对应数据</p>}
              <div className="mt-2 flex flex-wrap items-center gap-2">
                <button type="button" disabled={busy !== "" || !dirty || bad.length > 0}
                  onClick={() => void run(s.id, () => savePromptSlot(s.id, draft[s.id] ?? ""), "已保存，现在生效的是库里那份")}
                  className="inline-flex items-center gap-1 rounded-md border border-primary/50 bg-primary/10 px-2.5 py-1 text-[11px] font-medium text-primary disabled:opacity-40">
                  <Save className="h-3 w-3" /> {busy === s.id ? "保存中…" : "保存这份话术"}
                </button>
                <button type="button" disabled={busy !== "" || !s.edited}
                  onClick={() => void run(`reset:${s.id}`, () => resetPromptSlot(s.id), "已恢复内置默认（库里那条覆盖已删除）")}
                  className="inline-flex items-center gap-1 rounded-md border border-border/60 px-2.5 py-1 text-[11px] disabled:opacity-40">
                  <RotateCcw className="h-3 w-3" /> 恢复默认
                </button>
                <button type="button" disabled={busy !== ""}
                  onClick={() => { setEditingId(""); setFormTitle(`${s.title} 改一版`); setFormScope(s.scope); setFormText(draft[s.id] ?? s.template); }}
                  className="inline-flex items-center gap-1 rounded-md border border-border/60 px-2.5 py-1 text-[11px] disabled:opacity-40">
                  <Plus className="h-3 w-3" /> 拿它当底子另建一份
                </button>
                {dirty && <span className="text-[11px] text-warning">有未保存的修改</span>}
                {!dirty && !s.edited && <span className="text-[11px] text-muted-foreground">与内置默认一致</span>}
                {notice[s.id] && <span className="text-[11px] text-muted-foreground">{notice[s.id]}</span>}
                {notice[`reset:${s.id}`] && <span className="text-[11px] text-muted-foreground">{notice[`reset:${s.id}`]}</span>}
              </div>
            </div>
          );
        })}
      </div>
    </GlassCard>
  );
}
