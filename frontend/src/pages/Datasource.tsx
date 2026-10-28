import { Link } from "react-router-dom";
import { RefreshCw } from "lucide-react";
import { PageHeader } from "@/components/ui/PageHeader";
import { Disclaimer } from "@/components/ui/Disclaimer";
import { GlassCard } from "@/components/ui/GlassCard";
import { CaveatText, Contract, Panel, Provenance, Row } from "@/components/ui/DataPanel";
import { useNonce, useProbe } from "@/lib/probe";
import { SortHeader } from "@/components/ui/SortHeader";
import { useTableSort } from "@/lib/useSort";
import { ageText, dash } from "@/lib/datastate";
import { plain } from "@/lib/agent";
import { cn } from "@/lib/utils";

/**
 * 数据源健康（映射表 #9）。
 *
 * 这一屏回答的是用户最本质的那个问题：**这些免费的源，今天还能不能用。**
 * 读的是 `config.datasource`（30 行）+ `operations.datasource_probe` 的末次探测。
 *
 * ⚠️ 这一屏最容易撒的谎是**把"上次探测时说能用"演成"现在能用"**：本项目没有调度，
 * 探测不会自己跑 —— 接口实测 30 个源里 **29 个的探测已过期**（最新一次距今 38 小时，阈值 7 天）。
 * 所以"这屏有多旧"放在第一行、`probe_stale` 逐源标出来，而不是只在页脚写一句。
 * 页面**不做假探测**：没有触发端点就不摆"重新探测"按钮。
 */

interface Source {
  datasource_key?: string | null; name?: string | null; kind?: string | null; provider?: string | null;
  layer?: string | null; ds_role?: string | null; cost?: string | null; health?: string | null;
  is_active?: boolean | null; last_probe_at?: string | null; stale_seconds?: number | null;
  probe_stale?: boolean | null; last_ok_at?: string | null; last_err?: string | null;
  latency_ms?: number | null; row_count?: number | null; auth_env_name?: string | null; note?: string | null;
}
interface ProbeSummary {
  sources_total?: number | null; never_probed?: number | null; stale_now?: number | null;
  newest_probe_age_seconds?: number | null; stale_threshold_seconds?: number | null; note?: string | null;
}
interface Datasource {
  available?: boolean | null; reason?: string | null; generated_at?: string | null; basis?: string | null;
  counts?: Record<string, number> | null; health_order?: string[] | null;
  probe?: ProbeSummary | null; sources?: Source[] | null;
}

/**
 * 状态的配色档。**`no_permission` 与 `down` 必须长得不一样**：
 * 前者是"我去授权就能解"，后者是"源死了"，动作完全不同，混成一个灰就等于把两件事压成一件事。
 * 取值不写死 —— 库里出现新档位时原样显示字符串，落到中性档，不猜语义。
 */
function healthTone(h?: string | null): "ok" | "perm" | "dead" | "none" {
  if (!h) return "none";
  if (h === "ok") return "ok";
  if (h === "no_permission") return "perm";
  if (h === "down" || h === "broken") return "dead";
  return "none";
}

/** 这一档对用户的动作是什么 —— 只写实测确定的那几档，出现新取值就留空，不猜语义 */
const HEALTH_ACTION: Record<string, string> = {
  ok: "能用",
  no_permission: "缺授权：补 key / 权限即可解，与源死没死无关",
  down: "源侧不可达",
  broken: "接口或数据形状坏了",
  unknown: "没判出状态",
};

export function Datasource() {
  const [nonce, refresh] = useNonce();
  const ds = useProbe<Datasource>("/datasource", nonce);
  const srcSort = useTableSort();

  return (
    <div className="mx-auto max-w-5xl">
      <PageHeader title="数据获取" level={1}
        subtitle="30 个源逐个看：登记的状态是什么、上次探测是什么时候、那个状态还能不能信。数据是登记出来的，不是猜的。"
        actions={<button type="button" onClick={refresh}
          className="flex items-center gap-1 rounded-lg bg-primary/15 px-2.5 py-1.5 text-[12px] font-medium text-primary hover:bg-primary/25">
          <RefreshCw className="h-3.5 w-3.5" /> 重新读取
        </button>} />

      <div className="space-y-4">
        <Panel title="源健康登记" state={ds}
          note="读 config.datasource + operations.datasource_probe 的末次探测。没有重新探测的触发端点，所以本页只报「上次探出来的是什么」。"
          contract={<Contract path="GET /api/datasource" source="config.datasource（30 行）+ operations.datasource_probe（末次）" fields={[
            ["counts / health_order", "按 health 分类的只数（后端自己数）与档位的展示顺序"],
            ["probe{stale_now,newest_probe_age_seconds,stale_threshold_seconds,never_probed}", "探测的新鲜度：没有调度时这一组决定整屏可信度"],
            ["sources[]{last_probe_at,stale_seconds,probe_stale}", "逐源的末次探测时刻与是否已过期"],
            ["basis", "这屏的出处"],
          ]} />}>
          {d => {
            const p = d.probe ?? {};
            const sources = d.sources ?? [];
            const staleCount = sources.filter(s => s.probe_stale === true).length;
            const neverProbed = sources.filter(s => !s.last_probe_at).length;
            return (
              <>
                <Row label="本屏生成于" value={`${dash(d.generated_at)}`} />
                <Row label="最新一次探测距今"
                  value={`${ageText(p.newest_probe_age_seconds)}${typeof p.stale_threshold_seconds === "number" ? `（过期阈值 ${ageText(p.stale_threshold_seconds)}）` : ""}`}
                  tone={typeof p.newest_probe_age_seconds === "number" && typeof p.stale_threshold_seconds === "number"
                    && p.newest_probe_age_seconds > p.stale_threshold_seconds ? "bad" : undefined} />
                <Row label="探测已过期 / 从未探测"
                  value={`${dash(p.stale_now)} / ${dash(p.never_probed)} 个（共 ${dash(p.sources_total)}）—— 逐源见下面「上次探测」列的过期标记（本页实数：${staleCount} 过期 · ${neverProbed} 从未探）`}
                  tone={(p.stale_now ?? 0) > 0 ? "warn" : undefined} />
                <p className="mt-1 text-[11px] leading-relaxed text-warning">
                  所以这一屏的 <code>health</code> 是<b className="text-foreground">历史值</b>：
                  它回答"上次探到的时候是什么状态"，不回答"现在能不能用"。
                </p>
                <CaveatText text={p.note} />

                <div className="mt-2 flex flex-wrap gap-1.5">
                  {(d.health_order ?? Object.keys(d.counts ?? {})).map(h => (
                    <span key={h} className={cn("rounded px-1.5 py-0.5 text-[11px]", pillClass(healthTone(h)))}>
                      {h} {d.counts?.[h] ?? 0}
                      <span className="ml-1 text-[10px] opacity-80">{HEALTH_ACTION[h] ?? ""}</span>
                    </span>
                  ))}
                  {typeof d.counts?.total === "number" && (
                    <span className="rounded bg-muted px-1.5 py-0.5 text-[11px] text-muted-foreground">合计 {d.counts.total}</span>
                  )}
                </div>

                <div className="mt-2 overflow-x-auto">
                  <table className="w-full text-[11px]">
                    <thead><tr className="text-left text-muted-foreground">
                      <SortHeader label="源" sortKey="name" dir={srcSort.dirOf("name")} onSort={srcSort.toggle} />
                      <SortHeader label="状态" sortKey="health" dir={srcSort.dirOf("health")} onSort={srcSort.toggle}
                        title="ok / no_permission / down / broken / unknown 是五种不同的动作，排序只按字面聚堆" />
                      <SortHeader label="层 / 角色" sortKey="layer" dir={srcSort.dirOf("layer")} onSort={srcSort.toggle} />
                      <SortHeader label="上次成功" sortKey="last_ok_at" dir={srcSort.dirOf("last_ok_at")} onSort={srcSort.toggle}
                        title="从未成功过的行始终排在最后（NULL 不是 0）" />
                      <SortHeader label="上次探测" sortKey="last_probe_at" dir={srcSort.dirOf("last_probe_at")} onSort={srcSort.toggle}
                        title="按 ISO 时刻排；从未探测的行排在最后" />
                      <SortHeader label="延迟 / 行数" sortKey="latency_ms" dir={srcSort.dirOf("latency_ms")} onSort={srcSort.toggle}
                        title="排的是延迟；行数量纲不同，不参与排序" />
                      <th className="font-normal">末次错误 / 备注</th>
                    </tr></thead>
                    <tbody>
                      {srcSort.apply(sources).map((s, i) => (
                        <tr key={`${s.datasource_key}-${i}`} className="border-t border-border/40 align-top">
                          <td className="py-1 pr-2">
                            <p className="font-medium text-foreground">{dash(s.name)}{s.cost ? <span className="ml-1 text-[10px] text-muted-foreground">[{s.cost}]</span> : null}</p>
                            <p className="break-all font-mono text-[10px] text-muted-foreground">{dash(s.datasource_key)} · {dash(s.provider)}/{dash(s.kind)}</p>
                          </td>
                          <td className="whitespace-nowrap pr-2">
                            <span className={pillClass(healthTone(s.health))}>{dash(s.health)}</span>
                            {s.is_active === false && <span className="ml-1 text-[10px] text-muted-foreground">未启用</span>}
                            {s.auth_env_name && <span className="mt-0.5 block break-all text-[10px] text-muted-foreground">key：{s.auth_env_name}</span>}
                          </td>
                          <td className="whitespace-nowrap pr-2 text-muted-foreground">{dash(s.layer)} / {dash(s.ds_role)}</td>
                          <td className="whitespace-nowrap pr-2 font-mono">{dash(s.last_ok_at)}</td>
                          <td className="whitespace-nowrap pr-2 font-mono">
                            {s.last_probe_at
                              ? <>{s.last_probe_at}
                                {s.probe_stale === true && <span className="ml-1 rounded bg-warning/15 px-1 text-[10px] text-warning">已过期{typeof s.stale_seconds === "number" ? ` · ${ageText(s.stale_seconds)}前` : ""}</span>}
                              </>
                              : <span className="text-muted-foreground">从未探测</span>}
                          </td>
                          <td className="whitespace-nowrap pr-2 font-mono tabular-nums text-muted-foreground">
                            {typeof s.latency_ms === "number" ? `${s.latency_ms} ms` : "—"} / {dash(s.row_count)}
                          </td>
                          <td className="max-w-72 break-all">
                            {s.last_err && <span className="text-destructive/90">{plain(s.last_err)}</span>}
                            {s.note && <p className="text-[10px] leading-relaxed text-muted-foreground">{plain(s.note)}</p>}
                            {!s.last_err && !s.note && <span className="text-muted-foreground">—</span>}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                  {sources.length === 0 && <p className="mt-1 text-[11px] text-muted-foreground">后端交出 0 个源。这是"登记表里没行"，不是"所有源都正常"。</p>}
                </div>
                <Provenance bits={[d.basis]} />
                <p className="mt-2 text-[11px] leading-relaxed text-muted-foreground">
                  哪些表真的缺天，看 <Link className="text-primary underline" to="/coverage">覆盖体检</Link>；
                  该跑没跑，看 <Link className="text-primary underline" to="/jobs">任务调度</Link>；
                  加工算到哪一天，看 <Link className="text-primary underline" to="/data-process">数据加工</Link>。
                </p>
              </>
            );
          }}
        </Panel>

        <GlassCard>
          <h2 className="text-sm font-bold">这一屏还不能回答的三件事</h2>
          <ul className="mt-1 space-y-1 text-[11px] leading-relaxed text-muted-foreground">
            <li>· <b className="text-foreground">"现在"能不能用</b>：要么当场重探，要么等下一次探测。触发端点还没有
              （<code>POST /api/datasource/probe</code>），所以这里不摆一个点了没反应的"重新探测"按钮。</li>
            <li>· <b className="text-foreground">探测判据是什么</b>：接口没交 <code>probe_spec</code> —— 同一个 <code>ok</code>
              是"能连通"还是"取回了一行真数据"，页面无法区分，只能原样转述。</li>
            <li>· <b className="text-foreground">这个源是不是反复坏</b>：只给了末次探测，没有流水
              （<code>history[]</code>）—— 一次坏了与十次里坏八次，在这一屏上长得一样。</li>
          </ul>
          <p className="mt-2 text-[11px] leading-relaxed text-muted-foreground">
            另有一条后端自己标出来的：某些源走了"探出结果就不再复探"的粘性逻辑（原文在上面那句 caveat 里），
            这类源的 <code>health</code> 会比别的更旧，页面按 <code>probe_stale</code> 逐源显示，不合并成一句"大多过期"。
          </p>
        </GlassCard>
      </div>

      <Disclaimer compact />
    </div>
  );
}

function pillClass(tone: "ok" | "perm" | "dead" | "none"): string {
  const base = "rounded px-1 py-0.5 text-[10px]";
  if (tone === "ok") return `${base} bg-primary/12 text-primary`;
  if (tone === "perm") return `${base} bg-warning/15 text-warning`;
  if (tone === "dead") return `${base} bg-destructive/15 text-destructive`;
  return `${base} bg-muted text-muted-foreground`;
}
