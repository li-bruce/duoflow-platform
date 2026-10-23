import { Activity, ChevronDown, LayoutDashboard, MessageSquare } from 'lucide-react';
import { dateTime } from './api';
import type { Capability, Module, Monitor, Surfaces } from './types';

const names:Record<string,string>={local:'本机可用',unknown:'等待检测',healthy:'在线',degraded:'连接不稳定',offline:'离线',stale:'状态已过期'};
export const sourceName=(source?:Module['source'])=>source==='core'?'平台核心':source==='remote'?'远程服务':'本机模块';
export const capabilityAvailable=(cap?:Capability)=>Boolean(cap&&cap.availability?.available!==false);
export const moduleAvailable=(module?:Module)=>Boolean(module&&module.enabled!==false&&!['offline','stale'].includes(module.monitor?.state||''));
export function AvailabilityBadge({state='unknown'}:{state?:string}){return <span className={'availability availability-'+state}><span/>{names[state]||state}</span>;}
export function MonitorPanel({monitor,source}:{monitor?:Monitor;source?:Module['source']}){
  if(source!=='remote')return <div className="monitor-local"><Activity size={15}/><span>本机加载的模块，无需远程心跳。</span></div>;
  return <section className="monitor-panel" aria-label="服务心跳状态"><div className="monitor-title"><span><Activity size={16}/>运行状态</span><AvailabilityBadge state={monitor?.state}/></div><p>{monitor?.message||'等待服务心跳检测。'}</p><div className="monitor-metrics"><div><small>最近检测</small><strong>{dateTime(monitor?.last_checked_at||undefined)}</strong></div><div><small>最近成功</small><strong>{dateTime(monitor?.last_success_at||undefined)}</strong></div><div><small>响应时间</small><strong>{monitor?.latency_ms==null?'—':`${monitor.latency_ms} ms`}</strong></div><div><small>连续失败</small><strong>{monitor?.consecutive_failures??0} 次</strong></div></div><small className="monitor-note">{monitor?`每 ${monitor.interval_seconds} 秒检测 · ${monitor.stale_after_seconds} 秒后视为过期。`:''}启用表示已发布，在线状态由心跳单独判断。</small></section>;
}
export function SurfaceSummary({surfaces,capabilities=[]}:{surfaces?:Surfaces;capabilities?:Capability[]}){
  const title=(id:string)=>capabilities.find(cap=>cap.id===id)?.title||id;
  return <div className="surface-summary"><section><h4><LayoutDashboard size={16}/>页面工具 <span>{surfaces?.page_tools.length||0}</span></h4>{surfaces?.page_tools.length?surfaces.page_tools.map(tool=><details key={tool.id}><summary><strong>{tool.title}</strong><span className="tag">{tool.kind==='embedded'?'独立工具页面':'共享表单'}</span><ChevronDown size={12}/></summary><p>{tool.description}</p><code>{tool.id}</code><ul>{tool.capability_ids.map(id=><li key={id}>{title(id)}</li>)}</ul></details>):<p className="subtle">此模块未声明页面工具。</p>}</section><section><h4><MessageSquare size={16}/>对话能力 <span>{surfaces?.conversation.capability_ids.length||0}</span></h4>{surfaces?.conversation.capability_ids.length?<ul className="surface-capabilities">{surfaces.conversation.capability_ids.map(id=><li key={id}><strong>{title(id)}</strong><code>{id}</code></li>)}</ul>:<p className="subtle">此模块未声明对话能力。</p>}{!!surfaces?.conversation.starter_prompts.length&&<div className="surface-prompts"><small>建议提问</small>{surfaces.conversation.starter_prompts.map((prompt,i)=><p key={i}>{prompt}</p>)}</div>}</section></div>;
}
