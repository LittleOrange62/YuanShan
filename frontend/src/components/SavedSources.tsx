import { useEffect, useState } from 'react';
import { ArrowDown, ArrowUp, CircleDashed, Radio, ShieldAlert, Trash2, XCircle, CheckCircle2 } from 'lucide-react';
import { activeSourceId, clearSourceMark, currentSource, failoverEnabled, FAIL_LIMIT, moveAgentSource, readSources, removeAgentSource, setActiveSource, setFailoverEnabled, type SavedSource } from '@/lib/agent-api';
import { sourceLabel } from '@/lib/ai-access';
import { toast } from 'sonner';

function timeText(at: number) { return at ? new Date(at).toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' }) : ''; }
function TestBadge({ row }: { row: SavedSource }) {
  const stamp = timeText(row.testedAt);
  if (row.testState === 'passed') return <span className="inline-flex items-center gap-1 text-xs text-primary"><CheckCircle2 size={14} />测试通过 {stamp}</span>;
  if (row.testState === 'failed') return <span className="inline-flex items-center gap-1 text-xs" title={row.testNote}><XCircle size={14} />已判定不通（连续 {row.failures} 次）：{row.testNote || '连接失败'} {stamp}</span>;
  if (row.failures > 0) return <span className="inline-flex items-center gap-1 text-xs" title={row.testNote}><XCircle size={14} />失败 {row.failures} 次，再失败 {FAIL_LIMIT - row.failures} 次才切换到下一条</span>;
  return <span className="inline-flex items-center gap-1 text-xs text-muted-foreground"><CircleDashed size={14} />未测试</span>;
}

export function SavedSources() {
  const [rows, setRows] = useState<SavedSource[]>(readSources);
  const [active, setActive] = useState(activeSourceId);
  const [serving, setServing] = useState(() => currentSource()?.id ?? "");
  const [auto, setAuto] = useState(failoverEnabled);
  useEffect(() => {
    const update = () => { setRows(readSources()); setActive(activeSourceId()); setServing(currentSource()?.id ?? ""); setAuto(failoverEnabled()); };
    window.addEventListener('astock-connection-changed', update);
    window.addEventListener('storage', update);
    return () => { window.removeEventListener('astock-connection-changed', update); window.removeEventListener('storage', update); };
  }, []);
  const usable = rows.filter(row => row.failures < FAIL_LIMIT).length;
  return <section aria-label="已保存 AI 来源" className="glass rounded-2xl p-5">
    <div className="mb-1 flex flex-wrap items-center justify-between gap-2">
      <h2 className="font-semibold">已保存 AI 来源</h2>
      <span className="text-xs text-muted-foreground">共 {rows.length} 条 · 可用 {usable} 条 · 列表顺序即自动切换优先级，顺序循环</span>
    </div>
    <label className="mt-2 flex flex-wrap items-start gap-3 rounded-lg border border-border bg-muted/20 p-3">
      <input type="checkbox" checked={auto} onChange={e => { setAuto(e.target.checked); setFailoverEnabled(e.target.checked); }}
        aria-label="来源不通时自动切换" className="mt-1" />
      <span className="min-w-0 flex-1 text-xs leading-6 text-muted-foreground">
        <span className="block text-sm font-medium text-foreground">这个不通自动用下一个（默认开启）</span>
        同一条来源要<b>连续失败 {FAIL_LIMIT} 次</b>才判定不通并让位给下一条；失败 {FAIL_LIMIT - 1} 次时下一次仍用它，等于先重试一次。
        切换只在<b>任务开始前</b>生效，同一轮对话不会中途换模型，已有会话仍锁定它当初的来源。
        全部都不通时不清除标记，按列表顺序轮流再试。上下文超限、本机存储与后端访问密钥问题不算来源故障。
      </span>
    </label>
    {!rows.length && <p className="mt-3 text-sm text-muted-foreground">还没有已保存的来源。在上方「AI 来源」里填好后点「保存来源」；保存后这里可以排序、切换和删除。</p>}
    {auto && !!rows.length && usable === 0 && <p role="alert" className="mt-3 flex items-start gap-2 text-xs leading-6"><ShieldAlert size={14} className="mt-1 shrink-0" />所有来源都已连续失败 {FAIL_LIMIT} 次，正在按顺序轮流再试；反复不通请先在上方「测试连接」确认地址、密钥与模型。</p>}
    <ul className="mt-3 space-y-2">
      {rows.map((row, index) => <li key={row.id} className={`rounded-lg border p-3 ${row.id === active ? 'border-primary bg-primary/10' : 'border-border'}`}>
        <div className="flex flex-wrap items-center justify-between gap-2">
          <span className="flex min-w-0 flex-wrap items-center gap-2">
            <b className="text-sm">{index + 1}. {sourceLabel(row)}</b>
            <span className="text-xs text-muted-foreground">{row.model}</span>
            {row.id === serving && <span className="inline-flex items-center gap-1 text-xs text-primary"><Radio size={12} />本次任务会用</span>}
            {row.id === active && row.id !== serving && <span className="text-xs text-muted-foreground">你选的当前项（已让位）</span>}
            {auto && row.id !== active && row.failures < FAIL_LIMIT && rows.findIndex(item => item.id === serving) < index && <span className="text-xs text-muted-foreground">排队待用</span>}
          </span>
          <span className="flex shrink-0 gap-1">
            <button type="button" onClick={() => moveAgentSource(row.id, -1)} disabled={index === 0} aria-label={`上移 ${sourceLabel(row)}`} className="rounded border border-border p-1.5 disabled:opacity-30"><ArrowUp size={14} /></button>
            <button type="button" onClick={() => moveAgentSource(row.id, 1)} disabled={index === rows.length - 1} aria-label={`下移 ${sourceLabel(row)}`} className="rounded border border-border p-1.5 disabled:opacity-30"><ArrowDown size={14} /></button>
            <button type="button" onClick={() => { removeAgentSource(row.id); toast.info('已从本机列表移除；该来源的历史会话记录仍在本地保留'); }} aria-label={`移除 ${sourceLabel(row)}`} className="rounded border border-border p-1.5"><Trash2 size={14} /></button>
          </span>
        </div>
        <p className="mt-1 truncate text-xs text-muted-foreground" title={row.baseURL}>{row.baseURL || '订阅登录，无服务地址'}</p>
        <div className="mt-1 flex flex-wrap items-center gap-3">
          <TestBadge row={row} />
          {row.testState !== 'untested' && <button type="button" onClick={() => clearSourceMark(row.id)} className="rounded border border-border px-2 py-1 text-xs">清除测试标记</button>}
          {row.id !== active && <button type="button" onClick={() => setActiveSource(row.id)} className="rounded border border-border px-2 py-1 text-xs">设为当前</button>}
        </div>
      </li>)}
    </ul>
    <p className="mt-3 text-xs leading-6 text-muted-foreground">来源与密钥只存在这台电脑的浏览器里；「测试连接」会向所选服务发真实请求并可能消耗额度。切换当前来源只影响之后新建的对话与任务。</p>
  </section>;
}
