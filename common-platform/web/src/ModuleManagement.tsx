import { isPlatformAdmin } from './types';
import { useCallback, useEffect, useRef, useState } from 'react';
import { Boxes, Check, ChevronRight, Cloud, LockKeyhole, Pause, Play, RefreshCw } from 'lucide-react';
import { api, post } from './api';
import type { Module, User } from './types';
import { Empty, ErrorNotice, Loading, Success } from './ui';
import { AvailabilityBadge, MonitorPanel, sourceName, SurfaceSummary } from './ModuleStatus';

type ManagedModule=Module&{
  manifest_protocol_version?:string|null;invoke_protocol_version?:string|null;
  registration?:{access_definition:'registered'|'disabled'|'not_registered';flow_ids:string[];component_ids:string[];missing_flow_ids:string[];unmapped_capability_ids:string[]};
};

function RegistrationSummary({module}:{module:ManagedModule}){
  const registration=module.registration;
  const accessLabels={registered:'已注册',disabled:'已停用',not_registered:'未注册'};
  return <section aria-label="模块注册与安装概况">
    {module.source==='remote'&&<p>Manifest 协议：<strong>{module.manifest_protocol_version||'未提供'}</strong>{' · '}Invoke 协议：<strong>{module.invoke_protocol_version||'未提供'}</strong></p>}
    <h4>已加载定义概况</h4>
    {registration?<>
      <p>权限定义：{accessLabels[registration.access_definition]} · 步骤流程定义：{registration.flow_ids.length} · 独立交互组件定义：{registration.component_ids.length}</p>
      {!!registration.flow_ids.length&&<p>流程：{registration.flow_ids.join('、')}</p>}
      {!!registration.component_ids.length&&<p>独立交互组件：{registration.component_ids.join('、')}</p>}
      {!!registration.missing_flow_ids.length&&<p className="notice info">权限办理视图引用的流程未加载或不属于本模块：{registration.missing_flow_ids.join('、')}</p>}
      {!!registration.unmapped_capability_ids.length&&<p className="notice info">尚未映射权限的能力：{registration.unmapped_capability_ids.join('、')}</p>}
    </>:<p className="subtle">当前响应未提供定义概况，请在对应安装和权限管理中核对。</p>}
    <p className="subtle">以上仅统计已加载定义。独立交互组件不包含步骤流程内部的组件，也不证明前端包已经构建可用。对话 skills 是顺序引导；两端入口注册、组件与流程安装、用户授权和实际业务验收需分别完成。</p>
    <p className="subtle">权限定义描述模块规则，实际用户授权另行分配。远程接入顺序：保存连接并发现 → 安装所需流程或组件 → 注册权限定义 → 启用 → 分配用户授权。启用不会自动授予业务访问权。</p>
  </section>;
}

export default function ModuleManagement({user,onRegistryChanged,compact=false}:{user:User;onRegistryChanged?:()=>void;compact?:boolean}){
  const [items,setItems]=useState<ManagedModule[]>([]);const [selected,setSelected]=useState('');const [loading,setLoading]=useState(true);const [busy,setBusy]=useState(false);const [error,setError]=useState<unknown>();const [pollError,setPollError]=useState<unknown>();const [notice,setNotice]=useState('');
  const identityKey=JSON.stringify([user.id,user.role,[...(user.roles||[])].sort(),user.authorization_revision]);
  const identity=useRef(identityKey);identity.current=identityKey;
  const generation=useRef(0);const mounted=useRef(false);const sequence=useRef(0);const reading=useRef<number|null>(null);const busyRef=useRef(false);
  const load=useCallback(async(silent=false)=>{
    const request=++sequence.current;const epoch=generation.current;const owner=identity.current;
    const current=()=>mounted.current&&epoch===generation.current&&owner===identity.current&&request===sequence.current;
    reading.current=request;if(!silent){setLoading(true);setError(null);}
    try{const value=await api<{items:ManagedModule[]}>('/modules');if(current()){setItems(value.items);setPollError(null);return value.items;}}
    catch(e){if(current()){if(silent)setPollError(e);else setError(e);}}
    finally{if(reading.current===request)reading.current=null;if(current())setLoading(false);}
  },[]);
  useEffect(()=>{
    mounted.current=true;generation.current++;sequence.current++;reading.current=null;busyRef.current=false;
    setItems([]);setSelected('');setError(null);setPollError(null);setNotice('');setBusy(false);setLoading(isPlatformAdmin(user));
    if(!isPlatformAdmin(user))return()=>{mounted.current=false;generation.current++;sequence.current++;};
    void load();const timer=window.setInterval(()=>{if(!busyRef.current&&reading.current===null)void load(true);},5000);
    return()=>{mounted.current=false;generation.current++;sequence.current++;clearInterval(timer);};
  },[load,identityKey]);
  const module=items.find(item=>item.id===selected)||items[0];
  async function toggle(){
    if(!module||module.protected||module.revision===undefined||busyRef.current)return;
    const epoch=generation.current;const owner=identity.current;
    const current=()=>mounted.current&&epoch===generation.current&&owner===identity.current;
    busyRef.current=true;setBusy(true);sequence.current++;reading.current=null;setLoading(false);setError(null);setPollError(null);setNotice('');
    try{
      await post(`/modules/${encodeURIComponent(module.id)}/${module.enabled?'disable':'enable'}`,{expected_revision:module.revision});
      if(!current())return;
      const rows=await load(true);if(!current())return;
      onRegistryChanged?.();
      setNotice(!rows?'更新请求已完成，但列表读取失败，请刷新核对当前状态。':module.enabled?'模块已停用。已有记录保留，新入口已从页面和对话移除。':'模块已启用。页面和对话按各自声明及用户授权提供入口，组件安装与业务验收须分别核对。');
    }catch(e){if(current())setError(e);}finally{if(current()){busyRef.current=false;setBusy(false);}}
  }
  if(!isPlatformAdmin(user))return <Empty title="模块管理由管理员维护" description="你仍可使用已启用并授权给你的页面工具和对话能力。"/>;
  return <section className={'module-management '+(compact?'compact':'')} aria-label="统一模块管理"><div className="module-management-intro"><div className="icon-tile"><Boxes size={22}/></div><div><h2>在一处管理所有模块</h2><p>统一管理已加载模块的启停。连接发现、两端入口、组件安装与用户授权分别核对。</p></div><button className="icon-button" onClick={()=>void load()} disabled={loading||busy} title="刷新模块列表" aria-label="刷新模块列表"><RefreshCw size={17} className={loading?'spin':''}/></button></div><div className="module-management-stats"><span><strong>{items.length}</strong>个模块</span><span><strong>{items.filter(item=>item.enabled).length}</strong>已启用</span><span><strong>{items.filter(item=>item.source==='remote').length}</strong>远程服务</span><small>状态每 5 秒更新</small></div><ErrorNotice error={error||pollError}/>{notice&&<Success>{notice}</Success>}{loading&&!items.length?<Loading label="正在读取模块目录…"/>:!items.length?<Empty title="暂无可管理模块"/>:<div className="module-management-layout"><aside className="module-management-list">{items.map(item=><button key={item.id} className={module?.id===item.id?'selected':''} onClick={()=>{setSelected(item.id);setNotice('');}} disabled={busy}><span className="module-list-icon">{item.source==='remote'?<Cloud size={19}/>:<Boxes size={19}/>}</span><span><strong>{item.name}</strong><small>{sourceName(item.source)} · v{item.version}</small><span className="module-list-state"><span className={'tag '+(item.enabled?'green':'')}>{item.enabled?'已启用':'已停用'}</span>{item.source==='remote'&&<AvailabilityBadge state={item.monitor?.state}/>}</span></span><ChevronRight size={14}/></button>)}</aside>{module&&<article className="module-detail"><div className="module-detail-heading"><div><span className="eyebrow">{sourceName(module.source)}</span><h3>{module.name}</h3><code>{module.id}</code></div><span className={'tag '+(module.enabled?'green':'')}>{module.enabled?<Check size={12}/>:<Pause size={12}/>} {module.enabled?'已启用':'已停用'}</span></div><p className="subtle">{module.description}</p><div className="module-detail-meta"><span>版本 {module.version}</span><span>修订 {module.revision??'—'}</span><span>{module.capabilities.length} 项能力</span>{module.service_id&&<code>{module.service_id}</code>}</div><RegistrationSummary module={module}/><MonitorPanel monitor={module.monitor} source={module.source}/><SurfaceSummary surfaces={module.surfaces} capabilities={module.capabilities}/><div className="module-manage-actions">{module.protected?<div className="notice info"><LockKeyhole size={16}/>平台核心模块维持登录、注册与管理能力，持续启用。</div>:<><button className={'button '+(module.enabled?'secondary':'primary')} disabled={busy||module.revision===undefined} onClick={()=>void toggle()}>{busy?<Loading label="正在更新…"/>:module.enabled?<><Pause size={15}/>停用模块</>:<><Play size={15}/>启用模块</>}</button><p>停用后保留资源、任务和对话记录。远程服务的启用状态在此处与“服务接入”页同步。</p></>}</div></article>}</div>}</section>;
}
