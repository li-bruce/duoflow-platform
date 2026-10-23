import {useCallback, useEffect, useRef, useState} from 'react';
import {api, post, requestId, dateTime} from './api';
import {ErrorNotice, Loading, SectionHeading} from './ui';
import FlowHost from './workflows/FlowHost';
import type {WorkflowDefinition} from './workflows/sdk';
import './business.css';

type Personal={id:string;flow_id:string;title:string;step_title:string;object_ref:{object_id:string}|null;forked_from:string|null;updated_at:string};
type Entry=WorkflowDefinition&{experience?:{title:string;module_id:string}};
type WorkItem={title:string;project_id:string;state:string;revision:number;flow_id:string;object_ref:{object_id:string};actions:{id:string;title:string}[]};
type Open={instance_id:string;conversation_id?:string};
export default function MyBusiness(){
  const [catalog,setCatalog]=useState<Entry[]>([]),[personal,setPersonal]=useState<Personal[]>([]);
  const [flow,setFlow]=useState(''),[items,setItems]=useState<WorkItem[]>([]),[cursor,setCursor]=useState<string|null>(null);
  const [selected,setSelected]=useState(()=>new URLSearchParams(location.hash.split('?')[1]||'').get('instance')||'');
  const [error,setError]=useState<unknown>(),[sourceError,setSourceError]=useState<unknown>(),[busy,setBusy]=useState(false),[loading,setLoading]=useState(true);
  const generation=useRef(0),objectsGeneration=useRef(0),alive=useRef(true);
  const load=useCallback(async()=>{const seq=++generation.current;try{
    const [a,b]=await Promise.all([api<{items:Entry[]}>('/business/catalog'),api<{items:Personal[]}>('/business/instances')]);
    if(!alive.current||seq!==generation.current)return;setCatalog(a.items);setPersonal(b.items);setError(undefined);
  }catch(e){if(alive.current&&seq===generation.current)setError(e);}finally{if(alive.current&&seq===generation.current)setLoading(false);}},[]);
  useEffect(()=>{alive.current=true;void load();const timer=setInterval(()=>void load(),15000);return()=>{alive.current=false;generation.current++;objectsGeneration.current++;clearInterval(timer);};},[load]);
  const loadObjects=useCallback(async(id:string,next?:string)=>{const seq=++objectsGeneration.current;setSourceError(undefined);try{
    const response=await api<{items:WorkItem[];next_cursor:string|null}>('/business/objects?'+new URLSearchParams({flow_id:id,...(next?{cursor:next}:{})}));
    if(!alive.current||seq!==objectsGeneration.current)return;setItems(old=>next?[...old,...response.items]:response.items);setCursor(response.next_cursor);
  }catch(e){if(alive.current&&seq===objectsGeneration.current){setSourceError(e);if(!next){setItems([]);setCursor(null);}}}},[]);
  useEffect(()=>{setItems([]);setCursor(null);objectsGeneration.current++;if(!flow)return;void loadObjects(flow);const timer=setInterval(()=>void loadObjects(flow),15000);return()=>{clearInterval(timer);objectsGeneration.current++;};},[flow,loadObjects]);
  useEffect(()=>{if(loading)return;if(flow&&!catalog.some(entry=>entry.id===flow)){setFlow('');setItems([]);setCursor(null);objectsGeneration.current++;}},[catalog,flow,loading]);
  async function open(body:Record<string,unknown>,surface:'page'|'conversation'){
    setBusy(true);setError(undefined);try{const result=await post<Open>('/business/open',{...body,surface,idempotency_key:body.instance_id?undefined:requestId()});
      if(!alive.current)return;
      if(surface==='conversation'){
        const state=await api<{revision:number;drafts:Record<string,unknown>}>('/conversations/state');
        if(!alive.current)return;
        await api('/conversations/state',{method:'PATCH',body:JSON.stringify({expected_revision:state.revision,current_conversation_id:result.conversation_id,drafts:state.drafts})});
        if(alive.current)location.hash='chat';
      }else{setSelected(result.instance_id);location.hash='business?instance='+encodeURIComponent(result.instance_id);await load();}
    }catch(e){if(alive.current)setError(e);}finally{if(alive.current)setBusy(false);}
  }
  const refresh=()=>{void load();if(flow)void loadObjects(flow);};
  return <div className="business-center"><SectionHeading eyebrow="MY BUSINESS" title="我的业务 / 继续办理" description="个人草稿、办理入口与授权业务目录在这里汇合。页面和对话继续同一个个人流程。"/>
    <div className="business-toolbar"><button className="button secondary" onClick={refresh}>刷新业务</button><a href="/reports/collaboration-case.html" target="_blank" rel="noreferrer">查看协作案例与架构说明</a></div>
    <ErrorNotice error={error}/>{loading?<Loading/>:<>
    <section className="panel"><h2>继续我的办理</h2><p className="subtle">每条记录属于本人；分支有独立草稿。打开时重新读取模块的正式业务状态。</p>
      {!personal.length?<p>还没有可恢复的个人流程，请从下方办理入口开始。</p>:<div className="business-list">{personal.map(item=><article key={item.id}>
        <div><strong>{item.title}</strong><p>{item.step_title} · {item.object_ref?'对象 '+item.object_ref.object_id.slice(0,8):'尚未绑定正式对象'}{item.forked_from?' · 对话分支':''}</p><small>上次办理 {dateTime(item.updated_at)}</small></div>
        <div className="business-buttons"><button disabled={busy} onClick={()=>void open({instance_id:item.id},'page')}>页面继续</button><button disabled={busy} onClick={()=>void open({instance_id:item.id},'conversation')}>对话继续</button></div>
      </article>)}</div>}
    </section>
    <section className="panel"><h2>按职责进入工作台</h2><p className="subtle">入口由当前授权决定。多个项目中兼任不同职责时，对象范围仍逐次检查。</p><div className="business-entries">{catalog.map(entry=><article key={entry.id}>
      <span className="tag">{entry.experience?'已注册业务权限':'既有流程'}</span><h3>{entry.title}</h3><p>{entry.description}</p>
      <div className="business-buttons"><button disabled={busy||!entry.available} onClick={()=>void open({flow_id:entry.id},'page')}>新建个人工作台</button><button disabled={busy||!entry.available} onClick={()=>void open({flow_id:entry.id},'conversation')}>在对话中开始</button><button disabled={!entry.available} onClick={()=>setFlow(entry.id)}>查看可办理业务</button></div>
    </article>)}</div>{!catalog.length&&<p>当前没有已授权的办理入口，请联系业务授权管理员。</p>}</section>
    {flow&&<section className="panel"><h2>{catalog.find(f=>f.id===flow)?.title} · 业务目录</h2><p className="subtle">模块实时返回当前允许查看的记录。可执行动作表示当前办理条件；历史记录可以只读。</p><ErrorNotice error={sourceError}/>
      {!items.length&&!sourceError&&<p>当前没有可访问记录。</p>}<div className="business-list">{items.map(item=><article key={item.object_ref.object_id}><div><strong>{item.title}</strong><p>项目 {item.project_id||'—'} · {item.state} · 业务修订 {item.revision}</p><small>{item.actions.length?'可执行：'+[...new Set(item.actions.map(a=>a.title))].join('、'):'当前只读或等待其他参与者办理'}</small></div><div className="business-buttons"><button disabled={busy} onClick={()=>void open({flow_id:flow,object_id:item.object_ref.object_id},'page')}>打开页面</button><button disabled={busy} onClick={()=>void open({flow_id:flow,object_id:item.object_ref.object_id},'conversation')}>在对话中办理</button></div></article>)}</div>
      {cursor&&<button onClick={()=>void loadObjects(flow,cursor)}>加载下一页</button>}
    </section>}
    </>}
    {selected&&<section className="panel"><div className="business-toolbar"><h2>当前办理</h2><button onClick={()=>{setSelected('');location.hash='business';}}>收起工作台</button></div><FlowHost key={selected} instanceId={selected} onChanged={refresh}/></section>}
  </div>;
}
