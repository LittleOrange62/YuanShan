import { useEffect, useState } from 'react';
import { ArrowDown, ArrowUp, CircleDashed, Radio, ShieldAlert, Trash2, XCircle, CheckCircle2 } from 'lucide-react';
import { activeSourceId, blockedReason, clearSourceMark, currentSource, DEFAULT_TIMEOUT_SECONDS, DOWN_FORGET_MIN, FAIL_LIMIT, failoverEnabled, giveUpAfterCycle, moveAgentSource, orderMode, readSources, removeAgentSource, restartEachRun, setActiveSource, setFailoverEnabled, setGiveUpAfterCycle, setOrderMode, setRestartEachRun, setSourceTimeout, sourceJudgedDown, TIMEOUT_CEILING, TIMEOUT_FLOOR, TIMEOUT_SEGMENTS, type OrderMode, type SavedSource } from '@/lib/agent-api';
import { sourceLabel } from '@/lib/ai-access';
import { toast } from 'sonner';

function timeText(at: number) { return at ? new Date(at).toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' }) : ''; }
function TestBadge({ row }: { row: SavedSource }) {
  const stamp = timeText(row.testedAt);
  if (row.testState === 'passed') return <span className="inline-flex items-center gap-1 text-xs text-primary"><CheckCircle2 size={14} />测试通过 {stamp}</span>;
  if (sourceJudgedDown(row)) return <span className="inline-flex items-center gap-1 text-xs" title={row.testNote}><XCircle size={14} />已判定不通（连续 {row.failures} 次）：{row.testNote || '连接失败'} {stamp}；{DOWN_FORGET_MIN} 分钟内不会被选中，之后自动再试一次</span>;
  if (row.failures >= FAIL_LIMIT) return <span className="inline-flex items-center gap-1 text-xs" title={row.testNote}><ShieldAlert size={14} />上次判不通已满 {DOWN_FORGET_MIN} 分钟，下一次任务会自动再试它{row.testNote ? `（${row.testNote}）` : ''}</span>;
  if (row.failures > 0) return <span className="inline-flex items-center gap-1 text-xs" title={row.testNote}><XCircle size={14} />失败 {row.failures} 次，再失败 {FAIL_LIMIT - row.failures} 次才切换到下一条</span>;
  return <span className="inline-flex items-center gap-1 text-xs text-muted-foreground"><CircleDashed size={14} />未测试</span>;
}

export function SavedSources() {
  const [rows, setRows] = useState<SavedSource[]>(readSources);
  const [active, setActive] = useState(activeSourceId);
  const [serving, setServing] = useState(() => currentSource()?.id ?? "");
  const [auto, setAuto] = useState(failoverEnabled);
  const [restart, setRestart] = useState(restartEachRun);
  const [giveUp, setGiveUp] = useState(giveUpAfterCycle);
  const [order, setOrder] = useState<OrderMode>(orderMode);
  const [stopped, setStopped] = useState(blockedReason);
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  useEffect(() => {
    const update = () => { setRows(readSources()); setActive(activeSourceId()); setServing(currentSource()?.id ?? "");
      setAuto(failoverEnabled()); setRestart(restartEachRun()); setGiveUp(giveUpAfterCycle()); setOrder(orderMode());
      setStopped(blockedReason()); };
    window.addEventListener('astock-connection-changed', update);
    window.addEventListener('storage', update);
    return () => { window.removeEventListener('astock-connection-changed', update); window.removeEventListener('storage', update); };
  }, []);
  const usable = rows.filter(row => !sourceJudgedDown(row)).length;
  /** 改完这一格要按回车或点到别处才落盘；被拒时如实说拒在哪，不静默回到 180。 */
  const commitTimeout = (row: SavedSource) => {
    const raw = (drafts[row.id] ?? String(row.timeoutSeconds ?? DEFAULT_TIMEOUT_SECONDS)).trim();
    const seconds = Number(raw);
    if (setSourceTimeout(row.id, seconds)) { setDrafts({ ...drafts, [row.id]: "" }); return; }
    toast.error(`超时秒数要填 ${TIMEOUT_FLOOR}~${TIMEOUT_CEILING} 之间的数字，这次没有改：现在仍是 ${row.timeoutSeconds ?? DEFAULT_TIMEOUT_SECONDS} 秒`);
    setDrafts({ ...drafts, [row.id]: "" });
  };
  return <section aria-label="已保存 AI 来源" className="glass rounded-2xl p-5">
    <div className="mb-1 flex flex-wrap items-center justify-between gap-2">
      <h2 className="font-semibold">已保存 AI 来源</h2>
      <span className="text-xs text-muted-foreground">共 {rows.length} 条 · 可用 {usable} 条 · {order === 'random' ? '本轮顺序随机（一圈内不重复）' : '列表顺序即自动切换优先级'}</span>
    </div>
    <div className="mt-2 grid gap-2 rounded-lg border border-border bg-muted/20 p-3">
      <label className="flex flex-wrap items-start gap-3">
        <input type="checkbox" checked={auto} onChange={e => { setAuto(e.target.checked); setFailoverEnabled(e.target.checked); }}
          aria-label="来源不通时自动切换" className="mt-1" />
        <span className="min-w-0 flex-1 text-xs leading-6 text-muted-foreground">
          <span className="block text-sm font-medium text-foreground">这个不通自动用下一个（默认开启）</span>
          同一条来源要<b>连续失败 {FAIL_LIMIT} 次</b>才判定不通并让位给下一条；失败 {FAIL_LIMIT - 1} 次时下一次仍用它，等于先重试一次。
          切换只在<b>任务开始前</b>生效，同一轮对话不会中途换模型，已有会话仍锁定它当初的来源。
          失败要算在来源头上由错误文字判定：上下文超限、本机存储与后端访问密钥问题<b>不算</b>来源故障。
        </span>
      </label>
      <label className="flex flex-wrap items-start gap-3">
        <input type="checkbox" checked={restart} onChange={e => { setRestart(e.target.checked); setRestartEachRun(e.target.checked); }}
          aria-label="模型每次重头使用" className="mt-1" />
        <span className="min-w-0 flex-1 text-xs leading-6 text-muted-foreground">
          <span className="block text-sm font-medium text-foreground">每次重头使用（默认开启）</span>
          每个新任务都从列表<b>第 1 条</b>开始用，不粘在上一条用熟的那条上。
          与下面「随机」同时开时<b>随机优先</b>（起点随机、一圈内不重复），这条就被随机盖住，界面顶部那句会跟着改口。
        </span>
      </label>
      <fieldset className="flex flex-wrap items-start gap-3 border-0 p-0">
        <legend className="sr-only">顺序方式</legend>
        <span className="block min-w-0 flex-1 text-xs leading-6 text-muted-foreground">
          <span className="block text-sm font-medium text-foreground">顺序方式</span>
          让位时的走法。<b>列表循环</b>=按上面这个顺序依次；<b>随机</b>=每次新任务随机抽一条起点，一圈内不重复撞同一条。
        </span>
        <span className="flex shrink-0 gap-3 text-sm">
          {(['cycle', 'random'] as OrderMode[]).map(mode => <label key={mode} className="flex items-center gap-1.5">
            <input type="radio" name="agent-order-mode" value={mode} checked={order === mode}
              onChange={() => { setOrder(mode); setOrderMode(mode); }} aria-label={mode === 'cycle' ? '列表循环' : '随机'} />
            {mode === 'cycle' ? '列表循环' : '随机'}</label>)}
        </span>
      </fieldset>
      <label className="flex flex-wrap items-start gap-3">
        <input type="checkbox" checked={giveUp} onChange={e => { setGiveUp(e.target.checked); setGiveUpAfterCycle(e.target.checked); }}
          aria-label="列表循环一次后提示无法连接" className="mt-1" />
        <span className="min-w-0 flex-1 text-xs leading-6 text-muted-foreground">
          <span className="block text-sm font-medium text-foreground">循环一圈仍不通就提示无法连接（默认开启）</span>
          每条都试过一圈还不通时<b>停住</b>，把每条的失败原因与"多久前失败的"逐条列给你看，本任务不发请求。
          停住只管这一次：判不通的标记有 <b>{DOWN_FORGET_MIN} 分钟有效期</b>，过了就自动再给它一次机会，不需要人来点。
          关掉这条则退回旧行为：不清标记，按顺序轮流继续再试。
        </span>
      </label>
      <p className="text-xs leading-6 text-muted-foreground">超时口径：{TIMEOUT_SEGMENTS}。连接与不吐字节的判定按每条来源走，判失败才换下一条。</p>
    </div>
    {!rows.length && <p className="mt-3 text-sm text-muted-foreground">还没有已保存的来源。在上方「AI 来源」里填好后点「保存来源」；保存后这里可以排序、切换和删除。</p>}
    {!!stopped && <p role="alert" className="mt-3 flex items-start gap-2 text-xs leading-6"><XCircle size={14} className="mt-1 shrink-0" />{stopped}</p>}
    {!stopped && auto && !!rows.length && usable === 0 && <p role="alert" className="mt-3 flex items-start gap-2 text-xs leading-6"><ShieldAlert size={14} className="mt-1 shrink-0" />所有来源都已连续失败 {FAIL_LIMIT} 次{giveUp ? '，本任务已停住，不会发出去' : '，正在按顺序轮流再试'}；反复不通请先在上方「测试连接」确认地址、密钥与模型。</p>}
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
        {!!row.baseURL && <div className="mt-2 flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
          <label htmlFor={`timeout-${row.id}`} className="flex items-center gap-1.5">
            <span>这条回答最多等多久</span>
            <input id={`timeout-${row.id}`} type="number" inputMode="numeric" min={TIMEOUT_FLOOR} max={TIMEOUT_CEILING} step={30}
              value={drafts[row.id] ?? (row.timeoutSeconds ?? DEFAULT_TIMEOUT_SECONDS)}
              onChange={e => setDrafts({ ...drafts, [row.id]: e.target.value })}
              onBlur={() => drafts[row.id] !== undefined && commitTimeout(row)}
              onKeyDown={e => { if (e.key === 'Enter') { e.preventDefault(); commitTimeout(row); } }}
              className="w-20 rounded-md border border-border bg-background px-2 py-1 text-foreground focus:outline-none focus:ring-2 focus:ring-ring" />
            <span>秒</span>
            <span className="rounded border border-border px-1.5 py-0.5">可改</span>
          </label>
          <span>改完按回车生效；默认 {DEFAULT_TIMEOUT_SECONDS} 秒，可填 {TIMEOUT_FLOOR}~{TIMEOUT_CEILING} 秒。到点就判这次不通并换下一条。</span>
        </div>}
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
