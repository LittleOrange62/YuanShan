/**
 * 「这个端点到底算不算接上了」的**纯判定层**（系统配置页逐能力探测用）。
 *
 * 单独成文件、零 React import，是为了能被 `node --test` 直接钉住 ——
 * 这一屏唯一的价值就是把**四种"没有"分开说**：
 *  · `not_wired`（404）：这一屏根本没有数据来源；
 *  · `error` / `offline`：有了但坏着 / 连不上；
 *  · `available:false`：实现了、今天没数，后端自己道了歉；
 *  · **200 但没交 `available`**：实现了、忘了表态。
 * 最后这一档以前被算成「可用」，于是"忘了表态"的一排端点集体显绿 ——
 * 把"没说不行"读成"行"，正是三天没落库能藏住的那类错。
 */
import { describeState, type Availability, type DataState } from "./datastate.ts";

export type ProbeKind = "ok" | "unavailable" | "broken" | "missing" | "pending";

export interface ProbeVerdict {
  /** 屏幕上那个码：404 / 报错 / 连不上 / 200 / 200? */
  code: string;
  kind: ProbeKind;
  note: string;
}

export function verdictOf(state: DataState<Availability>): ProbeVerdict {
  if (state.kind === "not_wired") return { code: "404", kind: "missing", note: `端点未实现（${state.path}）—— 那一屏只能显示字段契约` };
  if (state.kind === "error") return { code: "报错", kind: "broken", note: state.reason };
  if (state.kind === "offline") return { code: "连不上", kind: "broken", note: state.reason };
  if (state.kind === "loading") return { code: "…", kind: "pending", note: "探测中" };
  if (state.kind === "idle") return { code: "未发", kind: "pending", note: state.reason };
  const text = describeState(state);
  if (text) return { code: "200", kind: "unavailable", note: text };
  // 走到这里说明后端既没报错，也没说 available:false —— 那它必须说 available:true 才算可用。
  if (state.data.available !== true) {
    return { code: "200?", kind: "unavailable", note: "接口在、也返回 200，但没交 available:true —— 不能算「可用」" };
  }
  return { code: "200", kind: "ok", note: "可用" };
}
