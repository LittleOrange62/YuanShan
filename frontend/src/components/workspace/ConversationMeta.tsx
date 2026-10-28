import { useEffect, useState } from 'react';
import { Tag, X } from 'lucide-react';
import { agentRequest } from '@/lib/agent-api';

type Conversation = { id: string; title: string; category?: string; tags?: string[] };
type Facets = { categories: { name: string; count: number }[]; tags: { name: string; count: number }[]; total: number };

/** 会话档案：改名、分类、标签都直接落在服务端，换浏览器也还在。 */
export function ConversationMeta({ conversation, onSaved, onError, disabled }:
  { conversation: Conversation; onSaved: () => void; onError: (message: string) => void; disabled: boolean }) {
  const [facets, setFacets] = useState<Facets>({ categories: [], tags: [], total: 0 });
  const [title, setTitle] = useState(conversation.title);
  const [category, setCategory] = useState(conversation.category || '');
  const [tags, setTags] = useState<string[]>(conversation.tags || []);
  const [newTag, setNewTag] = useState('');
  const [busy, setBusy] = useState(false);
  const [revision, setRevision] = useState(0);
  useEffect(() => { setTitle(conversation.title); setCategory(conversation.category || ''); setTags(conversation.tags || []); }, [conversation.id, conversation.title, conversation.category]);
  useEffect(() => {
    const abort = new AbortController();
    void agentRequest<Facets>('/conversation-facets', undefined, abort.signal).then(setFacets).catch(() => setFacets({ categories: [], tags: [], total: 0 }));
    return () => abort.abort();
  }, [conversation.id, revision]);
  async function post(path: string, body: Record<string, unknown>) {
    setBusy(true);
    try { await agentRequest<{ ok: boolean }>(`/conversations/${conversation.id}/${path}`, body); onSaved(); setRevision(n => n + 1); }
    catch (e) { onError(e instanceof Error ? e.message : '会话信息未能保存'); }
    finally { setBusy(false); }
  }
  function addTag() {
    const value = newTag.trim().replace(/^#/, '');
    if (!value || tags.includes(value) || tags.length >= 8) return;
    setTags([...tags, value]); setNewTag('');
  }
  const dirty = title.trim() !== conversation.title || category !== (conversation.category || '') || tags.join() !== (conversation.tags || []).join();
  return <form onSubmit={e => { e.preventDefault(); void post('rename', { title }); }} className="grid gap-2 rounded-lg border border-border bg-muted/20 p-3 text-xs">
    <div className="flex flex-wrap items-center gap-2">
      <label className="min-w-0 flex-1 basis-48">会话名称
        <input aria-label="会话名称" value={title} disabled={disabled || busy} maxLength={60} onChange={e => setTitle(e.target.value)}
          className="mt-1 w-full rounded border border-border bg-background p-2" /></label>
      <label className="min-w-0 basis-40">分类
        <input aria-label="会话分类" list="conversation-categories" value={category} disabled={disabled || busy} maxLength={20}
          onChange={e => setCategory(e.target.value)} className="mt-1 w-full rounded border border-border bg-background p-2" />
        <datalist id="conversation-categories">{facets.categories.map(c => <option key={c.name} value={c.name}>{c.count} 条</option>)}</datalist></label>
    </div>
    <div className="flex flex-wrap items-center gap-2">
      <span className="inline-flex items-center gap-1 text-muted-foreground"><Tag size={13} />标签</span>
      {tags.map(tag => <span key={tag} className="inline-flex items-center gap-1 rounded-full border border-border bg-background px-2 py-1">
        {tag}<button type="button" aria-label={`移除标签 ${tag}`} disabled={disabled || busy} onClick={() => setTags(tags.filter(t => t !== tag))} className="text-muted-foreground hover:text-danger"><X size={11} /></button></span>)}
      <input aria-label="新增标签" value={newTag} disabled={disabled || busy || tags.length >= 8} maxLength={16} placeholder={tags.length >= 8 ? '标签已达八个' : '回车添加'}
        onChange={e => setNewTag(e.target.value)} onKeyDown={e => { if (e.key === 'Enter') { e.preventDefault(); addTag(); } }}
        className="min-w-24 flex-1 rounded border border-border bg-background p-1.5" />
    </div>
    {!!facets.tags.length && <div className="flex flex-wrap gap-1 text-muted-foreground">已有标签：{facets.tags.slice(0, 12).map(t => <button key={t.name} type="button" disabled={disabled || busy || tags.includes(t.name)} onClick={() => setTags([...tags, t.name])} className="rounded border border-border px-1.5 py-0.5 text-[11px] hover:text-foreground disabled:opacity-40">{t.name} · {t.count}</button>)}</div>}
    <div className="flex items-center gap-2">
      <button type="submit" disabled={disabled || busy || !title.trim() || title.trim() === conversation.title}>保存名称</button>
      <button type="button" disabled={disabled || busy || category === (conversation.category || '')} onClick={() => void post('category', { category })}>保存分类</button>
      <button type="button" disabled={disabled || busy || !dirty || tags.join() === (conversation.tags || []).join()} onClick={() => void post('tags', { tags })}>保存标签</button>
    </div>
  </form>;
}
