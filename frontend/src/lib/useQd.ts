import { useEffect, useState } from "react";
import { fetchBoardLadder, fetchDistribution, fetchLeaderFollow, fetchOwnSectorCycle, fetchQdNews, fetchSectorCycle, fetchSentimentCycle } from "./qd";
import type { BoardLadder, Distribution, OwnSectorCycle, QdNews, QdState, SectorCycle, SectorFollow, SentimentCycle } from "./qd";

/**
 * 各面板共用的取数 hook。
 *
 * `date === ""` 表示"不传 date，让后端取 qd 表最新 trade_date" —— 契约明令
 * 不许前端回落到"今天"（今天可能没收盘、也可能不是交易日），所以这里刻意
 * 不预填当天日期。
 */
function useQd<T>(loader: (date?: string) => Promise<QdState<T>>, date: string, nonce: number): QdState<T> {
  const [state, setState] = useState<QdState<T>>({ kind: "loading" });
  useEffect(() => {
    let live = true;
    setState({ kind: "loading" });
    loader(date || undefined).then(next => { if (live) setState(next); });
    return () => { live = false; };
  }, [loader, date, nonce]);
  return state;
}

export const useSentimentCycle = (date: string, nonce: number) =>
  useQd<SentimentCycle>(fetchSentimentCycle, date, nonce);
export const useBoardLadder = (date: string, nonce: number) =>
  useQd<BoardLadder>(fetchBoardLadder, date, nonce);
export const useSectorCycle = (date: string, nonce: number) =>
  useQd<SectorCycle>(fetchSectorCycle, date, nonce);
/**
 * 前三甲 + 轨迹 + 相似板块（`/api/qd/sector-follow`，**读 `qd.sector_follow` 落库行**）。
 * 2026-10-25 起判据在每日加工链的 `qd_sector` 那一步算好落库，接口不再回母体现算
 * （旧注释写的"现算约 2 秒、缓存一小时"是搬家前的事，已作废）。
 * loader 必须是**模块级常量**，写进组件会变成每次渲染新函数、界面停在"取数中…"。
 */
const followLoader = (date?: string) => fetchLeaderFollow(date);
export const useLeaderFollow = (date: string, nonce: number) =>
  useQd<SectorFollow>(followLoader, date, nonce);
/** 本轨板块周期（`/api/market/sector-cycle`，已上线） */
export const useOwnSectorCycle = (date: string, nonce: number) =>
  useQd<OwnSectorCycle>(fetchOwnSectorCycle, date, nonce);
/**
 * `fetchQdNews` 有两个参数，直接交给 useQd 会因签名不符报错；包一层必须是
 * **模块级常量** —— 写在 hook 里会变成每次渲染新函数，进 useEffect 依赖后
 * 反复重置成 loading，界面永远停在"取数中…"。
 */
const newsLoader = (date?: string) => fetchQdNews(date);

/** 本地快讯（`/api/qd/news`） */
export const useQdNews = (date: string, nonce: number) =>
  useQd<QdNews>(newsLoader, date, nonce);

/** 区间分布不吃日期选择 —— 它统计的是整段历史，随刷新按钮重取即可 */
export function useDistribution(nonce: number, days = 0) {
  const [state, setState] = useState<QdState<Distribution>>({ kind: "loading" });
  useEffect(() => {
    let live = true;
    setState({ kind: "loading" });
    fetchDistribution(days).then(next => { if (live) setState(next); });
    return () => { live = false; };
  }, [days, nonce]);
  return state;
}
