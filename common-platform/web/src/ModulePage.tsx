import { useEffect, useState } from 'react';
import { ExternalLink, RefreshCw } from 'lucide-react';
import { api } from './api';
import type { Module } from './types';
import { Empty, ErrorNotice, Loading } from './ui';
import { moduleAvailable } from './ModuleStatus';

type PageInfo={url:string;title:string;description:string;capability_ids:string[]};
export default function ModulePage({moduleId,toolId,modules}:{moduleId:string;toolId:string;modules:Module[]}){
  const [info,setInfo]=useState<PageInfo|null>(null);const [error,setError]=useState<unknown>();const [loading,setLoading]=useState(true);const [reload,setReload]=useState(0);
  const module=modules.find(item=>item.id===moduleId);const tool=module?.surfaces?.page_tools.find(item=>item.id===toolId);
  const available=moduleAvailable(module)&&tool?.kind==='embedded';
  const supported=typeof HTMLIFrameElement!=='undefined'&&'credentialless' in HTMLIFrameElement.prototype;
  const version=JSON.stringify([module?.revision,module?.version,tool?.id]);
  useEffect(()=>{let active=true;const controller=new AbortController();setInfo(null);setError(null);setLoading(true);if(!available||!supported){setLoading(false);return()=>controller.abort();}void api<PageInfo>(`/module-pages/${encodeURIComponent(moduleId)}/${encodeURIComponent(toolId)}`,{signal:controller.signal}).then(value=>{const url=new URL(value.url);if(!['http:','https:'].includes(url.protocol)||url.origin===location.origin||url.username||url.password)throw new Error('该工具页面地址不能在隔离窗口内加载。');if(active){setInfo(value);setLoading(false);}}).catch(e=>{if(active){setError(e);setLoading(false);}});return()=>{active=false;controller.abort();};},[moduleId,toolId,available,supported,version,reload]);
  if(!available)return <Empty title="工具页面暂不可用" description={!module?'所属模块已停用，或当前身份没有访问权限。已有工作记录仍可查看。':'服务已离线或心跳过期，恢复后可继续打开工具页面。'}/>;
  if(!supported)return <Empty title="当前浏览器不支持隔离工具页面" description="请使用支持 credentialless iframe 的新版 Chrome 或 Edge。此工具不会在无法隔离登录凭证的浏览器内加载。"/>;
  return <section className="module-page" aria-label={tool?.title||'模块工具页面'}><div className="module-page-heading"><div><span className="eyebrow">独立工具页面</span><h3>{info?.title||tool?.title}</h3><p>{info?.description||tool?.description}</p></div><button className="icon-button" aria-label="重新加载工具页面" title="重新加载工具页面" onClick={()=>setReload(v=>v+1)}><RefreshCw size={16}/></button></div><ErrorNotice error={error}/>{loading&&<Loading label="正在核验工具访问权限…"/>}{info&&<iframe key={`${moduleId}:${toolId}:${version}:${reload}`} {...{credentialless:''}} sandbox="allow-scripts allow-forms" referrerPolicy="no-referrer" src={info.url} title={info.title} className="module-page-frame"/>}<div className="module-page-note"><ExternalLink size={14}/><span>此页面由独立服务提供，使用隔离窗口，不传递工作台登录凭证。</span></div><details className="module-page-help"><summary>刚发布的网页工具未显示？</summary><p>新网页工具需要刷新浏览器页面以更新加载许可。刷新前请保存当前输入。</p><button className="text-button" onClick={()=>location.reload()}><RefreshCw size={13}/>刷新工作台页面</button></details></section>;
}
