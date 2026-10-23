import { useMemo, useState } from 'react';
import type { Data } from './types';
import type { UIContext } from './ResultView';
import CapabilityForm from './CapabilityForm';
import { capabilityAvailable } from './ModuleStatus';
import { Empty } from './ui';
import './workspace.css';

type Block = Data & {type:string;title?:string};
const display=(value:unknown)=>value===null||value===undefined?'—':typeof value==='object'?JSON.stringify(value):String(value);

function DataTable({block}:{block:Block}){
  const columns=(block.columns||[]) as {key:string;label:string}[];
  const rows=(block.rows||[]) as Data[];
  const [query,setQuery]=useState('');const [sort,setSort]=useState<{key:string;reverse:boolean}|null>(null);const [page,setPage]=useState(0);
  const selected=useMemo(()=>{const result=rows.filter(row=>columns.some(c=>display(row[c.key]).toLocaleLowerCase().includes(query.toLocaleLowerCase())));if(sort)result.sort((a,b)=>{const av=a[sort.key],bv=b[sort.key];const order=typeof av==='number'&&typeof bv==='number'?av-bv:display(av).localeCompare(display(bv),'zh-CN',{numeric:true});return sort.reverse?-order:order;});return result;},[rows,columns,query,sort]);
  const pages=Math.max(1,Math.ceil(selected.length/20));const current=Math.min(page,pages-1);
  return <div className="workspace-table"><div className="workspace-toolbar"><input aria-label={(block.title||'表格')+'筛选'} placeholder="在当前结果中筛选" value={query} onChange={e=>{setQuery(e.target.value);setPage(0);}}/><span>显示 {selected.length} / {rows.length} 条</span></div><div className="workspace-table-scroll"><table><thead><tr>{columns.map(c=><th key={c.key}><button type="button" onClick={()=>setSort({key:c.key,reverse:sort?.key===c.key?!sort.reverse:false})}>{c.label}{sort?.key===c.key?(sort.reverse?' ↓':' ↑'):''}</button></th>)}</tr></thead><tbody>{selected.slice(current*20,current*20+20).map((row,i)=><tr key={i}>{columns.map(c=><td key={c.key}>{display(row[c.key])}</td>)}</tr>)}</tbody></table></div>{!selected.length&&<Empty title="没有匹配的记录"/>}<div className="workspace-toolbar"><button type="button" className="button secondary small" disabled={current===0} onClick={()=>setPage(current-1)}>上一页</button><span>第 {current+1} / {pages} 页</span><button type="button" className="button secondary small" disabled={current===pages-1} onClick={()=>setPage(current+1)}>下一页</button></div>{typeof block.caption==='string'&&<p className="subtle">{block.caption}</p>}</div>;
}

function Chart({block}:{block:Block}){
  const series=(block.series||[]) as {label:string;value:number}[];const [selected,setSelected]=useState<number|null>(null);
  const maximum=Math.max(1,...series.map(item=>Math.abs(item.value)));
  return <div className="workspace-chart" role="group" aria-label={block.title||'统计图表'}><p className="subtle">{display(block.x_label||'类别')} / {display(block.y_label||'数值')}</p>{series.map((item,i)=><button type="button" key={i} className={'workspace-bar '+(selected===i?'selected':'')} onClick={()=>setSelected(selected===i?null:i)} aria-pressed={selected===i}><span>{item.label}</span><span className="workspace-bar-track"><span style={{width:`${Math.max(1,Math.abs(item.value)/maximum*100)}%`,background:item.value<0?'#aa5760':undefined}}/></span><strong>{item.value}</strong></button>)}{!series.length&&<Empty title="没有可绘制的数据"/>}{selected!==null&&<p role="status">已选择 {series[selected].label}：{series[selected].value}</p>}{typeof block.caption==='string'&&<p className="subtle">{block.caption}</p>}</div>;
}

function MapView({block}:{block:Block}){
  const points=(block.points||[]) as {label:string;lat:number;lon:number;count?:number}[];const [selected,setSelected]=useState<number|null>(null);
  return <div className="workspace-map"><p className="subtle">经纬度分布示意（非行政区底图） · {String(block.precision||'精度以来源声明为准')}</p><svg viewBox="0 0 720 360" role="img" aria-label={block.title||'空间分布图'}><rect width="720" height="360" fill="#edf3f5"/>{[-120,-60,0,60,120].map(lon=><g key={lon}><line x1={(lon+180)*2} x2={(lon+180)*2} y1="0" y2="360" stroke="#d0dde0"/><text x={(lon+180)*2+3} y="354">{lon}°</text></g>)}{[-60,0,60].map(lat=><g key={lat}><line x1="0" x2="720" y1={(90-lat)*2} y2={(90-lat)*2} stroke="#d0dde0"/><text x="4" y={(90-lat)*2-4}>{lat}°</text></g>)}{points.map((point,i)=><circle key={i} cx={(point.lon+180)*2} cy={(90-point.lat)*2} r={Math.min(17,5+Math.sqrt(point.count||1))} fill={selected===i?'#df9c33':'#167b89'} opacity=".8" tabIndex={0} role="button" aria-label={`${point.label}：${point.count??1}`} onClick={()=>setSelected(i)} onKeyDown={e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();setSelected(i);}}}><title>{point.label} · {point.count??1}</title></circle>)}</svg>{selected!==null&&points[selected]&&<p role="status">{points[selected].label} · 纬度 {points[selected].lat} · 经度 {points[selected].lon} · 数量 {points[selected].count??1}</p>}{!points.length&&<Empty title="没有可显示的位置"/>}{typeof block.caption==='string'&&<p className="subtle">{block.caption}</p>}</div>;
}

function ActionList({block,context}:{block:Block;context:UIContext}){
  const items=(block.items||[]) as {label:string;capability_id:string;input:Data}[];const [selected,setSelected]=useState<number|null>(null);const [notice,setNotice]=useState('');
  const action=selected===null?null:items[selected];const capability=context.capabilities.find(cap=>cap.id===action?.capability_id);
  return <div><div className="workspace-actions">{items.map((item,i)=>{const cap=context.capabilities.find(c=>c.id===item.capability_id);return <button type="button" key={i} className={'button '+(selected===i?'primary':'secondary')} disabled={!cap||!capabilityAvailable(cap)} onClick={()=>{setSelected(i);setNotice('');}}>{item.label}</button>;})}</div>{notice&&<p role="status">{notice}</p>}{action&&capability&&<CapabilityForm key={action.capability_id+JSON.stringify(action.input)} capability={capability} initialInput={context.formDrafts?.[capability.id]||action.input} conversationId={context.conversationId} onInputChange={data=>context.onFormInputChange?.(capability.id,data)} compact onResult={value=>{context.onFormInputChange?.(capability.id,{});context.onResult?.(value);context.onChanged();setNotice(value.summary||'操作已提交，可在运行记录中继续查看。');}}/>}</div>;
}

function FileDownload({block}:{block:Block}){
  const url=typeof block.download_url==='string'&&/^\/api\/v1\/invocations\/[0-9a-f]{32}\/workspace\/\d+\/\d+\/download$/.test(block.download_url)?block.download_url:null;
  return <div>{url?<a className="button secondary" href={url} download={String(block.filename).replace(/[\\/\r\n]/g,'_')}>下载 {String(block.filename)}</a>:<p role="alert">此文件尚无可用的平台下载链接，请刷新结果或重新生成。</p>}</div>;
}

export default function WorkspaceView({data,context}:{data:Data;context:UIContext}){
  const blocks=(Array.isArray(data.blocks)?data.blocks:[]) as Block[];
  return <div className="workspace-blocks">{blocks.map((block,index)=><section className={'workspace-block block-'+block.type} key={index} aria-label={block.title||block.type}>{block.title&&<h3>{block.title}</h3>}{block.type==='text'?<p className="workspace-text">{String(block.text||'')}</p>:block.type==='table'?<DataTable block={block}/>:block.type==='metrics'?<div className="result-stats">{((block.items||[]) as Data[]).map((item,i)=><div key={i}><strong>{display(item.value)} {display(item.unit||'')}</strong><span>{display(item.label)}</span></div>)}</div>:block.type==='chart'?<Chart block={block}/>:block.type==='map'?<MapView block={block}/>:block.type==='evidence'?<div>{((block.items||[]) as Data[]).map((item,i)=><article className="workspace-evidence" key={i}><header><strong>{display(item.feature)}：{display(item.value)}</strong><span className="tag">{display(item.status)}</span></header><blockquote>{display(item.quote)}</blockquote>{item.source!==undefined&&<small>依据：{display(item.source)}</small>}</article>)}</div>:block.type==='actions'?<ActionList block={block} context={context}/>:block.type==='image'?(['image/png','image/jpeg','image/webp'].includes(String(block.media_type))?<figure><img className="workspace-image" src={`data:${block.media_type};base64,${block.content_base64}`} alt={String(block.alt||'')}/><figcaption>{String(block.alt||'')}</figcaption></figure>:<p>不支持的图片格式。</p>):block.type==='download'?<FileDownload block={block}/>:<p>此内容类型尚不受支持。</p>}</section>)}</div>;
}

export function AgentFlowView({data}:{data:Data}){
  const steps=(Array.isArray(data.steps)?data.steps:[]) as Data[];
  const skills=(Array.isArray(data.skills)?data.skills:[]) as Data[];
  const statuses:Record<string,string>={awaiting_input:'等待填写',awaiting_result:'等待结果',failed:'执行未完成',completed:'流程已完成',blocked:'等待授权角色办理',exited:'已退出',catalog:'可用流程',unavailable:'流程暂不可用'};
  return <section className="agent-flow"><span className="eyebrow">{statuses[String(data.status)]||'当前办理流程'}</span><h3>{String(data.title||data.skill_id||'已登记流程')}</h3>{!!skills.length&&<ul>{skills.map(skill=><li key={String(skill.command)}><strong>{String(skill.title)}</strong><p><code>{String(skill.command)}</code></p></li>)}</ul>}<ol>{steps.map((step,i)=><li key={String(step.id||i)} className={data.current_step===step.id||data.current_step===i?'current':''}><strong>{step.status==='completed'?'✓ ':''}{String(step.title||step.id)}</strong><small>{String(step.capability_id||'')}</small></li>)}</ol><p className="subtle">核对表单后执行，结果完成后输入“继续”进入下一步。分支保留对话上下文，不撤销已完成的业务操作。</p></section>;
}
