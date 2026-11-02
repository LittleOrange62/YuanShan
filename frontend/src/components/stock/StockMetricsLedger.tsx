import { useMemo, useState } from "react";
import { RefreshCw, Search } from "lucide-react";
import { Caveats, Panel } from "@/components/ui/DataPanel";
import { DocDrawer, DrawerSection } from "@/components/ui/DocDrawer";
import { plain } from "@/lib/agent";
import { useNonce, useProbe } from "@/lib/probe";
import { dash } from "@/lib/datastate";
import { cn } from "@/lib/utils";

/**
 * 全指标总账（用户 2026-10-16 第 ④ 条：「把所有的指标再汇总一遍……把逻辑和各种指标的含义都说清楚」）。
 *
 * 三件事要说清，且都不由前端编：
 *  · 有哪几块、每块的数**出自哪张表 / 哪个模块**（写入方，不是页面）；
 *  · 每个字段在说什么话 —— 这张清单是后端**现场 import 各模块那份 definitions** 取来的活对象，
 *    这里不复制第二份措辞（复制＝第二份真相，迟早对不上）；
 *  · 没有独立 definitions 的块只登记出处与逻辑，字段义留在各接口自己的 payload 里，
 *    这一句必须上屏，否则读者会以为"总账里没有＝这块没指标"。
 * 搜索框只筛这一屏已经交出的行，不改后端取数口径。
 *
 * 2026-10-31（用户全站第 1 条）：这一块原来把口径散在两处 —— 标题行那枚 `Caliber` 问号角标
 * （悬停才念）与 `Panel note=` 那颗长在 Panel 标题行上的按钮（一句搜筛状态话术）。
 * 现在合成标题行**最右**的一颗「这块的说明」，刷新/搜索框那两件元信息排在按钮后面；
 * 后端那句 `how_to_read` 原来裸摊在表头上面一行，同属说明，一起进抽屉（屏上不再出现第二遍）。
 * 每一块自己的 `fields_note`（「字段义在该接口 payload 里」那类）按文件头那条纪律**留在屏上**。
 */

interface LedgerField { name: string; says: string }
interface LedgerBlock {
  key: string; title: string; endpoint?: string | null; owner?: string | null;
  source?: string | null; logic?: string | null; fields_note?: string | null;
  fields?: LedgerField[] | null; field_count?: number | null; caliber?: string | null;
}
interface LedgerPayload {
  available: boolean; reason?: string | null; spec?: string | null; code?: string | null;
  page?: string | null; blocks?: LedgerBlock[] | null; field_total?: number | null;
  how_to_read?: string | null; caveats?: { code?: string; text?: string }[] | null;
}

export function StockMetricsLedger({ code }: { code: string | null }) {
  const [nonce, refresh] = useNonce();
  const [q, setQ] = useState("");
  const state = useProbe<LedgerPayload>("/stock/metrics-ledger", nonce, { code: code ?? "" });
  const blocks = state.kind === "ok" ? (state.data.blocks ?? []) : [];
  const needle = q.trim().toLowerCase();
  const shown = useMemo(() => blocks.map(b => {
    if (!needle) return b;
    const hit = (s?: string | null) => (s ?? "").toLowerCase().includes(needle);
    const fields = (b.fields ?? []).filter(f => hit(f.name) || hit(f.says));
    return hit(b.title) || hit(b.source) || hit(b.owner) || fields.length
      ? { ...b, fields: needle ? fields : b.fields, field_count: fields.length } : null;
  }).filter(Boolean) as LedgerBlock[], [blocks, needle]);
  const totalFields = blocks.reduce((n, b) => n + (b.field_count ?? 0), 0);
  const led = state.kind === "ok" ? state.data : null;
  // 原来挂在 `Panel note=` 上那句筛法状态话，现在进标题行那颗按钮的抽屉（一模块一颗）。
  const searchNote = `搜到 ${shown.length} 块（只在已交出的行里筛，不改后端口径）`
    + `${code ? `；本屏选的是 ${code}` : "；还没选一只，总账与代码无关，照样能看"}`;

  return (
    <div>
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <h2 className="text-base font-bold">全指标总账</h2>
        {/* 用户 2026-10-31：说明按钮摆在这块标题行的最右，按钮在前，搜索框与「重新取数」排在它后面。
            抽屉里只有原先就摊在屏上/悬停里的句子与后端原句，页面不新写一句口径。 */}
        <span className="ml-auto flex flex-wrap items-center gap-2">
          <DocDrawer title="全指标总账">
            <DrawerSection heading="这一屏有哪几块、字段义从哪来">
              <p>
                这一屏有哪几块、每块的数出自哪张表或哪个模块、每个字段在说什么话，全在这张表里；
                字段清单是后端现场取各模块那份 definitions，不是第二份措辞。
                某一格此刻是「有数／判不了／库里没这一列」由那一块自己上屏，这张总账不替读数下结论。
              </p>
            </DrawerSection>
            <DrawerSection heading="怎么读这一屏（后端原句）">
              <p>{led?.how_to_read ? plain(led.how_to_read) : '后端这一次没交出这句话（没读到数时本来就没有）。'}</p>
            </DrawerSection>
            <DrawerSection heading="搜字段时这一屏在说什么">
              <p>{searchNote}。</p>
            </DrawerSection>
          </DocDrawer>
          <label className="flex items-center gap-1 text-[11px] text-muted-foreground">
            <Search className="h-3.5 w-3.5" />
            <input value={q} onChange={e => setQ(e.target.value)}
              placeholder="搜字段名或说法，如 倍量 / 情景带"
              className="w-56 rounded border border-border/60 bg-muted/30 px-2 py-1 text-[11px] text-foreground" />
          </label>
          <button type="button" onClick={refresh} title="重新取总账"
            className="flex items-center gap-1 rounded-lg bg-primary/15 px-2 py-1 text-[11px] font-medium text-primary hover:bg-primary/25">
            <RefreshCw className="h-3.5 w-3.5" /> 重新取数
          </button>
        </span>
      </div>

      <Panel title={`这块屏上一共 ${totalFields} 个字段，分 ${blocks.length} 块`} state={state}>
        {p => (
          <>
            {/* `p.how_to_read` 搬进了上面那颗按钮的抽屉；每一块自己的 `fields_note` 仍摊在块内
                （文件头那条纪律：不写这一句，读者会以为"总账里没有＝这块没指标"）。 */}
            {shown.map(b => (
              <section key={b.key} className="mb-3 rounded-lg border border-border/60 bg-muted/20 p-2">
                <h3 className="text-[12px] font-bold text-foreground">
                  {b.title}
                  <span className="ml-1 font-normal text-muted-foreground">
                    {b.field_count ? `${b.field_count} 个字段` : "字段义在该接口自己的 payload 里"}
                  </span>
                </h3>
                <dl className="mt-1 grid gap-1 text-[11px] leading-relaxed sm:grid-cols-[70px_1fr]">
                  <dt className="text-muted-foreground">接口</dt>
                  <dd className="font-mono text-foreground">{dash(b.endpoint)}</dd>
                  <dt className="text-muted-foreground">出自</dt>
                  <dd className="text-foreground">{dash(b.source)}</dd>
                  <dt className="text-muted-foreground">判据住</dt>
                  <dd className="font-mono text-[10px] text-muted-foreground">{dash(b.owner)}</dd>
                  <dt className="text-muted-foreground">逻辑</dt>
                  <dd className="text-foreground">{dash(b.logic)}</dd>
                </dl>
                {b.fields_note && (
                  <p className={cn("mt-1 rounded border border-border/50 bg-background/40 p-1.5",
                    "text-[11px] leading-relaxed text-muted-foreground")}>{b.fields_note}</p>
                )}
                {!!(b.fields ?? []).length && (
                  <table className="mt-1 w-full text-[11px]">
                    <thead>
                      <tr className="text-left text-muted-foreground">
                        <th className="w-[180px] px-1.5 py-1 font-medium">字段</th>
                        <th className="px-1.5 py-1 font-medium">这一格在说什么话</th>
                      </tr>
                    </thead>
                    <tbody>
                      {(b.fields ?? []).map(f => (
                        <tr key={f.name} className="border-t border-border/40 align-top">
                          <td className="px-1.5 py-1 font-mono text-[10px] text-primary">{f.name}</td>
                          <td className="px-1.5 py-1 leading-relaxed text-foreground">{f.says}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                )}
                {needle && !(b.fields ?? []).length && !!b.field_count && (
                  <p className="mt-1 text-[11px] text-muted-foreground">
                    这一块里搜不到「{q}」——上面那几块是别的块里命中的行；不代表这块没这个字段。
                  </p>
                )}
              </section>
            ))}
            {!shown.length && (
              <p className="text-[11px] leading-relaxed text-warning">
                搜「{q}」一块都没命中。这张表只收这一屏的块，搜不到不等于这个指标不存在 ——
                换一块屏（如首页短线情绪、倍量、突破信号）看它自己的功能点说明。
              </p>
            )}
            <Caveats items={p.caveats ?? []} />
          </>
        )}
      </Panel>
    </div>
  );
}
