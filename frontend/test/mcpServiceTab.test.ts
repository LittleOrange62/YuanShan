import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';

/**
 * 钉住「AI 探索 · MCP 服务」这一片**真的接到了调用点**：
 * tab 注册在案、面板读的是后端那三条 `/api/mcp/*`、条数由接口现算、按钮拦下来会点名缺什么。
 *
 * 后端侧的对照（工具 ↔ 端点 ↔ 屏幕）由 `tests/test_mcp_registry_sync.py` 逐条把关，
 * 这一份只管前端这一半：别出现第二份清单、别把数字抄死在页面上。
 */

const settings = fs.readFileSync(new URL('../src/pages/Settings.tsx', import.meta.url), 'utf8');
const panel = fs.readFileSync(new URL('../src/components/settings/McpServicePanel.tsx', import.meta.url), 'utf8');
const sys = fs.readFileSync(new URL('../src/pages/System.tsx', import.meta.url), 'utf8');

test('MCP 服务是「AI 探索」的第三片 tab，且 tab 解析不再写死两值', () => {
  assert.match(settings, /\{ id: 'mcp', label: 'MCP 服务', icon: Server \}/);
  assert.match(settings, /TABS\.some\(t => t\.id === fromUrl\)/,
    '解析必须由 TABS 派生；写死两值的话，加第三片就静默回落到 chat');
  assert.match(settings, /ai-panel-mcp/);
  assert.match(settings, /<McpServicePanel \/>/);
});

test('面板的清单与条数全部现读接口，页面上没有第二份台账', () => {
  assert.match(panel, /useProbe<Registry>\('\/mcp\/registry'/);
  assert.match(panel, /useProbe<Status>\('\/mcp\/status'/);
  assert.match(panel, /apiUrl\('\/api\/mcp\/try'\)/,
    '试调用必须走智能体用的那一条（后端 mcpboard.try_invoke），不是前端自己拼 URL');
  assert.match(panel, /registry\.data\.capabilities/, '清单一律来自登记表接口');
  assert.doesNotMatch(panel, /24 个|8 个|共 \d+ 条/, '条数抄死就会漂');
});

test('自检取不到就显示「未取到」，不许兜成通过', () => {
  assert.match(panel, /未取到/);
  assert.match(panel, /c\.available\s*\?\s*<CheckCircle2/s, '绿勾只能来自接口给的那个布尔值');
  assert.match(panel, /调用失败：[\s\S]*这不是「今天没有数据」/);
});

test('主按钮拦下来会点名还缺哪几样', () => {
  assert.match(panel, /blockers\.length > 0/);
  assert.match(panel, /还没法调用，缺：/, 'disabled 不能是静默的（用户 10-19 定的交付口径）');
});

test('屏幕上不出现 markdown 星号：后端散文字段一律过 plain()', () => {
  assert.match(panel, /const plain = \(s: string\) => \(s \|\| ''\)\.replace\(\/\\\*\\\*\/g, ''\)/,
    '共用出口必须在，且只剥星号、不碰数据值本身');
  for (const label of ['plain(c.label)', 'plain(c.reason)', 'plain(c.detail)', 'plain(r)', 'plain(err)',
                       'plain(out.reason ||', 'plain(out.label)', 'plain(c.where)', 'plain(c.note)']) {
    assert.ok(panel.includes(label), `渲染点少了 ${label}`);
  }
});

test('A/B 层新增的两条屏幕能力也登记进了系统自检清单', () => {
  for (const p of ['/market/emotion', '/quant/sector-trend', '/stock/indicators']) {
    assert.ok(sys.includes(p), `${p} 要出现在 CAPABILITIES 里，与 MCP 工具成对`);
  }
});
