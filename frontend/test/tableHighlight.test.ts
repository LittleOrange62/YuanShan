import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import test from 'node:test';
import { fileURLToPath } from 'node:url';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const read = (p: string) => fs.readFileSync(path.join(HERE, p), 'utf8');

/**
 * 「单击一格 → 整行＋整列高亮」这一份共用实现（`lib/useTableInteraction.ts`）。
 *
 * 2026-10-26 换了实现方式：选中态**不再进 React**。起因是量出来的一个数 ——
 * `/volume-double` 那张 25 行 × 32 列（800 格）的表上，**一次单击记到 461 毫秒的长任务**，
 * 而 React 把第一格的改动真正提交出去只要 6~10 毫秒：钱全花在"整表重新协调一遍"，
 * 不在渲染。行级 `React.memo` 救不了（列高亮会动到每一行的那一格 ⇒ 每行 props 都变）。
 *
 * 现在的形状：每格的 className 与选中态无关（静态），点击时在 DOM 上给
 * 行／列／交叉那一格各标一个类，颜色只住 `src/index.css`。
 * 这一份钉的是"别再退回每格算 className"那条路，不是某个具体写法。
 */

const hook = read('../src/lib/useTableInteraction.ts');
const block = read('../src/lib/useStockRowInteraction.ts');
const css = read('../src/index.css');

test('选中态不进 React：一次单击不许把整表重渲染一遍', () => {
  assert.doesNotMatch(hook, /const \[selectedCell, setSelectedCell\] = useState/,
    '退回 useState 就等于把 461 毫秒那个长任务请回来（判据见文件头上那段）');
  assert.match(hook, /const selRef = useRef<CellSel \| null>\(null\)/,
    '选中态住 ref，点击不触发渲染');
  // 每格那份 className 必须与选中无关（这是"不重渲染"能成立的前提）
  assert.match(hook, /const getCellClass = useCallback\(\(_row: number, _col: string, baseClass\?: string\) =>\s*\r?\n\s*cn\(baseClass, "cursor-pointer hover:bg-primary\/5"\)/,
    'getCellClass 不许再读选中态；它一读，整表就跟着点一次重算一次');
  assert.match(hook, /const getRowClass = useCallback\(\(_row: number, baseClass\?: string\) => cn\(baseClass\)/,
    '整行同理（行高亮也走 DOM 那个类）');
});

test('点击只认被点那一格自己，不靠行列号凑选择器', () => {
  // 一屏多张表（倍量页两块、分组自选几张、首页几块）—— 行列号在两张表里是两回事，
  // 只有那一格知道自己属于哪张 `<table>`。
  assert.match(hook, /handleCellClick = useCallback\(\(target: HTMLTableCellElement, row: number, col: string\)/,
    '被点那一格是必交参数（调用点从 e.currentTarget 拿）');
  assert.match(hook, /td\.closest\("table"\)/, '清场与上色都限定在这一格所在的那张表里');
  assert.match(hook, /const colIdx = td\.cellIndex/, '整列靠格序定位，不靠列名拼选择器');
});

test('行、列、交叉那一格各标一个类，颜色只住样式表一处', () => {
  for (const name of ['XL_ROW', 'XL_COL', 'XL_CELL']) {
    assert.match(hook, new RegExp(`export const ${name} = "xl-(row|col|cell)"`), `${name} 要在 hook 里导出`);
  }
  assert.match(hook, /classList\.add\(XL_ROW\)/);
  assert.match(hook, /classList\.add\(XL_COL\)/);
  assert.match(hook, /classList\.add\(XL_CELL\)/);
  assert.match(hook, /el\.classList\.remove\(XL_ROW, XL_COL, XL_CELL\)/,
    '清场必须三个一起清，漏一个就留下"上一格还亮着"');
  // 颜色不许写在 hook 的 className 里（utilities 与作者样式同层、顺序不可靠，靠 td.xl-* 的权重）
  assert.match(css, /td\.xl-row \{ background-color: hsl\(var\(--primary\) \/ \.05\)/);
  assert.match(css, /td\.xl-col \{ background-color: hsl\(var\(--primary\) \/ \.05\)/);
  assert.match(css, /td\.xl-cell \{ background-color: hsl\(var\(--primary\) \/ \.10\)/);
  assert.doesNotMatch(hook.slice(0, hook.indexOf('export function useTableFilter')), /bg-primary\/10/,
    '交叉那一格的深色只许住在样式表，不许在 hook 里再拼一份 utility');
});

test('换排序／翻页／换筛条件那条清选中的路仍然连着 DOM（不然高亮跟着行号漂）', () => {
  assert.match(hook, /const clearSelection = useCallback\(\(\) => \{\s*\r?\n\s*selRef\.current = null;\s*\r?\n\s*if \(tableRef\.current\) clearPaint\(tableRef\.current\)/,
    '清选中必须同时清掉 DOM 上那三个类 —— 只清状态等于没清');
  assert.match(block, /cell\.clearSelection\(\)/, '共用块那份 clearAll 仍然连着这条');
  assert.match(block, /onClick: \(e: MouseEvent<HTMLTableCellElement>\) => cell\.handleCellClick\(e\.currentTarget, i, k\)/,
    'cellProps 把被点那一格交出去（24 个调用点不用各改，但这条链断不得）');
});

test('要读「现在选中哪一格」的，只能订阅、并且圈在子组件里', () => {
  assert.match(hook, /export function useCellSelection\(store: CellStore\)/,
    '读选中态的出口是一份可订阅的存储，不是 hook 的返回值');
  assert.match(hook, /useSyncExternalStore\(store\.subscribe, store\.get, store\.get\)/);
  assert.match(block, /export \{ useCellSelection, type CellSel, type CellStore \} from "@\/lib\/useTableInteraction"/,
    '页面从共用块这一层拿（低一层那份只给共用块自己用，规矩钉在 groupedDossier.test.ts）');
  // 两个读者各自都把订阅关在小组件里
  const confluence = read('../src/pages/Confluence.tsx');
  const grouped = read('../src/pages/Grouped.tsx');
  for (const [name, src, fn] of [['选股汇总', confluence, 'FocusPane'], ['分组自选', grouped, 'SelectedHint']] as const) {
    assert.match(src, new RegExp(`function ${fn}\\(`), `${name} 那处读选中态的子组件不见了`);
    const head = src.slice(0, src.indexOf(`function ${fn}`));
    assert.doesNotMatch(head, /useCellSelection\(/,
      `${name}：订阅提到了页面组件本体 —— 单击又会重渲染整张表`);
  }
});
