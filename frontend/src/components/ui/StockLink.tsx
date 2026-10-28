import type { ReactNode } from 'react';
import { emUrl } from '@/lib/quoteLink';
import { useLinkTemplates } from '@/lib/quoteLinkTemplates';

/**
 * 股票名称 → 东方财富行情页外链。
 *
 * 规则本体 `app/quote_link.py`，浏览器镜像 `lib/quoteLink.ts`，共同真值表
 * `test/emUrl.fixtures.json`。认不出的代码（板块、伪代码、脏串）**退回纯文本**，
 * 不拼一条可能 404 的地址出去 —— 少一个链接可以接受，点开了是死页更糟。
 *
 * 一律新标签打开：看盘时没人希望手一抖把当前这一页换掉。
 */
export function StockLink({ code, name, className, children }: {
  code?: string | null;
  name?: string | null;
  className?: string;
  children?: ReactNode;
}): ReactNode {
  const label = children ?? ((name && String(name).trim()) || String(code ?? ''));
  // 按市场的模板（系统配置那张表）。没拿到之前按出厂口径拼，链接不会因为配置读不到而消失。
  const templates = useLinkTemplates();
  const url = emUrl(code, templates);
  if (!url || !label) return <>{label}</>;
  return (
    <a href={url} target="_blank" rel="noopener noreferrer" title={`在东财查看 ${code}`}
       onClick={(e) => e.stopPropagation()}
       className={className ?? 'text-foreground underline decoration-dotted underline-offset-2 hover:text-primary'}>
      {label}
    </a>
  );
}
