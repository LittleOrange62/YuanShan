import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import {
  blockedList, checkTone, emptyTemplateForm, guardValueLabel, historyLine, historyTone,
  needsCombo, preflightLine, sendResultLine, stepFormFrom, stepMissing, stepPayload, stepSummary,
  stepTone, templateFormFrom, templateMissing, templatePayload, usesPoint,
  type WxCheck, type WxGuard, type WxPreflight, type WxStep, type WxTemplate,
} from '../src/lib/wxpushRules.ts';

/**
 * 个人微信推送面板的判据层。钉的是四件"说错就误导"的事：
 *  ① **不许把"按完了"说成"送达了"** —— 这一侧读不回回执，措辞必须停在 injected；
 *  ② 预检拦住时要能列出**还差哪几样**（主按钮不静默禁用这条纪律的判据半边）；
 *  ③ 表单往返：只交改过的字段、换内容来源时不许留着上一来源的旧值；
 *  ④ 面板真的接上了 dry_run 与真发两条路，且步骤/闸的清单来自后端而不是前端写死。
 */

const step = (over: Partial<WxStep> = {}): WxStep => ({
  key: 'search', label: '打开搜索框', kind: 'hotkey', enabled: true, mode: 'hotkey',
  mode_label: '按一个组合键', modes: null, value: 'ctrl+f', value_label: 'Ctrl+F',
  point: [0, 0], wait_ms: 900, wait_default: 900, hint: '', risky: false,
  is_default: true, invalid_reason: null, ...over,
});

test('① 结果措辞：injected 只说按完了，并且明说要去手机上看', () => {
  const r = sendResultLine({ status: 'injected', op_count: 31, chars: 120, to: '某人' });
  assert.equal(r.tone, 'warn', '按完了不能显示成"绿=成功送达"');
  assert.match(r.text, /手机/);
  assert.doesNotMatch(r.text, /已送达|发送成功/, '自称送达就是假话（这一侧读不回回执）');
});

test('① 五种状态各有各的话，不许合成一句"失败"', () => {
  assert.equal(sendResultLine({ status: 'dry_run', ok: true, op_count: 28 }).text,
    '预检通过：真发会按 28 个动作（下面逐条列出，**这次一个键都没按**）');
  assert.match(sendResultLine({ status: 'aborted', aborted_at: '核对前台就是微信窗口',
    reason: '前台跑掉了' }).text, /停在「核对前台就是微信窗口」/);
  assert.match(sendResultLine({ status: 'blocked', reason: '没有渲染表面' }).text, /预检拒发/);
  assert.match(sendResultLine({ status: 'rejected', stage: '正文', reason: '超上限' }).text,
    /正文/);
  assert.equal(sendResultLine({}).tone, 'bad', '读不懂的状态不能当成没事');
});

test('② 拦住时要逐条列出还差哪几样，可选关掉的项不算拦', () => {
  const pre: WxPreflight = {
    ok: false, blocked: ['input_desktop', 'idle'], foreground: 0, hwnd: 123,
    idle_seconds: 1, checked_at: 'now', reason: '整体那句',
    checks: [
      { key: 'input_desktop', label: '这个会话有没有输入桌面', ok: false, required: true,
        detail: 'GetForegroundWindow() 回 0' } as WxCheck,
      { key: 'idle', label: '键盘空闲够不够', ok: false, required: true,
        detail: '现在空闲 1 秒' } as WxCheck,
      { key: 'cooldown', label: '离上一条够不够久', ok: false, required: false,
        detail: '这道闸关着' } as WxCheck,
    ],
  };
  assert.deepEqual(blockedList(pre), ['这个会话有没有输入桌面 —— GetForegroundWindow() 回 0',
    '键盘空闲够不够 —— 现在空闲 1 秒']);
  assert.equal(blockedList(pre).length, 2, '可选没过的那项不该出现在"还差哪几样"里');
  assert.match(preflightLine(pre), /差 2 样/);
  assert.equal(checkTone({ ok: false, required: true } as WxCheck), 'bad');
  assert.equal(checkTone({ ok: false, required: false } as WxCheck), 'warn');
  assert.match(preflightLine(null), /还没读回来/, '预检没读回来时不许显示"可以发"');
});

test('② 步骤行：跳过/关掉是提示色，后端校验不过是红', () => {
  assert.equal(stepTone(step()), 'ok');
  assert.equal(stepTone(step({ mode: 'skip' })), 'warn');
  assert.equal(stepTone(step({ enabled: false })), 'warn');
  assert.equal(stepTone(step({ risky: true })), 'warn');
  assert.equal(stepTone(step({ invalid_reason: '组合键看不懂' })), 'bad');
});

test('② 摘要要说"这一步现在实际做什么"，不是名字', () => {
  assert.match(stepSummary(step()), /Ctrl\+F/);
  assert.equal(stepSummary(step({ enabled: false })), '整步关掉：序列里不会出现这一步');
  assert.equal(stepSummary(step({ mode: 'skip', mode_label: '保持原尺寸（推荐：坐标点击更稳）' })),
    '这一步跳过');
  assert.match(stepSummary(step({ mode: 'click_point', modes: [], value: '' }) ), /窗口内偏移/);
});

test('② 只有需要按键的那几档才要求填组合键', () => {
  assert.equal(needsCombo(step()), true, 'hotkey 那一档必须给键');
  assert.equal(needsCombo(step({ kind: 'keys' })), true);
  assert.equal(needsCombo(step({ kind: 'window', mode: 'hotkey_then_restore' })), true);
  assert.equal(needsCombo(step({ kind: 'window', mode: 'restore' })), false,
    '直接唤窗那一档不按键，空键值不该被拦');
  assert.equal(needsCombo(step({ kind: 'text', mode: 'paste' })), false);
  assert.deepEqual(stepMissing(step(), { ...stepFormFrom(step()), value: '' }),
    ['这一格「按哪个组合键」是空的（空着等于这一步按不出来）']);
});

test('③ 步骤表单：只交改过的字段，没改就回 null 不发请求', () => {
  const s = step();
  const f = stepFormFrom(s);
  assert.equal(stepPayload(s, f), null, '一个字段都没改就别打这一次 POST');
  assert.deepEqual(stepPayload(s, { ...f, waitMs: 1500 }), { key: 'search', wait_ms: 1500 });
  assert.deepEqual(stepPayload(s, { ...f, enabled: false, value: 'ctrl+q' }),
    { key: 'search', enabled: false, value: 'ctrl+q' });
  // 坐标只在"点固定坐标"那一档才参与，别在别的档里把它写回去
  assert.equal(stepPayload(s, { ...f, pointX: 9 })?.point, undefined);
  assert.deepEqual(stepPayload(step({ mode: 'click_point', point: [1, 2] }),
    { ...stepFormFrom(step({ mode: 'click_point', point: [1, 2] })), pointY: 8 }),
    { key: 'search', point: [1, 8] });
});

test('③ 内容档：换来源时不许留着上一个来源的旧值', () => {
  const t: WxTemplate = {
    key: 'a', label: '收盘小结', source: 'text', source_label: '固定文字', text: '今天 {date}',
    scenes: {}, script: '', timeout_seconds: 30, title: '', enabled: true, note: '',
    summary: '', invalid_reason: null,
  };
  const back = templatePayload(templateFormFrom(t));
  assert.deepEqual(back, { key: 'a', label: '收盘小结', source: 'text', enabled: true,
    note: '', text: '今天 {date}' }, '固定文字那一档不该带 scenes/script');
  const switched = templatePayload({ ...templateFormFrom(t), source: 'scene',
    sceneKeys: ['watchlist', 'market'], title: '前缀' });
  assert.deepEqual(switched.scenes, { watchlist: {}, market: {} });
  assert.equal(switched.text, undefined, '切到内容档那一档后，旧那格文字不许还跟着提交');
  assert.equal(switched.title, '前缀');
  assert.deepEqual(Object.keys(templatePayload({ ...templateFormFrom(t), source: 'script',
    script: 'scripts/x.py' })), ['key', 'label', 'source', 'enabled', 'note', 'script',
  'timeout_seconds']);
});

test('③ 内容档缺项说得出"还差哪几样"，空手点保存不发注定 400 的请求', () => {
  assert.deepEqual(templateMissing(emptyTemplateForm()),
    ['键名（英文，面板与接口都靠它认这一条）', '名字', '要发的固定文字']);
  assert.deepEqual(templateMissing({ ...emptyTemplateForm(), key: 'k', label: 'L',
    source: 'scene' }), ['至少勾一档内容']);
  assert.deepEqual(templateMissing({ ...emptyTemplateForm(), key: 'k', label: 'L',
    source: 'script' }), ['脚本路径（仓库内的相对路径）']);
  assert.deepEqual(templateMissing({ ...emptyTemplateForm(), key: 'k', label: 'L', text: '有字' }), []);
});

test('③ 闸的读数形状：布尔说开关、数字带单位', () => {
  const g = (over: Partial<WxGuard>): WxGuard => ({
    key: 'min_idle_seconds', label: '要求键盘空闲不少于', type: 'int', value: 10, default: 10,
    unit: '秒', min: 0, max: 600, note: '', is_default: true, ...over,
  });
  assert.equal(guardValueLabel(g({})), '10 秒');
  assert.equal(guardValueLabel(g({ type: 'bool', value: true })), '开着');
  assert.equal(guardValueLabel(g({ type: 'bool', value: false })), '关着');
});

test('④ 留痕行：按完了算提示色而不是成功，dry_run 才算绿', () => {
  assert.equal(historyTone({ status: 'injected', ok: true }), 'warn');
  assert.equal(historyTone({ status: 'dry_run', ok: true }), 'ok');
  assert.equal(historyTone({ status: 'aborted', ok: false }), 'bad');
  assert.equal(historyTone({ status: 'blocked', ok: false }), 'warn');
  assert.match(historyLine({ at: '今天 15:00', status: 'aborted', to: '某人', chars: 30,
    aborted_at: '回车发送', reason: '前台跑掉了' }),
    /停在「回车发送」.*前台跑掉了/);
});

test('④ 面板与登记点：两条路都接上了、清单来自后端、主按钮不静默禁用', () => {
  const src = readFileSync(new URL('../src/components/WxPushPanel.tsx', import.meta.url), 'utf8');
  assert.match(src, /sendWxPush\(sendPayload\(\), true\)/, '「先看一遍」没接 dry_run');
  assert.match(src, /sendWxPush\(sendPayload\(\), false\)/, '「真发」没接');
  assert.match(src, /blockedList\(/, '被拦住时没列出还差哪几样');
  assert.match(src, /stepPayload\(/, '步骤格没接保存');
  assert.match(src, /patchWxPush\("template"/, '内容档没接保存');
  assert.match(src, /s\?\.scene_choices/, '内容档清单写成前端常量 —— 后端加一档界面就看不见');
  assert.match(src, /s\?\.steps/, '步骤表写成前端常量 —— 后端加一步界面就看不见');
  // 「真发这一条」那颗：只能被 busy 禁用，不能被预检禁用（拦住要说出来，不能悄悄按不动）
  // 按 JSX 元素本身锚定 —— 文件顶部注释里也写了这句话，`indexOf` 会先撞上注释。
  const btn = /<button[\s\S]{0,220}?>\s*真发这一条/.exec(src);
  assert.ok(btn, '没找到「真发这一条」那颗按钮');
  assert.match(btn[0], /disabled=\{busy !== ""\}/, '主按钮的禁用条件不是"忙不忙"');
  assert.doesNotMatch(btn[0], /disabled=\{[^}]*preflight/, '主按钮被预检静默禁用了 —— 拦住要列原因');
  // 「按完了」不等于送达：界面上必须有一句让人去手机上看
  assert.match(src, /手机/);
});

test('④ 挂进了系统配置那一屏，并登记进能力自检清单', () => {
  const page = readFileSync(new URL('../src/pages/System.tsx', import.meta.url), 'utf8');
  assert.match(page, /import \{ WxPushPanel \} from "@\/components\/WxPushPanel"/);
  assert.match(page, /<WxPushPanel \/>/);
  assert.match(page, /\{ path: "\/wxpush"/, '没登记进能力自检清单 —— 接口坏了没人知道');
  const lib = readFileSync(new URL('../src/lib/wxpush.ts', import.meta.url), 'utf8');
  assert.match(lib, /apiUrl\('\/api\/wxpush\/send'\)/);
  assert.match(lib, /authHeaders\(\)/, '没带访问键：开了访问键的机器上会 401');
});
