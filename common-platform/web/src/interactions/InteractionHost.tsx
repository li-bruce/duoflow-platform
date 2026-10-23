import { Suspense, useCallback, useEffect, useRef, useState } from 'react';
import { api, errorText, post, requestId } from '../api';
import { ErrorNotice, Loading } from '../ui';
import ErrorBoundary from '../ErrorBoundary';
import type { Data, Result } from '../types';
import type { InteractionDefinition, InteractionInstance } from './sdk';
import { installedComponent } from './catalog';
import './interactions.css';

export default function InteractionHost({instanceId,surface='page',onChanged}:{instanceId:string;surface?:'page'|'conversation';onChanged?:()=>void}){
  const [item,setItem]=useState<InteractionInstance>();
  const [definition,setDefinition]=useState<InteractionDefinition>();
  const [draft,setDraft]=useState<Data>({});
  const [error,setError]=useState<unknown>();
  const [notice,setNotice]=useState('正在恢复…');
  const [executing,setExecuting]=useState(false);
  const [expanded,setExpanded]=useState(false);
  const current=useRef<InteractionInstance|undefined>(undefined);
  const desired=useRef<Data|null>(null);
  const saving=useRef<Promise<void>|null>(null);
  const timer=useRef<ReturnType<typeof setTimeout>|undefined>(undefined);
  const alive=useRef(true);
  const blocked=useRef(false);
  const actionRequest=useRef<{action:string;revision:number;key:string}|null>(null);
  const changed=useRef(onChanged);changed.current=onChanged;
  const flush=useCallback(async()=>{
    if(saving.current)await saving.current;
    if(blocked.current)throw new Error('状态未保存，请重新读取后继续。');
    if(!desired.current||!current.current)return;
    const job=(async()=>{
      while(desired.current&&current.current&&!blocked.current){
        const next=desired.current;setNotice('正在保存…');
        try{
          const saved=await api<InteractionInstance>(`/interactions/instances/${instanceId}`,{method:'PATCH',body:JSON.stringify({state:next,expected_revision:current.current.revision})});
          current.current=saved;
          if(desired.current===next)desired.current=null;
          if(alive.current){setItem(saved);setNotice(desired.current?'正在保存…':'已保存');}
        }catch(e){blocked.current=true;if(alive.current){setError(e);setNotice('尚未保存，请重新读取');}throw e;}
      }
    })();
    saving.current=job;try{await job;}finally{saving.current=null;}
  },[instanceId]);
  const reload=useCallback(async()=>{
    setError(undefined);
    try{
      const [record,catalog]=await Promise.all([api<InteractionInstance>(`/interactions/instances/${instanceId}`),api<{items:InteractionDefinition[]}>('/interactions/catalog')]);
      const found=catalog.items.find(d=>d.id===record.component_id&&d.version===record.component_version);
      if(!found)throw new Error('组件当前不可用，或版本已变化。');
      installedComponent(found);
      if(alive.current){current.current=record;desired.current=null;blocked.current=false;actionRequest.current=null;setItem(record);setDefinition(found);setDraft(record.state);setNotice('已恢复保存状态');}
    }catch(e){if(alive.current){setError(e);setItem(undefined);setDefinition(undefined);}}
  },[instanceId]);
  useEffect(()=>{alive.current=true;void reload();return()=>{alive.current=false;if(timer.current)clearTimeout(timer.current);void flush().catch(()=>{});};},[reload,flush]);
  const pending=!!item&&['queued','running'].includes(item.status);
  useEffect(()=>{
    if(!pending)return;
    const poll=setInterval(()=>{void api<InteractionInstance>(`/interactions/instances/${instanceId}`).then(record=>{if(alive.current){current.current=record;setItem(record);setNotice(record.status==='completed'?'任务已完成并保存':'任务状态：'+record.status);}}).catch(e=>{if(alive.current)setError(e);});},1500);
    return()=>clearInterval(poll);
  },[instanceId,pending]);
  useEffect(()=>{const guard=(e:BeforeUnloadEvent)=>{if(desired.current||saving.current||executing){e.preventDefault();e.returnValue='';}};window.addEventListener('beforeunload',guard);return()=>window.removeEventListener('beforeunload',guard);},[executing]);
  function updateState(next:Data){
    if(executing||pending||blocked.current)return;
    setDraft(next);desired.current=next;actionRequest.current=null;setNotice('有修改待保存');
    if(timer.current)clearTimeout(timer.current);
    timer.current=setTimeout(()=>void flush().catch(()=>{}),250);
  }
  async function invokeAction(action:string){
    if(executing||pending)return;
    setExecuting(true);setError(undefined);
    try{
      if(timer.current)clearTimeout(timer.current);
      await flush();
      if(!current.current)throw new Error('交互尚未加载。');
      const revision=current.current.revision;
      if(!actionRequest.current||actionRequest.current.action!==action||actionRequest.current.revision!==revision)actionRequest.current={action,revision,key:requestId()};
      const response=await post<{instance:InteractionInstance;result:Result}>(`/interactions/instances/${instanceId}/actions/${encodeURIComponent(action)}`,{expected_revision:revision,idempotency_key:actionRequest.current.key});
      current.current=response.instance;actionRequest.current=null;
      if(alive.current){setItem(response.instance);setDraft(response.instance.state);setNotice(response.instance.status==='queued'?'任务已提交，等待结果':'操作已完成并保存');changed.current?.();}
    }catch(e){if(alive.current){setError(e);setNotice('操作未确认完成，可按原请求重试');}}
    finally{if(alive.current)setExecuting(false);}
  }
  async function continueChat(){
    try{await flush();const response=await post<{conversation_id:string}>(`/interactions/instances/${instanceId}/attach`);
      const state=await api<{revision:number}>('/conversations/state');
      await api('/conversations/state',{method:'PATCH',body:JSON.stringify({expected_revision:state.revision,current_conversation_id:response.conversation_id,drafts:{}})});
      location.hash='chat';
    }catch(e){setError(e);}
  }
  const Component=definition?installedComponent(definition):null;
  return <section className={'interaction-host '+(expanded?'interaction-expanded':'')} aria-label={item?.title||'共享交互组件'} data-instance-id={instanceId}>
    <div className="interaction-heading"><div><strong>{item?.title||'共享组件'}</strong><small role="status">{notice}</small></div><div className="interaction-controls"><button type="button" onClick={()=>setExpanded(v=>!v)}>{expanded?'收起':'展开'}</button><button type="button" disabled={executing||!!saving.current} onClick={()=>void reload()}>重新读取</button>{surface==='page'&&<button type="button" disabled={executing} onClick={()=>void continueChat()}>在对话中继续</button>}</div></div>
    <ErrorNotice error={error||item?.error}/>
    {Component&&item?<ErrorBoundary resetKey={instanceId}><Suspense fallback={<Loading label="正在加载组件…"/>}><Component input={item.input} state={draft} result={desired.current?null:item.result} surface={surface} busy={executing||pending||blocked.current} actions={item.actions} updateState={updateState} invokeAction={invokeAction}/></Suspense></ErrorBoundary>:!error&&<Loading label="正在恢复交互…"/>}
    {item?.run_id&&<div className="interaction-controls">{item.status==='failed'&&<button onClick={()=>void post(`/runs/${item.run_id}/retry`).then(()=>reload()).catch(setError)}>重试任务</button>}{pending&&<button onClick={()=>void post(`/runs/${item.run_id}/cancel`).then(()=>reload()).catch(setError)}>停止等待此任务</button>}</div>}
    <div className="interaction-meta">实例 {instanceId.slice(0,8)} · 修订 {item?.revision||'—'} · {surface==='page'?'固定页面':'统一对话'}{item?.forked_from?' · 来自分支副本':''}</div>
    {Boolean(error)&&<p className="subtle">{errorText(error)}</p>}
  </section>;
}
