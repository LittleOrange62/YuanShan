// 本文件继承自上游 vibe-astock v1.1.0（Apache-2.0，见根 LICENSE），本机有实质性改动。
// 改动与归属清单：docs/上游改动与归属清单.md；本机自写部分：LICENSE.local.md。
import { useEffect, useRef, useState } from 'react';
import { Link, Outlet, useLocation } from 'react-router-dom';
import { ChevronsLeft, ChevronsRight, Github, Cog, ListChecks, Menu, Moon, SlidersHorizontal, Sparkles, Sun, X } from 'lucide-react';
import { cn } from '@/lib/utils';
import { useDarkMode } from '@/hooks/useDarkMode';
import { moduleFor, chatPageFor, navigationPath } from '@/lib/workspace/modules';
import { ModuleNav } from '@/components/workspace/ModuleNav';
import { WorkspaceProvider, useWorkspace } from '@/lib/workspace/state';
import { AgentToggle } from '@/components/workspace/AgentToggle';
import { EverestLogo } from '@/components/workspace/EverestLogo';
import { AiAnalysisPanel } from '@/components/workspace/AiAnalysisPanel';
import { WorkspaceChat } from '@/components/workspace/WorkspaceChat';
import { Dialog } from '@/components/workspace/Dialog';
import product from "../../../../product.json";
const APP_VERSION = `v${product.version}`;
function readCollapsed() { try { return localStorage.getItem('va-sidebar') === 'collapsed'; } catch { return false; } }
/** 右侧「AI分析」栏的收起状态（用户 2026-10-10 要它和左侧一样能收）：默认收起 = 不占用首屏宽度。 */
function readRailCollapsed() { try { return localStorage.getItem('va-ai-rail') !== 'expanded'; } catch { return true; } }
/** 侧栏底部运维区的三条入口共用同一款式的类串（只有最后一条留 8px 下边距） */
function opsCls(compact: boolean, last = false) {
  return cn(last ? 'mb-2' : 'mb-1', 'flex min-h-10 items-center gap-2 rounded-lg text-sm text-muted-foreground hover:bg-muted hover:text-foreground',
    compact ? 'justify-center' : 'px-3');
}
function Shell() {
  const { pathname, search } = useLocation();
  const state = useWorkspace();
  const { dark, toggle } = useDarkMode();
  const [collapsed, setCollapsed] = useState(readCollapsed);
  const [railCollapsed, setRailCollapsed] = useState(readRailCollapsed);
  const [mobileOpen, setMobileOpen] = useState(false);
  const [chatOpen, setChatOpen] = useState(false);
  const [storageError, setStorageError] = useState('');
  const navRef = useRef<HTMLElement>(null);
  const mainRef = useRef<HTMLElement>(null);
  const activeModule = moduleFor(pathname);
  // 面包屑标题：一级模块内的页面查 pages；底部运维区那三条不走 MODULES，只能显式给。
  // 不拿 chatPageFor() 顶替 —— 那是会话/恢复用的稳定标识，与界面标题是两套词（如 /backtest 显示「历史统计」而标识是「涨停样本统计」）。
  const title = activeModule?.pages.find(page => page.to === navigationPath(pathname))?.title
    ?? (pathname === '/settings' ? 'AI 探索' : pathname === '/system' ? '系统配置' : pathname === '/jobs' ? '任务调度' : '工作空间');
  useEffect(() => { setMobileOpen(false); setChatOpen(false); }, [pathname, search]);
  useEffect(() => {
    const query = window.matchMedia('(min-width: 768px)');
    const close = () => { if (query.matches) setMobileOpen(false); };
    query.addEventListener('change', close); return () => query.removeEventListener('change', close);
  }, []);
  useEffect(() => { navRef.current?.querySelector('[aria-current="page"]')?.scrollIntoView({ block: 'nearest' }); }, [pathname, collapsed]);
  /**
   * 滚动期间临时摘掉毛玻璃（2026-10-26，用户点名「首页滚起来很卡」）。
   * `backdrop-filter` 在滚动时每一帧都要重取一次背后的快照，是这一屏画不动的头号项；
   * 停手 160ms 后原样回来 —— 静止看着和以前一样，代价只在"正在滚的那半秒里糊得不明显"。
   * ⚠️ 这条**不走 React state**：一个滚动帧 setState 一次＝把画的活儿换成协调整棵树的活儿，
   *    只会更卡。只往 `<html>` 写一个属性，样式表那一行跟着开关。
   */
  useEffect(() => {
    const el = mainRef.current;
    if (!el) return;
    const root = document.documentElement;
    let idle = 0 as unknown as ReturnType<typeof setTimeout>;
    const onScroll = () => {
      root.dataset.vscroll = "1";
      clearTimeout(idle);
      idle = setTimeout(() => { delete root.dataset.vscroll; }, 160);
    };
    el.addEventListener('scroll', onScroll, { passive: true });
    return () => { el.removeEventListener('scroll', onScroll); clearTimeout(idle); delete root.dataset.vscroll; };
  }, []);
  const collapse = () => {
    try { localStorage.setItem('va-sidebar', collapsed ? 'expanded' : 'collapsed'); setCollapsed(!collapsed); setStorageError(''); }
    catch { setStorageError('无法保存侧栏选择。'); }
  };
  /** 右侧栏同款：选择只存浏览器本地（这是"这台机器怎么看盘"，不是页面数据） */
  const toggleRail = () => {
    try { localStorage.setItem('va-ai-rail', railCollapsed ? 'expanded' : 'collapsed'); setRailCollapsed(!railCollapsed); setStorageError(''); }
    catch { setStorageError('无法保存右侧栏选择。'); }
  };
  const sidebar = (compact: boolean, mobile = false) => <>
    <div className={cn('border-b border-border', compact ? 'px-2 py-5' : 'px-5 py-4')}>
      <div className="flex items-center justify-between gap-1"><Link to="/" aria-label="YuanShan 首页" className="flex items-center gap-2.5"><EverestLogo className="h-7 w-7 shrink-0 text-primary" />{!compact && <span className="workspace-brand text-lg font-semibold tracking-tight">Yuan<span className="text-primary">Shan</span></span>}</Link>
        {mobile && <button type="button" onClick={() => setMobileOpen(false)} aria-label="关闭导航" className="p-2"><X className="h-4 w-4" /></button>}</div>
      {!compact && <div data-ai-identity className="mt-2 space-y-1"><p className="text-[10px] leading-4 text-muted-foreground">A 股短线复盘与跟踪工作台</p><Link to="/settings" title={state.label} className="flex min-w-0 items-start gap-1 text-left text-[10px] leading-5 text-muted-foreground hover:text-primary"><span aria-hidden="true" className="mt-2 h-1 w-1 shrink-0 rounded-full bg-primary" /><span>{state.previouslyTested && (state.status === 'authenticated' || state.status === 'saved') ? `已接入AI：${state.name}` : state.label}</span></Link><AgentToggle /></div>}
      {compact && <div className="mt-3 flex justify-center"><AgentToggle compact /></div>}
    </div>
    <nav ref={mobile ? undefined : navRef} aria-label="产品导航" className={cn('min-h-0 flex-1 space-y-0.5 overflow-auto py-3', compact ? 'px-1.5' : 'px-3')}>
      <ModuleNav compact={compact} expandSidebar={collapse} navigate={() => {
        if (mobile) { setMobileOpen(false); requestAnimationFrame(() => mainRef.current?.focus()); }
      }} />
    </nav>
    <div className={cn('border-t border-border', compact ? 'p-1.5' : 'p-3')}>
      {/* 运维区：这三条管的是「这个程序本身」而不是「今天行情」，所以不进一级模块 */}
      <Link to="/settings" title="AI 探索" aria-label="AI 探索" aria-current={pathname === '/settings' ? 'page' : undefined} onClick={() => setMobileOpen(false)} className={opsCls(compact)}><Cog className="h-4 w-4" />{!compact && 'AI 探索'}</Link>
      <Link to="/system" title="系统配置" aria-label="系统配置" aria-current={pathname === '/system' ? 'page' : undefined} onClick={() => setMobileOpen(false)} className={opsCls(compact)}><SlidersHorizontal className="h-4 w-4" />{!compact && '系统配置'}</Link>
      <Link to="/jobs" title="任务调度" aria-label="任务调度" aria-current={pathname === '/jobs' ? 'page' : undefined} onClick={() => setMobileOpen(false)} className={opsCls(compact, true)}><ListChecks className="h-4 w-4" />{!compact && '任务调度'}</Link>
      <div className={cn('flex items-center text-muted-foreground', compact ? 'flex-col gap-3' : 'justify-between gap-2')}>
        <a href="https://www.douyin.com/" target="_blank" rel="noopener noreferrer" aria-label="外部主页（新标签页打开）" title="外部主页" className="p-1 hover:text-foreground"><Github className="h-3.5 w-3.5" /></a>
        {!mobile && <button type="button" onClick={collapse} aria-label={compact ? '展开侧栏' : '收起侧栏'} className="p-1">{compact ? <ChevronsRight className="h-3.5 w-3.5" /> : <ChevronsLeft className="h-3.5 w-3.5" />}</button>}
      </div>{storageError && <p role="alert" className="text-xs text-destructive">{storageError}</p>}
    </div>
  </>;
  return <>
    <a className="workspace-skip" href="#workspace-main" onClick={e => { e.preventDefault(); mainRef.current?.focus(); }}>跳到内容</a>
    <div className="flex h-dvh overflow-hidden">
      <aside aria-label="产品侧栏" className={cn('workspace-sidebar hidden shrink-0 flex-col md:flex', collapsed ? 'w-16' : 'w-64')}>{sidebar(collapsed)}</aside>
      <div className="flex min-w-0 flex-1 flex-col overflow-hidden">
        <header className="workspace-topbar flex min-h-16 shrink-0 items-center justify-between gap-2 px-4 md:px-8">
          <div className="flex min-w-0 items-center gap-3 text-xs"><button type="button" aria-label="打开导航" onClick={() => setMobileOpen(true)} className="p-2 md:hidden"><Menu className="h-4 w-4" /></button><span className="hidden text-muted-foreground sm:inline">工作空间 /</span><strong className="truncate font-medium">{title}</strong></div>
          <div className="flex shrink-0 items-center gap-2"><span className="hidden text-[10px] text-muted-foreground lg:inline">{APP_VERSION}</span><button type="button" onClick={toggle} aria-label={dark ? '切换为浅色' : '切换为深色'} className="rounded p-2 text-muted-foreground hover:bg-muted">{dark ? <Sun className="h-4 w-4" /> : <Moon className="h-4 w-4" />}</button>{pathname !== '/' && pathname !== '/settings' && <button type="button" onClick={() => setChatOpen(true)} className="ai-chat-trigger inline-flex items-center gap-1.5 rounded-full px-3 py-2 text-xs"><Sparkles className="h-4 w-4" />{state.enabled ? '问 Agent' : '问模型'}</button>}</div>
        </header>
        <main ref={mainRef} id="workspace-main" tabIndex={-1} className="min-h-0 flex-1 overflow-auto"><div className="workspace-content"><Outlet /></div></main>
      </div>
      {/* 右侧「AI分析」栏（用户 2026-10-10 要它跟左侧一样可收起）：
          开着 w-64，收起只留一条 36px 的把手 —— 收起的那 256px 全部还给中间那一列，
          倍量榜这种 16 列的宽表就是这么拿回不横向滚动的。
          窄屏（<1280px）本来就不塞进来：那样会把中间的数据表挤到横向滚动，读不动。 */}
      {railCollapsed ? (
        <aside aria-label="AI分析（已收起）" className="workspace-ai-rail hidden w-9 shrink-0 flex-col items-center gap-3 border-l border-border py-3 xl:flex">
          <button type="button" onClick={toggleRail} aria-label="展开 AI分析栏" title="展开 AI分析栏"
                  className="rounded p-1.5 text-muted-foreground hover:bg-muted hover:text-foreground">
            <ChevronsLeft className="h-4 w-4" />
          </button>
          <Sparkles className="h-4 w-4 shrink-0 text-primary" />
          <span className="text-[10px] tracking-[0.18em] text-muted-foreground" style={{ writingMode: 'vertical-rl' }}>AI分析</span>
        </aside>
      ) : (
        <aside aria-label="AI分析" className="workspace-ai-rail hidden w-64 shrink-0 flex-col xl:flex"><AiAnalysisPanel onCollapse={toggleRail} /></aside>
      )}
    </div>
    {mobileOpen && <Dialog titleId="mobile-nav-title" close={() => setMobileOpen(false)} className="max-w-xs"><h2 id="mobile-nav-title" className="sr-only">产品导航</h2><div className="workspace-sidebar flex h-[85dvh] flex-col">{sidebar(false, true)}</div></Dialog>}
    {chatOpen && pathname !== '/' && pathname !== '/settings' && <Dialog titleId="page-chat-title" close={() => setChatOpen(false)} className="max-w-2xl"><div className="flex items-center justify-between px-5 py-3"><h2 id="page-chat-title" className="text-sm font-semibold">页面对话</h2><button type="button" onClick={() => setChatOpen(false)} aria-label="关闭页面对话" className="p-2"><X className="h-4 w-4" /></button></div><WorkspaceChat key={pathname + search} page={chatPageFor(pathname)} /></Dialog>}
  </>;
}
export function Layout() { return <WorkspaceProvider><Shell /></WorkspaceProvider>; }
