import './registration.css';
import { isPlatformAdmin } from './types';
import { useCallback, useEffect, useRef, useState } from 'react';
import { Activity, Check, ChevronDown, Cloud, KeyRound, Link2, Pause, Play, Plus, RefreshCw, Save, Search } from 'lucide-react';
import { api, post } from './api';
import type { Capability, Monitor, Surfaces, User } from './types';
import { Empty, ErrorNotice, Loading, PrettyData, Success } from './ui';
import { AvailabilityBadge, MonitorPanel, SurfaceSummary } from './ModuleStatus';
import MonitoringSummary from './MonitoringSummary';
import type { MonitoringSnapshot } from './MonitoringSummary';

type Health = {ok:boolean;message:string;latency_ms?:number};
type Manifest = {protocol_version?:string;module:{id:string;name:string;version:string;description?:string};capabilities:Capability[];surfaces?:Surfaces};
type Admission = {version:string;revision:number;passed:boolean;checked_at:number;checks:{id:string;title:string;passed:boolean;code:string}[]};
type Service = {id:string;name:string;base_url:string;timeout_seconds:number;token_configured:boolean;enabled:boolean;revision:number;discovered_revision?:number|null;manifest?:Manifest|null;last_health?:Health|null;monitor?:Monitor;admission?:Admission|null;registration?:unknown};
type Form = {id?:string;expected_revision?:number;name:string;base_url:string;timeout_seconds:number;token:string;clear_token:boolean};
const fresh=():Form=>({name:'',base_url:'http://127.0.0.1:8030',timeout_seconds:60,token:'',clear_token:false});
const synced=(item?:Service)=>Boolean(item?.manifest&&item.discovered_revision===item.revision);
const stateLabel=(item:Service)=>item.enabled?'已发布':item.admission?.passed?'检查通过 · 待发布':item.admission?'接入未通过':synced(item)?'待接入检查':'待发现';
const invokeVersion=(version?:string)=>version==='1.2'?'1.1':['1.0','1.1'].includes(version||'')?'1.0':'未提供';

export default function RemoteServices({user,onRegistryChanged,compact=false}:{user:User;onRegistryChanged?:()=>void;compact?:boolean}){
  const [items,setItems]=useState<Service[]>([]);
  const [monitoring,setMonitoring]=useState<MonitoringSnapshot>();
  const [selected,setSelected]=useState<string|null>(null);
  const [form,setForm]=useState<Form>(fresh);
  const [loading,setLoading]=useState(true);
  const [busy,setBusy]=useState('');
  const [dirty,setDirty]=useState(false);
  const [error,setError]=useState<unknown>();
  const [notice,setNotice]=useState('');
  const [health,setHealth]=useState<Health|null>(null);const [pollError,setPollError]=useState<unknown>();
  const identityKey=JSON.stringify([user.id,user.role,[...(user.roles||[])].sort(),user.authorization_revision]);
  const identity=useRef(identityKey);identity.current=identityKey;
  const generation=useRef(0);const mounted=useRef(false);const sequence=useRef(0);
  const reading=useRef<number|null>(null);const busyRef=useRef(false);const editVersion=useRef(0);const dirtyRef=useRef(false);
  const select=useCallback((item:Service|null)=>{
    editVersion.current++;dirtyRef.current=false;
    setSelected(item?.id||null);
    setForm(item?{id:item.id,expected_revision:item.revision,name:item.name,base_url:item.base_url,timeout_seconds:item.timeout_seconds,token:'',clear_token:false}:fresh());
    setDirty(false);setError(null);setNotice('');setHealth(item?.last_health||null);
  },[]);
  const load=useCallback(async(silent=false)=>{
    const request=++sequence.current;const epoch=generation.current;const owner=identity.current;
    const current=()=>mounted.current&&epoch===generation.current&&owner===identity.current&&request===sequence.current;
    reading.current=request;if(!silent){setLoading(true);setError(null);}
    try{const value=await api<{items:Service[];monitoring?:MonitoringSnapshot}>('/remote-services');if(current()){setItems(value.items);setMonitoring(value.monitoring);setPollError(null);return value.items;}}
    catch(e){if(current()){if(silent)setPollError(e);else setError(e);}}
    finally{if(reading.current===request)reading.current=null;if(current())setLoading(false);}
  },[]);
  useEffect(()=>{
    mounted.current=true;generation.current++;sequence.current++;reading.current=null;busyRef.current=false;
    setItems([]);setMonitoring(undefined);select(null);setBusy('');setPollError(null);setLoading(isPlatformAdmin(user));
    if(!isPlatformAdmin(user))return()=>{mounted.current=false;generation.current++;sequence.current++;};
    const version=editVersion.current;
    void load().then(rows=>{if(rows&&version===editVersion.current&&rows.length)select(rows[0]);});
    const timer=window.setInterval(()=>{if(!busyRef.current&&reading.current===null)void load(true);},5000);
    return()=>{mounted.current=false;generation.current++;sequence.current++;clearInterval(timer);};
  },[load,select,identityKey]);
  function update<K extends keyof Form>(key:K,value:Form[K]){editVersion.current++;dirtyRef.current=true;setForm(v=>({...v,[key]:value}));setDirty(true);setNotice('');setHealth(null);}
  function accept(item:Service){sequence.current++;setItems(rows=>rows.some(row=>row.id===item.id)?rows.map(row=>row.id===item.id?item:row):[item,...rows]);select(item);}
  async function run(action:string,operation:()=>Promise<void>){
    if(busyRef.current)return;
    busyRef.current=true;sequence.current++;reading.current=null;setLoading(false);setBusy(action);setError(null);setPollError(null);setNotice('');
    const epoch=generation.current;const owner=identity.current;
    const current=()=>mounted.current&&epoch===generation.current&&owner===identity.current;
    try{await operation();}catch(e){if(current())setError(e);}finally{if(current()){busyRef.current=false;setBusy('');}}
  }
  function currentOperation(){const epoch=generation.current;const owner=identity.current;return()=>mounted.current&&epoch===generation.current&&owner===identity.current;}
  async function save(){
    const current=currentOperation();
    await run('save',async()=>{
      const item=await post<Service>('/remote-services',{...(form.id?{id:form.id,expected_revision:form.expected_revision}:{}),name:form.name,base_url:form.base_url,timeout_seconds:form.timeout_seconds,...(form.token.trim()?{token:form.token}:{}),clear_token:form.clear_token});
      if(!current())return;
      accept(item);setNotice(item.enabled?'配置已保存，服务仍处于发布状态。':synced(item)?'配置已保存。可查看能力预览并启用发布。':'连接已保存。接下来发现能力，检查预览后启用发布。');onRegistryChanged?.();
    });
  }
  async function discover(){
    if(!selected)return;const current=currentOperation();
    await run('discover',async()=>{const item=await post<Service>(`/remote-services/${encodeURIComponent(selected)}/discover`);if(!current())return;accept(item);setNotice('已读取并验证能力声明。请核对两端入口；需要独立组件、流程和权限定义的模块，还须完成对应安装与注册。');onRegistryChanged?.();});
  }
  async function testHealth(){
    if(!selected)return;const current=currentOperation();setHealth(null);
    await run('health',async()=>{const value=await post<Health>(`/remote-services/${encodeURIComponent(selected)}/health`);if(!current())return;setHealth(value);setItems(rows=>rows.map(row=>row.id===selected?{...row,last_health:value}:row));});
  }
  async function checkAdmission(){
    const saved=items.find(item=>item.id===selected);if(!saved)return;
    const current=currentOperation();
    await run('admission',async()=>{
      const item=await post<Service>(`/remote-services/${encodeURIComponent(saved.id)}/admission`,{expected_revision:saved.revision});
      if(!current())return;accept(item);onRegistryChanged?.();
      setNotice(item.admission?.passed?'全部接入检查已通过，可以启用发布。':'接入检查尚未全部通过，请按下方结果修正后重新检查。');
    });
  }
  async function refreshList(){const version=editVersion.current;const rows=await load();if(!rows||version!==editVersion.current||dirtyRef.current)return;select(rows.find(item=>item.id===selected)||null);}
  async function publish(enable:boolean){
    const saved=items.find(item=>item.id===selected);if(!saved)return;
    const current=currentOperation();
    await run(enable?'enable':'disable',async()=>{
      const item=await post<Service>(`/remote-services/${encodeURIComponent(saved.id)}/${enable?'enable':'disable'}`,{expected_revision:saved.revision});
      if(!current())return;
      accept(item);setNotice(enable?'服务已发布。固定页面和对话按声明及当前用户授权提供能力入口；独立组件和流程的安装、实际业务验收需分别完成。':'服务已停用。固定页面和对话将不再提供它的能力入口。');onRegistryChanged?.();
    });
  }
  if(!isPlatformAdmin(user))return <Empty title="服务接入由管理员维护" description="管理员发布服务后，你可以在固定页面和对话中使用授权范围内的能力。"/>;
  const saved=items.find(item=>item.id===selected);
  const manifest=saved?.manifest;
  const isSynced=synced(saved);
  const admitted=Boolean(saved?.admission?.passed&&saved.admission.revision===saved.revision&&(saved.enabled||Date.now()/1000-saved.admission.checked_at<=300));
  const staleEdit=Boolean(saved&&form.expected_revision!==saved.revision);
  return <section className={'model-settings remote-services '+(compact?'compact':'')} aria-label="远程服务接入">
    <div className="model-intro"><div className="icon-tile"><Cloud size={21}/></div><div><h2>让外部服务成为工作台能力</h2><p>远程实现按统一 HTTP 契约接入；页面工具和对话能力分别声明，共用同一服务。</p></div><span className="tag">{items.length} 个服务</span></div>
    <MonitoringSummary value={monitoring} serviceId={saved?.id} revision={saved?.revision}/>
    {loading&&items.length===0?<Loading label="正在读取服务连接…"/>:<div className="model-layout">
      <aside className="model-list"><button className="button secondary full" onClick={()=>select(null)} disabled={!!busy}><Plus size={16}/>新建服务</button>{items.map(item=><button key={item.id} className={'model-item '+(selected===item.id?'selected':'')} onClick={()=>select(item)} disabled={!!busy}><span className="model-item-name"><Cloud size={16}/>{item.name}</span><small>{item.base_url}</small><span className={'connection-dot '+(item.enabled?'enabled':'')}>{stateLabel(item)}{item.manifest?` · v${item.manifest.module.version}`:''}</span><AvailabilityBadge state={item.monitor?.state}/></button>)}<button className="text-button service-refresh" disabled={!!busy||loading} onClick={()=>void refreshList()}><RefreshCw size={13} className={loading?'spin':''}/>刷新列表</button></aside>
      <div className="model-editor">
        <div className="editor-title"><h3>{selected?'管理服务连接':'添加一个服务连接'}</h3>{dirty?<span className="tag amber">有未保存修改</span>:saved&&<span className={'tag '+(saved.enabled?'green':isSynced?'':'amber')}>{stateLabel(saved)}</span>}</div>
        {staleEdit&&<div className="notice info" role="status"><div>正在编辑连接修订 {form.expected_revision}，服务器已更新到修订 {saved?.revision}。当前输入与预期修订保持不变；请核对后重新加载。<button className="text-button" disabled={!!busy} onClick={()=>select(saved||null)}>{dirty?'放弃编辑并加载最新连接':'加载最新连接'}</button></div></div>}
        <ol className="service-steps" aria-label="服务接入步骤"><li className={saved?'done':'current'}><span>{saved?<Check size={13}/>:'1'}</span>保存连接</li><li className={isSynced?'done':saved?'current':''}><span>{isSynced?<Check size={13}/>:'2'}</span>发现与预览</li><li className={saved?.admission?.passed?'done':isSynced?'current':''}><span>{saved?.admission?.passed?<Check size={13}/>:'3'}</span>接入检查</li><li className={saved?.enabled?'done':saved?.admission?.passed?'current':''}><span>{saved?.enabled?<Check size={13}/>:'4'}</span>启用发布</li></ol>
        <form onSubmit={e=>{e.preventDefault();void save();}}>
          <div className="form-grid"><label className="field">服务名称<input required maxLength={100} placeholder="例如：文档整理服务" value={form.name} disabled={!!busy} onChange={e=>update('name',e.target.value)}/></label><label className="field">请求超时（秒）<input type="number" required min={5} max={300} value={form.timeout_seconds} disabled={!!busy} onChange={e=>update('timeout_seconds',Number(e.target.value))}/></label><label className="field wide">服务基础地址<input type="url" required placeholder="http://127.0.0.1:8030" value={form.base_url} disabled={!!busy} onChange={e=>update('base_url',e.target.value)}/><small>本机示例：http://127.0.0.1:8030。其他机器使用 HTTPS，例如 https://tools.example.org。HTTP 仅支持本机回环地址。</small></label><label className="field wide">Bearer Token <span className="field-badge"><KeyRound size={12}/>{saved?.token_configured?'已配置，原值不回显':'未配置'}</span><input type="password" autoComplete="new-password" value={form.token} disabled={!!busy||form.clear_token} onChange={e=>update('token',e.target.value)} placeholder={saved?.token_configured?'留空保留已有令牌，输入新值可替换':'输入服务令牌，接入验收必须配置凭据'}/><small>令牌由后端保存和使用，不放入对话消息或浏览器持久存储。</small></label></div>
          {saved?.token_configured&&<label className="checkbox-row"><input type="checkbox" checked={form.clear_token} disabled={!!busy} onChange={e=>{update('clear_token',e.target.checked);if(e.target.checked)setForm(v=>({...v,token:''}));}}/>保存时移除已有令牌</label>}
          {dirty&&saved&&<p className="service-change-note">修改连接并保存后，服务会停用；重新发现能力并通过接入检查后可发布。</p>}
          <div className="form-actions"><button type="submit" className="button primary" disabled={!!busy}>{busy==='save'?<Loading label="保存中…"/>:<><Save size={16}/>保存连接</>}</button><button type="button" className="button secondary" disabled={!selected||dirty||!!busy} onClick={()=>void discover()}>{busy==='discover'?<Loading label="正在发现能力…"/>:<><Search size={16}/>{manifest?'重新发现能力':'发现能力'}</>}</button><button type="button" className="button secondary" disabled={!selected||dirty||!!busy} onClick={()=>void testHealth()}>{busy==='health'?<Loading label="正在检测…"/>:<><Activity size={16}/>健康检测</>}</button></div>
        </form>
        <ErrorNotice error={error||pollError}/>{notice&&<Success>{notice}</Success>}
        {saved&&<section className="service-contract-versions" aria-label="远程协议版本"><span>Manifest 协议：<strong>{manifest?(manifest.protocol_version||'未提供'):'尚未发现'}</strong></span>{' · '}<span>Invoke 协议：<strong>{invokeVersion(manifest?.protocol_version)}</strong></span><p className="subtle">模块业务版本、连接修订与传输协议分别管理。Manifest 1.2 使用 Invoke 1.1；旧 Manifest 1.0 / 1.1 使用 Invoke 1.0。</p></section>}
        {saved&&<section className="service-preview registration-admission" aria-label="模块接入检查"><h3>模块接入检查</h3><p>平台逐项检查声明、身份验证和接口；模块同时核对双方协议与实际处理函数。全部通过后才能发布。</p>
          <button type="button" className="button secondary" disabled={!isSynced||dirty||!!busy} onClick={()=>void checkAdmission()}>{busy==='admission'?<Loading label="正在检查接入…"/>:'检查模块接入'}</button>
          {saved.admission?<><p role="status">{saved.admission.passed?(admitted?'全部通过':'请重新检查，验收已超过五分钟'):'尚未全部通过'} · 检查后五分钟内可启用发布</p><ul>{saved.admission.checks.map(check=><li key={check.id}><strong>{check.passed?'已通过':check.code==='not_checked'?'尚未检查':'未通过'}</strong> · <span>{check.title}</span>{!check.passed&&check.code!=='not_checked'&&<details><summary>查看检查编号</summary><code>{check.code}</code></details>}</li>)}</ul></>:<p>尚未进行接入检查。能力发现和健康检测不能代替接入检查。</p>}
          {saved.registration!=null&&<details><summary>查看模块公开内容</summary><PrettyData data={saved.registration}/></details>}
        </section>}
        {saved&&<MonitorPanel monitor={saved.monitor} source="remote"/>}
        {health&&<div className={'test-result '+(health.ok?'passed':'not-passed')} role="status"><div className="test-result-heading"><Activity size={17}/><strong>{health.ok?'最近手动检测：通过':'最近手动检测：未通过'}</strong>{health.latency_ms!==undefined&&<span>{health.latency_ms} ms</span>}</div><p>{health.message}</p><small>此处保留最近手动检测结果；上方运行状态来自持续心跳。</small></div>}
        {saved&&<section className="service-preview" aria-label="服务能力预览"><div className="service-preview-heading"><div><span className="eyebrow">发现与同步</span><h3>{manifest?manifest.module.name:'连接已保存，等待发现'}</h3></div><span className={'tag '+(isSynced?'green':'amber')}>{isSynced?'已与当前连接同步':'需要发现能力'}</span></div>{manifest?<><p className="subtle">{manifest.module.description}</p><div className="service-manifest-meta"><span className="tag violet"><Cloud size={12}/>远程模块</span><code>{manifest.module.id}</code><span>版本 {manifest.module.version}</span><span>{manifest.capabilities.length} 项能力</span></div><SurfaceSummary surfaces={manifest.surfaces} capabilities={manifest.capabilities}/><div className="service-capabilities">{manifest.capabilities.map(cap=><details className="contract-item" key={cap.id}><summary><div><Link2 size={16}/><strong>{cap.title}</strong><code>{cap.id}</code></div><span className="tag">v{cap.version}</span><ChevronDown size={14}/></summary><p>{cap.description}</p><div className="contract-bindings"><span>执行：平台后台任务</span><span>呈现：共享表单与结果卡</span><span>角色：{cap.roles?.join(' / ')||'按能力授权'}</span></div><div className="schema-columns"><div><h4>输入 Schema</h4><PrettyData data={cap.input_schema}/></div><div><h4>输出 Schema</h4><PrettyData data={cap.output_schema}/></div></div></details>)}</div></>:<p className="subtle">点击“发现能力”，平台将读取远程声明并展示能力名称、版本和输入输出结构。</p>}<details className="service-revisions"><summary>查看连接与发现修订号<ChevronDown size={13}/></summary><div>当前连接：<code>{saved.revision}</code></div><div>已发现连接：<code>{saved.discovered_revision||'尚未发现'}</code></div></details><div className="form-actions">{saved.enabled?<button className="button secondary" disabled={!!busy||dirty} onClick={()=>void publish(false)}>{busy==='disable'?<Loading label="正在停用…"/>:<><Pause size={15}/>停用服务</>}</button>:<button className="button primary" disabled={!isSynced||!admitted||!!busy||dirty} onClick={()=>void publish(true)}>{busy==='enable'?<Loading label="正在发布…"/>:<><Play size={15}/>启用发布</>}</button>}<span className="subtle">{saved.enabled?'页面和对话已使用这份能力声明。':isSynced?'完成接入检查后可启用发布。':'先发现能力，再完成接入检查。'}</span></div></section>}
        <details className="service-help"><summary><Link2 size={14}/>接入说明<ChevronDown size={13}/></summary><p>保存连接只登记地址与凭据；发现读取并验证能力声明；接入检查验证接口与模块主动确认；启用发布让固定页面与对话按各自声明和用户授权提供能力入口。健康检测只说明服务可达性。</p><p>两端能力注册与独立组件、步骤流程的安装分别办理。组件包与 flow 包需要受控安装、构建和重启；对话 skills 是声明的顺序引导，不代表这些组件已经安装。</p><p>模块使用范围权限时，按“发现并保持禁用 → 安装所需流程或组件 → 注册权限定义 → 接入检查全部通过 → 启用 → 分配用户授权”接入。权限定义描述规则，实际用户授权另行分配；发布服务不会授予业务权限。</p><p>接入检查确认协议与已安装依赖，不自动安装组件、授予用户权限，也不代替真实业务操作验收。本机示例服务：<code>http://127.0.0.1:8030</code>。开发接入协议见项目内 <code>docs/remote-service-protocol.md</code>。</p></details>
      </div>
    </div>}
  </section>;
}
