import { useEffect, useState } from "react";

/**
 * 一屏多块时的**分批放行**（用户 2026-11-01 点名 /quadrant：
 * 「页面打开的时候，每个模块都显示加载中，那就直接卡住了，能否逐步加载，先加载 2 个，再 3s 后继续加载」）。
 *
 * 判据只住这里一份：
 *  · 节奏（一批两块、每约 3 秒放开下一批）是用户给的两个数，不是实测调出来的；
 *  · 顺序＝读者从屏上往下看的顺序（页面按 DOM 顺序排号），所以第一屏那块永远最先出数；
 *  · 排队中那一块屏上念的那句（`STAGE_QUEUE_NOTE`）也必须在这里 —— 每页各写一遍早晚分叉，
 *    而这句话写错了就是谎话：把"还没发"说成"取数中"，读者收到的还是"卡住了"。
 *
 * 这一份刻意不 import 取数层（`probe.ts` 会连出 api/datastate 那一串），这样 `node --test`
 * 能直接打这几个纯函数。
 */

/** 第一批放几块出去 */
export const IN_FLIGHT_BATCH = 2;
/** 之后每隔多久放开下一批。这是**上限**：慢的那几块本来就要等，分批要解决的是"别一起抢、先给读者一块能读的"。 */
export const STAGE_STEP_MS = 3_000;

/** 第 `i` 块在第几毫秒放行（0 起算；一块一批＝不排队） */
export function stageReleaseAt(i: number, batch = IN_FLIGHT_BATCH, stepMs = STAGE_STEP_MS): number {
  return Math.floor(i / Math.max(1, batch)) * stepMs;
}

/** 第 `i` 块现在该不该发请求（单调：到点就放行，不会又关回去） */
export function stageOpen(i: number, elapsedMs: number,
                          batch = IN_FLIGHT_BATCH, stepMs = STAGE_STEP_MS): boolean {
  return elapsedMs >= stageReleaseAt(i, batch, stepMs);
}

/** 这一屏一共 `total` 块，返回每块「现在能不能发请求」。
 *  调用方把取反交给 `useProbe` 的 `skip`，并把 `STAGE_QUEUE_NOTE` 交给它的 `idleNote`。 */
export function useStageLadder(total: number, batch = IN_FLIGHT_BATCH, stepMs = STAGE_STEP_MS): boolean[] {
  const [elapsed, setElapsed] = useState(0);
  const last = stageReleaseAt(Math.max(0, total - 1), batch, stepMs);
  useEffect(() => {
    if (elapsed >= last) return undefined;   // 全放行完了就把计时器收掉，别空转
    const t = setTimeout(() => setElapsed(e => e + stepMs), stepMs);
    return () => clearTimeout(t);
  }, [elapsed, last, stepMs]);
  return Array.from({ length: total }, (_, i) => stageOpen(i, elapsed, batch, stepMs));
}

/** 排队中那一块要说的那一句（全站一份） */
export const STAGE_QUEUE_NOTE =
  "这一块排在上面那块之后自动取数（现在还没发这一条请求）—— 不是这一屏没有数据，也不用点「重新取数」。"
  + "一屏几块一起抢后端时读到的就是满屏转圈，所以一次只先取两块。";
