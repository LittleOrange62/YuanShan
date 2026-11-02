import { createContext, useContext, useEffect, useRef, useState, type ReactNode } from 'react';
import { createPortal } from 'react-dom';
import { cn } from '@/lib/utils';

/**
 * 说明抽屉（2026-10-28 用户点名）：每一块右上角一个「说明」按钮，点开在**屏幕下方**
 * 弹出一只扁长抽屉，把这一块所有说明性文字（口径 · 字段解释 · 算例 · 定义表）搬进去，
 * 主图与数字留在原处不被文字挤掉。
 *
 * 四条设计取舍，都是容易做错的地方：
 *  1. **portal 到 body**：外层是带圆角与 `overflow` 的卡片，就地渲染会被裁掉半截，
 *     而"弹窗只露出一半"比不弹更糟。
 *  2. **不锁滚动、不做模态**：读者点开是要「边看图边读说明」，锁住滚动等于逼他在
 *     两者之间选一个；所以 `aria-modal=false`、不做焦点圈禁，只保证 Esc 与 X 都能关，
 *     关闭后焦点回到那个按钮（键盘用户不至于被丢在页面顶部）。
 *  3. **高度 40vh、宽度占满**：用户原话「扁一点、长一点」，纵向留给文字行数，
 *     横向占满内容区；`min-h` 兜住空内容时缩成一条缝。
 *  4. **不许把该留在屏幕上的东西塞进来**：缺数声明 / 截断上限 / 告警这三类走
 *     `NoticeBar`，永远留在块内（同一条纪律见 `NoticeBar` 顶部注释）。
 */
export type DocDrawerProps = {
  /** 抽屉标题：就用这一块在屏幕上的标题，两处不同词会让读者不确定他说的是哪一块 */
  title: string;
  label?: string;
  children: ReactNode;
};

/**
 * 这一块在屏幕上的标题。由 `Panel` 往下发（`DataPanel.tsx` 那一处），说明按钮与抽屉标题
 * 都从这儿拿 —— 逐块再抄一遍标题字符串，改天两处就会分叉成「屏上叫 A、弹窗叫 B」。
 */
export const BlockTitleCtx = createContext<string | null>(null);
export const useBlockTitle = (): string | null => useContext(BlockTitleCtx);

/**
 * 「此刻已经在一口抽屉里面」。抽屉体里如果还套着说明框（字段字典那种），
 * 它必须改成分节标题而不是再开一口抽屉 —— 弹窗里长出一个「点开另一口弹窗」的按钮，
 * 读者按下去会以为回到了页面，实际是同一块内容换了个壳。
 */
export const InsideDrawerCtx = createContext(false);

/** 按钮上那几个字全站只住这一处：逐块自己写会漂，漂了就没人知道这些按钮是不是同一个东西 */
export const DEFAULT_DOC_LABEL = '这块的说明';

/**
 * 两块的**块名**撞车时（首页「资金轮动 · 份额口径（本地逐日）」与「资金轮动 · 今日即时」是两把尺，
 * 截到「·」前就成了两颗同名按钮），调用方交小节名，前缀仍住这一处。
 */
export const docLabel = (section: string) => `${DEFAULT_DOC_LABEL} · ${section}`;

/**
 * 一块里有多处出口时（口径、字段义、覆盖、出处…）按钮**不能都叫同一个名字**。
 * 10-29 全站把说明搬进抽屉之后，`/stock-review` 那一屏量出过：量化档案一块 3 颗、相似样本 4 颗、
 * 窗口趋势 4 颗，全是同一串「这块的说明」，悬停才分得出各管哪一节 —— 四颗同名按钮摆在一起，
 * 读者只知道"这里有说明"，不知道点哪一颗才是自己要的那一段（用户 10-29：一个模块一颗；
 * 真要把几处合成一颗得改的是各块的出口结构，那属于另一轮）。
 * 做法：标题本来就是「块名 · 那一节」拼出来的（`ExplainBox`/`CaliberFold` 都这么交），
 * 所以把「·」后面那截拿来当按钮上的小节名，取不到就用默认那串。
 */
export function docLabelFor(title: string, block: string | null, explicit?: string): string {
  if (explicit) return explicit;
  const prefix = block ? `${block} · ` : '';
  let section = '';
  if (prefix && title.startsWith(prefix)) {
    section = title.slice(prefix.length).trim();            // 「块名 · 那一节」⇒ 取后一半
  } else if (!block || title.trim() !== block.trim()) {
    section = title.trim();                                  // 自己起了名（`<DocDrawer title="这一块的口径">`）就用它
  }
  // 节名要能当按钮用：括号里的补充、后半句、冒号后的解释一律切掉；切完还太长就退回默认串
  //（一长串按钮文字会把标题那一行挤成两行，比同名更难读）。
  section = section.split(/[（(：:；;·]/)[0].trim();
  if (section.length === 0 || section.length > 12) return DEFAULT_DOC_LABEL;
  return `${DEFAULT_DOC_LABEL} · ${section}`;
}

/**
 * 抽屉那一行标题（以及悬停、`aria-label`）怎么拼。**10-29 实测过的一条丑话**：
 * `Panel` 那颗固定叫「这一屏的口径说明」，拼上后缀就成了「…这一屏的口径说明的说明」——
 * 全站 124 处出口一起念错，所以补一次就够，且只在**结尾已经是「说明」时**不补。
 * 后缀里的 ` · ` 也一起收在这儿：逐块各拼一遍迟早出现「XX说明」「XX的说明」「XX · 说明」三种写法。
 */
const endsWithDoc = (t: string) => t.trim().endsWith('说明');
export const docHeading = (title: string) => (endsWithDoc(title) ? title : `${title} · 说明`);

/**
 * 同一时刻只留一口抽屉。抽屉是贴屏底固定的，两块同时点就会叠在一起 ——
 * 读者看到的那份说明不知道属于上面哪一块，而挡住的那口既没关掉也点不着。
 * 做法是把"当前开着的那口的关闭函数"记在模块里，新开的先把旧的收掉（焦点不抢，
 * 因为旧的那口是被程序关的，不走 close() 那条归还焦点的路）。
 */
let openDrawer: (() => void) | null = null;

export function DocDrawer({ title, label, children }: DocDrawerProps) {
  const [open, setOpen] = useState(false);
  const trigger = useRef<HTMLButtonElement>(null);
  const heading = docHeading(title);
  // 小节名从「块名 · 那一节」那个拼接式里取（块名由 `Panel`/`GlassCard docTitle` 往下发）
  const shownLabel = docLabelFor(title, useBlockTitle(), label);

  useEffect(() => {
    if (!open) return;
    const prev = openDrawer;
    if (prev && prev !== myClose.current) prev();
    openDrawer = () => { if (openDrawer === myClose.current) openDrawer = null; setOpen(false); };
    myClose.current = openDrawer;
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') setOpen(false); };
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('keydown', onKey);
      if (openDrawer === myClose.current) openDrawer = null;
    };
  }, [open]);

  const myClose = useRef<(() => void) | null>(null);
  const close = () => { setOpen(false); trigger.current?.focus(); };

  return (
    <>
      <button type="button" ref={trigger} aria-expanded={open}
        onClick={() => setOpen(v => !v)}
        className="rounded border border-border/70 px-1.5 py-0.5 text-[10px] text-muted-foreground hover:bg-muted/50 hover:text-foreground"
        title={open ? `收起${heading}` : `展开${heading}（口径 · 字段解释 · 算例）`}>
        {shownLabel}
      </button>
      {open && createPortal(
        <div className="fixed inset-x-0 bottom-0 z-50" role="dialog" aria-modal="false" aria-label={heading}>
          <div aria-hidden onClick={() => close()}
            className="absolute inset-0 -z-10 bg-background/40 backdrop-blur-[1px]" title="点空白处关闭" />
          <section className="mx-auto flex h-[40vh] min-h-[9rem] w-full flex-col border-t border-border bg-background/95 shadow-2xl">
            <header className="flex shrink-0 items-center gap-2 border-b border-border px-3 py-1.5">
              <h2 className="flex-1 truncate text-[12px] font-semibold">{heading}</h2>
              <p className="hidden shrink-0 text-[10px] text-muted-foreground sm:block">Esc 或点空白处也能关</p>
              <button type="button" onClick={() => close()} aria-label={`关闭${heading}`}
                className="shrink-0 rounded border border-border px-2 py-0.5 text-[11px] hover:bg-muted/60">
                关闭 X
              </button>
            </header>
            <div className="min-h-0 flex-1 overflow-y-auto overscroll-contain px-3 py-2 text-[11px] leading-relaxed">
              <InsideDrawerCtx.Provider value>{children}</InsideDrawerCtx.Provider>
            </div>
          </section>
        </div>,
        document.body,
      )}
    </>
  );
}

/** 抽屉里给每一段说明起一个小标题；不给标题就没法在长文里定位。 */
export function DrawerSection({ heading, children, className }: {
  heading: string; children: ReactNode; className?: string;
}) {
  return (
    <div className={cn('mb-3 border-b border-border/50 pb-2 last:mb-0 last:border-0', className)}>
      <h3 className="mb-1 text-[11px] font-semibold text-foreground">{heading}</h3>
      <div className="text-muted-foreground">{children}</div>
    </div>
  );
}
