import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { classify, emPath, emUrl, normalize, routeIdOf } from '../src/lib/quoteLink.ts';

/**
 * 前端这份镜像必须与 `app/quote_link.py` 逐码一致。
 * 真值表 `emUrl.fixtures.json` 由 `python scripts/ops/gen_em_link_fixtures.py` 生成，
 * 不要手改；`pytest tests/test_quote_link.py` 会验证同一张表没落后于后端本体。
 */
const fixtures = JSON.parse(
  readFileSync(new URL('./emUrl.fixtures.json', import.meta.url), 'utf-8'),
) as { cases: Record<string, string | null> };

test('镜像与后端真值表逐码相等', () => {
  const drift = Object.entries(fixtures.cases)
    .filter(([code, url]) => emUrl(code) !== url)
    .map(([code, url]) => `${code}: 表=${url} 前端=${emUrl(code)}`);
  assert.deepEqual(drift, [], '前后端链接规则漂了，重跑生成脚本前先确认改的是哪一边');
});

test('北交所是个股页要带斜杠，指数走 zs（这两条最容易写错）', () => {
  assert.equal(emUrl('920000.BJ'), 'https://quote.eastmoney.com/bj/920000.html');
  assert.equal(emUrl('899050.BJ'), 'https://quote.eastmoney.com/zs899050.html');
  // 老码段换码后 404，宁可不给链接
  assert.equal(emUrl('430047.BJ'), null);
});

test('同一串代码在市场不同时报到不同页面，认不出的不拼地址', () => {
  assert.equal(emUrl('000001.SH'), 'https://quote.eastmoney.com/zs000001.html');   // 上证指数
  assert.equal(emUrl('000001.SZ'), 'https://quote.eastmoney.com/sz000001.html');   // 平安银行
  assert.equal(emUrl('880501.SH'), null);       // 通达信板块没有个股页
  assert.equal(emUrl('999999.SH'), null);       // 伪代码
  assert.equal(emUrl('60051'), null);          // 位数不对
  assert.equal(emUrl(''), null);
  assert.equal(emUrl(null), null);
  assert.equal(emPath('一段中文'), null);
});

test('三种写法归一，裸 6 位按代码段猜市场', () => {
  assert.deepEqual(normalize('sh600367'), ['600367', 'SH']);
  assert.deepEqual(normalize('600367.SH'), ['600367', 'SH']);
  assert.deepEqual(normalize('600367'), ['600367', 'SH']);
  assert.equal(emUrl('002827'), 'https://quote.eastmoney.com/sz002827.html');
  assert.equal(classify('510300', 'SH'), 'fund');
  assert.equal(classify('600519', 'SH'), 'stock');
});

// ---------------------------------------------------------------- 按市场可配置（用户 2026-10-08）
test('行归属与后端同一套顺序：指数→北证→创业板→科创板→交易所兜底', () => {
  assert.equal(routeIdOf('000001.SH'), 'index');
  assert.equal(routeIdOf('899050.BJ'), 'index');          // 北证50 走 zs，不走 bj/
  assert.equal(routeIdOf('920779.BJ'), 'bj');
  assert.equal(routeIdOf('430047.BJ'), null, '老码段没页面，宁可不给链接');
  assert.equal(routeIdOf('301234.SZ'), 'cyb');
  assert.equal(routeIdOf('688981.SH'), 'kcb');
  assert.equal(routeIdOf('510300.SH'), 'sh');             // 基金跟着交易所
  assert.equal(routeIdOf('880501.SH'), null);
});

test('给了模板就按模板拼，只替换 ****** 那一段', () => {
  const t = { bj: 'https://example.test/bj/******.html' };
  assert.equal(emUrl('920779.BJ', t), 'https://example.test/bj/920779.html');
  assert.equal(emUrl('600519.SH', t), 'https://quote.eastmoney.com/sh600519.html', '别的行不受影响');
  // 用户没改过（后端整张表 = 出厂值）时，与不传模板必须一模一样
  assert.equal(emUrl('000001.SZ', { sz: 'https://quote.eastmoney.com/sz******.html' }), emUrl('000001.SZ'));
});

test('模板写坏了就退回出厂口径，绝不拼一条没有代码的死链', () => {
  for (const bad of ['https://x/y.html', 'https://x/******/******.html', 'http://x/******.html', 'x ******.y']) {
    assert.equal(emUrl('920779.BJ', { bj: bad }), 'https://quote.eastmoney.com/bj/920779.html', bad);
  }
  assert.equal(emUrl('920779.BJ', null), 'https://quote.eastmoney.com/bj/920779.html', '还没拿到配置时按出厂拼');
});

test('拒收原因原样上屏：星号占位不许被粗体清洗吃掉', () => {
  // 后端那句原因里同时有 `**恰好一个**`（粗体）和 `******`（代码占位，是数据）。
  // 共用出口 plain() 原来一把剥掉所有 `**`，结果页面上变成"要恰好一个 作为代码占位"、
  // 样例地址变成 `bj/.html` —— 恰好把用户要照抄的那一段吃了。
  // `agent.ts` 的 import 是不带扩展名的，node 直跑不了它，所以这里把它那条正则原样抓出来跑。
  const src = readFileSync(new URL('../src/lib/agent.ts', import.meta.url), 'utf8');
  const body = /export function plain[\s\S]*?\n}/.exec(src)?.[0] ?? '';
  const lit = /^\/(.*)\/([a-z]*)$/.exec(/\.replace\((\/(?:\\.|[^\\/])*\/[a-z]*)/.exec(body)?.[1] ?? '');
  assert.ok(lit, `没从 plain() 里抓到那条正则，函数写法变了：${body.slice(0, 120)}`);
  const strip = (s: string) => s.replace(new RegExp(lit[1], lit[2].includes('g') ? 'g' : ''), '');
  const why = '地址里要**恰好一个** ****** 作为代码占位（现在 2 个），例如 https://quote.eastmoney.com/bj/******.html；这一笔整条没写入，仍按原值运行';
  assert.equal(strip(why), '地址里要恰好一个 ****** 作为代码占位（现在 2 个），例如 https://quote.eastmoney.com/bj/******.html；这一笔整条没写入，仍按原值运行');
});

test('前后端的行集合与默认模板是同一份（配置面板按这个列表画行）', () => {
  const py = readFileSync(new URL('../src/lib/quoteLink.ts', import.meta.url), 'utf8');
  assert.match(py, /routeIdOf/, '镜像要有"属于哪一行"这一步，否则前端只能自己猜');
  const store = readFileSync(new URL('../src/lib/quoteLinkTemplates.ts', import.meta.url), 'utf8');
  assert.match(store, /\/api\/config\/quote-links/, '配置要有出口：整页拉一次，不逐个渲染点回后端');
  // 2026-10-26 首页提速实测到的洞：`inflight` 只挡得住"同一拍里的并发"，而**每个新挂载的
  // StockLink 都会在 effect 里调一次 loadLinks** —— 翻到下一页＝一整批新格子＝一整发配置请求。
  // 拉过之后要用模块级那份 snapshot 挡住，而失败时 snapshot 仍是 null ⇒ 下一发照常重试。
  assert.match(store, /if \(!force && snapshot\) return snapshot;/,
    '拿到过就不许再打后端（分页/换筛选每次都重取配置就是这个成因）');
  assert.ok(store.indexOf('if (!force && snapshot)') < store.indexOf('if (inflight && !force)'),
    '两条闸的顺序必须是先看 snapshot 再看 inflight，否则每次挂载还是要重新起一发');
  const panel = readFileSync(new URL('../src/components/QuoteLinksPanel.tsx', import.meta.url), 'utf8');
  assert.match(panel, /saveLink\(/, '每一行真的提交，不是只读的文字');
  assert.match(panel, /row\.default\)/, '改坏了要能一键回出厂');
  assert.match(panel, /sample_url/, '每行给一个按当前模板拼出来的样例');
  const link = readFileSync(new URL('../src/components/ui/StockLink.tsx', import.meta.url), 'utf8');
  assert.match(link, /emUrl\(code, templates\)/, '渲染点真的用上了模板');
});
