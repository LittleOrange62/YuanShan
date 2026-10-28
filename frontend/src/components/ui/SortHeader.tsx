import type { SortDir } from '@/lib/useSort';

/**
 * 可点排序的表头格。保留原来 `title` 里的口径说明（那些"两套判据、排序会分叉"的提醒
 * 是页面的一部分，不能被排序按钮吃掉），只在后面追加一句怎么点。
 *
 * `aria-sort` 一起给：读屏和键盘用户要能听到"当前按热度降序"。
 */
export function SortHeader({ label, sortKey, dir, onSort, title, className = 'font-normal' }: {
  label: string;
  sortKey: string;
  dir: SortDir | null;
  onSort: (key: string) => void;
  title?: string;
  className?: string;
}) {
  const mark = dir === 'asc' ? '↑' : dir === 'desc' ? '↓' : '↕';
  return (
    <th aria-sort={dir === 'asc' ? 'ascending' : dir === 'desc' ? 'descending' : 'none'}
        className={`${className} whitespace-nowrap p-0`}>
      <button type="button" onClick={() => onSort(sortKey)}
              title={`${title ? `${title}\n` : ''}点一下按「${label}」排序，再点翻向，第三次取消；缺数的行始终排在最后`}
              className="flex w-full items-center gap-1 px-2 py-1.5 text-left hover:text-foreground">
        <span>{label}</span>
        <span aria-hidden="true" className={dir ? 'text-primary' : 'text-muted-foreground/40'}>{mark}</span>
      </button>
    </th>
  );
}
