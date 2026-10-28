import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';

/**
 * 验收《系统配置》09-27 挂到系统配置页的「局域网访问」面板，顺带钉住两条会过期的写法。
 *
 * 三条事实必须一直在（他们报备时也是这么写的）：
 *   ① 总开关要**重启**才换绑定地址（所以「进程实际绑定」与「配置要求的绑定」要同时给）；
 *   ② **加白 / 删白立即生效**，不用重启；
 *   ③ 只有回环来源能改这一格（`can_edit=false` 时控件禁用并说明原因）。
 * ②原来只在"要重启"那条横幅里出现一次 —— 状态一致时（今天就是）整句话根本不显示，
 * 于是这张卡最主要的"两种时机不一样"就看不见了，所以白名单那一段里必须常驻一句。
 */

const sys = fs.readFileSync(new URL('../src/pages/System.tsx', import.meta.url), 'utf8');
const lan = fs.readFileSync(new URL('../src/components/LanAccessPanel.tsx', import.meta.url), 'utf8');

test('体检项数由接口说，页面不再写死「11 项」', () => {
  assert.doesNotMatch(sys, /doctor 的 \d+ 项/, '标题里写死数字，doctor 加项后就成假话（同一类坑在 /api/jobs 吃过）');
  assert.match(sys, /hasData\(doctor\) \? \(doctor\.data\.items \?\? \[\]\)\.length/, '项数必须从 doctor 返回里数');
  assert.match(sys, /共 \{doctorCount\} 项|逐项结论\$\{doctorCount/, '标题里的数要用那个派生值');
});

test('局域网面板把两种生效时机分开说，且常驻', () => {
  assert.match(lan, /进程实际绑定/);
  assert.match(lan, /配置要求的绑定/);
  assert.match(lan, /要重启服务才生效/, '总开关：绑定地址启动那一刻定死');
  assert.match(lan, /加白 \/ 删白立即生效/, '白名单：每个请求现读，常驻在白名单那一段里');
  assert.match(lan, /加白 \/ 删白不用重启/, '要重启那条横幅里也得提醒一句"这一半不用"');
  assert.ok(lan.indexOf('加白 / 删白立即生效') < lan.indexOf('<ul className="mt-1.5 space-y-1">'),
    '常驻那句要在白名单列表之前 —— 放到 only-when-needs-restart 的横幅里就等于没有');
});

test('只有回环能改：禁用要带原因，颜色不借涨跌色', () => {
  assert.match(lan, /disabled=\{!s\.can_edit/, 'can_edit=false 时控件必须禁用');
  assert.match(lan, /s\.can_edit && s\.edit_reason/, '禁用要给出后端那句原因，不能只是灰着');
  assert.doesNotMatch(lan, /UP_TEXT|DOWN_TEXT|pctColor/, '网络开关与涨跌无关，不许蹭涨跌色');
  assert.match(lan, /jobCardClass|jobLabelClass/, '配色走 lib/colors 的中性档位');
});
