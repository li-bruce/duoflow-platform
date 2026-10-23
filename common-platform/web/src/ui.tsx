import { AlertCircle, ArrowRight, Check, LoaderCircle, PackageOpen } from 'lucide-react';
import type { ReactNode } from 'react';
import { errorText } from './api';
export const statusNames:Record<string,string>={queued:'排队中',running:'运行中',waiting_human:'等待人工处理',succeeded:'已完成',completed:'已完成',failed:'运行失败',cancelled:'已取消',draft:'草稿',in_review:'待审核',accepted:'已接受',returned:'已退回',pending:'等待执行',success:'已完成',error:'未完成'};
export function Status({value}:{value:string}){return <span className={'status status-'+value}><span/>{statusNames[value]||value||'待处理'}</span>;}
export function ErrorNotice({error,onRetry}:{error:unknown;onRetry?:()=>void}){if(!error)return null;return <div className="notice error" role="alert"><AlertCircle size={17}/><div>{errorText(error)}</div>{onRetry&&<button className="text-button" onClick={onRetry}>重试</button>}</div>;}
export function Loading({label='正在读取…'}:{label?:string}){return <div className="loading" role="status"><LoaderCircle size={18} className="spin"/>{label}</div>;}
export function Empty({title,description,action}:{title:string;description?:string;action?:ReactNode}){return <div className="empty"><PackageOpen size={30} strokeWidth={1.35}/><h3>{title}</h3>{description&&<p>{description}</p>}{action}</div>;}
export function SectionHeading({eyebrow,title,description,actions}:{eyebrow?:string;title:string;description?:string;actions?:ReactNode}){return <div className="section-heading"><div>{eyebrow&&<div className="eyebrow">{eyebrow}</div>}<h1>{title}</h1>{description&&<p>{description}</p>}</div>{actions&&<div className="heading-actions">{actions}</div>}</div>;}
export function Success({children}:{children:ReactNode}){return <div className="notice success" role="status"><Check size={17}/><div>{children}</div></div>;}
export function ActionLink({children,onClick}:{children:ReactNode;onClick:()=>void}){return <button className="text-button" onClick={onClick}>{children}<ArrowRight size={15}/></button>;}
export function PrettyData({data}:{data:unknown}){return <pre className="json-view">{JSON.stringify(data,null,2)}</pre>;}
