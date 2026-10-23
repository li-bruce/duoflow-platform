import type { Session } from './types';
let csrfToken = '';
export class ApiError extends Error {constructor(public code:string,message:string,public status:number,public details?:unknown){super(message);this.name='ApiError';}}
export function setSession(value:Session|null){csrfToken=value?.csrf_token||'';}
export async function api<T>(path:string,options:RequestInit={}):Promise<T>{
  const headers = new Headers(options.headers); headers.set('Accept','application/json');
  if(options.body && !(options.body instanceof FormData) && !headers.has('Content-Type')) headers.set('Content-Type','application/json');
  if(options.method && !['GET','HEAD','OPTIONS'].includes(options.method.toUpperCase()) && csrfToken) headers.set('X-CSRF-Token',csrfToken);
  let response:Response;
  try {response=await fetch('/api/v1'+path,{...options,headers,credentials:'same-origin'});} catch {throw new ApiError('network_error','暂时无法连接服务，请检查本机服务是否已启动后重试。',0);}
  const body=await response.json().catch(()=>null);
  if(!response.ok){if(response.status===401&&path!=='/auth/login'&&path!=='/auth/session')window.dispatchEvent(new Event('session-expired'));const e=body?.error;throw new ApiError(e?.code||'request_failed',e?.message||(typeof body?.detail==='string'?body.detail:`请求未完成（${response.status}）`),response.status,e?.details||body?.detail);}
  return body as T;
}
export const post=<T,>(path:string,body:unknown={})=>api<T>(path,{method:'POST',body:JSON.stringify(body)});
export const requestId=()=>crypto.randomUUID();
export function errorText(error:unknown):string{if(error instanceof Error)return error.message;if(typeof error==='string')return error;if(error&&typeof error==='object'){const v=error as Record<string,unknown>;return String(v.message||v.code||'操作未完成，请重试。');}return '操作未完成，请重试。';}
export function dateTime(value?:string){if(!value)return '—';const d=new Date(value);return Number.isNaN(d.getTime())?value:d.toLocaleString('zh-CN',{month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit'});}
