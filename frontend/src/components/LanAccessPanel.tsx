import { useCallback, useEffect, useState } from "react";
import { GlassCard } from "@/components/ui/GlassCard";
import { jobCardClass, jobLabelClass } from "@/lib/colors";
import { loadNetState, patchNet, type NetState } from "@/lib/netconfig";
import { cn } from "@/lib/utils";
import { Plus, ShieldAlert, Trash2 } from "lucide-react";

const DASH = "—";

/**
 * 系统配置 →「局域网访问」。这台机器的**网络边界**只有这一处可改，所以界面上必须
 * 把三件事说全，缺一条就会让人误判自己已经开放（或已经关上）了：
 *
 *  1. **配置说的 ≠ 进程实际绑的**。绑定地址在启动那一刻定死，勾完开关必须重启；
 *     而加白/删白是每个请求现读、**立刻生效**。两种时机不一样，不能都写成"重启后生效"。
 *  2. **只有本机回环能改这一格**（`can_edit`）。已经放行的那台机器如果能自己加白，
 *     白名单形同没有。
 *  3. 本产品**没有登录口令**，白名单是唯一那道门。加进白名单的机器看到的就是全部数据。
 *     这条不写在界面上，就等于让人在没意识到的情况下把库开放出去。
 */
export function LanAccessPanel() {
  const [s, setS] = useState<NetState | null>(null);
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState("");
  const [draftIp, setDraftIp] = useState("");
  const [refused, setRefused] = useState("");

  const reload = useCallback(() => loadNetState()
    .then(r => { setS(r); setErr(""); })
    .catch(e => setErr(e instanceof Error ? e.message : "网络边界现状读不到")), []);
  useEffect(() => { void reload(); }, [reload]);

  const run = async (label: string, fn: () => Promise<NetState>) => {
    setBusy(label); setRefused("");
    try {
      const next = await fn();
      setS(next);
      if (label === "add") setDraftIp("");
    } catch (e) {
      // 拒绝必须原样带出原因（非法 IP / 不在名单里 / 只能在主机上改），不许折叠成"失败了"
      setRefused(e instanceof Error ? e.message : "修改失败");
    } finally {
      setBusy("");
    }
  };

  const open = Boolean(s?.running_lan_open);
  const tone = !s ? "quiet" : open ? (s.allowlist.length ? "warn" : "bad") : "normal";

  return (
    <GlassCard>
      <div className="flex flex-wrap items-center gap-2">
        <h2 className="text-sm font-semibold">局域网访问</h2>
        <span className={cn("rounded-md px-1.5 py-0.5 text-[11px]", jobLabelClass(tone))}>
          {s ? (open ? (s.allowlist.length ? `已开放 · 白名单 ${s.allowlist.length} 台` : "已开放但白名单为空")
            : "未开放（只允许本机）") : DASH}
        </span>
        <button type="button" onClick={() => void reload()} disabled={busy !== ""}
          className="ml-auto rounded-lg border border-border px-2 py-1 text-[11px] text-muted-foreground hover:border-primary/40 hover:text-primary disabled:opacity-60">
          重新读取
        </button>
      </div>

      {err && <p className="mt-2 text-[11px] text-destructive">{err}</p>}
      {s?.config_warning && <p className="mt-2 text-[11px] text-warning">{s.config_warning}</p>}

      {s && (
        <>
          <label className={cn("mt-3 flex items-start gap-2 rounded-lg border border-border bg-muted/30 p-2.5",
            !s.can_edit && "opacity-70")}>
            <input type="checkbox" className="mt-0.5 h-4 w-4 accent-primary" checked={s.lan_enabled}
              disabled={!s.can_edit || busy !== ""}
              onChange={e => void run("toggle", () => patchNet("lan_enabled", e.target.checked))} />
            <span className="min-w-0">
              <span className="block text-xs font-medium">允许局域网内的其他机器访问本机</span>
              <span className="mt-0.5 block text-[11px] leading-4 text-muted-foreground">
                勾选后服务启动时绑定 <code>0.0.0.0</code>（不勾只绑 <code>127.0.0.1</code>）。
                本机 <code>127.0.0.1</code> 两种情况下都能访问，不需要写进白名单。
              </span>
              {!s.can_edit && s.edit_reason && (
                <span className="mt-1 flex items-start gap-1 text-[11px] text-warning">
                  <ShieldAlert className="mt-0.5 h-3 w-3 shrink-0" />{s.edit_reason}
                </span>
              )}
            </span>
          </label>

          {/* 配置说了什么 / 进程实际做了什么 —— 两者不一致时必须当场看见，不然会让人以为已经开了 */}
          <dl className="mt-3 grid gap-1 text-[11px] sm:grid-cols-2">
            <Row k="进程实际绑定" v={`${s.running_host ?? DASH}:${s.running_port ?? DASH}`} />
            <Row k="配置要求的绑定" v={s.should_bind ?? DASH} />
            <Row k="本机访问" v={s.loopback_url ?? DASH} href={s.loopback_url ?? undefined} />
            <Row k="配置文件" v={s.config_path ?? DASH} />
            <Row k="名单最后改动" v={s.changed_at
              ? `${s.changed_at}${s.changed_by ? ` 由 ${s.changed_by}` : ""}`
              : "没有留痕（早于留痕机制，或手工改的文件）"} />
          </dl>
          {s.needs_restart && (
            <p className={cn("mt-2 rounded-lg px-2.5 py-2 text-[11px] leading-4", jobCardClass("warn"))}>
              已保存，但<strong>要重启服务才生效</strong>（绑定地址在启动那一刻定死，运行中改不了）。
              重启：关闭 YuanShan.cmd 再双击 启动 YuanShan.cmd。
              注意<strong>加白 / 删白不用重启</strong>，那是每个请求现读的。
            </p>
          )}

          <div className="mt-3">
            <p className="text-xs font-medium">来源白名单（{s.allowlist.length || DASH}）</p>
            <p className="mt-0.5 text-[11px] leading-4 text-muted-foreground">
              只有这些地址能访问；匹配的是一般对端地址，不认 <code>X-Forwarded-For</code> 这类可伪造头。
              只支持单个 IPv4，不支持网段。
              <br />
              <strong>加白 / 删白立即生效</strong>（每个请求现读），不用重启 ——
              要重启的只有上面那个总开关（绑定地址在启动那一刻定死）。
              这两个时机不一样，别一起写成"重启后生效"。
            </p>
            <ul className="mt-1.5 space-y-1">
              {s.allowlist.length === 0 && <li className="text-[11px] text-muted-foreground">（空 —— 开了局域网也只有本机能用）</li>}
              {s.allowlist.map(ip => (
                <li key={ip} className="flex items-center gap-2 rounded-lg border border-border bg-muted/20 px-2 py-1">
                  <code className="min-w-0 flex-1 truncate text-[11px]">{ip}</code>
                  <button type="button" disabled={!s.can_edit || busy !== ""} title="从白名单删除"
                    onClick={() => void run(`remove:${ip}`, () => patchNet("remove", ip))}
                    className="rounded-md p-1 text-muted-foreground hover:bg-destructive/10 hover:text-destructive disabled:opacity-50">
                    <Trash2 className="h-3.5 w-3.5" />
                  </button>
                </li>
              ))}
            </ul>
            {s.can_edit && (
              <div className="mt-1.5 flex items-center gap-1.5">
                <input value={draftIp} onChange={e => setDraftIp(e.target.value)}
                  placeholder="192.168.186.7" aria-label="要放行的 IPv4 地址"
                  className="min-w-0 flex-1 rounded-lg border border-border bg-background px-2 py-1 text-[12px]" />
                <button type="button" disabled={busy !== "" || !draftIp.trim()}
                  onClick={() => void run("add", () => patchNet("add", draftIp.trim()))}
                  className="flex items-center gap-1 rounded-lg bg-primary/15 px-2 py-1 text-[11px] font-medium text-primary hover:bg-primary/25 disabled:opacity-60">
                  <Plus className="h-3.5 w-3.5" />加白
                </button>
              </div>
            )}
            {refused && <p className="mt-1.5 text-[11px] text-warning">未采纳：{refused}</p>}
          </div>

          {open && s.lan_urls.length > 0 && (
            <p className="mt-3 text-[11px] leading-4 text-muted-foreground">
              对面浏览器里填：{s.lan_urls.map(u => <code key={u} className="mr-1">{u}</code>)}
              （用哪条看它接在哪个网卡上）
            </p>
          )}

          <p className="mt-2 flex items-start gap-1 text-[11px] leading-4 text-muted-foreground">
            <ShieldAlert className="mt-0.5 h-3 w-3 shrink-0" />
            这台机器没有登录口令，<strong>白名单就是唯一那道门</strong>：加进去的机器看到的就是本机全部数据、
            并能触发采集与加工。对面连不上时先看 Windows 防火墙有没有放行
            {s.running_port ? ` ${s.running_port}` : ""}。
          </p>

          <div className="mt-2">
            <p className="text-[11px] text-muted-foreground">
              最近被挡：{s.total ? `累计 ${s.total} 次` : "无"}
            </p>
            {s.recent.length > 0 && (
              <ul className="mt-1 space-y-0.5">
                {s.recent.slice(-5).reverse().map((d, i) => (
                  <li key={`${d.client}-${d.at}-${i}`} className="text-[11px] text-muted-foreground">
                    {d.at || DASH}　<code>{d.client}</code>　{d.reason}
                  </li>
                ))}
              </ul>
            )}
          </div>
        </>
      )}
      {!s && !err && <p className="mt-2 text-[11px] text-muted-foreground">正在读取网络边界现状…</p>}
    </GlassCard>
  );
}

function Row({ k, v, href }: { k: string; v: string; href?: string }) {
  return (
    <div className="flex items-baseline gap-1.5">
      <dt className="shrink-0 text-muted-foreground">{k}</dt>
      <dd className="min-w-0 truncate">
        {href ? <a href={href} target="_blank" rel="noopener noreferrer" className="text-primary hover:underline">{v}</a>
          : <code>{v}</code>}
      </dd>
    </div>
  );
}
