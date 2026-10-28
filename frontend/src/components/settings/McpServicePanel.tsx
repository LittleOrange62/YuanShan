import { useMemo, useState } from 'react';
import { CheckCircle2, Copy, RefreshCw, XCircle } from 'lucide-react';
import { ExplainBox } from '@/components/ui/DataPanel';
import { SortHeader } from '@/components/ui/SortHeader';
import { useTableSort } from '@/lib/useSort';
import { useNonce, useProbe } from '@/lib/probe';
import { hasData, type DataState } from '@/lib/datastate';
import { apiUrl } from '@/lib/base';
import { authHeaders } from '@/lib/api';
import { cn } from '@/lib/utils';
import { toast } from 'sonner';

/**
 * 「AI 探索 · MCP 服务」这一片：把登记表原样摆出来，让人不问代码也能看清
 * 「智能体能取到哪一格数、那一格数出自哪个端点、屏幕上在哪一屏」。
 *
 * 两条规矩：
 * 1. **条数与清单一律现读接口**（`/api/mcp/registry`），页面不抄第二份台账 —— 抄了就会漂。
 * 2. **自检项取不到就显示「未取到＋原因」**，不显示 ✓；红线由后端 `redlines` 说，这里不写死。
 */

interface ParamSpec { name: string; type: string; default: unknown; required: boolean; bounds: Record<string, string> }
interface CapabilityEntry {
  capability_id: string; label: string; where: string; layer: string; path: string;
  handler: string; source_module: string; tables: string[]; note: string; slow: boolean;
  params: ParamSpec[];
}
interface Check { key: string; label: string; available: boolean; reason: string; detail: string }
interface Registry {
  available?: boolean; reason?: string;
  service_name?: string;
  capabilities: CapabilityEntry[];
  counts?: { A: number; B: number; C: number; index: number; tools: number };
  tools?: string[];
  doc?: { ok: boolean; rows_in_doc: number; rows_in_registry: number;
          missing_in_doc: string[]; extra_in_doc: string[]; path_mismatch: string[]; doc: string };
}
interface Status {
  available?: boolean; reason?: string; service_name?: string;
  all_green?: boolean; blocking?: string[]; optional_pending?: string[];
  checks: Check[]; redlines: string[];
  config?: { stdio: unknown; http: unknown; http_start_command: string; cwd: string };
  counts?: { A: number; B: number; C: number; index: number; tools: number };
}
interface TryResult {
  available: boolean; capability_id: string; label: string; handler: string;
  tables: string[]; reason: string;
  result: { data: unknown; _meta: Record<string, unknown> } | null;
}

/** 屏幕上不出现 markdown 星号：后端的 reason/caveat 是 markdown 写的，这里只保证渲染层不带标记 */
const plain = (s: string) => (s || '').replace(/\*\*/g, '');

const LAYERS = [
  { id: 'all', label: '全部' },
  { id: 'A', label: 'A 层 全市场榜' },
  { id: 'B', label: 'B 层 个股明细' },
  { id: 'C', label: 'C 层 只进目录' },
];

export function McpServicePanel() {
  const [nonce, refresh] = useNonce();
  const registry = useProbe<Registry>('/mcp/registry', nonce);
  const status = useProbe<Status>('/mcp/status', nonce);
  const [layer, setLayer] = useState('all');
  const [openId, setOpenId] = useState<string | null>(null);
  const table = useTableSort(null);

  const rows = useMemo(() => {
    if (!hasData(registry)) return [];
    const all = registry.data.capabilities || [];
    return all.filter(c => layer === 'all' || c.layer === layer);
  }, [registry, layer]);

  return <div className="space-y-6">
    <section className="glass rounded-2xl p-5">
      <div className="mb-3 flex items-center justify-between gap-3">
        <h2 className="font-semibold">MCP 服务 · 把屏幕上那些数交给外部智能体</h2>
        <button type="button" onClick={refresh}
          className="inline-flex items-center gap-1.5 rounded-lg border border-border px-3 py-1.5 text-xs hover:bg-muted/50">
          <RefreshCw className="h-3.5 w-3.5" /> 重新体检
        </button>
      </div>
      <p className="text-xs text-muted-foreground">
        智能体通过 MCP 协议调用本服务，取回的就是你在榜单、个股、目录那几屏上看到的那一份数 —— 同一个端点、同一个函数，
        服务侧不重算、不缓存、不改形。台账在 <code>docs/MCP服务登记表.md</code>，机器读的那份在
        <code> mcp_service/capabilities.py</code>，两份由 <code>tests/test_mcp_registry_sync.py</code> 逐条对齐。
      </p>
      <div className="mt-3 grid gap-2 sm:grid-cols-2 lg:grid-cols-4">
        <Stat label="A 层 全市场榜" value={registry} pick={d => d.counts?.A} state={status} />
        <Stat label="B 层 个股明细" value={registry} pick={d => d.counts?.B} state={status} />
        <Stat label="C 层 只进目录" value={registry} pick={d => d.counts?.C} state={status} />
        <Stat label="A/B 层专用工具（另有通用 3 条）" value={registry} pick={d => d.counts?.tools} state={status} />
      </div>
      <ExplainBox title="权限边界（由服务侧代码写死，不是这里抄的一句话）">
        <ul className="list-disc space-y-1 pl-5 text-sm">
          {(hasData(status) ? status.data.redlines || [] : []).map(r => <li key={r}>{plain(r)}</li>)}
          {!hasData(status) && <li className="list-none text-muted-foreground">未取到：{describe(status)}</li>}
        </ul>
      </ExplainBox>
    </section>

    <section className="glass rounded-2xl p-5">
      <h2 className="mb-1 font-semibold">接入前自检</h2>
      {hasData(status) && <p className="mb-3 text-xs text-muted-foreground">
        {status.data.all_green ? '全绿。' : `有项未过：阻塞 ${(status.data.blocking || []).join('、') || '无'}`
          + `；可选项 ${(status.data.optional_pending || []).join('、') || '无'}`
          + '（可选项不影响本机 stdio 接入，只影响局域网常驻那一种）'}
      </p>}
      {!hasData(status) && <p className="mb-3 text-sm text-muted-foreground">未取到：{describe(status)}</p>}
      <ul className="space-y-2">
        {(hasData(status) ? status.data.checks : []).map(c => <li key={c.key} className="flex items-start gap-2 text-sm">
          {c.available
            ? <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0 text-emerald-500" />
            : <XCircle className="mt-0.5 h-4 w-4 shrink-0 text-red-500" />}
          <span className="min-w-0">
            <span className="font-medium">{plain(c.label)}</span>
            <span className="ml-2 text-xs text-muted-foreground">{plain(c.detail)}</span>
            {c.reason && <span className="block text-xs text-red-500">{plain(c.reason)}</span>}
          </span>
        </li>)}
      </ul>
    </section>

    <section className="glass rounded-2xl p-5">
      <h2 className="mb-1 font-semibold">工具对照表</h2>
      <p className="mb-3 text-xs text-muted-foreground">
        一行＝一个能被智能体调用的读数。点列头排序（再点翻向，第三次取消），点「看明细」展开参数与主读表。
        {hasData(registry) && registry.data.doc && !registry.data.doc.ok
          && <span className="ml-2 text-red-500">台账与登记表不一致，见下方。</span>}
      </p>
      <div className="mb-3 flex flex-wrap gap-2">
        {LAYERS.map(l => <button key={l.id} type="button" onClick={() => setLayer(l.id)}
          className={cn('rounded-lg border px-3 py-1.5 text-xs',
            layer === l.id ? 'border-primary bg-primary/10 text-primary' : 'border-border text-muted-foreground')}>
          {l.label}
        </button>)}
      </div>
      {!hasData(registry) && <p className="text-sm text-muted-foreground">未取到：{describe(registry)}</p>}
      {hasData(registry) && <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead className="border-b border-border text-xs text-muted-foreground">
            <tr>
              <SortHeader label="工具 / capability_id" sortKey="capability_id" dir={table.dirOf('capability_id')} onSort={table.toggle} />
              <SortHeader label="标签" sortKey="label" dir={table.dirOf('label')} onSort={table.toggle} />
              <SortHeader label="屏幕落点" sortKey="where" dir={table.dirOf('where')} onSort={table.toggle} />
              <SortHeader label="层" sortKey="layer" dir={table.dirOf('layer')} onSort={table.toggle} />
              <SortHeader label="来源端点" sortKey="path" dir={table.dirOf('path')} onSort={table.toggle} />
              <SortHeader label="参数数" sortKey="param_count" dir={table.dirOf('param_count')} onSort={table.toggle} />
              <th className="p-2 text-left font-normal">操作</th>
            </tr>
          </thead>
          <tbody>
            {table.apply(rows.map(r => ({ ...r, param_count: r.params.length }))).map(r => <FragmentRow key={r.capability_id}
              c={r} open={openId === r.capability_id} onToggle={() => setOpenId(openId === r.capability_id ? null : r.capability_id)} />)}
          </tbody>
        </table>
      </div>}
      {hasData(registry) && registry.data.doc && !registry.data.doc.ok && <div className="mt-3 rounded-lg border border-red-500/40 bg-red-500/5 p-3 text-xs">
        <p className="font-medium text-red-500">台账漂移（这份表以登记表为准）</p>
        <p>文档少：{registry.data.doc.missing_in_doc.join('、') || '无'}｜文档多：{registry.data.doc.extra_in_doc.join('、') || '无'}</p>
        <p>端点不一致：{registry.data.doc.path_mismatch.join('；') || '无'}</p>
      </div>}
    </section>

    <section className="glass rounded-2xl p-5">
      <h2 className="mb-1 font-semibold">接入配置</h2>
      <p className="mb-3 text-xs text-muted-foreground">
        stdio 那条给本机智能体（它自己拉起进程，不要求服务常驻）；HTTP 那条给局域网/别的电脑，
        需要先跑下面的启动命令。路径按当前这台机器的真实解释器现读。
      </p>
      {!hasData(status) && <p className="text-sm text-muted-foreground">未取到：{describe(status)}</p>}
      {hasData(status) && status.data.config && <div className="space-y-3">
        <ConfigBlock title="stdio（本机智能体）" text={JSON.stringify(status.data.config.stdio, null, 2)} />
        <ConfigBlock title="streamable HTTP（局域网/常驻）" text={JSON.stringify(status.data.config.http, null, 2)} />
        <ConfigBlock title="HTTP 模式启动命令" text={status.data.config.http_start_command} />
        <p className="text-xs text-muted-foreground">工作目录 <code>{status.data.config.cwd}</code>；
          改了后端地址就设环境变量 <code>YUANSHAN_MCP_BASE</code>，后端开了 <code>VR_API_KEY</code> 就给 MCP 进程同名变量。</p>
      </div>}
    </section>

    <TryCall capabilities={hasData(registry) ? registry.data.capabilities || [] : []} />
  </div>;
}

function Stat({ label, value, pick, state }: {
  label: string; value: DataState<Registry>; pick: (d: Registry) => number | undefined; state: DataState<Status>;
}) {
  const n = hasData(value) ? pick(value.data) : undefined;
  const offline = state.kind === 'offline' || value.kind === 'offline';
  return <div className="rounded-xl border border-border px-3 py-2">
    <p className="text-xs text-muted-foreground">{label}</p>
    <p className="text-lg font-semibold">{typeof n === 'number' ? n : offline ? '连不到后端' : '未取到'}</p>
  </div>;
}

function describe(state: DataState<unknown>): string {
  if (state.kind === 'loading') return '取数中…';
  if (state.kind === 'ok') return plain((state.data as { reason?: string })?.reason || '接口返回了 available:false，且没给原因');
  return plain(String((state as { reason?: string; path?: string }).reason || (state as { path?: string }).path || state.kind));
}

function FragmentRow({ c, open, onToggle }: { c: CapabilityEntry; open: boolean; onToggle: () => void }) {
  return <>
    <tr className="border-b border-border/60">
      <td className="p-2 font-mono text-xs">{c.capability_id}</td>
      <td className="p-2">{plain(c.label)}</td>
      <td className="p-2 text-xs">{plain(c.where)}</td>
      <td className="p-2 text-xs">{c.layer}</td>
      <td className="p-2 font-mono text-xs">{c.path}</td>
      <td className="p-2 text-xs">{c.params.length}</td>
      <td className="p-2"><button type="button" onClick={onToggle}
        className="rounded-md border border-border px-2 py-1 text-xs hover:bg-muted/50">{open ? '收起' : '看明细'}</button></td>
    </tr>
    {open && <tr className="border-b border-border/60 bg-muted/30">
      <td colSpan={7} className="p-3 text-xs">
        <p>处理函数 <code>{c.handler}</code>｜取数模块 <code>{c.source_module || '—'}</code>
          {c.slow && <span className="ml-2 text-muted-foreground">慢榜（首打可到几十秒）</span>}
          {c.note && <span className="ml-2 text-amber-500">{plain(c.note)}</span>}</p>
        <p className="mt-1">主读表：{c.tables.join('、') || '现场聚合，不落新表'}</p>
        <table className="mt-2 w-full text-xs">
          <thead className="text-muted-foreground"><tr><th className="p-1 text-left">参数</th>
            <th className="p-1 text-left">类型</th><th className="p-1 text-left">默认</th>
            <th className="p-1 text-left">上下界</th><th className="p-1 text-left">必填</th></tr></thead>
          <tbody>{c.params.map(p => <tr key={p.name} className="border-t border-border/40">
            <td className="p-1 font-mono">{p.name}</td><td className="p-1">{p.type}</td>
            <td className="p-1">{JSON.stringify(p.default)}</td>
            <td className="p-1">{Object.entries(p.bounds).map(([k, v]) => `${k}=${v}`).join(' ') || '—'}</td>
            <td className="p-1">{p.required ? '是' : '否'}</td></tr>)}
            {c.params.length === 0 && <tr><td colSpan={5} className="p-1 text-muted-foreground">无参数</td></tr>}
          </tbody>
        </table>
        <p className="mt-1 text-muted-foreground">{c.layer === 'C'
          ? `这一层不占工具名：用通用工具取 capability_data(capability_id="${c.capability_id}", params=…)`
          : '这一条已经是一个专用工具，智能体可直接按工具名调用。'}</p>
      </td>
    </tr>}
  </>;
}

function ConfigBlock({ title, text }: { title: string; text: string }) {
  const [copied, setCopied] = useState('');
  return <div>
    <div className="mb-1 flex items-center justify-between gap-2">
      <p className="text-xs font-medium text-muted-foreground">{title}</p>
      <button type="button" title="复制到剪贴板" onClick={async () => {
        try {
          await navigator.clipboard.writeText(text);
          setCopied('已复制');
          toast.success('已复制到剪贴板');
        } catch {
          setCopied('复制失败');
          toast.error('浏览器拒绝了剪贴板访问，请手动选中复制');
        }
        window.setTimeout(() => setCopied(''), 2000);
      }} className="inline-flex items-center gap-1 rounded-md border border-border px-2 py-1 text-xs hover:bg-muted/50">
        <Copy className="h-3.5 w-3.5" />{copied || '复制'}
      </button>
    </div>
    <pre className="overflow-x-auto rounded-lg bg-muted/40 p-3 text-xs">{text}</pre>
  </div>;
}

function TryCall({ capabilities }: { capabilities: CapabilityEntry[] }) {
  const [id, setId] = useState('sector_heat');
  const [raw, setRaw] = useState('{\n  "limit": 5\n}');
  const [busy, setBusy] = useState(false);
  const [out, setOut] = useState<TryResult | null>(null);
  const [err, setErr] = useState('');
  const chosen = capabilities.find(c => c.capability_id === id);

  const run = async () => {
    setErr('');
    let params: Record<string, unknown> = {};
    if (raw.trim()) {
      try {
        params = JSON.parse(raw) as Record<string, unknown>;
      } catch (e) {
        setErr(`参数不是合法 JSON：${e instanceof Error ? e.message : ''}`);
        return;
      }
    }
    setBusy(true);
    try {
      const resp = await fetch(apiUrl('/api/mcp/try'), {
        method: 'POST',
        headers: { ...authHeaders(), 'Content-Type': 'application/json' },
        body: JSON.stringify({ capability_id: id, params }),
      });
      const body = await resp.json();
      if (!resp.ok) throw new Error(String(body?.detail || `HTTP ${resp.status}`));
      setOut((body?.data ?? body) as TryResult);
    } catch (e) {
      setErr(e instanceof Error ? e.message : '调用失败');
    } finally {
      setBusy(false);
    }
  };

  const blockers: string[] = [];
  if (!id.trim()) blockers.push('先选一条工具');
  if (!chosen && id.startsWith('/api/') === false) blockers.push('这条不在登记表里：从下拉里选，或直接填 /api/... 路径');

  return <section className="glass rounded-2xl p-5">
    <h2 className="mb-1 font-semibold">试调用</h2>
    <p className="mb-3 text-xs text-muted-foreground">
      走的是智能体用的同一条只读链路（<code>mcp_service.client</code>）。返回的 <code>_meta</code> 里
      <code> as_of</code> 就是这一份数实际读到的那场交易日。
    </p>
    <div className="flex flex-wrap items-start gap-3">
      <label className="text-xs">工具
        <select value={id} onChange={e => setId(e.target.value)}
          className="mt-1 block min-w-56 rounded-lg border border-border bg-card px-2 py-1.5 text-sm">
          {capabilities.map(c => <option key={c.capability_id} value={c.capability_id}>
            {c.capability_id}（{c.layer}｜{c.label}）
          </option>)}
        </select>
      </label>
      <label className="text-xs">参数 JSON
        <textarea value={raw} onChange={e => setRaw(e.target.value)} rows={4} spellCheck={false}
          className="mt-1 block w-full min-w-64 rounded-lg border border-border bg-card px-2 py-1.5 font-mono text-xs" />
      </label>
      <div className="pt-5">
        <button type="button" onClick={run} disabled={busy || blockers.length > 0}
          className={cn('rounded-lg px-4 py-2 text-sm',
            busy || blockers.length > 0 ? 'bg-muted text-muted-foreground' : 'bg-primary text-primary-foreground')}>
          {busy ? '调用中…' : '真的调一次'}
        </button>
        {blockers.length > 0 && <p className="mt-1 max-w-64 text-xs text-amber-500">还没法调用，缺：{blockers.join('；')}</p>}
      </div>
    </div>
    {chosen && <p className="mt-2 text-xs text-muted-foreground">
      可传参数：{chosen.params.map(p => p.name).join('、') || '无'}（refresh 不接受）
    </p>}
    {err && <p className="mt-3 rounded-lg border border-red-500/40 bg-red-500/5 p-2 text-xs text-red-500">{plain(err)}</p>}
    {out && <div className="mt-3 rounded-lg border border-border bg-muted/30 p-3 text-xs">
      {out.available ? <>
        <p className="font-medium">{plain(out.label)}｜{out.handler}</p>
        <p className="mt-1">回执：数据日 {String(out.result?._meta?.as_of ?? '未给出')}｜取回 {String(out.result?._meta?.row_count ?? '—')} 行
          ｜耗时 {String(out.result?._meta?.elapsed_ms ?? '—')} ms｜来源 {String(out.result?._meta?.source_path ?? '')}</p>
        {Array.isArray(out.result?._meta?.degraded) && (out.result!._meta.degraded as string[]).length > 0
          && <p className="mt-1 text-amber-500">缺数自述：{(out.result!._meta.degraded as string[]).map(plain).join(' ｜ ')}</p>}
        <pre className="mt-2 max-h-72 overflow-auto rounded bg-background/60 p-2">{truncate(plain(JSON.stringify(out.result?.data ?? null, null, 1)))}</pre>
      </> : <p className="text-red-500">调用失败：{plain(out.reason || '未给原因')}（这不是「今天没有数据」）</p>}
    </div>}
  </section>;
}

function truncate(s: string): string {
  const MAX = 6000;
  return s.length > MAX ? `${s.slice(0, MAX)}\n…（已截断，完整数据由智能体调用取回）` : s;
}
