/** 个人微信推送的纯判据层（没有 fetch、没有 JSX —— `node --test` 能直接驱动）。
 *
 * 分工与 `notifyRules.ts` 一模一样：配置、校验、步骤表、预检闸的**真源在后端**
 * （`wxpushconfig.py` / `duanxian/wxpush.py`），这一层只做三件事：
 *  1. 把后端交回来的读数规成面板要的形状；
 *  2. 把「这一步现在是什么状态、为什么不能发」说成人话；
 *  3. 表单 ↔ 后端 payload 的往返（那几处有真用例，改判据先看测试）。
 *
 * ⚠️ 这一条通道不是网络请求，是**在这台机器的桌面上真按键盘**：
 *   所以「主按钮不许静默禁用」在这里尤其重要 —— 拦下来必须当场列出还差哪几样。
 */

export interface WxMode { value: string; label: string }

export interface WxStep {
  key: string; label: string; kind: string;
  enabled: boolean; mode: string; mode_label?: string | null; modes?: WxMode[] | null;
  value: string; value_label: string; point: number[];
  wait_ms: number; wait_default: number; hint: string; risky: boolean;
  is_default: boolean; invalid_reason?: string | null;
}

export interface WxGuard {
  key: string; label: string; type: string; value: boolean | number | null;
  default: boolean | number | null; unit?: string | null;
  min?: number | null; max?: number | null; note: string; is_default: boolean;
}

export interface WxTemplate {
  key: string; label: string; source: string; source_label: string;
  text: string; scenes: Record<string, Record<string, unknown>>;
  script: string; timeout_seconds: number; title: string;
  enabled: boolean; note: string; summary: string; invalid_reason?: string | null;
}

export interface WxCheck {
  key: string; label: string; ok: boolean; detail: string; required: boolean;
}

export interface WxPreflight {
  ok: boolean; blocked: string[]; checks: WxCheck[]; foreground: number;
  hwnd: number; idle_seconds: number | null; checked_at: string;
  reason?: string | null;
}

export interface WxOp { op: string; label: string; combo?: string | null; offset?: number[] | null }

export interface WxHistoryRow {
  at?: string; status?: string; stage?: string; to?: string; chars?: number;
  ok?: boolean; reason?: string; blocked?: string[]; aborted_at?: string;
  op_count?: number; elapsed_ms?: number; source?: string;
  checks_failed?: string[]; body_head?: string; via?: string;
}

export interface WxState {
  enabled: boolean; config_path: string; history_path: string; default_to: string;
  steps: WxStep[]; guards: WxGuard[]; templates: WxTemplate[];
  scene_choices?: { value: string; label: string }[];
  counts?: Record<string, number>; steps_enabled?: number;
  invalid_reason?: string | null; warning?: string | null;
  steps_dropped?: string[] | null;
  changed_at?: string | null; changed_by?: string | null;
  note?: string; explain?: string; api?: string; can_edit?: boolean;
  preflight?: WxPreflight | null; history?: WxHistoryRow[]; generated_at?: string;
}

/** 后端 `state()` 少了哪一块都会长成"面板上一片空白，用户以为没配"，所以先补齐再渲染。 */
export function normalizeState(data: Partial<WxState> | null | undefined): WxState {
  const out = (data || {}) as WxState;
  return {
    ...out,
    enabled: !!out.enabled,
    config_path: String(out.config_path || ''),
    history_path: String(out.history_path || ''),
    default_to: String(out.default_to || ''),
    steps: Array.isArray(out.steps) ? out.steps : [],
    guards: Array.isArray(out.guards) ? out.guards : [],
    templates: Array.isArray(out.templates) ? out.templates : [],
    scene_choices: Array.isArray(out.scene_choices) ? out.scene_choices : [],
    history: Array.isArray(out.history) ? out.history : [],
  };
}

export type Tone = 'ok' | 'warn' | 'bad';

/** 步骤行颜色：坏了（后端校验不过）> 关了/跳过 > 会动你桌上的东西 > 正常。 */
export function stepTone(step: WxStep): Tone {
  if (step.invalid_reason) return 'bad';
  if (!step.enabled || step.mode === 'skip') return 'warn';
  if (step.risky) return 'warn';
  return 'ok';
}

/** 一行读得懂的话：这一步现在**实际会做什么**（不是它名字）。
 * 「保持原尺寸」「不做这步」这类模式光看步骤名会被读成"它在做事"。 */
export function stepSummary(step: WxStep): string {
  if (!step.enabled) return '整步关掉：序列里不会出现这一步';
  if (step.mode === 'skip') return '这一步跳过';
  const bits = [step.mode_label || step.mode];
  if (step.value_label) bits.push(step.value_label);
  if (step.value && !step.value_label) bits.push(step.value);
  if (usesPoint(step)) bits.push(`窗口内偏移 ${step.point?.[0] ?? 0},${step.point?.[1] ?? 0}`);
  bits.push(`做完等 ${step.wait_ms} 毫秒`);
  return bits.join(' · ');
}

/** 只有选了「点固定坐标」那一档，坐标那两格才参与发送 —— 别在跳过时也渲染它 */
export function usesPoint(step: WxStep): boolean {
  return step.mode === 'click_point';
}

/** 有没有需要填组合键（勾了「只按快捷键」却没键 = 注定按不出来，主按钮要拦的就是这种） */
export function needsCombo(step: WxStep): boolean {
  if (!step.enabled || step.mode === 'skip') return false;
  if (step.kind === 'hotkey') return true;
  if (step.kind === 'keys') return true;
  if (step.kind === 'window' && String(step.mode).startsWith('hotkey')) return true;
  return false;
}

/** 预检逐条的颜色：必检没过是红；可选没过只是提示。 */
export function checkTone(c: WxCheck): Tone {
  if (c.ok) return 'ok';
  return c.required ? 'bad' : 'warn';
}

/** 「真发」那颗按钮拦下来时要说的那句：后端那句 `reason` 是给机器的，
 *  这一句是给人看的清单（用户 10-19 的规矩：拦住不发必须列出还差哪几样）。 */
export function blockedList(pre: WxPreflight | null | undefined): string[] {
  if (!pre) return ['还没读到预检结果（点「重新读取」再看一次）'];
  return (pre.checks || [])
    .filter((c) => c.required && !c.ok)
    .map((c) => `${c.label} —— ${c.detail}`);
}

export function preflightLine(pre: WxPreflight | null | undefined): string {
  if (!pre) return '预检结果还没读回来';
  if (pre.ok) return `预检全过：可以发（前台窗口 ${pre.foreground || '—'}，微信句柄 ${pre.hwnd || '—'}）`;
  const bad = (pre.checks || []).filter((c) => c.required && !c.ok);
  return `现在还不能发，差 ${bad.length} 样：${bad.map((c) => c.label).join('、')}`;
}

/** 发送结果那句人话。⚠️ 状态最多只到 `injected`：这一侧读不回回执，绝不替后端说"已送达"。 */
export function sendResultLine(row: Record<string, unknown>): { tone: Tone; text: string } {
  const status = String(row.status || '');
  const reason = typeof row.reason === 'string' ? row.reason : '';
  const ops = Number(row.op_count || 0);
  if (status === 'dry_run') {
    return row.ok
      ? { tone: 'ok', text: `预检通过：真发会按 ${ops} 个动作（下面逐条列出，**这次一个键都没按**）` }
      : { tone: 'bad', text: reason || '预检没过（dry_run 没按任何键）' };
  }
  if (status === 'injected') {
    return { tone: 'warn', text: `已按完 ${ops} 个动作。这一侧读不回回执 —— `
      + '到底进没进那个会话，**要到自己的手机微信上看那一条**。'
      + (reason ? `（后端原话：${reason}）` : '') };
  }
  if (status === 'aborted') {
    return { tone: 'bad', text: `按到一半停手了（停在「${String(row.aborted_at || '—')}」）：${reason}` };
  }
  if (status === 'blocked') {
    return { tone: 'bad', text: `预检拒发：${reason}` };
  }
  if (status === 'rejected') {
    return { tone: 'bad', text: `没发出去（${String(row.stage || '—')}）：${reason}` };
  }
  return { tone: 'bad', text: reason || `没发出去（状态 ${status || '未知'}）` };
}

export function historyLine(row: WxHistoryRow): string {
  const bits = [row.at || '—', row.status || '—'];
  if (row.to) bits.push(`→ ${row.to}`);
  if (row.chars) bits.push(`${row.chars} 字`);
  if (row.elapsed_ms) bits.push(`${row.elapsed_ms} 毫秒`);
  if (row.source) bits.push(String(row.source));
  if (row.aborted_at) bits.push(`停在「${row.aborted_at}」`);
  if (row.reason) bits.push(String(row.reason));
  return bits.join('｜');
}

export function historyTone(row: WxHistoryRow): Tone {
  if (row.ok && row.status === 'injected') return 'warn';   // 按完了但没回执，不绿
  if (row.ok && row.status === 'dry_run') return 'ok';
  if (row.status === 'rejected' || row.status === 'blocked') return 'warn';
  return row.ok ? 'ok' : 'bad';
}

// ---------------------------------------------------------------- 表单往返

export interface TemplateForm {
  key: string; label: string; source: string; text: string; title: string;
  sceneKeys: string[]; script: string; timeout_seconds: number;
  enabled: boolean; note: string; editing: boolean;
}

export function emptyTemplateForm(): TemplateForm {
  return { key: '', label: '', source: 'text', text: '', title: '', sceneKeys: [],
    script: '', timeout_seconds: 30, enabled: true, note: '', editing: false };
}

export function templateFormFrom(t: WxTemplate): TemplateForm {
  return { key: t.key, label: t.label, source: t.source || 'text', text: t.text || '',
    title: t.title || '', sceneKeys: Object.keys(t.scenes || {}), script: t.script || '',
    timeout_seconds: t.timeout_seconds || 30, enabled: t.enabled !== false,
    note: t.note || '', editing: true };
}

/** 写回后端：**只交这一种来源该交的字段**，否则把「固定文字」切到「内容档」时
 *  旧那格的文字会留在配置里，读回来又是一份混合形状（本项目吃过好几次）。 */
export function templatePayload(f: TemplateForm): Record<string, unknown> {
  const out: Record<string, unknown> = { key: f.key, label: f.label, source: f.source,
    enabled: f.enabled, note: f.note };
  if (f.source === 'text') out.text = f.text;
  if (f.source === 'scene') out.scenes = Object.fromEntries(f.sceneKeys.map((k) => [k, {}]));
  if (f.source === 'scene' && f.title) out.title = f.title;
  if (f.source === 'script') { out.script = f.script; out.timeout_seconds = f.timeout_seconds; }
  return out;
}

/** 新增/编辑同一份表单，保存前那句「还差哪几样」—— 空手点保存不该发一次注定 400 的请求。 */
export function templateMissing(f: TemplateForm): string[] {
  const miss: string[] = [];
  if (!f.key.trim()) miss.push('键名（英文，面板与接口都靠它认这一条）');
  if (!f.label.trim()) miss.push('名字');
  if (f.source === 'text' && !f.text.trim()) miss.push('要发的固定文字');
  if (f.source === 'scene' && !f.sceneKeys.length) miss.push('至少勾一档内容');
  if (f.source === 'script' && !f.script.trim()) miss.push('脚本路径（仓库内的相对路径）');
  return miss;
}

export interface StepForm {
  key: string; enabled: boolean; mode: string; value: string;
  pointX: number; pointY: number; waitMs: number;
}

export function stepFormFrom(s: WxStep): StepForm {
  return { key: s.key, enabled: s.enabled, mode: s.mode, value: s.value,
    pointX: Number(s.point?.[0] ?? 0), pointY: Number(s.point?.[1] ?? 0),
    waitMs: s.wait_ms };
}

/** 只交改过的字段（后端 `set_step` 按字段合并）；全没改就别发这个请求。 */
export function stepPayload(s: WxStep, f: StepForm): Record<string, unknown> | null {
  const out: Record<string, unknown> = { key: f.key };
  if (f.enabled !== s.enabled) out.enabled = f.enabled;
  if (f.mode !== s.mode) out.mode = f.mode;
  if (f.value !== s.value) out.value = f.value;
  if (usesPoint(s) && (f.pointX !== Number(s.point?.[0] ?? 0) || f.pointY !== Number(s.point?.[1] ?? 0)))
    out.point = [f.pointX, f.pointY];
  if (f.waitMs !== s.wait_ms) out.wait_ms = f.waitMs;
  return Object.keys(out).length > 1 ? out : null;
}

export function stepMissing(s: WxStep, f: StepForm): string[] {
  const miss: string[] = [];
  if (needsCombo({ ...s, mode: f.mode, enabled: f.enabled }) && !f.value.trim())
    miss.push('这一格「按哪个组合键」是空的（空着等于这一步按不出来）');
  if (usesPoint({ ...s, mode: f.mode }) && !(f.pointX >= 0 && f.pointY >= 0))
    miss.push('坐标那两格要填非负数');
  if (!(f.waitMs >= 0)) miss.push('等待毫秒数要 ≥ 0');
  return miss;
}

/** 面板上「按哪个键」那一格的形状提示（不让用户猜语法 —— 后端只认 `修饰键+单键`）。 */
export const COMBO_HINT = '写成 ctrl+f、alt+1、enter、ctrl+shift+f 这种；只认 Ctrl / Alt / Shift / Win 加一个键';

export function guardValueLabel(g: WxGuard): string {
  if (g.type === 'bool') return g.value ? '开着' : '关着';
  return `${String(g.value)}${g.unit ? ` ${g.unit}` : ''}`;
}
