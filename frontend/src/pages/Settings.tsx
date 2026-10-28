import { useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { KeyRound, MessagesSquare, Server, SlidersHorizontal } from 'lucide-react';
import { PageHeader } from '@/components/ui/PageHeader';
import { ExplainBox } from '@/components/ui/DataPanel';
import { AgentAccess } from '@/components/AgentAccess';
import { SavedSources } from '@/components/SavedSources';
import { AgentToggle } from '@/components/workspace/AgentToggle';
import { WorkspaceChat } from '@/components/workspace/WorkspaceChat';
import { McpServicePanel } from '@/components/settings/McpServicePanel';
import { useWorkspace } from '@/lib/workspace/state';
import { loadAccessKey, saveAccessKey } from '@/lib/api';
import { cn } from '@/lib/utils';
import { toast } from 'sonner';

/**
 * 「AI 探索」：AI 对话与 AI 接入合成一页两 tab。
 *
 * 对话面板原先常驻首页，现在整体搬到这里来 —— 首页只留右上角「问模型」按钮开的浮层对话。
 * 两个 tab 共用同一次 `workspace.refresh()`：换来源会立刻反映到对话侧的模式与标题上。
 *
 * ⚠️ `WorkspaceChat` 仍传 `page="首页"`：那是**会话与待发的稳定标识**
 * （`/conversations?page=` 的过滤键、`astock-workspace-last:首页` 等 localStorage 键），
 * 不是界面文案。换成「AI 探索」会让已有会话从列表里凭空消失。
 */
const TABS = [
  { id: 'chat', label: 'AI 对话', icon: MessagesSquare },
  { id: 'access', label: '接入设置', icon: SlidersHorizontal },
  // 第三片：把「屏幕上那些数怎么被外部智能体取到」摊开。用 ?tab= 而不是新路由 ——
  // 同一个路由挂两条导航项会被 frontend/test/modules.test.ts 判红（交易日志那一屏同理）。
  { id: 'mcp', label: 'MCP 服务', icon: Server },
] as const;
type TabId = (typeof TABS)[number]['id'];
export function Settings() {
  const workspace = useWorkspace();
  const [accessKey, setAccessKey] = useState(loadAccessKey());
  const [params, setParams] = useSearchParams();
  const fromUrl = params.get('tab');
  const tab: TabId = TABS.some(t => t.id === fromUrl) ? (fromUrl as TabId) : 'chat';
  const pick = (next: TabId) => {
    // tab 进 URL：别的页面「先去接入 AI」这类引导要能直接落到设置那一片。
    setParams(next === 'chat' ? {} : { tab: next }, { replace: true });
  };
  return <div className="space-y-6">
    <PageHeader title="AI 探索" subtitle="一个页面里既能和 AI 聊，也能管来源，也能把屏幕上那些数以 MCP 服务交给外部智能体；所选连接由首页、复盘、深挖与页面问答共用。" />
    <div role="tablist" aria-label="AI 探索" className="flex flex-wrap gap-2">
      {TABS.map(({ id, label, icon: Icon }) => <button key={id} type="button" role="tab" id={`ai-tab-${id}`}
        aria-selected={tab === id} aria-controls={`ai-panel-${id}`} onClick={() => pick(id)}
        className={cn('inline-flex items-center gap-2 rounded-xl border px-4 py-2.5 text-sm font-medium',
          tab === id ? 'border-primary bg-primary/10 text-primary' : 'border-border text-muted-foreground hover:bg-muted/50')}>
        <Icon className="h-4 w-4" />{label}
      </button>)}
    </div>
    {tab === 'chat'
      ? <div role="tabpanel" id="ai-panel-chat" aria-labelledby="ai-tab-chat"><WorkspaceChat onConfigureAccess={() => pick('access')} /></div>
      : tab === 'access'
      ? <div role="tabpanel" id="ai-panel-access" aria-labelledby="ai-tab-access" className="space-y-6">
        <section className="glass rounded-2xl p-5"><div className="mb-4 flex items-center justify-between"><h2 className="font-semibold">AI 来源</h2><AgentToggle /></div><AgentAccess onSaved={workspace.refresh} /></section>
        <SavedSources />
        {/* 这一段的口径（旧设置的去处 · 页面问答读什么 · 复盘与深挖怎么发起）折进通用说明框（用户 10-14）。
            下一行「后端访问密钥」那块本来就已经是收起来的 details，不再套第二层。 */}
        <ExplainBox title="旧设置的去处 · 页面问答读什么 · 复盘与深挖怎么发起">
          <p className="text-sm">旧版分散的 AI 设置不再用于产品调用；此前保存的单条来源已自动并入下面的来源列表，密钥没有被迁移到别处。页面问答在 Agent 关闭时仅阅读本次明确提供的材料；开启后可查询公开资料。完整复盘与个股深挖由各自的任务按钮明确发起。</p>
        </ExplainBox>
        <details className="glass rounded-2xl p-5"><summary className="cursor-pointer text-sm font-medium">后端访问设置</summary><div className="mt-4 space-y-3"><h3 className="flex items-center gap-2 text-sm"><KeyRound size={16} />后端访问密钥（可选）</h3><p className="text-xs text-muted-foreground">仅当后端设置了访问密钥时填写，本机默认可留空。保存在本浏览器中，清理网站数据会移除；这不是 AI 服务商密钥。</p><div className="flex gap-2"><input type="password" value={accessKey} onChange={e=>setAccessKey(e.target.value)} className="min-w-0 flex-1 rounded-lg border border-border bg-card px-3 py-2" aria-label="后端访问密钥" /><button className="rounded-lg bg-primary px-4 py-2 text-primary-foreground" onClick={()=>{try { saveAccessKey(accessKey.trim()); workspace.refresh(); toast.success('已保存后端访问设置'); } catch(e) { toast.error(e instanceof Error ? e.message : '保存失败'); }}}>保存</button></div></div></details>
      </div>
      : <div role="tabpanel" id="ai-panel-mcp" aria-labelledby="ai-tab-mcp"><McpServicePanel /></div>}
  </div>;
}
