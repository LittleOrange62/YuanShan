import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { WEEK_DAY_LABELS, freqProblem, parseWeeklyDays, switchBadge, type LoopSwitchView }
  from '../src/lib/hostLoops.ts';

/**
 * 常驻循环面板的读数层。钉的是两件"说错就误导"的事：
 *  ① 关掉到底停不停是**逐条事实**（10-08 有四条只在启动时读开关），后端没交这一格时
 *     界面必须说"不知道"，不许默认成"即时生效"；
 *  ② 选了每周却没勾周几 ⇒ 宿主按"判不了就放行"天天跑，与用户选的相反，必须当场说出来。
 */

test('后端没交 live_switch 时说"未知"，不假装即时生效', () => {
  assert.equal(switchBadge({ key: 'x' } as LoopSwitchView).tone, 'unknown');
  assert.match(switchBadge({ key: 'x' } as LoopSwitchView).label, /未知/);
  assert.equal(switchBadge({ key: 'x', live_switch: true } as LoopSwitchView).tone, 'ok');
  const dead = switchBadge({ key: 'x', live_switch: false } as LoopSwitchView);
  assert.equal(dead.tone, 'warn');
  assert.match(dead.label, /重启/);
});

test('weekly 的空周几是问题，daily 的空周几不是', () => {
  assert.equal(freqProblem({ key: 'k', has_freq: true, run_freq: 'daily', weekly_days: '' } as LoopSwitchView), null);
  assert.match(String(freqProblem({ key: 'k', has_freq: true, run_freq: 'weekly', weekly_days: '' } as LoopSwitchView)), /天天跑/);
  assert.equal(freqProblem({ key: 'k', has_freq: true, run_freq: 'weekly', weekly_days: '0,2' } as LoopSwitchView), null);
  // 没有 has_freq 的键（间隔型/跟段跑）根本不该被问这个问题
  assert.equal(freqProblem({ key: 'k', run_freq: 'weekly' } as LoopSwitchView), null);
});

test('weekly_days 解析：0 是合法值（周一），坏值不当空', () => {
  assert.deepEqual(parseWeeklyDays('0,2,4').days, [0, 2, 4]);
  assert.deepEqual(parseWeeklyDays('').days, []);
  assert.equal(parseWeeklyDays('').bad, false);
  assert.equal(parseWeeklyDays('1,8').bad, true);      // 越界要说"读不懂"，不能静默当没勾
  assert.equal(parseWeeklyDays(null).bad, false);
  assert.equal(WEEK_DAY_LABELS[0], '周一');
  assert.equal(WEEK_DAY_LABELS.length, 7);
});

test('面板必须把这两格接到调用点上，而不是只写在 lib 里', () => {
  const src = readFileSync(new URL('../src/components/LoopSwitchboard.tsx', import.meta.url), 'utf8');
  assert.match(src, /switchBadge/, '开关生效时机那一格没上界面');
  assert.match(src, /patchLoop\(l\.key, "run_freq"/, '频度下拉没接写入');
  assert.match(src, /patchLoop\(l\.key, "weekly_days"/, '周几勾选没接写入');
  assert.match(src, /freqProblem/, '每周没勾周几那句警告没说出口');
  // 「一个都没注册过」已经过期：本机现在有一条只拉服务不跑数据的看护
  assert.doesNotMatch(src, /一个都没有注册过|零条/, 'NAMING 里还留着"计划任务零条"这句过期事实');
  assert.match(src, /YuanShan-KeepAlive8910/, '没写出那条看护是谁、干什么的');
  // 后端那些句子内嵌了 ** 粗体标记，页面上原样显示就是脏字符：每一格都得过 plain()
  assert.match(src, /plain\(l\.cadence/, '节奏（cadence）那格没过 plain()，星号会漏到页面上');
  assert.match(src, /plain\(l\.switch_effect/, '关掉之后那一格没过 plain()');
});
