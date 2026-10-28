import test from 'node:test';
import assert from 'node:assert/strict';
import {
  emptyForm, formFromPlan, formToPlan, limitOf, type Form,
} from '../src/lib/notifyPlanForm.ts';
import type { NotifyCatalog, NotifyPlan } from '../src/lib/notifyRules.ts';

/**
 * 「一条计划」表单的读回 / 写回。钉的是两件会静默坏事的东西：
 *  ① **界面不许自造默认值**（留空就是不写，用后端 `DEFAULT_LIMITS` / `empty_means` 的含义）；
 *  ② **键名只能来自后端目录**（`scenes[].fields`）—— 目录没读到时写不出任何参数，
 *     因为那时前端并不知道这一档有哪些格子。
 * 这两件都是"发出去的内容与面板上填的不一样"的成因，而且不报错。
 */

const CATALOG = {
  scenes: [
    { value: 'confluence', label: '策略共振榜', default_limit: 12, max_limit: 60, limit_unit: '只',
      fields: [
        { key: 'min_hits', label: '共振门槛', type: 'int', min: 1, max: 6, default: 2 },
        { key: 'sources', label: '只发这些策略选中的票', type: 'multi', empty_means: '全部策略',
          choices: [{ value: 'dgt', label: '打板跟' }, { value: 'rank', label: '名次稳定榜' }] },
      ] },
    { value: 'screen', label: '条件筛选结果', default_limit: 12, max_limit: 60, limit_unit: '只',
      fields: [
        { key: 'rules', label: '筛选规则', type: 'multi', as_objects: true, required: true,
          choices: [{ value: 'align_bear', label: '空头排列' }] },
        { key: 'universe', label: '票池', type: 'select', default: 'watchlist',
          choices: [{ value: 'watchlist', label: '自选池' }, { value: 'market', label: '全市场' }] },
      ] },
    { value: 'watchlist', label: '自选股当日明细', default_limit: 30, max_limit: 120, limit_unit: '只',
      fields: [
        { key: 'group_id', label: '自选分组', type: 'select', numeric: true, empty_means: '全部分组',
          choices: [{ value: '896', label: 'AI影视投流（16 只）' }] },
        { key: 'pct_min', label: '只报涨幅不低于', type: 'num', unit: '%' },
      ] },
    { value: 'dossier', label: '个股量化档案', default_limit: 6, max_limit: 20, limit_unit: '只',
      fields: [
        { key: 'codes', label: '重点个股', type: 'codes', empty_means: '按自选前几只' },
        { key: 'blocks', label: '档案里带哪几块', type: 'multi', empty_means: '六块全带',
          choices: [{ value: 'oper', label: '操作分与象限' }, { value: 'scores', label: '分项能力分' }] },
      ] },
    { value: 'ambush', label: '埋伏票榜', default_limit: 10, max_limit: 60, limit_unit: '只',
      fields: [
        { key: 'types', label: '只要哪几类埋伏', type: 'multi', empty_means: '五类都要',
          choices: [{ value: 'sticky_pull', label: '均线汇集蓄势' }] },
        { key: 'min_weekly_ups', label: '近 6 周上涨周数不少于', type: 'int', min: 0, max: 10, default: 0 },
        { key: 'only_strict', label: '只要严格通过的', type: 'bool' },
      ] },
    { value: 'market', label: '市场概览', default_limit: 4, max_limit: 9, limit_unit: '层',
      fields: [{ key: 'limit', label: '连板天梯列几层', type: 'int', min: 1, max: 9, default: 4 }] },
  ],
} as unknown as NotifyCatalog;

const base = (): Form => ({
  ...emptyForm(), id: 'p1', name: '收盘后', run_at: '16:20', channels: ['feishu-main'],
});

function plan(scenes: Record<string, unknown>): NotifyPlan {
  return { id: 'p1', name: '收盘后', run_at: '16:20', run_freq: 'daily', weekly_days: '',
    trade_days_only: true, channels: [{ key: 'feishu-main' }], scenes } as NotifyPlan;
}

test('填了什么就写什么：新档（埋伏榜）不需要前端认识它', () => {
  const f = base();
  f.scenes = ['ambush'];
  f.limits = { ambush: '5' };
  f.params = { ambush: { types: ['sticky_pull'], min_weekly_ups: '3', only_strict: true } };
  assert.deepEqual(formToPlan(f, CATALOG).scenes, {
    ambush: { limit: 5, types: ['sticky_pull'], min_weekly_ups: 3, only_strict: true },
  });
});

test('留空一律不写：不抄一份默认值进配置', () => {
  const f = base();
  f.scenes = ['ambush', 'confluence'];
  f.params = { ambush: { types: [], min_weekly_ups: '', only_strict: false },
    confluence: { min_hits: '', sources: [] } };
  assert.deepEqual(formToPlan(f, CATALOG).scenes, { ambush: {}, confluence: {} });
  assert.deepEqual(limitOf(f, 'ambush'), {});
});

test('规则那一格写回的是执行体要的形状（{rule_key, params}）', () => {
  const f = base();
  f.scenes = ['screen'];
  f.params = { screen: { rules: ['align_bear'], universe: 'market' } };
  assert.deepEqual(formToPlan(f, CATALOG).scenes.screen, {
    rules: [{ rule_key: 'align_bear', params: {} }], universe: 'market',
  });
});

test('代码框接住"逗号/中文逗号/空格"混着抄的写法', () => {
  const f = base();
  f.scenes = ['dossier'];
  f.params = { dossier: { codes: '600519，300750.SZ 002424' } };
  assert.deepEqual((formToPlan(f, CATALOG).scenes.dossier as Record<string, unknown>).codes,
    ['600519', '300750.SZ', '002424']);
});

test('分组那种"数字键"写回数字，不是字符串', () => {
  const f = base();
  f.scenes = ['watchlist'];
  f.params = { watchlist: { group_id: '896', pct_min: '2.5' } };
  assert.deepEqual(formToPlan(f, CATALOG).scenes.watchlist, { group_id: 896, pct_min: 2.5 });
});

test('目录没读到 ⇒ 一格参数也写不出（不猜键名）', () => {
  const f = base();
  f.scenes = ['ambush'];
  f.limits = { ambush: '5' };
  f.params = { ambush: { types: ['sticky_pull'] } };
  // 只带条数：那条不住在描述符里（上下限另有 `{DEFAULT,MAX}_LIMITS` 那两张表）
  assert.deepEqual(formToPlan(f, undefined).scenes, { ambush: { limit: 5 } });
});

test('读回 → 写回 必须一模一样（编辑一条已有计划不该悄悄改内容）', () => {
  const original = {
    confluence: { min_hits: 3, sources: ['dgt', 'rank'], limit: 8 },
    screen: { rules: [{ rule_key: 'align_bear', params: {} }], universe: 'watchlist' },
    watchlist: { group_id: 896, pct_min: 1.5, limit: 40 },
    dossier: { codes: ['600519', '300750.SZ'], blocks: ['oper', 'scores'] },
    ambush: { types: ['sticky_pull'], min_weekly_ups: 4, only_strict: true },
    market: { limit: 6 },
  };
  const f = formFromPlan(plan(original), CATALOG);
  assert.deepEqual(f.scenes, Object.keys(original));
  assert.deepEqual(f.limits, { confluence: '8', watchlist: '40', market: '6' });
  assert.deepEqual(f.params.confluence, { min_hits: '3', sources: ['dgt', 'rank'] });
  assert.deepEqual(f.params.screen, { rules: ['align_bear'], universe: 'watchlist' });
  assert.equal(f.params.ambush.only_strict, true);
  const back = formToPlan(f, CATALOG).scenes as Record<string, Record<string, unknown>>;
  for (const [key, value] of Object.entries(original)) {
    assert.deepEqual(back[key], value, `${key} 这一档往返后变了`);
  }
});

test('周几读回：weekly_days 那一串要在表单里变成勾', () => {
  const p = plan({ market: {} });
  p.run_freq = 'weekly'; p.weekly_days = '0,4';
  assert.deepEqual(formFromPlan(p, CATALOG).days, [0, 4]);
  assert.equal(formToPlan({ ...formFromPlan(p, CATALOG), scenes: ['market'] }, CATALOG).weekly_days, '0,4');
});

test('点「保存计划」不许 silently 不动：缺什么就按名字列出来（10-17 用户点名）', async () => {
  const { missingParts } = await import('../src/lib/notifyPlanForm.ts');
  const f = { ...emptyForm(), scenes: [], channels: [], name: '' };
  assert.deepEqual(missingParts(f), [
    '计划名字（空的：历史与面板都靠它认出这一条是哪条）',
    '至少勾一档内容（一条都不勾，发出去就是一句「没勾选任何内容」）',
    '至少选一条渠道（到点无处可发；渠道不可用时那一格是点不动的）',
  ]);
  // 三样齐了就不许再报缺（否则用户点了没反应，还以为是坏了）
  assert.deepEqual(missingParts({ ...f, name: '收盘后', scenes: ['market'], channels: ['feishu-main'] }), []);
  // 名字只有空格＝没填（后端 validate_plan 也是这条口径）
  assert.equal(missingParts({ ...f, name: '   ', scenes: ['market'], channels: ['c'] }).length, 1);
});
