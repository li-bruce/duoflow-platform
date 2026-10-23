import { useEffect, useState } from 'react';
import { api, post, requestId } from '../api';
import { ErrorNotice, Loading } from '../ui';
import type { WorkflowDefinition, WorkflowInstance } from './sdk';
import FlowHost from './FlowHost';
export default function FlowWorkbench(){
  const [flows,setFlows]=useState<WorkflowDefinition[]>();const [instances,setInstances]=useState<WorkflowInstance[]>([]);const [selected,setSelected]=useState('');const [error,setError]=useState<unknown>();
  async function load(){try{const [a,b]=await Promise.all([api<{items:WorkflowDefinition[]}>('/flows/catalog'),api<{items:WorkflowInstance[]}>('/flows/instances')]);setFlows(a.items);setInstances(b.items);}catch(e){setError(e);}}
  useEffect(()=>{void load();},[]);
  async function create(id:string){try{const item=await post<WorkflowInstance>('/flows/instances',{flow_id:id,idempotency_key:requestId()});setSelected(item.id);await load();}catch(e){setError(e);}}
  return <div className="flow-workbench"><ErrorNotice error={error}/><div className="flow-catalog">{flows?flows.map(flow=><article key={flow.id}><h3>{flow.title}</h3><p>{flow.description}</p><button disabled={!flow.available} onClick={()=>void create(flow.id)}>打开工作台</button></article>):<Loading/>}</div><label className="flow-resume">恢复已有流程<select aria-label="恢复已有流程" value={selected} onChange={e=>setSelected(e.target.value)}><option value="">选择个人流程</option>{instances.map(item=><option key={item.id} value={item.id}>{item.title} · {item.id.slice(0,8)}</option>)}</select></label>{selected&&<FlowHost key={selected} instanceId={selected} onChanged={()=>void load()}/>}</div>;
}
