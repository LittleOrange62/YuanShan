import assert from 'node:assert/strict';
import fs from 'node:fs';
import test from 'node:test';
import { FOLDABLE_CAVEAT_CODES, caveatFoldable, splitCaveats } from '../src/lib/caveatKind.ts';

/**
 * 「哪些 caveat 可以被折起来」这一处判断的闸（用户 2026-10-21：描述性内容全部收缩，别占屏）。
 * 这里钉的不是"折了多少条"，而是**折错方向的代价**：
 * 一条截断声明被折进 `<details>`，屏幕就会把"只取了前 200 行"念成"全场就这些票"。
 */

const C = (code: string, text = `${code} 的句子`): { code: string; text: string } => ({ code, text });

test('没点过名的 code 一律留在屏上', () => {
  for (const code of ['brand_new_backend_code', 'some_future_thing', '', null, undefined])
    assert.equal(caveatFoldable(code as never), false, `${code} 不该被折`);
  assert.equal(splitCaveats([C('brand_new_backend_code')]).foldable.length, 0,
    '后端随时能加新 code；默认折叠会把某天多出来的一条告警静默藏掉');
});

test('缺数／截断／失败／陈旧那一族，绝不折', () => {
  for (const code of ['board_truncated', 'pool_truncated', 'truncated', 'empty_board',
    'ambush_counts_unread', 'dossier_blocks_missing', 'concept_failed', 'labels_fallback',
    'stale_ranks', 'is_st', 'caliber_break', 'no_snapshot', 'partial_last', 'energy_track_censored',
    'confluence_source_absent', 'resumed_gap', 'weight_mismatch', 'system_entry_filtered_out',
    'spike_not_computed', 'quadrant_bad_token'])
    assert.equal(caveatFoldable(code), false, `${code} 是"库不健康"类，必须留在屏上`);
});

test('纯口径解释类才允许折', () => {
  for (const code of ['quadrant_design', 'entry_quadrant', 'entry_quadrant_null', 'zero_axis',
    'rank_direction', 'env_rank_levels', 'relative_not_absolute', 'y_axis_levels', 'coverage',
    'universe', 'note', 'three_state', 'null_not_zero', 'st_vs_strict', 'selection_bias'])
    assert.equal(caveatFoldable(code), true, `${code} 是解释，占屏主力，该折`);
});

/** 用户 2026-10-21「全部收缩」之后我第一版白名单是**照 code 名字**收的，逐条读后端文案才发现五个放错了。
 *  这一条钉住那次纠正：名字像解释、实际是后端按当下一批 append 出来的缺数计数，一律不许折。 */
test('名字像解释、内容却是"这批哪几行没数"的那五个 code 不放进白名单', () => {
  const why: Record<string, string> = {
    short_window: '「这些票的可用 K 线不足 40 日…：000001、000002…」——点着名字说哪几行不能横比',
    thin_window: '「这些场次窗口点数不足 8，斜率不具解释力：…」——同上',
    min_bars: '「这一批里有 N 行凑不满 10 根倍数，八个分布读数留空」——折起来那几格空着就没人知道为什么',
    axis_weight_drift: '「说明式轴权重与 config.view_effective_binding 不一致」——配置对不上，是坏消息',
    group_series: '「成员快照累计 N 天，M 日窗口需要 M 场，不够只能是 insufficient」——当下取数不足',
  };
  for (const [code, reason] of Object.entries(why)) {
    assert.equal(FOLDABLE_CAVEAT_CODES.has(code), false, `${code} 该留在屏上：${reason}`);
    assert.equal(caveatFoldable(code), false, `${code} 不可折`);
  }
});

test('白名单里若有"后端是 append 出来的"那种 code，必须在这里点名并给出理由', () => {
  // append ⇒ 这条句子的**内容**取决于当下的数据，今天装解释明天装告警；
  // 写在 list/`_NOTE` 里的才是固定散文。两者靠 code 名字分不出来，只能逐个对账。
  const py = fs.readFileSync(new URL('../../duanxian/quant.py', import.meta.url), 'utf8');
  const appended = new Set<string>();
  for (const m of py.matchAll(/append\(\s*\{[^}]{0,240}?'code':\s*'([a-z0-9_]+)'/g)) appended.add(m[1]);
  assert.ok(appended.size >= 20, `只扫到 ${appended.size} 个 append 出来的 code，读取方式大概坏了`);
  const sanctioned: Record<string, string> = {
    note: '_caveats(extra=…) 的通用兜底：句子本身由下面那条句式闸再判一次，带"失败/截断/不一致"就不折',
    future_function: '只在 scope=group_rebuilt 这一档出现，说的是"这一档的名单是今日回算的"这个口径，行本身照样标着 scope',
    y_axis_levels: '数字是**全场字段级**的（Y 轴只有 32 个不同名次、最大一档并列 755 只），讲的是"路径里连着几个数字相同＝并列不是没动"这条读法；它不指认哪一行缺数，所以可折。同屏那条真缺数的 caliber_break 不在白名单，仍留在屏上。',
  };
  const leaked = [...FOLDABLE_CAVEAT_CODES].filter(c => appended.has(c) && !(c in sanctioned));
  assert.deepEqual(leaked, [], `这些 code 后端是按当下数据 append 的，却没在这里交代为什么仍可折：${leaked.join('、')}`);
  for (const c of Object.keys(sanctioned))
    assert.ok(FOLDABLE_CAVEAT_CODES.has(c), `${c} 的豁免说明还在，但白名单已经把它撤了 —— 理由该一起删掉`);
});

test('note 那句通用兜底：带坏消息词就不折，纯口径散文照折', () => {
  assert.equal(caveatFoldable('note', '这一屏是描述统计，不是买卖信号，母体本身就是涨起来的票'), true);
  for (const t of ['上游接口查询失败，这一格用的是缓存', '榜被上限截断，只交了前 80 行',
    '说明式权重与注册表不一致', '取数超时，本轮回退到上一场'])
    assert.equal(caveatFoldable('note', t), false, `「${t}」这类句子必须留在屏上`);
  // 其余 code 不套句式闸，否则「短历史是 caveat 不是缺数」这种正常解释也会被顶回屏上，收缩白做
  assert.equal(caveatFoldable('coverage', '各表起点不同，短历史是 caveat 不是缺数。'), true);
});

test('白名单里不许有对不上后端的死条目', () => {
  // 打错一个字母 ⇒ 那条 code 永远匹配不上，折叠行为静默少一条，而测试若只看集合就会全绿。
  const walk = (rel: string): string[] => fs.readdirSync(new URL(rel, import.meta.url), { withFileTypes: true })
    .flatMap(e => e.isDirectory() ? walk(`${rel}${e.name}/`)
      : (e.name.endsWith('.py') ? [`${rel}${e.name}`] : []));
  const py = ['../../duanxian/', '../../app/']
    .flatMap(d => walk(d))
    .map(f => fs.readFileSync(new URL(f, import.meta.url), 'utf8')).join('\n');
  const known = new Set<string>();
  for (const m of py.matchAll(/'code':\s*'([a-z0-9_]+)'/g)) known.add(m[1]);
  for (const m of py.matchAll(/^\s+'([a-z0-9_]+)':\s*'/gm)) known.add(m[1]);   // _NOTE 那批键
  for (const m of py.matchAll(/_caveats\(([^)]*)\)/g)) {
    for (const k of m[1].matchAll(/'([a-z0-9_]+)'/g)) known.add(k[1]);
  }
  const dead = [...FOLDABLE_CAVEAT_CODES].filter(c => !known.has(c));
  assert.deepEqual(dead, [], `这些 code 在后端里找不到（打错或已改名）：${dead.join('、')}`);
});

test('拆分保序，空句子两边都不进', () => {
  const { visible, foldable } = splitCaveats([C('coverage'), C('board_truncated'),
    { code: 'note', text: '' }, C('zero_axis'), null as never]);
  assert.deepEqual(foldable.map(c => c.code), ['coverage', 'zero_axis'], '折叠那份保持后端给的顺序');
  assert.deepEqual(visible.map(c => c.code), ['board_truncated']);
});
