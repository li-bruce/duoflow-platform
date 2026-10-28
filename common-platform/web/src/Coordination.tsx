import { useCallback, useEffect, useRef, useState } from 'react';
import { api, post } from './api';
import { isPlatformAdmin, type User } from './types';
import { ErrorNotice, Loading, SectionHeading } from './ui';
import './coordination.css';

type Kind='state.changed'|'publication.requested'|'resource.published'|'resource.withdrawn';
type Mode='notify'|'sync';
type Module={id:string;title:string;service_id:string;declaration:{emits:Kind[];accepts:{source_module:string;kind:Kind;mode:Mode}[]}};
type Subscription={id:string;source_module:string;target_module:string;kind:Kind;scope:{organization_id:string;project_id:string};mode:Mode};
type Approval={event_id:string;module_id:string;object_ref:{object_type:string;object_id:string};object_revision:number;status:string;revision:number;scope:{organization_id:string;project_id:string}};
type Snapshot={version:string;config_revision:number;counts:Record<string,number>;objects:{module_id:string;object_ref:{object_type:string;object_id:string};state:string;revision:number;scope:{organization_id:string;project_id:string}}[];approvals:Approval[];backlogs:{module_id:string;pending:number;leased:number}[]};
type Summary={state:string;summary:Snapshot|null};
type Configuration={modules:Module[];module_ids:string[];subscriptions:Subscription[];status?:string};
const kindLabels:Record<Kind,string>={'state.changed':'业务状态变化','publication.requested':'申请公开','resource.published':'资源已公开','resource.withdrawn':'资源已撤回'};

export default function Coordination({user}:{user:User}){
  const [value,setValue]=useState<Summary>();const [configuration,setConfiguration]=useState<Configuration>();
  const [selected,setSelected]=useState<string[]>([]);const [rules,setRules]=useState<Subscription[]>([]);
  const [draft,setDraft]=useState({source_module:'',target_module:'',kind:'resource.published' as Kind,mode:'notify' as Mode,organization_id:'',project_id:''});
  const [error,setError]=useState<unknown>();const [busy,setBusy]=useState(false);const [loaded,setLoaded]=useState(false);
  const epoch=useRef(0);const dirty=useRef(false);const inFlight=useRef(false);
  const key=JSON.stringify([user.id,user.role,user.roles,user.authorization_revision]);
  const load=useCallback(async()=>{
    if(inFlight.current)return;inFlight.current=true;const current=epoch.current;
    try{
      const [summary,config]=await Promise.all([api<Summary>('/coordination/summary'),api<Configuration>('/coordination/configuration')]);
      if(current!==epoch.current)return;
      setValue(summary);setConfiguration(config);setError(undefined);
      if(!dirty.current){setSelected(config.module_ids);setRules(config.subscriptions);}
      setLoaded(true);
    }catch(e){if(current===epoch.current){setError(e);setLoaded(true);}}
    finally{if(current===epoch.current)inFlight.current=false;}
  },[]);
  useEffect(()=>{
    epoch.current++;inFlight.current=false;dirty.current=false;setValue(undefined);setConfiguration(undefined);setLoaded(false);setBusy(false);setError(undefined);setSelected([]);setRules([]);setDraft({source_module:'',target_module:'',kind:'resource.published',mode:'notify',organization_id:'',project_id:''});
    if(!isPlatformAdmin(user))return;
    void load();const timer=window.setInterval(()=>void load(),5000);
    return()=>{epoch.current++;clearInterval(timer);};
  },[key,load]);
  if(!isPlatformAdmin(user))return <p>业务协同管理由平台管理员办理。</p>;
  const snapshot=value?.summary;const modules=configuration?.modules||[];
  const title=(id:string)=>modules.find(m=>m.id===id)?.title||id;
  const active=modules.filter(m=>selected.includes(m.id));
  const source=active.find(m=>m.id===draft.source_module);
  const target=active.find(m=>m.id===draft.target_module);
  const compatible=Boolean(source?.declaration.emits.includes(draft.kind)&&target?.declaration.accepts.some(x=>x.source_module===draft.source_module&&x.kind===draft.kind&&x.mode===draft.mode));
  async function run(operation:()=>Promise<unknown>,saved=false){
    if(busy)return;setBusy(true);setError(undefined);const current=epoch.current;
    try{await operation();if(current===epoch.current){if(saved)dirty.current=false;await load();}}
    catch(e){if(current===epoch.current)setError(e);}
    finally{if(current===epoch.current)setBusy(false);}
  }
  function addRule(){
    if(!compatible||!draft.organization_id.trim()||!draft.project_id.trim())return;
    dirty.current=true;setRules(rows=>[...rows,{id:'sub-'+crypto.randomUUID(),source_module:draft.source_module,target_module:draft.target_module,kind:draft.kind,mode:draft.mode,scope:{organization_id:draft.organization_id.trim(),project_id:draft.project_id.trim()}}]);
  }
  return <>
    <SectionHeading eyebrow="BUSINESS COORDINATION" title="业务协同" description="各模块独立办理，协同中心汇集状态，并把变化送到订阅它的模块。"/>
    {error!=null&&<ErrorNotice error={error}/>}
    {!loaded?<Loading label="正在读取业务协同汇总…"/>:<>
      {configuration?.status==='pending'&&<p>正在核对上次配置结果，刷新后查看确认状态。</p>}
      <div className="coord-head"><span className={'coord-status '+value?.state}>{value?.state==='connected'?'独立协同中心已连接':value?.state==='unconfigured'?'等待连接协同中心':'协同中心暂未连接'}</span><button disabled={busy} onClick={()=>void load()}>刷新汇总</button></div>
      <div className="coord-stats"><article><span>汇集的业务对象</span><strong>{snapshot?.counts.objects??'—'}</strong></article><article><span>等待公开审批</span><strong>{snapshot?.counts.approvals_pending??'—'}</strong></article><article><span>待送达 / 已确认消息</span><strong>{snapshot?`${snapshot.counts.deliveries_pending+snapshot.counts.deliveries_leased} / ${snapshot.counts.deliveries_acked}`:'—'}</strong></article></div>
      <section className="coord-panel"><h2>公开申请</h2><p>管理员决定后，来源模块收到处理结果，核对申请版本并执行自己的公开操作。</p>
        {!snapshot?.approvals.some(x=>x.status==='pending')?<p className="muted">当前没有等待处理的公开申请。</p>:<div className="coord-table"><table><thead><tr><th>来源模块</th><th>申请对象 / 版本</th><th>所属项目</th><th>处理</th></tr></thead><tbody>{snapshot.approvals.filter(x=>x.status==='pending').map(x=><tr key={x.event_id}><td>{title(x.module_id)}</td><td>{x.object_ref.object_id}<small>版本 {x.object_revision}</small></td><td>{x.scope.project_id}</td><td><button disabled={busy} onClick={()=>void run(()=>post(`/coordination/approvals/${encodeURIComponent(x.event_id)}/decide`,{expected_revision:x.revision,decision:'approved'}))}>同意公开申请</button><button disabled={busy} onClick={()=>void run(()=>post(`/coordination/approvals/${encodeURIComponent(x.event_id)}/decide`,{expected_revision:x.revision,decision:'rejected'}))}>退回申请</button></td></tr>)}</tbody></table></div>}
      </section>
      <section className="coord-panel"><h2>各模块的业务状态</h2><p>这些状态来自模块提交的业务事件。各模块保持自己的办理节奏。</p>
        {!snapshot?.objects.length?<p className="muted">模块接入事件发送后，这里展示各自的最新状态。</p>:<div className="coord-table"><table><thead><tr><th>模块</th><th>业务对象</th><th>当前状态</th><th>版本</th></tr></thead><tbody>{snapshot.objects.map(x=><tr key={`${x.module_id}/${x.object_ref.object_type}/${x.object_ref.object_id}`}><td>{title(x.module_id)}</td><td>{x.object_ref.object_id}</td><td>{x.state}</td><td>{x.revision}</td></tr>)}</tbody></table></div>}
      </section>
      <section className="coord-panel"><h2>消息分发</h2><p>一个资源公开后，可以同时通知多个模块；每个模块独立确认自己的消息。</p><div className="coord-backlogs">{snapshot?.backlogs.map(x=><article key={x.module_id}><strong>{title(x.module_id)}</strong><span>待接收 {x.pending} · 处理中 {x.leased}</span></article>)}</div></section>
      <details className="coord-panel"><summary>配置参与模块与订阅关系</summary><p>先选择参与协同的模块，再约定“哪个模块的什么变化，要送给谁”。</p>
        {!modules.length?<p>模块在接入说明中加入协同声明后，会出现在这里。</p>:<div className="coord-modules">{modules.map(m=><label key={m.id}><input type="checkbox" checked={selected.includes(m.id)} onChange={e=>{dirty.current=true;setSelected(s=>e.target.checked?[...s,m.id]:s.filter(id=>id!==m.id));}}/>{m.title}</label>)}</div>}
        <div className="coord-rule-form"><label>来源模块<select value={draft.source_module} onChange={e=>setDraft(d=>({...d,source_module:e.target.value}))}><option value="">选择来源</option>{active.map(m=><option key={m.id} value={m.id}>{m.title}</option>)}</select></label><label>业务变化<select value={draft.kind} onChange={e=>setDraft(d=>({...d,kind:e.target.value as Kind}))}>{Object.entries(kindLabels).filter(([kind])=>kind!=='publication.requested').map(([kind,label])=><option key={kind} value={kind}>{label}</option>)}</select></label><label>接收模块<select value={draft.target_module} onChange={e=>setDraft(d=>({...d,target_module:e.target.value}))}><option value="">选择接收方</option>{active.filter(m=>m.id!==draft.source_module).map(m=><option key={m.id} value={m.id}>{m.title}</option>)}</select></label><label>处理方式<select value={draft.mode} onChange={e=>setDraft(d=>({...d,mode:e.target.value as Mode}))}><option value="notify">告知：生成通知</option><option value="sync">同步：处理来源更新</option></select></label><label>机构编号<input value={draft.organization_id} onChange={e=>setDraft(d=>({...d,organization_id:e.target.value}))}/></label><label>项目编号<input value={draft.project_id} onChange={e=>setDraft(d=>({...d,project_id:e.target.value}))}/></label><button disabled={!compatible||!draft.organization_id.trim()||!draft.project_id.trim()||busy} onClick={addRule}>添加订阅</button></div>
        {source&&target&&!compatible&&<p>请在两侧模块的协同声明中约定这个事件及处理方式。</p>}
        <ul className="coord-rules">{rules.map(r=><li key={r.id}><span>{title(r.source_module)} → {title(r.target_module)} · {kindLabels[r.kind]} · {r.mode==='notify'?'告知':'同步'}<small>{r.scope.organization_id} / {r.scope.project_id}</small></span><button aria-label={`移除订阅 ${r.id}`} onClick={()=>{dirty.current=true;setRules(s=>s.filter(x=>x.id!==r.id));}}>移除</button></li>)}</ul>
        <button disabled={busy||value?.state!=='connected'||!modules.length} onClick={()=>void run(()=>api('/coordination/configuration',{method:'PUT',body:JSON.stringify({expected_revision:snapshot?.config_revision??0,module_ids:selected,subscriptions:rules})}),true)}>保存协同配置</button>
      </details>
    </>}
  </>;
}
