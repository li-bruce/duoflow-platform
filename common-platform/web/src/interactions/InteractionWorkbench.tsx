import { useCallback, useEffect, useState } from 'react';
import { api, post, requestId } from '../api';
import { ErrorNotice, Loading, SectionHeading } from '../ui';
import type { Data } from '../types';
import type { InteractionDefinition, InteractionInstance } from './sdk';
import InteractionHost from './InteractionHost';

export default function InteractionWorkbench(){
  const [catalog,setCatalog]=useState<InteractionDefinition[]>([]),[items,setItems]=useState<InteractionInstance[]>([]);
  const [selected,setSelected]=useState(''),[error,setError]=useState<unknown>(),[busy,setBusy]=useState(false);
  const load=useCallback(async()=>{try{const [defs,records]=await Promise.all([api<{items:InteractionDefinition[]}>('/interactions/catalog'),api<{items:InteractionInstance[]}>('/interactions/instances')]);setCatalog(defs.items);setItems(records.items);}catch(e){setError(e);}},[]);
  useEffect(()=>{void load();},[load]);
  async function create(def:InteractionDefinition){setBusy(true);setError(undefined);try{const input:Data={};for(const [key,value]of Object.entries((def.input_schema.properties||{}) as Record<string,Data>)){if('default'in value)input[key]=value.default;}const next=await post<InteractionInstance>('/interactions/instances',{component_id:def.id,input,idempotency_key:requestId()});setSelected(next.id);await load();}catch(e){setError(e);}finally{setBusy(false);}}
  return <><SectionHeading eyebrow="SHARED INTERACTIONS" title="组件工作台" description="一次开发的业务组件，可以在这里操作，也可以在统一对话中按需调用。"/><ErrorNotice error={error}/><div className="interaction-catalog">{catalog.map(def=><article className="panel" key={def.id}><h3>{def.title}</h3><p>{def.description}</p><button className="button primary" disabled={busy||!def.available} onClick={()=>void create(def)}>新建交互</button><p className="subtle">对话入口：/component {def.id}</p></article>)}</div>{!catalog.length&&<p>暂无当前身份可用的组件包。安装组件与后端能力后会在这里出现。</p>}{busy&&<Loading label="正在创建…"/>}{!!items.length&&<label className="field">恢复已有交互<select value={selected} onChange={e=>setSelected(e.target.value)}><option value="">请选择</option>{items.map(item=><option key={item.id} value={item.id}>{item.title} · {item.id.slice(0,8)} · {item.status==='completed'?'已完成':'可继续'}</option>)}</select></label>}{selected&&<InteractionHost key={selected} instanceId={selected} onChanged={()=>void load()}/>}</>;
}
