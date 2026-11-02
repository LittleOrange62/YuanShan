import type { ReactNode } from 'react';
import { plain } from '@/lib/agent';
import { cn } from '@/lib/utils';

/**
 * 三类"读数诚实性"声明（2026-10-28 用户定稿）：**该事实不存在就整条不渲染，
 * 存在就必须留在块内** —— 不许折进说明抽屉、也不许套进任何默认收起的说明框。
 *
 * 为什么单独立一个组件而不是继续散着写 `<p>`：这三类是屏幕上唯一"改了就会让页面说假话"
 * 的文字，散着写迟早出现"只画了 40 行却说不出行数上限"那一类条件与真实预算脱钩的缺陷
 * （`/grouped` 今天刚修的那处 `> 150` 就是标本）。这里把"有才显示"这件事收成一个口，
 * 调用方只负责给条件与一句话。
 *
 * 三种类型各自回答什么：
 *  - `missing`  缺数：这一格/这一列是**没读到**，不是 0、不是"没有"。要说清是哪一族缺。
 *  - `truncated` 截断上限：交出的行数有顶，屏上看到的不是全部（分母也要一起给）。
 *  - `warning`  告警：两把尺对不上、弹窗被拦、判定分叉这类"需要人知道"的事。
 *
 * 措辞纪律：一句话，必要时带一个数；精简不等于删事实。中文串里别嵌 ASCII 双引号。
 */
export type NoticeKind = 'missing' | 'truncated' | 'warning';

const TONE: Record<NoticeKind, string> = {
  missing: 'text-muted-foreground border-border/60',
  // 截断不是坏消息，但必须看得见 —— 用中性色，不抢告警的颜色语义
  truncated: 'text-muted-foreground border-border/60',
  warning: 'text-warning border-warning/40 bg-warning/5',
};

const TAG: Record<NoticeKind, string> = { missing: '缺数', truncated: '上限', warning: '注意' };

export function NoticeBar({ kind, when = true, children, className }: {
  kind: NoticeKind;
  /** 条件为假整条消失（不渲染一条「今天没问题」占位） */
  when?: boolean;
  children: ReactNode;
  className?: string;
}) {
  // 空内容也当假：传了 `when` 却没话可说时不许画一只空框
  if (!when || children == null || children === '' || (Array.isArray(children) && children.length === 0)) return null;
  // 后端散文字段（`caliber_note`、各种 `reason`）常常带着成对粗体上屏 ⇒ 屏幕上就是两只星号。
  // 剥星号这件事全站只在这一道出口做（`plain()` 只剥**成对**粗体，连号星号是数据、原样留着），
  // 逐块各剥一次迟早漏一块 —— 10-29 实测「涨停分析」那一块念后端 `caliber_note` 时就漏过。
  // JSX 那种（调用方自己排的富文本）不动。
  const text = typeof children === 'string' ? plain(children) : children;
  return (
    <p role="status"
      className={cn('mt-1 flex gap-1.5 rounded border border-dashed px-2 py-1 text-[10px] leading-relaxed',
        TONE[kind], className)}>
      <b className="shrink-0 font-semibold">{TAG[kind]}</b>
      <span className="min-w-0">{text}</span>
    </p>
  );
}

/** 一批条件声明：调用方只管列「什么情况说什么」，不渲染的事实在这里被丢掉。 */
export function NoticeList({ items, className }: {
  items: { kind: NoticeKind; when?: boolean; text: ReactNode }[];
  className?: string;
}) {
  const live = items.filter(x => x.when !== false && x.text != null && x.text !== '');
  if (!live.length) return null;
  return (
    <div className={cn('space-y-0.5', className)}>
      {live.map((x, i) => <NoticeBar key={i} kind={x.kind} className="mt-0">{x.text}</NoticeBar>)}
    </div>
  );
}
