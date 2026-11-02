import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import test from 'node:test';
import { fileURLToPath } from 'node:url';
import {
  readPageCrumb, registerPageCrumb, releasePageCrumb, samePageCrumb, subscribePageCrumb,
} from '../src/lib/workspace/pageCrumbStore.ts';

/**
 * 顶栏「工作空间 / 个股深度复盘 · 代码 名称」那一条的后端侧（登记表）与接线侧。
 * 用户 2026-10-28：「"现在复盘的是哪一只"这个展示框不要了。在页面最上方
 * "工作空间 / 个股深度复盘"后面，动态的跟上个股代码和名称。」
 *
 * 钉的都是"写错了不报错、屏幕上却指错一只票"那一类：换页没放掉、晚到的清理抹掉新页、
 * 以及每次渲染都通知导致顶栏跟着自动刷新抖。
 */

const HERE = path.dirname(fileURLToPath(import.meta.url));
const read = (rel: string) => fs.readFileSync(path.join(HERE, rel), 'utf8');

test('登记表：一次只有一屏在册，只有持有者能放，内容没变不通知', () => {
  const a = {}; const b = {};
  let hits = 0;
  const off = subscribePageCrumb(() => { hits += 1; });

  assert.equal(readPageCrumb(), null, '起始状态得是"没有对象"，顶栏不自己猜一只票');
  assert.equal(registerPageCrumb(a, { text: '600519.SH 贵州茅台' }), true);
  assert.equal(hits, 1);
  assert.equal(registerPageCrumb(a, { text: '600519.SH 贵州茅台' }), false,
    '同一份内容重复登记不许通知：页面每次渲染都跑这个 effect，通知一次顶栏抖一次');

  // 另一屏来登记：覆盖（一次只有一屏在册）
  assert.equal(registerPageCrumb(b, { text: '000001.SZ 平安银行' }), true);
  assert.equal(readPageCrumb()?.text, '000001.SZ 平安银行');

  // 晚到的清理：A 页卸载钩子在 B 页登记之后才跑，不许把 B 那份抹掉
  const hitsBefore = hits;
  assert.equal(releasePageCrumb(a), false, '拿别页的 token 来放是无效的');
  assert.equal(readPageCrumb()?.text, '000001.SZ 平安银行', '顶栏不许被别人的卸载钩子清空');
  assert.equal(hits, hitsBefore, '没放成就不许通知');

  assert.equal(registerPageCrumb(b, null), true, '自己那份传 null = 这一屏此刻没有对象');
  assert.equal(readPageCrumb(), null);
  off();
});

test('hint 换字也要通知（只有代码时那一句"名称为什么没跟上"是给人看的）', () => {
  const t = {};
  registerPageCrumb(t, { text: 'AAPL', hint: '本机证券字典里没有这一只' });
  assert.equal(samePageCrumb({ text: 'AAPL', hint: '本机证券字典里没有这一只' }, { text: 'AAPL' }), false);
  assert.equal(registerPageCrumb(t, { text: 'AAPL', hint: '证券字典读不到：查询失败' }), true);
  assert.match(readPageCrumb()!.hint!, /读不到/);
  releasePageCrumb(t);
});

test('接线：页面登记、顶栏画，两处都真接上了（不是只写了登记函数）', () => {
  const page = read('../src/pages/StockReview.tsx');
  const layout = read('../src/components/layout/Layout.tsx');
  const glue = read('../src/lib/workspace/pageCrumb.ts');

  assert.match(page, /import \{ usePageCrumb \} from "@\/lib\/workspace\/pageCrumb"/);
  assert.match(page, /usePageCrumb\(code \? \{/, '没选一只时不许登记（登记了就等于顶栏挂着一只谁都没选的票）');
  assert.match(page, /text: `\$\{code\}\$\{name \? ` \$\{name\}` : ''\}`/,
    '名称读不到时只有代码，不许留上一只票的名字');
  assert.match(page, /"\/quant\/security-search"/, '名称要从证券字典现读，页面不自己养第二张代码→名称的表');
  assert.ok(!page.includes('<h2 className="text-sm font-bold">现在复盘的是哪一只</h2>'),
    '那块展示框已经撤掉，不许长回来');
  // 只认 import 那一行：注释里本来就要写"那块 GlassCard 撤了"，按整份文本比会把解释当成违规
  assert.ok(!/^import .*GlassCard.*$/m.test(page), '展示框撤了，卡片外壳的 import 也要一起走（留着就是 tsc 的未使用报错）');
  assert.match(page, /还没选一只，这一页不替你挑/, '引导句留在屏上：收进弹窗等于没有');

  assert.match(glue, /useSyncExternalStore\(subscribePageCrumb, readPageCrumb, readPageCrumb\)/);
  assert.match(glue, /useEffect\(\(\) => \(\) => \{ releasePageCrumb\(token\); \}, \[token\]\)/,
    '卸载要放掉自己那份');

  assert.match(layout, /const crumb = useReadPageCrumb\(\)/);
  assert.match(layout, /\{title\}<\/strong>\{crumb && /, '顶栏要在标题后面跟着这一屏的对象');
  assert.match(layout, /title=\{crumb\.hint\}/, '名称为什么没跟上要能在悬停里问到');
});
