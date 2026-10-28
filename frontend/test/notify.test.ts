import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import {
  channelDraft, channelHealth, channelPayload, emptyChannelDraft, parseWeeklyDays, planHealth,
  scheduleText, scenesText, supportsSign, todayTone, urlPlaceholder,
  type NotifyCatalog, type NotifyPlan,
} from '../src/lib/notifyRules.ts';   // 纯判据层：`notify.ts` 那边 import 了 api.ts（class 参数属性擦不掉）

/**
 * 消息推送面板的判据层。钉的是四件"说错就误导"的事：
 *  ① 计划"就绪 / 发不出去 / 配置不合法"必须分开说（合成一句"未就绪"就没人去修）；
 *  ② `weekly` 而没勾周几 ⇒ 后端按"判不了就放行"天天发，界面必须当场反着提醒；
 *  ③ 渠道那格要分得清"没填"与"填了但变量不存在"——后者是最难查的假配置；
 *  ④ 内容摘要里每一格的话术**来自后端描述符**（`scenes[].fields`），这里不再按档名写 if。
 */

/** 一份**与后端同形状**的最小目录（描述符 + 已解好的候选）。
 *  真实的那份由 `notifyconfig.SCENE_FIELDS` + `duanxian/notify.py::scene_catalog()` 生成；
 *  这里只留被测的几格，键名/位词照抄后端，改了后端描述符这条用例会当场红。 */
const CATALOG = {
  scenes: [
    { value: 'confluence', label: '策略共振榜', default_limit: 12, max_limit: 60, limit_unit: '只',
      fields: [
        { key: 'min_hits', label: '共振门槛', type: 'int', min: 1, max: 6, default: 2, bit_prefix: '≥' },
        { key: 'sources', label: '只发这些策略选中的票', type: 'multi', empty_means: '全部策略',
          bit_suffix: '只发其中 %s 个策略',
          choices: [{ value: 'dgt', label: '打板跟' }, { value: 'rank', label: '名次稳定榜' }] },
      ] },
    { value: 'screen', label: '条件筛选结果', default_limit: 12, max_limit: 60, limit_unit: '只',
      fields: [
        { key: 'rules', label: '筛选规则', type: 'multi', as_objects: true, required: true,
          empty_error: '一条规则都没选 ⇒ 这一块会发出空白', bit_suffix: '%s 条规则',
          choices: [{ value: 'align_bear', label: '空头排列' }] },
        { key: 'universe', label: '票池', type: 'select', default: 'watchlist',
          choices: [{ value: 'watchlist', label: '自选池' }, { value: 'market', label: '全市场' }] },
      ] },
    { value: 'watchlist', label: '自选股当日明细', default_limit: 30, max_limit: 120, limit_unit: '只',
      fields: [
        { key: 'group_id', label: '自选分组', type: 'select', numeric: true, empty_means: '全部分组',
          bit_prefix: '分组 ', choices: [{ value: '896', label: 'AI影视投流（16 只）' }] },
        { key: 'pct_min', label: '只报涨幅不低于', type: 'num', unit: '%', bit_prefix: '涨幅≥', bit_suffix: '%' },
      ] },
    { value: 'dossier', label: '个股量化档案', default_limit: 6, max_limit: 20, limit_unit: '只',
      fields: [
        { key: 'codes', label: '重点个股', type: 'codes', empty_means: '按自选前几只', bit_suffix: '指定 %s 只' },
        { key: 'blocks', label: '档案里带哪几块', type: 'multi', empty_means: '六块全带',
          bit_suffix: '带 %s 块', choices: [{ value: 'oper', label: '操作分与象限' }] },
      ] },
    { value: 'market', label: '市场概览', default_limit: 4, max_limit: 9, limit_unit: '层',
      fields: [{ key: 'limit', label: '连板天梯列几层', type: 'int', min: 1, max: 9, default: 4, unit: '层' }] },
    { value: 'voldbl', label: '倍量形态榜', default_limit: 10, max_limit: 60, limit_unit: '只',
      fields: [
        { key: 'min_count', label: '近 40 场倍量次数不少于', type: 'int', min: 0, max: 40, default: 0, bit_prefix: '倍量≥' },
        { key: 'quadrants', label: '限定四维象限', type: 'multi', empty_means: '不限象限',
          bit_suffix: '限定 %s 个象限', choices: [{ value: 'Q1', label: '买入' }] },
      ] },
  ],
} as unknown as NotifyCatalog;

test('时刻与频度说成人话，含"每周没勾周几"那句反提醒', () => {
  const base = { id: 'p', name: 'n', enabled: true, run_at: '16:20' } as NotifyPlan;
  assert.equal(scheduleText({ ...base, run_freq: 'daily', trade_days_only: true } as NotifyPlan),
    '16:20 每天（只交易日）');
  // 0=周一、4=周五（ISO 序）：这条期望自己也得核对，别把"周几"想成从周日开始
  assert.match(scheduleText({ ...base, run_freq: 'weekly', weekly_days: '0,4',
    trade_days_only: false } as NotifyPlan), /^16:20 每周 周一、周五（含周末与休市日）$/);
  const trap = scheduleText({ ...base, run_freq: 'weekly', weekly_days: '' } as NotifyPlan);
  assert.match(trap, /没勾周几/);
  assert.match(trap, /实际会天天发/);
});

test('weekly_days 坏值不当空值（说"写坏了"而不是"没勾"）', () => {
  assert.deepEqual(parseWeeklyDays('0,2').days, [0, 2]);
  assert.equal(parseWeeklyDays('').bad, false);
  assert.equal(parseWeeklyDays('1,9').bad, true);
  assert.match(scheduleText({ id: 'p', name: 'n', run_at: '16:20', run_freq: 'weekly',
    weekly_days: '1,9' } as NotifyPlan), /周几写坏了/);
});

test('计划健康分四档：不合法 / 没渠道 / 渠道都在但不可用 / 就绪', () => {
  assert.equal(planHealth({ id: 'p', invalid_reason: '时刻要写成 HH:MM' } as NotifyPlan).tone, 'bad');
  assert.equal(planHealth({ id: 'p', run_at: '16:00', channels: [], scenes: { market: {} } } as NotifyPlan).tone, 'bad');
  assert.match(planHealth({ id: 'p', channels: [], scenes: { market: {} } } as NotifyPlan).label, /没选渠道/);
  const ghost = planHealth({ id: 'p', channels: [{ key: 'gone', exists: false }],
    scenes: { market: {} } } as NotifyPlan);
  assert.equal(ghost.tone, 'bad');
  assert.match(ghost.label, /已删除/);
  const partial = planHealth({ id: 'p', channels: [{ key: 'a', exists: true, usable: true },
    { key: 'b', exists: true, usable: false }], scenes: { market: {} } } as NotifyPlan);
  assert.equal(partial.tone, 'warn');
  assert.match(partial.label, /只有部分渠道/);
  assert.equal(planHealth({ id: 'p', enabled: true, channels: [{ key: 'a', exists: true, usable: true }],
    scenes: { market: {} } } as NotifyPlan).tone, 'ok');
  // 勾了渠道但一条内容都没选 ⇒ 会发一条空正文，也算坏
  assert.match(planHealth({ id: 'p', enabled: true, channels: [{ key: 'a', exists: true, usable: true }],
    scenes: {} } as NotifyPlan).label, /空正文/);
});

test('渠道那格区分"没凭据"与"变量不存在"', () => {
  assert.equal(channelHealth({ key: 'c', has_secret: false, missing_reason: '配置里写的是 NOTIFY_X，但 .env 里没有' } as never).tone, 'bad');
  assert.match(channelHealth({ key: 'c', has_secret: false, missing_reason: '配置里写的是 NOTIFY_X，但 .env 里没有' } as never).label, /NOTIFY_X/);
  assert.equal(channelHealth({ key: 'c', has_secret: true, enabled: false } as never).tone, 'warn');
  assert.equal(channelHealth({ key: 'c', has_secret: true, enabled: true } as never).label, '可用');
});

test('内容摘要用中文名并带上条数（列表上一行看得完）', () => {
  const plan = { id: 'p', name: 'n', scenes: { confluence: { min_hits: 3 },
    screen: { rules: [{ rule_key: 'a' }, { rule_key: 'b' }] },
    dossier: { codes: ['600519', '300750'] } } } as NotifyPlan;
  const text = scenesText(plan, undefined, CATALOG);
  assert.match(text, /策略共振榜（≥3）/);
  assert.match(text, /条件筛选结果（2 条规则）/);
  assert.match(text, /个股量化档案（指定 2 只）/);
  assert.equal(scenesText({ id: 'p', scenes: {} } as NotifyPlan), '（没选内容）');
  // 目录没读到（后端那份失败）时**不许编出话术**：只剩档名，用户看得见"没拿到参数清单"
  assert.equal(scenesText({ id: 'p', scenes: { confluence: { min_hits: 3 } } } as NotifyPlan),
    '策略共振榜');
  // 目录给了、里面却没这一格（配置文件被人手改过）⇒ 原样点名，不能当没看见
  assert.equal(scenesText({ id: 'p', scenes: { confluence: { min_hits: 3, ghost: 7 } } } as NotifyPlan,
    undefined, CATALOG), '策略共振榜（≥3，ghost=7）');
});

test('面板必须真的接上预览与立刻发一次，且不在前端写死渠道种类', () => {
  const src = readFileSync(new URL('../src/components/NotifyPanel.tsx', import.meta.url), 'utf8');
  assert.match(src, /previewPlan\(/, '没接预览 —— 用户只能发出去才知道对不对');
  assert.match(src, /patchNotify\("run_now"/, '「立刻发一次」没接');
  assert.match(src, /patchNotify\("test_channel"/, '「发一条测试」没接');
  assert.match(src, /kind_choices/, '渠道种类写成前端常量 —— 后端加了种类界面就永远看不到');
  // 凭据只写不读：输入框必须是 password，而且**不许把服务端读回来的字段填进值里**。
  // （本地草稿 state 的 value 是可以的 —— 那是用户刚敲的；泄漏只发生在"回填"那一步。）
  assert.match(src, /type="password"/, '凭据输入框不是 password');
  assert.doesNotMatch(src, /value=\{[^}]*\.url_masked|defaultValue=\{[^}]*url|value=\{[a-z]+\.url(?!_masked)/,
    '把后端读回来的凭据字段填进了输入框 = 屏幕上能看到明文');
});

test('「今天这一档」的轻重分三档，读不出的键不许显示成没事', () => {
  assert.equal(todayTone('sent'), 'ok');
  assert.equal(todayTone('waiting'), 'ok');
  assert.equal(todayTone('in_window'), 'warn');   // 还在补发窗口里，下一轮心跳就补
  assert.equal(todayTone('missed'), 'bad');       // 过窗了，宿主不会自己追
  assert.equal(todayTone('bad'), 'bad');
  assert.equal(todayTone('off'), 'warn');
  assert.equal(todayTone(undefined), 'warn');     // 后端没交 ≠ 正常
  assert.equal(todayTone('whatever_new_key'), 'warn');
});

test('面板那句「今天：…」原样用后端读数，不在前端重抄一遍判据', () => {
  const src = readFileSync(new URL('../src/components/NotifyPanel.tsx', import.meta.url), 'utf8');
  assert.match(src, /p\.today_label/, '没渲染今天这一档 —— 错过那一分钟就又变成看不见的事');
  assert.match(src, /todayTone\(p\.today_state\)/, '颜色没跟着后端那个键走');
  // 判据不许在前端各写一份：这里出现"自己算迟到几分钟"就是两份口径的开始
  assert.doesNotMatch(src, /run_at.*split\(.\:.*\).*Date\.now/, '前端在重算时刻差 —— 与宿主两份口径');
});

test('零条计划时也必须能建出第一条（表单不许只长在列表项里）', () => {
  // 10-16 浏览器实测：点「＋加一条计划」后按钮消失、表单无处渲染 ——
  // 因为 `<PlanForm>` 原本只出现在 `plans.map(...)` 的每一项内部。
  const src = readFileSync(new URL('../src/components/NotifyPanel.tsx', import.meta.url), 'utf8');
  assert.match(src, /form && !plans\.some/, '新增计划的表单必须能在计划列表外面渲染（否则第一条永远建不出来）');
  assert.ok((src.match(/<PlanForm/g) || []).length >= 2, '列表内一份 + 列表外一份，缺一份就有一条形同虚设');
});

test('预览请求必须把渠道引用换成键（否则后端拿对象当渠道名）', () => {
  const src = readFileSync(new URL('../src/lib/notify.ts', import.meta.url), 'utf8');
  assert.match(src, /channels:\s*raw\.map\(/,
    'previewPlan 没把 plan_view 的对象数组换成键 —— 预览会说"没有这条渠道"并把 dict 印上屏');
});

test('共振那一档可以说"只发某几个策略"，不选就是全部', () => {
  const plan = { id: 'p', scenes: { confluence: { min_hits: 3, sources: ['dgt', 'rank'] } } } as NotifyPlan;
  assert.equal(scenesText(plan, undefined, CATALOG), '策略共振榜（≥3，只发其中 2 个策略）');
  assert.equal(scenesText({ id: 'p', scenes: { confluence: { min_hits: 2 } } } as NotifyPlan,
    undefined, CATALOG), '策略共振榜（≥2）');
  // 每档自己的条数要上屏，且单位听后端的（天梯是"层"不是"条"）
  assert.equal(scenesText({ id: 'p', scenes: { watchlist: { limit: 5 }, market: { limit: 2 } } } as NotifyPlan,
    { watchlist: '只', market: '层' }, CATALOG), '自选股当日明细（5 只）、市场概览（2 层）');
  // 新加的那一档（倍量榜）**不需要改这里就能渲染摘要**：话术全在描述符的 bit 里
  assert.equal(scenesText({ id: 'p', scenes: { voldbl: { min_count: 3, quadrants: ['Q1'], limit: 7 } } } as NotifyPlan,
    undefined, CATALOG), '倍量形态榜（倍量≥3，限定 1 个象限，7 只）');
  // 分组那一格交的是**名字**（目录里带的），不是 `分组#896` 那种内部号
  assert.equal(scenesText({ id: 'p', scenes: { watchlist: { group_id: 896 } } } as NotifyPlan,
    undefined, CATALOG), '自选股当日明细（分组 AI影视投流（16 只））');
});

test('面板必须按 catalog 渲染每一档的参数格（加一档内容只动后端）', () => {
  const src = readFileSync(new URL('../src/components/NotifyPanel.tsx', import.meta.url), 'utf8');
  assert.match(src, /sceneFieldList\(catalog, x\.value\)/,
    '参数格必须来自 catalog 的描述符 —— 按档名手写 if 就会与后端分叉');
  assert.match(src, /<FieldControl /, '描述符没有接到调用点（等于后端写了、界面上填不了）');
  assert.match(src, /aria-label=\{`\$\{fd\.label \|\| fd\.key\} \$\{o\.label\}`\}/,
    '多选那一格的每一项要有可读的 aria-label（验收要点得中）');
  assert.match(src, /form\.scenes\.includes\(x\.value\)[\s\S]*?发送/,
    '勾选项要按 catalog 的档位渲染');
  // 后端读得到的东西界面必须也写得回去 —— 原先 `sources` 就是"后端认、界面不接"的那种假功能
  const formSrc = readFileSync(new URL('../src/lib/notifyPlanForm.ts', import.meta.url), 'utf8');
  assert.match(formSrc, /sceneFieldList\(catalog, key\)/, '写回时不看描述符 = 猜键名');
  assert.doesNotMatch(src, /form\.scenes\.includes\("(confluence|screen|watchlist|dossier)"\) &&/,
    '还在按档名手写参数区 —— 17 档就要 17 段，漏一段就是假功能');
});

test('摘要里的 `%s` 对数字格也要替换（10-17 浏览器实测抓到的就是这一格印了原样模板）', () => {
  const cat = {
    scenes: [{ value: 'abnormal', label: '异动筛查', limit_unit: '只', fields: [
      { key: 'recent', label: '近期严重异动往回看几场', type: 'select',
        choices: [{ value: '5', label: '往回 5 场' }] },
      { key: 'hot_limit', label: '涨幅偏离榜列几只', type: 'int', default: 20, bit_suffix: '偏离榜 %s 只' },
    ] }],
  } as unknown as NotifyCatalog;
  assert.equal(scenesText({ id: 'p', scenes: { abnormal: { hot_limit: 3, recent: '5', limit: 2 } } } as NotifyPlan,
    undefined, cat), '异动筛查（往回 5 场，偏离榜 3 只，2 只）');
});

test('加签那一格：以后端 kind_choices 为准，粘贴官方地址要写在提示语第一句', () => {
  // 后端没交 supports_sign 时才退回内置两种（抄一份在前端必然会有人改漏）
  assert.equal(supportsSign('feishu'), true);
  assert.equal(supportsSign('wecom'), false);
  assert.equal(supportsSign('wecom', [{ value: 'wecom', supports_sign: true }]), true);
  assert.equal(supportsSign('feishu', [{ value: 'feishu', supports_sign: false }]), false);
  const ph = urlPlaceholder('feishu', [{ value: 'feishu', official: 'https://open.feishu.cn/open-apis/bot/v2/hook/', supports_sign: true }]);
  assert.match(ph, /直接粘贴机器人地址/, '提示语要先接住"从群里复制一整条 https 地址"这个动作');
  assert.match(ph, /open\.feishu\.cn/, '要把能粘贴的官方前缀写出来，不能只给内部简写');
  assert.match(ph, /加签密钥填下面那一格/);
  assert.match(urlPlaceholder('mail'), /mailto:/);
});

test('渠道的密钥框：password、永不回显，填了真会写回（新增与编辑共用一条构造）', () => {
  const src = readFileSync(new URL('../src/components/NotifyPanel.tsx', import.meta.url), 'utf8');
  assert.match(src, /aria-label="加密密钥（选填）"/, '没有密钥那一格 ⇒ 开了签名校验的机器人根本配不上');
  assert.match(src, /value=\{draft\.secret\} type="password"/, '密钥必须是 password 且值只来自用户这次敲的');
  assert.match(src, /c\.sign_warning/, '后端那句事前提醒要显示出来');
  assert.match(src, /c\.has_sign/, '要说清这条到底带没带加签');
  // 请求体的拼法住在纯函数 `channelPayload`（一份，新增与编辑共用）—— 原先面板里另拼一段，
  // 漏掉的就是 `note` 这一格：改了备注、保存，备注没进去。
  const p = channelPayload({ key: 'feishu-main', kind: 'feishu', label: '盯盘群',
    url: '', url_env: 'NOTIFY_FEISHU_URL', secret: '', secret_env: 'FEISHU_SECRET',
    note: ' 收盘看这个群 ', editing: true });
  assert.equal(p.url, undefined, '凭据留空却把空串送上去 = 后端可能把已存的地址清掉');
  assert.equal(p.secret, undefined, '加签密钥同理：空着就是不改');
  assert.equal(p.url_env, 'NOTIFY_FEISHU_URL');
  assert.equal(p.secret_env, 'FEISHU_SECRET');
  assert.equal(p.note, '收盘看这个群');
  assert.equal(channelPayload({ ...emptyChannelDraft(), url: ' feishu://TOKEN ' }).url, 'feishu://TOKEN');
});

test('已配置的渠道必须能点开看明细、能编辑保存（10-17 用户点名「点不开、改不了」）', () => {
  const src = readFileSync(new URL('../src/components/NotifyPanel.tsx', import.meta.url), 'utf8');
  assert.match(src, /看明细 \/ 编辑/, '行上要有一个把已配渠道展开的动作 —— 原本只有勾选/测试/删除');
  assert.match(src, /setChanDraft\(channelDraft\(c\)\)/, '编辑必须回填这一条的可读字段');
  assert.ok((src.match(/<ChannelForm /g) || []).length >= 2,
    '列表内编辑 + 列表外加新：两处都接上同一份表单（缺一处就是某一侧用不了）');
  assert.match(src, /readOnly=\{draft\.editing\}/,
    '编辑态不许改键 —— 改键＝新建一条，指着旧键的计划当场变成「渠道已删」');
  assert.match(src, /<ChannelDetails /, '明细要真渲染，不是只在 title 里');
  // 回填只交屏幕上本来就有的东西：两格凭据必须是空（把 `******` 填回输入框再保存＝把掩码当凭据写回）
  const d = channelDraft({ key: 'feishu-main', kind: 'feishu', label: '盯盘群',
    url_masked: '******6a9d', url_env: 'NOTIFY_FEISHU_URL', secret_env: 'FEISHU_SECRET',
    note: '收盘看', has_sign: true } as never);
  assert.equal(d.url, ''); assert.equal(d.secret, '');
  assert.equal(d.url_env, 'NOTIFY_FEISHU_URL');
  assert.equal(d.secret_env, 'FEISHU_SECRET', '变量名要回填：不回填就等于把这条指回 .env 的引用清了');
  assert.equal(d.note, '收盘看');
  assert.equal(d.editing, true);
  assert.equal(emptyChannelDraft().editing, false);
});

test('每一档内容自己的条数：候选与默认/上限都从后端 catalog 来', () => {
  const src = readFileSync(new URL('../src/components/NotifyPanel.tsx', import.meta.url), 'utf8');
  assert.match(src, /catalog\?\.scenes \|\| \[\]\)\.filter\(\(x\) => form\.scenes\.includes\(x\.value\)\)/,
    '条数框要按 catalog 里已勾的那些档渲染 —— 在前端写死档位就会与后端的表分叉');
  assert.match(src, /max=\{x\.max_limit \|\| 500\}/, '上限要用后端给的 max_limit');
  assert.match(src, /placeholder=\{x\.default_limit/, '默认值由后端交，留空就是用它');
  assert.match(src, /limitLabel\(catalog, x\.value,/,
    '那一格的标题也听后端（连板天梯是"列几层"，不是"条数上限"）');
  // `limitOf`：留空不写（不抄一份默认值进配置）—— 逻辑搬去了纯逻辑层，那边有真往返用例
  const formSrc = readFileSync(new URL('../src/lib/notifyPlanForm.ts', import.meta.url), 'utf8');
  assert.match(formSrc, /raw !== "" && Number\.isFinite\(n\) && n > 0 \? \{ limit: Math\.trunc\(n\) \} : \{\}/);
});

test('「保存计划」按钮不许静默禁用，且必须把"还差什么"摊在屏幕上（10-17 用户点名）', () => {
  const src = readFileSync(new URL('../src/components/NotifyPanel.tsx', import.meta.url), 'utf8');
  assert.doesNotMatch(src, /disabled=\{busy !== "" \|\| !form\.name/,
    '名字空着就 disabled —— 点下去一句话都不说，屏幕上就是"没反应"');
  assert.match(src, /missingParts\(form\)\.length/, '点下去要按缺项拦（而不是发一个注定被拒的请求）');
  assert.match(src, /还差这几样，所以这条没保存/, '拦下来必须说清缺哪几样');
  assert.match(src, /aria-label="计划名字（必填）"/, '必填要在字段上就标出来，不要等点了才知道');
  assert.match(src, /＊必选至少一档/, '内容那一排要标必选');
  assert.match(src, /＊必选至少一条/, '渠道那一排要标必选');
  // 保存成功那句还得说清"保存≠立刻发"，并给出路（10-17 同一句抱怨的后半）
  assert.match(src, /保存不等于立刻发/, '不然"配好了手机上一条都没有"只能靠人来问');
  assert.match(src, /立刻发一次[^\n]*真发到群里|那是真发到群里/, '要现在就验，得指出那一个按钮且说明会真发');
});

test('计划那一系列的回执必须显示在计划那一格里（10-19 用户第二次报"点了保存计划没反应"）', () => {
  const src = readFileSync(new URL('../src/components/NotifyPanel.tsx', import.meta.url), 'utf8');
  // 病根不是按钮，是**回执的位置**：它原本只印在卡片最上面，而这一屏约 14 屏高、
  // 「保存计划」在 13,900px 处 —— 点完眼睛那片一字不变，成功之后表单还收起，看起来就是"没反应"。
  assert.match(src, /setNoticeAt\(\/\^\(plan\|pe\|now\|pd\):\//, '要按动作把回执归位（保存/启用/立刻发/删除都算计划那一格）');
  assert.match(src, /noticeAt !== "plan" && err/, '卡片顶那行不能让计划的消息也一起显示（会两处各说一遍）');
  assert.match(src, /noticeAt === "plan" && \(err \|\| notice\)/, '计划那一格里必须有一条，成败都要说');
  assert.match(src, /scrollIntoView\(\{ block: "center" \}\)/, '深屏里要点完看得见，就得把自己滚进视野');
  // 顺手补的一条真洞：渠道行那个「启用/停用」开关原本没传 `keepForm`，
  // 于是**手一抖勾一下就把自己正在填的计划表单整份抹掉**（表单收起、没说一句话）——也是"没反应"。
  assert.match(src, /enabled: c\.enabled === false \}\), true\)\}/, '渠道开关不许把正在填的计划表单关掉');
});
