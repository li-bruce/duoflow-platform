import http from 'node:http';
import fs from 'node:fs';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import crypto from 'node:crypto';
import Ajv2020 from 'ajv/dist/2020.js';
import {handle} from './handler.mjs';

const root=path.dirname(fileURLToPath(import.meta.url));
const manifest=JSON.parse(fs.readFileSync(path.join(root,'manifest.json'),'utf8'));
const requestSchema=JSON.parse(fs.readFileSync(path.join(root,'invoke-request.schema.json'),'utf8'));
const validator=new Ajv2020({strict:false,validateFormats:false});
const checkRequest=validator.compile(requestSchema);
const specs=new Map(manifest.capabilities.map(cap=>[cap.id,{cap,input:validator.compile(cap.input_schema),output:validator.compile(cap.output_schema)}]));
const token=process.env.PLATFORM_SERVICE_TOKEN;
if(!token||token.length<24)throw new Error('Set PLATFORM_SERVICE_TOKEN to a private service credential of at least 24 characters.');
const data=path.resolve(process.env.SERVICE_DATA_DIR||path.join(root,'data'));
fs.mkdirSync(data,{recursive:true});
const canonical=value=>Array.isArray(value)?value.map(canonical):value&&typeof value==='object'?Object.fromEntries(Object.keys(value).sort().map(key=>[key,canonical(value[key])])):value;
const hash=value=>crypto.createHash('sha256').update(JSON.stringify(canonical(value))).digest('hex');
let queue=Promise.resolve();
const fail=(code,message,status)=>Object.assign(new Error(message),{code,status});
function authenticated(request){const a=Buffer.from(request.headers.authorization||''),b=Buffer.from('Bearer '+token);return a.length===b.length&&crypto.timingSafeEqual(a,b);}
function reply(response,status,value){response.writeHead(status,{'Content-Type':'application/json; charset=utf-8','Cache-Control':'no-store','X-Content-Type-Options':'nosniff'});response.end(JSON.stringify(value));}
async function execute(request){
  if(!checkRequest(request))throw fail('request_contract','调用协议无效。',422);
  const cache=path.join(data,request.request_id+'.json'),fingerprint=hash(request);
  const saved=fs.existsSync(cache)?JSON.parse(fs.readFileSync(cache,'utf8')):null;
  // Detect altered retries before looking up their replacement capability.
  // Replay only after all current capability, identity and input checks pass.
  if(saved&&saved.fingerprint!==fingerprint)throw fail('idempotency_conflict','相同编号对应不同请求。',409);
  const entry=specs.get(request.capability_id);
  if(!entry||entry.cap.version!==request.capability_version)throw fail('capability_missing','能力或版本不存在。',404);
  const actor=request.context.actor;
  if(!actor.roles.includes(actor.role)||!entry.cap.roles.some(role=>actor.roles.includes(role)))throw fail('forbidden','当前身份无权执行此能力。',403);
  if(!entry.input(request.input))throw fail('input_validation','输入不符合业务契约。',422);
  if(saved)return saved.response;
  const result=await handle(request.input,actor);
  if(!entry.output(result.data)||typeof result.summary!=='string')throw fail('output_contract','业务返回不符合契约。',500);
  const response={protocol_version:'1.1',request_id:request.request_id,capability_id:request.capability_id,capability_version:request.capability_version,summary:result.summary,data:result.data};
  const temporary=cache+'.tmp';fs.writeFileSync(temporary,JSON.stringify({fingerprint,response}),{mode:0o600});fs.renameSync(temporary,cache);
  return response;
}
const server=http.createServer(async(request,response)=>{
  try{
    if(!authenticated(request))return reply(response,401,{error:{code:'service_auth',message:'服务认证失败。'}});
    if(request.method==='GET'&&request.url==='/platform/v1/manifest')return reply(response,200,manifest);
    if(request.method==='GET'&&request.url==='/platform/v1/health')return reply(response,200,{status:'ok'});
    if(request.method!=='POST'||request.url!=='/platform/v1/invoke')return reply(response,404,{error:{code:'not_found',message:'接口不存在。'}});
    let length=0;const chunks=[];
    for await(const chunk of request){length+=chunk.length;if(length>65536)throw fail('request_size','请求超过模板的 64 KB 限制。',413);chunks.push(chunk);}
    let body;try{body=JSON.parse(Buffer.concat(chunks).toString('utf8'));}catch{throw fail('invalid_json','请求不是有效 JSON。',422);}
    // Serialized requests plus durable receipts prevent simultaneous retries
    // within this one-process reference adapter. Handler side effects still
    // require domain-specific transactional idempotency.
    const work=queue.then(()=>execute(body));queue=work.catch(()=>{});
    reply(response,200,await work);
  }catch(error){reply(response,error.status||500,{error:{code:error.code||'handler_failed',message:error.status?error.message:'处理失败，请检查服务日志与业务实现。'}});}
});
server.listen(Number(process.env.PORT||8032),process.env.HOST||'127.0.0.1',()=>console.log('Component service ready.'));
for(const signal of ['SIGTERM','SIGINT'])process.on(signal,()=>server.close(()=>process.exit(0)));
