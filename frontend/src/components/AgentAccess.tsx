import { randomId } from "@/lib/random-id";
import { useEffect, useId, useRef, useState } from 'react';
import { agentRequest, AgentRequestError, findSavedSourceId, loadAgentConnection, noteSourceResult, saveAgentConnection, sourceIdentity, type AgentConnection } from '@/lib/agent-api';
import { API_PROVIDERS, DEFAULT_ACCESS_PROVIDER, SAVED_PROVIDERS, SUBSCRIPTION_PROVIDERS, draftFor, connectionFor, providerFor, sourceLabel, draftError, isChatBridge, isSubscription } from '@/lib/ai-access';
type Job = {id?:string; kind?:string; status:string; message?:string; error?:string; auth_url?:string|null; source?:{provider:string;model:string}};
type Health = {installed:boolean; subscription_ready:boolean; engine_reason?:string; models:{model:string;is_default:boolean}[];default_model:string; models_error:string};
type Subscription = {installed:boolean; authenticated:boolean; available:boolean; detail:string};
const pendingKey='astock-agent-pending-test';
function pendingDraft(): {id:string;connection:AgentConnection}|null {
 try {
  const value=JSON.parse(sessionStorage.getItem(pendingKey)||'null');const c=value?.connection;
  return typeof value?.id==='string'&&/^[a-f0-9]{32}$/.test(value.id)&&c&&
   SAVED_PROVIDERS.includes(c.provider)&&
   ['model','baseURL','apiKey'].every(k=>typeof c[k]==='string')?value:null;
 }catch{return null;}
}
export function AgentAccess({initialProvider,onSaved,onBusyChange}:{initialProvider?:string;onSaved?:()=>void;onBusyChange?:(busy:boolean)=>void}) {
 const modelListId=useId();
 const [initial]=useState(()=>{const pending=pendingDraft();const saved=pending?.connection||loadAgentConnection();return draftFor(pending?providerFor(pending.connection):initialProvider||(saved?providerFor(saved):DEFAULT_ACCESS_PROVIDER),saved);});
 const [provider,setProvider]=useState(initial.provider);
 const [model,setModel]=useState(initial.model);
 const [baseURL,setBaseURL]=useState(initial.baseURL);
 const [apiKey,setApiKey]=useState(initial.apiKey);
 const [job,setJob]=useState<Job>(()=>{const p=pendingDraft();return p?{id:p.id,kind:'probe',status:'running'}:{status:'idle'};});
 const [catalog,setCatalog]=useState<Health|null>(null);
 const [subscription,setSubscription]=useState<Subscription|null>(null);
 const [error,setError]=useState('');const [pollError,setPollError]=useState('');const [notice,setNotice]=useState('');
 // 最近一次测试结果只属于当时那份配置，用它决定「保存」要不要带上通过标记。
 const [probed,setProbed]=useState<{key:string;ok:boolean}>({key:'',ok:false});
 const [sending,setSending]=useState(false);const [revision,setRevision]=useState(0);
 const completedLogin=useRef('');
 const pollEpoch=useRef(0);
 const [unconfirmed,setUnconfirmed]=useState(()=>!!pendingDraft());
 const pending=useRef(pendingDraft()); const savedCallback=useRef(onSaved);savedCallback.current=onSaved;
 const busy=sending||job.status==='running';
 const cli=['claude','codebuddy'].includes(provider);
 const api=!isSubscription(provider);
 const preset=API_PROVIDERS.find(p=>p.id===provider);
 const validation=draftError({provider,model,baseURL,apiKey});
 useEffect(()=>{onBusyChange?.(busy);},[busy,onBusyChange]);
 useEffect(()=>{
  const abort=new AbortController();setSubscription(null);setCatalog(null);
  if(cli) void agentRequest<Subscription>(`/subscriptions/${provider}`,undefined,abort.signal).then(v=>{if(!abort.signal.aborted)setSubscription(v);}).catch(()=>{if(!abort.signal.aborted)setError('无法确认订阅状态，请刷新重试');});
  else if(provider==='codex-private') void agentRequest<Health>('/status',undefined,abort.signal).then(v=>{if(!abort.signal.aborted){setCatalog(v);setModel(current=>current||v.default_model||'');}}).catch(()=>{if(!abort.signal.aborted)setError('无法读取产品登录状态');});
  return()=>abort.abort();
 },[provider,revision]);
 useEffect(()=>{
  const abort=new AbortController();let timer:ReturnType<typeof setTimeout>;
  const poll=async()=>{
   const epoch=pollEpoch.current;
   try{
    const next=await agentRequest<Job>('/access',undefined,abort.signal);
    if(!abort.signal.aborted&&epoch===pollEpoch.current){setPollError('');reconcile(next);}
   }catch{if(!abort.signal.aborted&&epoch===pollEpoch.current)setPollError('连接状态读取失败，正在重新查询；不会自动重试模型请求');}
   finally{if(!abort.signal.aborted)timer=setTimeout(poll,1500);}
  };void poll();return()=>{abort.abort();clearTimeout(timer);};
 },[]);
 function reconcile(next:Job){
  if(pending.current&&next.id!==pending.current.id)return;
  if(pending.current||next.status!=='running')setUnconfirmed(false);
  setJob(next);
    const waiting=pending.current;
    if(waiting&&(next.status==='failed'||next.status==='cancelled')&&next.id===waiting.id){
     if(next.status==='failed')recordProbe(waiting.connection,false,next.error||'');
     pending.current=null;sessionStorage.removeItem(pendingKey);
    }
    if(next.kind==='login'&&next.status==='complete'&&next.id&&completedLogin.current!==next.id){completedLogin.current=next.id;setRevision(n=>n+1);}
    if(next.status==='complete' && pending.current && pending.current.id===next.id){
     const connection=pending.current.connection;
     recordProbe(connection,true,'');
     pending.current=null;sessionStorage.removeItem(pendingKey);
     setNotice(`${sourceLabel(connection)} / ${connection.model} 真实连接与证据工具测试通过。${findSavedSourceId(connection)?'结果已记在本机来源列表上。':'点「保存来源」写入本机列表后才能被任务和自动切换使用。'}`);
     setRevision(n=>n+1);savedCallback.current?.();
    }
 }
 /** 测试结果只写在这份配置对应的已保存来源上；未保存时留在界面上。 */
 function recordProbe(connection:AgentConnection,ok:boolean,note:string){
  const key=sourceIdentity(connection);
  setProbed({key,ok});
  const id=findSavedSourceId(connection);
  if(id)noteSourceResult(id,ok,note);
 }
 /** 保存不等于可用：只写入本机列表，测试状态由上一步决定。 */
 function save(){
  if(validation){setError(validation);return;}
  const connection=connectionFor({provider,model,baseURL,apiKey});
  const id=saveAgentConnection(connection);
  const alreadyTested=probed.ok&&probed.key===sourceIdentity(connection);
  if(alreadyTested)noteSourceResult(id,true);
  setError('');setNotice(`${sourceLabel(connection)} / ${connection.model} 已保存到本机来源列表${alreadyTested?'（含刚才的测试通过记录）':'；尚未测试通过，可能连不通'}。`);
  setRevision(n=>n+1);savedCallback.current?.();
 }
 async function start(kind:'login'|'probe',retry=false){
  if(busy&&!(retry&&unconfirmed&&!sending&&pending.current))return;
  pollEpoch.current+=1;setUnconfirmed(false);
  if(kind==='probe'&&validation){setError(validation);return;}
  setSending(true);setError('');setPollError('');setNotice('');setJob({status:'idle'});
  const connection:AgentConnection=connectionFor({provider,model,baseURL,apiKey});
  let dispatched=false;
  try{
   let requestId:string|undefined;
   if(kind==='probe'){
    requestId=pending.current&&JSON.stringify(pending.current.connection)===JSON.stringify(connection)?pending.current.id:randomId().replace(/-/g,'');
    pending.current={id:requestId,connection};sessionStorage.setItem(pendingKey,JSON.stringify(pending.current));
    setJob({id:requestId,kind:'probe',status:'running',message:'正在提交连接测试…'});
   }
   dispatched=true;
   const next=await agentRequest<Job>(`/access/${kind}`,kind==='probe'?{llm:connection,request_id:requestId}:{});
   reconcile(next);
  }catch(e){
   if(dispatched&&kind==='probe'&&pending.current&&!(e instanceof AgentRequestError&&e.status<500)){
    setUnconfirmed(true);setJob({id:pending.current.id,kind:'probe',status:'running'});
    // Keep transport uncertainty separate from actionable field/account errors.
   }else{
    pollEpoch.current+=1;pending.current=null;sessionStorage.removeItem(pendingKey);setJob({status:'failed'});
    setError(e instanceof Error?e.message:'接入失败');
   }
  }finally{setSending(false);}
 }
 function choose(value:string){
  const next=draftFor(value);
  setCatalog(null);setSubscription(null);setProvider(next.provider);setModel(next.model);setBaseURL(next.baseURL);setApiKey(next.apiKey);setError('');setNotice('');
 }
 return <section aria-label="复盘与追问 AI 接入" className="space-y-4 rounded-xl border border-border bg-card p-4 sm:p-6">
  <div><h2 className="font-semibold">AI 来源</h2><p className="mt-1 text-sm leading-6 text-muted-foreground">选择订阅或模型 API，首页聊天、复盘和研究共用这份连接。</p></div>
  <div className="grid grid-cols-2 gap-3">
   <button type="button" disabled={busy} aria-pressed={!api} onClick={()=>{if(api)choose('codex-private');}} className={`rounded-xl border p-3 text-left disabled:opacity-50 ${!api?'border-primary bg-primary/10':'border-border hover:bg-muted/40'}`}><span className="block font-medium">使用订阅</span><span className="mt-1 block text-xs text-muted-foreground">Codex · Claude · WorkBuddy</span></button>
   <button type="button" disabled={busy} aria-pressed={api} onClick={()=>{if(!api)choose(DEFAULT_ACCESS_PROVIDER);}} className={`rounded-xl border p-3 text-left disabled:opacity-50 ${api?'border-primary bg-primary/10':'border-border hover:bg-muted/40'}`}><span className="block font-medium">使用 API 密钥</span><span className="mt-1 block text-xs text-muted-foreground">new-api 网关 · DeepSeek · 通义 等</span></button>
  </div>
  {!api&&<div className="grid gap-2 sm:grid-cols-3">{SUBSCRIPTION_PROVIDERS.map(p=><button key={p.id} type="button" disabled={busy} aria-pressed={provider===p.id} onClick={()=>{if(provider!==p.id)choose(p.id);}} className={`rounded-lg border p-3 text-left disabled:opacity-50 ${provider===p.id?'border-primary bg-primary/10':'border-border'}`}><span className="block text-sm font-medium">{p.name}</span><span className="mt-1 block text-xs leading-5 text-muted-foreground">{p.detail}</span></button>)}</div>}
  {api&&<>
   <label className="block text-sm">API 服务商<select aria-label="Agent 接入来源" disabled={busy} value={provider} onChange={e=>choose(e.target.value)} className="mt-2 w-full rounded-lg border border-border bg-background p-3">{API_PROVIDERS.map(p=><option key={p.id} value={p.id}>{p.name}{['glm','kimi','qwen'].includes(p.id)?'（阿里云百炼）':isChatBridge(p.id)?'（Chat Completions）':p.id==='api-compatible'?' Responses API':''}</option>)}</select></label>
   <p className="text-xs leading-6 text-muted-foreground">{preset?.detail}</p>
  </>}
  <label className="block text-sm">模型<input aria-label="Agent 接入模型" disabled={busy} value={model} onChange={e=>setModel(e.target.value)} list={modelListId} className="mt-2 w-full rounded-lg border border-border bg-background p-3" placeholder={cli?'default 表示订阅默认':'填写账户可用模型'}/><span className="mt-1 block text-xs text-muted-foreground">{api?'可选择预设，也可直接填写账户可用的模型标识。':cli?'default 使用订阅默认模型，也可指定账户可用模型。':'模型列表来自产品专用登录。'}</span></label>
  <datalist id={modelListId}>{(provider==='codex-private'?catalog?.models?.map(m=>m.model):preset?.models.map(m=>m.id))?.map(id=><option key={id} value={id}/>)}</datalist>
  {cli&&<p role="status" className="text-xs leading-5">{subscription?.detail||'正在检测所选订阅安装与登录状态…'}</p>}
  {provider==='codex-private'&&<p className="text-sm">{catalog && !catalog.installed?`引擎实测跑不起来：${catalog.engine_reason||'原因未取得'}。这不是登录问题，先重跑 npm ci --prefix runtime（慢网要几分钟）`:catalog?.subscription_ready?'产品专用登录已建立':'产品专用 Codex 尚未登录'}{catalog?.installed&&<button type="button" disabled={busy} onClick={()=>void start('login')} className="ml-3 rounded border border-border px-3 py-2">登录 ChatGPT</button>}{catalog?.models_error&&<span className="block text-xs">{catalog.models_error}</span>}</p>}
  {api&&<>
   <label className="block text-sm">API 基础地址<input aria-label="Agent API 地址" disabled={busy} value={baseURL} onChange={e=>setBaseURL(e.target.value)} placeholder={isChatBridge(provider)?'http://127.0.0.1:3000/v1 或内网 http://192.168.x.x:3000/v1 或 https://服务地址/v1':'https://服务地址/v1'} className="mt-2 w-full rounded-lg border border-border bg-background p-3"/></label>
   <label className="block text-sm">{['glm','kimi','qwen'].includes(provider)?'百炼 API 密钥':'API 密钥'}<input aria-label="Agent API 密钥" type="password" disabled={busy} autoComplete="off" value={apiKey} onChange={e=>setApiKey(e.target.value)} className="mt-2 w-full rounded-lg border border-border bg-background p-3"/></label>
   <div className="rounded-lg border border-border bg-muted/20 p-3 text-xs leading-6 text-muted-foreground"><span className="font-medium text-foreground">预设配置 · 当前填写内容待测试</span><p>{isChatBridge(provider)?'本模式由本机转换层把引擎的 Responses 请求改写成该端点的 Chat Completions 调用，地址请填到 /v1 为止。服务商密钥只发给本机后端，不进入引擎子进程。预设不代表已验证可用；工具调用与模型权限以该渠道为准。':'服务地址必须支持 Responses API 和工具调用。预设不代表已验证可用，仅支持 Chat Completions 的接口不能直接使用（请改用 new-api 网关或 OpenAI 兼容模式）；具体模型权限以服务商账户为准。'}</p></div>
   {isChatBridge(provider)&&/^http:\/\//i.test(baseURL.trim())&&<p className="text-xs leading-6 text-muted-foreground">当前是明文 http：只有本机与内网 IP 允许这样填，密钥、问题和材料都不经 TLS 加密流出。请确认这条网络路径你信任；公网网关请改用 HTTPS。</p>}
   {validation&&<p className="text-xs text-muted-foreground">{validation}</p>}
  </>}
  <p className="text-xs leading-6 text-muted-foreground">「测试连接」会用所选服务发一次真实请求，可能消耗额度；「保存来源」只把这份配置写进本机来源列表，不测试。两者互不强制：可以先保存后测试，也可以只测试不保存。API 密钥保存在本机浏览器，发送给本机后端用于连接所选服务。同一系统账户下的程序可能读取这些本地数据。</p>
  {job.auth_url&&job.status==='running'&&<a href={job.auth_url} target="_blank" rel="noreferrer" className="text-sm text-primary underline">打开官方登录页</a>}
  <div className="flex flex-wrap gap-2"><button type="button" disabled={busy||!!validation||(cli?!subscription?.available:provider==='codex-private'?!catalog?.subscription_ready:false)} onClick={()=>void start('probe')} className="rounded bg-primary px-4 py-2 text-sm text-primary-foreground disabled:opacity-50">测试连接</button><button type="button" disabled={busy||!!validation} onClick={save} className="rounded border border-primary px-4 py-2 text-sm disabled:opacity-50">保存来源</button><button type="button" disabled={busy} onClick={()=>{setError('');setRevision(n=>n+1);}} className="rounded border border-border px-3 py-2 text-sm">刷新状态</button>
  {unconfirmed&&<p role="status" className="w-full text-xs">提交结果尚未确认，正在查询状态。可重新确认同一次测试，或取消。</p>}
  {unconfirmed&&!sending&&<button type="button" onClick={()=>void start('probe',true)} className="rounded border border-border px-3 py-2 text-sm">重新确认本次测试</button>}
  {busy&&!sending&&<button type="button" onClick={()=>{setSending(true);pollEpoch.current+=1;setUnconfirmed(false);pending.current=null;sessionStorage.removeItem(pendingKey);void agentRequest<Job>('/access/cancel',{}).then(setJob).catch(()=>setError('取消未确认，请重试')).finally(()=>{pollEpoch.current+=1;setSending(false);});}} className="rounded border border-border px-3 py-2 text-sm">取消</button>}</div>
  <p role="status" className="text-sm">{notice||(job.message && <><span>最近接入记录（{job.source ? `${job.source.provider || '来源未记录'} / ${job.source.model || '模型未记录'}` : '来源未记录'}）：</span>{job.message}</>)}</p>{(error||job.error||pollError)&&<p role="alert" className="text-sm">{error||job.error||pollError}</p>}
 </section>;
}
