import test from 'node:test';
import assert from 'node:assert/strict';
import { buildContext, CONTEXT_BUDGET, renderMembers } from '../src/lib/aiContext.ts';

/**
 * 钉住一件事：**这道题的素材里必须真的有个股数字**。
 * 2026-09-27 实测：`gatherContext` 从头截 7500 字，排在末尾的成分明细整段消失，
 * 模型回"材料里没有任何个股股价位置/涨幅/市值"，一次真调用就这么白烧掉。
 */

const SPEC = {
  sources: ['/quant/sector-heat?limit=12', '/market/emotion', '/qd/board-ladder'],
  expand: { from: '/quant/sector-heat?limit=12', pick: 'sector_code', top: 3,
            then: '/quant/sector-members?sector=', limit: 40 },
};

function heat(n = 12, padding = 0) {
  return { date: '2026-09-24', rows: Array.from({ length: n }, (_, i) => ({
    sector_code: `TDX_88${i}`, name: `板块${i}`, regime: 'overheated',
    ...(padding ? { padding: 'x'.repeat(padding) } : {}),
  })) };
}

function members(n = 3) {
  return { date: '2026-09-24', sector_name: '医疗', total_members: n,
    rows: Array.from({ length: n }, (_, i) => ({
      code: `60000${i}.SH`, name: `个股${i}`, close: 10 + i, pct_change: 1.5,
      pos60: 20 + i, gain60: 30 + i, turnover_rate_percent: 4.4,
      float_mcap_yi: 50 + i, sealed: false, bars: 44, pe_ttm: 30, pb: 2 })),
    caveats: [{ code: 'note', text: '成分表没有时点，含幸存者偏差' }] };
}

/** 返回读过的路径，顺便让调用方决定哪个源坏掉。 */
function reader(over: Record<string, unknown> = {}, broken: string[] = []) {
  const seen: string[] = [];
  const read = async (path: string) => {
    seen.push(path);
    if (broken.some(b => path.includes(b))) throw new Error('HTTP 500');
    if (path.includes('/quant/sector-members')) return over.members ?? members();
    if (path.includes('/quant/sector-heat')) return over.heat ?? heat();
    return { date: '2026-09-24' };
  };
  return { read, seen };
}

test('页面级源再长也不许挤掉个股明细', async () => {
  const { read } = reader({ heat: heat(12, 4000) });
  const context = await buildContext(SPEC, read);
  assert.ok(context.length <= CONTEXT_BUDGET, `context 超了后端上限：${context.length}`);
  assert.match(context, /pos60=/, '个股位置这一列必须在 —— 没了它这道题就是"材料为空"');
  assert.match(context, /code=60000\d\.SH/);
  assert.match(context, /幸存者偏差/, '口径必须跟着素材一起交给模型');
});

test('热度榜上前几名逐个展开，一个都不悄悄丢', async () => {
  const { read, seen } = reader();
  const context = await buildContext(SPEC, read);
  const expanded = seen.filter(p => p.includes('/quant/sector-members'));
  assert.equal(expanded.length, 3, `expand.top=3 就该发 3 次，实发 ${expanded.length}`);
  assert.equal((context.match(/按 60 日位置从低到高排/g) ?? []).length, 3);
});

test('榜上无可展开板块时如实说没取到，不留空段落', async () => {
  const { read, seen } = reader({ heat: heat(0) });
  const context = await buildContext(SPEC, read);
  assert.match(context, /个股明细未取到/);
  assert.equal(seen.filter(p => p.includes('/quant/sector-members')).length, 0);
});

test('抓不到的源写明未取到，其余源照常交出', async () => {
  const { read } = reader({}, ['/market/emotion']);
  const context = await buildContext(SPEC, read);
  assert.match(context, /【\/market\/emotion】未取到：HTTP 500/);
  assert.match(context, /pos60=/);
});

test('明细列是白名单，多余字段不进上下文', () => {
  const text = renderMembers(members(1));
  assert.doesNotMatch(text, /pe_ttm/, '15 列全塞进去会把预算吃空，分层用不到就不给');
  assert.match(text, /float_mcap_yi=50/);
});
