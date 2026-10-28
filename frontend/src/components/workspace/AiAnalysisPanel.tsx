import { useCallback, useEffect, useRef, useState } from 'react';
import { Sparkles, ChevronDown, Play, Trash2, AlertCircle, PanelRightClose } from 'lucide-react';
import { chatStream, hasLlm } from '@/lib/llm';
import { AI_PRESETS, currentSession, gatherContext, type AiPreset } from '@/lib/aiPresets';
import { AskAiChat } from '@/components/ui/AskAiChat';
import { ExplainBox } from '@/components/ui/DataPanel';
import { useReadPageAskAi } from '@/lib/workspace/pageAskAi';

import { cn } from '@/lib/utils';

/**
 * 右侧「AI分析」栏：上半是**预设问题**的运行结果，下半是**你以后配置的按需计算**。
 *
 * 三件事是刻意的：
 *  - 两块都按内容自然长高（整栏滚动），不写固定高度 —— 固定高度会把长答案截断成看不见的滚动条；
 *  - 每条结果都带 `generated_at` 与用的模型：没有生成时间的结果，读者分不清是刚算的还是上周的；
 *  - 没配置就是空的：下半区直说"还没有配置项"，不放一排点了没反应的按钮充数。
 */
interface Run { content: string; at: string; source: string; error?: string }
const STORE_KEY = 'yuanshan-ai-analysis-v1';

function loadStore(): Record<string, Run> {
  try { return JSON.parse(localStorage.getItem(STORE_KEY) || '{}') as Record<string, Run>; }
  catch { return {}; }
}

export function AiAnalysisPanel({ onCollapse }: { onCollapse?: () => void } = {}) {
  const [runs, setRuns] = useState<Record<string, Run>>(loadStore);
  const [busy, setBusy] = useState<string | null>(null);
  const [openPrompt, setOpenPrompt] = useState<string | null>(null);
  const [session, setSession] = useState('');
  const alive = useRef(true);
  // 这一屏登记了哪些材料（首页盘面数据 / 个股研究 / 自选股），用户 10-07 把「问 AI」入口收到这里
  const page = useReadPageAskAi();
  useEffect(() => () => { alive.current = false; }, []);

  // 结果按「预设 + 哪一场行情」存：换了一天的数，昨天那份结论不该继续顶在这里
  useEffect(() => {
    currentSession().then(d => alive.current && setSession(d)).catch(() => {});
  }, []);

  const save = useCallback((key: string, run: Run) => {
    setRuns(prev => {
      const next = { ...prev, [key]: run };
      try { localStorage.setItem(STORE_KEY, JSON.stringify(next)); } catch { /* 存不下就只在内存里 */ }
      return next;
    });
  }, []);

  async function runPreset(preset: AiPreset) {
    const key = `${preset.id}|${session}`;
    setBusy(preset.id);
    save(key, { content: '', at: new Date().toISOString(), source: '' });
    try {
      const context = await gatherContext(preset);
      // taskKind='analysis'：这一档允许给参考价位/止损/空间（用户逐条点了才跑、
      // 结果只留本机、不入库不进报告）。复盘报告与公开页面仍走产品红线。
      const res = await chatStream([{ role: 'user', content: preset.prompt }], context, {
        taskKind: 'analysis',
        onDelta: text => alive.current && save(key, {
          content: text, at: new Date().toISOString(), source: '生成中',
        }),
      });
      if (!alive.current) return;
      save(key, {
        content: res.content || '（模型返回空正文，未记录成结论）',
        at: new Date().toISOString(),
        source: res.ai_source ? `${res.ai_source.provider} · ${res.ai_source.model}` : '未知来源',
      });
    } catch (error) {
      if (!alive.current) return;
      save(key, {
        content: '', at: new Date().toISOString(), source: '',
        error: error instanceof Error ? error.message : '运行失败',
      });
    } finally {
      if (alive.current) setBusy(null);
    }
  }

  const llmReady = hasLlm();

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="flex items-center gap-2 border-b border-border px-4 py-3">
        <Sparkles className="h-4 w-4 shrink-0 text-primary" />
        <h2 className="flex-1 truncate text-sm font-semibold">AI分析</h2>
        {session && <span className="shrink-0 text-[10px] text-muted-foreground" title="结果按这一场行情算">{session}</span>}
        {/* 收起这一栏（用户 10-10 要）：收起后中间那一列会拿回这 256px，宽表不再横向滚动。
            选择记在浏览器本地，跟左侧栏一样是"这台机器的看盘习惯"，不是页面状态。 */}
        {onCollapse && (
          <button type="button" onClick={onCollapse} aria-label="收起 AI分析栏" title="收起 AI分析栏（中间那一列会拿回宽度）"
                  className="shrink-0 rounded p-1 text-muted-foreground hover:bg-muted hover:text-foreground">
            <PanelRightClose className="h-4 w-4" />
          </button>
        )}
      </div>
      {/* 用户 2026-10-21：「描述性内容全部收缩起来，避免过多占屏」——这一句挂在**每一屏**的右栏头上
          （内容一个字没变，只是换成点开可见）；summary 里留着"风险自担"，收起不等于把这条 caution 藏掉。 */}
      <ExplainBox title="这一栏是什么（逐条点了才跑 · 只存本机浏览器 · 风险自担）">
        <p>这一栏是你逐条点了才跑的情景推演，结果只存在本机浏览器、不入库、不进复盘报告；
          价位与止损都由材料里的数字推得，风险自担。</p>
      </ExplainBox>

      <div className="min-h-0 flex-1 space-y-3 overflow-auto p-3">
        <section className="rounded-xl border border-border bg-card/60 p-3">
          <p className="text-[10px] uppercase tracking-[0.16em] text-muted-foreground">问 AI · 本页上下文</p>
          {page ? (
            <>
              {/* 同上：这句也是每屏都摊着的描述，收起来但把"不校准、不负责"留在 summary 上 */}
              <ExplainBox title={`带的是哪一份材料（${page.label} 此刻这份读数 · 本产品不校准、不负责）`}>
                <p>带的是 <b className="text-foreground">{page.label}</b> 此刻这份读数（换页会换材料、开新对话）。
                  结论由你自己配置的模型给出，本产品不校准、不负责。</p>
              </ExplainBox>
              {/* key=label：换页 = 重挂一份干净会话。上一屏的问答留在这里会被读成"这一屏的答案" */}
              <div className="mt-2"><AskAiChat key={page.label} context={page.context} suggestions={page.suggestions ?? []} /></div>
            </>
          ) : (
            <p className="mt-1 text-[10px] leading-4 text-muted-foreground">
              这一屏没有登记可带的本页材料，所以这里不放一个点了没反应的输入框。
              首页盘面数据、个股研究、自选股三页会把各自当前读数登记进来；
              持仓（重点关注）<b className="text-foreground">永远不登记</b> —— 成本与盈亏不接入任何 AI。
            </p>
          )}
        </section>

        <section className="space-y-3">
          <p className="text-[10px] uppercase tracking-[0.16em] text-muted-foreground">预设分析</p>
          {!llmReady && (
            <p className="flex items-start gap-1.5 rounded-lg border border-warning/40 bg-warning/10 p-2 text-[11px] leading-5">
              <AlertCircle className="mt-0.5 h-3 w-3 shrink-0" />
              还没接入 AI，跑不了。到「AI 探索」保存一个来源后回来点运行。
            </p>
          )}
          {AI_PRESETS.map(preset => {
            const saved = runs[`${preset.id}|${session}`];
            const running = busy === preset.id;
            return (
              <article key={preset.id} className="rounded-xl border border-border bg-card/60">
                <h3 className="px-3 pt-3 text-[13px] font-semibold leading-5">{preset.title}</h3>
                <button type="button" onClick={() => setOpenPrompt(openPrompt === preset.id ? null : preset.id)}
                        aria-expanded={openPrompt === preset.id}
                        className="mt-1 flex w-full items-center gap-1 px-3 text-left text-[10px] text-muted-foreground hover:text-foreground">
                  <ChevronDown className={cn('h-3 w-3 transition-transform', openPrompt === preset.id && 'rotate-180')} />
                  {openPrompt === preset.id ? '收起完整提示词' : '看完整提示词'}
                </button>
                {openPrompt === preset.id && (
                  <p className="mx-3 mt-1 max-h-48 overflow-auto rounded-lg bg-muted/40 p-2 text-[11px] leading-5 text-muted-foreground">
                    {preset.prompt}
                  </p>
                )}
                <div className="mt-2 border-t border-border/60 px-3 py-2">
                  {saved?.error ? (
                    <p role="alert" className="text-[11px] leading-5 text-warning">运行失败：{saved.error}</p>
                  ) : saved?.content ? (
                    <p className="whitespace-pre-wrap text-[12px] leading-6">{saved.content}
                      {running && <span className="ml-1 inline-block h-3 w-1.5 animate-pulse bg-primary align-middle" />}
                    </p>
                  ) : (
                    <p className="text-[11px] text-muted-foreground">{running ? '正在读页面资料并生成…' : '还没跑过这道题。'}</p>
                  )}
                  {saved && !saved.error && (saved.content || running) && (
                    <p className="mt-1.5 text-[10px] text-muted-foreground/70">
                      {new Date(saved.at).toLocaleString('zh-CN', { hour12: false })} · {saved.source || '生成中'}
                      {' · 读的是 '}{preset.sources.length}{' 个页面数据源'}
                    </p>
                  )}
                </div>
                <div className="flex items-center gap-2 border-t border-border/60 px-3 py-2">
                  <button type="button" onClick={() => void runPreset(preset)} disabled={!llmReady || running}
                          className="inline-flex items-center gap-1 rounded-lg bg-primary/15 px-2.5 py-1 text-[11px] font-medium text-primary hover:bg-primary/25 disabled:opacity-40">
                    <Play className="h-3 w-3" />{running ? '生成中…' : saved?.content ? '重新运行' : '运行'}
                  </button>
                  {saved?.content && !running && (
                    <button type="button" onClick={() => drop(`${preset.id}|${session}`)}
                            className="inline-flex items-center gap-1 rounded-lg border border-border px-2 py-1 text-[11px] text-muted-foreground hover:text-foreground">
                      <Trash2 className="h-3 w-3" /> 清掉结果
                    </button>
                  )}
                </div>
              </article>
            );
          })}
        </section>

        <section>
          <p className="text-[10px] uppercase tracking-[0.16em] text-muted-foreground">按需运行（你自己配置）</p>
          <div className="mt-2 rounded-xl border border-dashed border-border/70 p-3 text-[11px] leading-5 text-muted-foreground">
            还没有配置项。这一区放"你点按钮才计算"的分析：配好题目、要读哪些页面资料、
            结果留多久，就出现在上面那一列的下面。
          </div>
        </section>
      </div>
    </div>
  );

  function drop(key: string) {
    setRuns(prev => {
      const next = { ...prev };
      delete next[key];
      try { localStorage.setItem(STORE_KEY, JSON.stringify(next)); } catch { /* 同上 */ }
      return next;
    });
  }
}
