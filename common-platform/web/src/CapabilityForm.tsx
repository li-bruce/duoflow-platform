import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from 'react';
import { JsonForms, withJsonFormsControlProps } from '@jsonforms/react';
import { and, formatIs, isEnumControl, isStringControl, not, rankWith, schemaMatches, UPDATE_CORE } from '@jsonforms/core';
import type { ControlProps, JsonSchema, UISchemaElement, Middleware } from '@jsonforms/core';
import { vanillaCells, vanillaRenderers } from '@jsonforms/vanilla-renderers';
import { ArrowRight, Check, FileCheck2, Play, RotateCcw, Sparkles } from 'lucide-react';
import { api, post, requestId } from './api';
import type { Capability, Data, ModelProfile, Result } from './types';
import { ErrorNotice, Loading, PrettyData } from './ui';
import { schemaValidator } from './schemaValidator';
import { AvailabilityBadge, capabilityAvailable } from './ModuleStatus';

const TextControl=withJsonFormsControlProps(({id:controlId,data,handleChange,path,label,required,description,errors,enabled,visible,uischema,schema}:ControlProps)=>{
  if(!visible)return null;const multiline=Boolean(uischema.options?.multi||schema.format==='textarea'||(schema.maxLength||0)>280);
  const id=controlId||'field-'+path.replace(/[^a-z0-9]/gi,'-');
  return <div className="field schema-field"><label htmlFor={id}>{label}{required&&<span className="required"> *</span>}</label>{multiline?<textarea id={id} value={data??''} disabled={!enabled} rows={7} maxLength={schema.maxLength} placeholder={description||'请输入内容'} onChange={e=>handleChange(path,e.target.value||undefined)}/>:<input id={id} value={data??''} type={schema.format==='password'?'password':'text'} disabled={!enabled} maxLength={schema.maxLength} placeholder={description||''} onChange={e=>handleChange(path,e.target.value||undefined)}/>} {description&&<small>{description}</small>}{errors&&<span className="field-error">{errors}</span>}</div>;
});
const ModelProfileControl=withJsonFormsControlProps(({id,data,handleChange,path,label,description,errors,enabled,visible}:ControlProps)=>{
  const [items,setItems]=useState<ModelProfile[]>([]);const [loading,setLoading]=useState(true);const [error,setError]=useState<unknown>();
  useEffect(()=>{let active=true;const load=async()=>{setLoading(true);try{const result=await api<{items:ModelProfile[]}>('/models');if(active){setItems(result.items.filter(p=>p.enabled!==false));setError(null);}}catch(e){if(active)setError(e);}finally{if(active)setLoading(false);}};void load();const changed=()=>void load();window.addEventListener('model-profiles-changed',changed);return()=>{active=false;window.removeEventListener('model-profiles-changed',changed);};},[]);
  if(!visible)return null;
  return <div className="field schema-field"><label htmlFor={id}>{label}</label><select id={id} value={data??''} disabled={!enabled||loading} onChange={e=>handleChange(path,e.target.value)}><option value="">{loading?'正在读取模型连接…':'使用平台默认模型'}</option>{items.map(p=><option key={p.id} value={p.id}>{p.name} · {p.model}</option>)}</select>{description&&<small>{description}</small>}{!loading&&!items.length&&<small>暂无已启用连接；请先由管理员配置模型。</small>}{errors&&<span className="field-error">{errors}</span>}<ErrorNotice error={error}/></div>;
});
// Syntax errors and pending file reads are UI state, not JSON values. Keep them
// separate from JSON Forms data so stale valid values can never be submitted.
const FieldValidity = createContext<(path:string,message:string)=>void>(()=>{});
function useFieldValidity(path:string){
  const report=useContext(FieldValidity);
  useEffect(()=>()=>report(path,''),[path,report]);
  return useCallback((message:string)=>report(path,message),[path,report]);
}
type FileValue={filename:string;media_type:string;content_base64:string};
type FieldProps={schema:JsonSchema;rootSchema:JsonSchema;value:unknown;onChange:(value:unknown)=>void;id:string;path:string;label:string;required?:boolean;enabled:boolean;depth?:number};
function FileField({schema,value,onChange,id,path,label,enabled,multiple=false}:{schema:JsonSchema;value:unknown;onChange:(value:unknown)=>void;id:string;path:string;label:string;enabled:boolean;multiple?:boolean}){
  const [error,setError]=useState('');const [reading,setReading]=useState(false);const input=useRef<HTMLInputElement>(null);const sequence=useRef(0);
  const report=useFieldValidity(path+'/file');
  useEffect(()=>()=>{sequence.current++;},[]);
  const files=(multiple?(Array.isArray(value)?value:[]):value?[value]:[]) as FileValue[];
  function clear(next:FileValue[]=[]){sequence.current++;setError('');setReading(false);report('');onChange(multiple?next:next[0]);if(input.current)input.current.value='';}
  async function select(selected:File[]){
    const request=++sequence.current;setError('');setReading(false);onChange(multiple?[]:undefined);
    if(!selected.length){report('');return;}
    if(multiple&&schema.maxItems!==undefined&&selected.length>schema.maxItems){const message=`最多选择 ${schema.maxItems} 个文件。`;setError(message);report(message);return;}
    if(selected.some(file=>!file.size||file.size>4*1024*1024)){const message='请选择非空且不超过 4 MiB 的文件。';setError(message);report(message);return;}
    setReading(true);report('文件尚在读取，请稍候。');
    try{
      const result=await Promise.all(selected.map(async file=>{const bytes=new Uint8Array(await file.arrayBuffer());if(!bytes.length||bytes.length>4*1024*1024)throw Error();let text='';for(let i=0;i<bytes.length;i+=8192)text+=String.fromCharCode(...bytes.subarray(i,i+8192));return {filename:file.name,media_type:file.type||'application/octet-stream',content_base64:btoa(text)};}));
      if(request!==sequence.current)return;onChange(multiple?result:result[0]);report('');
    }catch{if(request===sequence.current){setError('读取文件失败，请重新选择。');report('读取文件失败，请重新选择。');}}
    finally{if(request===sequence.current)setReading(false);}
  }
  return <div className="field platform-file-control"><label htmlFor={id}>{label}</label><input ref={input} id={id} type="file" multiple={multiple} disabled={!enabled||reading} onChange={event=>void select(Array.from(event.target.files||[]))}/>{files.map((file,index)=><div key={index} className="form-actions" style={{marginTop:0}}><p className="file-status">已选择：{String(file.filename)}</p>{multiple&&<button type="button" className="text-button" disabled={!enabled||reading} onClick={()=>clear(files.filter((_,i)=>i!==index))}>移除 {file.filename}</button>}</div>)}<small>{schema.description||`单文件最多 4 MiB${multiple&&schema.maxItems!==undefined?`，最多 ${schema.maxItems} 个`:''}。切换登录或刷新后请重新选择文件。`}</small>{reading&&<small>正在读取文件…</small>}{error&&<span role="alert" className="field-error">{error}</span>}{(files.length>0||error||reading)&&<button type="button" className="text-button" disabled={!enabled} onClick={()=>clear()}>清空{label}</button>}</div>;
}
function JsonValueField({value,onChange,id,path,label,schema,enabled}:FieldProps){
  const stringify=(v:unknown)=>v===undefined?'':JSON.stringify(v,null,2);
  const [text,setText]=useState(()=>stringify(value));const [error,setError]=useState('');const previous=useRef<string|undefined>(JSON.stringify(value));const report=useFieldValidity(path+'/json');
  useEffect(()=>{const current=JSON.stringify(value);if(current!==previous.current){previous.current=current;setText(stringify(value));setError('');report('');}},[value,report]);
  function edit(text:string){setText(text);if(!text.trim()){previous.current=undefined;setError('');report('');onChange(undefined);return;}try{const parsed:unknown=JSON.parse(text);previous.current=JSON.stringify(parsed);setError('');report('');onChange(parsed);}catch{const message='JSON 格式不正确，修正后才能提交。';setError(message);report(message);}}
  return <div className="field schema-field"><label htmlFor={id}>{label}（JSON）</label><textarea id={id} value={text} disabled={!enabled} rows={7} spellCheck={false} placeholder={schema.type==='array'?'[]':'{}'} onChange={event=>edit(event.target.value)}/><small>{schema.description||'可输入嵌套对象、数组及动态字段。字符串使用双引号；留空表示未填写。'}</small>{error&&<span className="field-error" role="alert">{error}</span>}</div>;
}
function resolveField(schema:JsonSchema,root:JsonSchema):JsonSchema{
  if(!schema.$ref?.startsWith('#/'))return schema;
  let resolved:unknown=root;for(const part of schema.$ref.slice(2).split('/'))resolved=resolved&&typeof resolved==='object'?(resolved as Data)[part.replace(/~1/g,'/').replace(/~0/g,'~')]:undefined;
  if(resolved&&typeof resolved==='object'){const { $ref: _ref,...rest}=schema;return {...resolved,...rest};}return schema;
}
function nullableBranch(schema:JsonSchema):JsonSchema|null{
  if(Array.isArray(schema.type)&&schema.type.includes('null')&&schema.type.length===2)return {...schema,type:schema.type.find(type=>type!=='null')};
  const branches=schema.anyOf||schema.oneOf;
  if(branches?.length===2&&branches.some(branch=>branch.type==='null')){const {anyOf:_any,oneOf:_one,...base}=schema;return {...base,...branches.find(branch=>branch.type!=='null')} as JsonSchema;}
  return null;
}
function defaultField(schema:JsonSchema):unknown{return schema.default!==undefined&&schema.default!==null?schema.default:schema.enum?.[0]??(schema.type==='object'?{}:schema.type==='array'?[]:schema.type==='boolean'?false:schema.type==='number'||schema.type==='integer'?0:'');}
function choicesFor(schema:JsonSchema):{value:unknown;label:string}[]|null{
  const label=(value:unknown)=>typeof value==='string'?(value||'（空字符串）'):JSON.stringify(value)??String(value);
  if(schema.enum)return schema.enum.map(value=>({value,label:label(value)}));
  if(schema.oneOf?.length&&schema.oneOf.every(option=>Object.prototype.hasOwnProperty.call(option,'const')||option.enum?.length===1))return schema.oneOf.map(option=>{
    const value=Object.prototype.hasOwnProperty.call(option,'const')?option.const:option.enum![0];
    return {value,label:option.title?.trim()||label(value)};
  });
  return null;
}
function ComplexField(props:FieldProps){
  const {value,onChange,id,path,label,enabled,rootSchema,depth=0}=props;const schema=resolveField(props.schema,rootSchema);const nullable=nullableBranch(schema);
  if(nullable)return <div><label className="checkbox-row"><input type="checkbox" checked={value!==undefined&&value!==null} disabled={!enabled} onChange={event=>onChange(event.target.checked?defaultField(resolveField(nullable,rootSchema)):null)}/>填写{label}</label>{value!==undefined&&value!==null?<ComplexField {...props} schema={nullable} depth={depth+1}/>:<p className="subtle">{label}未填写；勾选后可编辑，取消勾选提交空值（null）。</p>}</div>;
  if(schema.format==='platform-file')return <FileField {...props} schema={schema}/>;
  const item=schema.items&&!Array.isArray(schema.items)&&typeof schema.items==='object'?resolveField(schema.items,rootSchema):null;
  if(schema.type==='array'&&item?.format==='platform-file')return <FileField {...props} schema={schema} multiple/>;
  const choices=choicesFor(schema);
  if(choices)return <label className="field" htmlFor={id}>{label}{props.required?' *':''}<select id={id} value={value===undefined?'':JSON.stringify(value)} disabled={!enabled} onChange={event=>onChange(event.target.value?JSON.parse(event.target.value):undefined)}><option value="">请选择</option>{choices.map((option,index)=><option key={index} value={JSON.stringify(option.value)}>{option.label}</option>)}</select>{schema.description&&<small>{schema.description}</small>}</label>;
  if(depth>6||schema.anyOf||schema.oneOf||schema.allOf||Array.isArray(schema.type)||schema.type==='null'||schema.type==='array'||(!schema.type&&!schema.enum)||schema.type==='object'&&(!Object.keys(schema.properties||{}).length||schema.patternProperties))return <JsonValueField {...props} schema={schema}/>;
  if(schema.type==='object'){
    const object=value&&typeof value==='object'&&!Array.isArray(value)?value as Data:{};
    return <fieldset disabled={!enabled} style={{border:'1px solid #e4ebf1',borderRadius:8,padding:16,margin:'0 0 18px'}}><legend>{label}</legend>{Object.entries(schema.properties||{}).map(([key,child])=><ComplexField key={key} schema={child as JsonSchema} rootSchema={rootSchema} value={object[key]} onChange={next=>onChange({...object,[key]:next})} id={id+'-'+key} path={path+'/'+key} label={(child as JsonSchema).title||key} required={schema.required?.includes(key)} enabled={enabled} depth={depth+1}/>)}{schema.additionalProperties!==false&&<details className="advanced"><summary>编辑完整 JSON（含其他字段）</summary><JsonValueField {...props} path={path+'/whole'} id={id+'-whole'} label={label} schema={schema}/></details>}</fieldset>;
  }
  if(schema.type==='boolean')return <label className="checkbox-row"><input id={id} type="checkbox" checked={value===true} disabled={!enabled} onChange={event=>onChange(event.target.checked)}/>{label}</label>;
  const number=schema.type==='number'||schema.type==='integer';const multiline=schema.format==='textarea'||(schema.maxLength||0)>280;
  return <div className="field schema-field"><label htmlFor={id}>{label}{props.required?' *':''}</label>{multiline?<textarea id={id} rows={5} value={value===undefined?'':String(value)} disabled={!enabled} maxLength={schema.maxLength} onChange={event=>onChange(event.target.value)}/>:<input id={id} type={number?'number':schema.format==='password'?'password':'text'} step={schema.type==='integer'?1:'any'} min={schema.minimum} max={schema.maximum} maxLength={schema.maxLength} value={value===undefined?'':String(value)} disabled={!enabled} onChange={event=>onChange(event.target.value===''?undefined:number?Number(event.target.value):event.target.value)}/>} {schema.description&&<small>{schema.description}</small>}</div>;
}
const ComplexControl=withJsonFormsControlProps(({id,data,handleChange,path,label,required,errors,enabled,visible,schema,rootSchema}:ControlProps)=>!visible?null:<><ComplexField schema={schema} rootSchema={rootSchema} value={data} onChange={next=>handleChange(path,next)} id={id||'complex-'+path} path={path} label={label} required={required} enabled={enabled}/>{errors&&<span className="field-error">{errors}</span>}</>);
const renderers=[...vanillaRenderers,{tester:rankWith(3,and(isStringControl,not(isEnumControl))),renderer:TextControl},{tester:rankWith(5,formatIs('model-profile')),renderer:ModelProfileControl},{tester:rankWith(8,schemaMatches(schema=>schema.format==='platform-file'||schema.type==='object'||schema.type==='array'||Array.isArray(schema.type)||Boolean(schema.enum||schema.anyOf||schema.oneOf||schema.allOf)||!schema.type)),renderer:ComplexControl}];
const ajv=schemaValidator;
function initialValue(schema:JsonSchema,examples=false):Data{const result:Data={};for(const [key,prop]of Object.entries(schema.properties||{})){if(typeof prop!=='object')continue;const s=prop as JsonSchema & {examples?:unknown[]};if(examples&&s.examples?.length)result[key]=s.examples[0];else if(s.default!==undefined)result[key]=s.default;}return result;}
function previewInput(value:unknown):unknown{if(Array.isArray(value))return value.map(previewInput);if(value&&typeof value==='object'){const obj=value as Data;if('content_base64'in obj)return {filename:obj.filename,media_type:obj.media_type,content:'文件已选择；提交时上传正文'};return Object.fromEntries(Object.entries(obj).map(([k,v])=>[k,previewInput(v)]));}return value;}

export default function CapabilityForm({capability,initialInput,conversationId,onResult,onInputChange,compact=false}:{capability:Capability;initialInput?:Data;conversationId?:string;onResult:(value:Result)=>void;onInputChange?:(value:Data)=>void;compact?:boolean}){
  // Directory polling returns fresh objects for the same schema. Preserve its
  // identity so a refresh cannot reinitialize a field during an async file read.
  const schemaKey=JSON.stringify(capability.input_schema);
  const inputSchema=useMemo(()=>capability.input_schema,[schemaKey]);
  const [data,setData]=useState<Data>(()=>({...initialValue(capability.input_schema),...initialInput}));const [invalid,setInvalid]=useState(false);const [showErrors,setShowErrors]=useState(false);const [confirm,setConfirm]=useState(false);const [busy,setBusy]=useState(false);const [error,setError]=useState<unknown>();
  const live=useRef({data,invalid:false});
  const fieldIssues=useRef(new Map<string,string>());const [fieldIssueCount,setFieldIssueCount]=useState(0);const [formRevision,setFormRevision]=useState(0);
  const reportField=useCallback((path:string,message:string)=>{if(message)fieldIssues.current.set(path,message);else fieldIssues.current.delete(path);setFieldIssueCount(fieldIssues.current.size);},[]);
  const middleware=useCallback<Middleware>((state,action,reducer)=>{
    // The mounted reducer owns edits. Prop updates may carry a debounced older
    // snapshot; keep current input while still applying schema/validation changes.
    // Explicit reset remounts JsonForms using formRevision and its new initial data.
    const next=reducer(state,action.type===UPDATE_CORE?{...action,data:state.data}:action);
    live.current={data:next.data||{},invalid:Boolean(next.errors?.length)};return next;
  },[]);
  const available=capabilityAvailable(capability);
  const attempt=useRef<{scope:string;key:string}|null>(null);
  const uischema=useMemo(()=>({type:'VerticalLayout',elements:Object.entries(inputSchema.properties||{}).map(([key,raw])=>{const schema=raw as JsonSchema;return {type:'Control',scope:'#/properties/'+key,options:{multi:schema.format==='textarea'||(schema.maxLength||0)>280}};})}) as UISchemaElement,[inputSchema]);
  const hasExamples=Object.values(capability.input_schema.properties||{}).some(p=>Array.isArray((p as {examples?:unknown[]}).examples));
  async function invoke(){if(!available||live.current.invalid||fieldIssues.current.size)return;const payload=live.current.data;setBusy(true);setError(null);try{const scope=JSON.stringify([capability.id,conversationId||null,payload]);if(attempt.current?.scope!==scope)attempt.current={scope,key:requestId()};const result=await post<Result>('/invoke',{capability_id:capability.id,input:payload,idempotency_key:attempt.current.key,origin:conversationId?'conversation':'page',...(conversationId?{conversation_id:conversationId}:{})});attempt.current=null;setConfirm(false);onResult(result);}catch(e){setError(e);}finally{setBusy(false);}}
  function submit(){setShowErrors(true);setData(live.current.data);setInvalid(live.current.invalid);if(live.current.invalid||fieldIssues.current.size||!available)return;if(capability.execution.confirmation)setConfirm(true);else void invoke();}
  function reset(examples=false){const next=initialValue(capability.input_schema,examples);fieldIssues.current.clear();setFieldIssueCount(0);setFormRevision(value=>value+1);setData(next);live.current={data:next,invalid:!ajv.validate(capability.input_schema,next)};setShowErrors(false);setError(null);setConfirm(false);}
  return <div className={'capability-form '+(compact?'compact':'')}>
    <div className="capability-heading"><span className={'mode-icon '+(capability.execution.mode==='background'?'ai':'')} >{capability.execution.mode==='background'?<Sparkles size={20}/>:<FileCheck2 size={20}/>}</span><div><h2>{capability.title}</h2><p>{capability.description}</p></div></div>
    <div className="capability-meta"><span className="tag">{capability.execution.mode==='background'?'后台任务':'即时执行'}</span><span className="tag">v{capability.version}</span><span className={'tag '+(capability.service_id?'violet':'')}>{capability.service_id?'远程服务':'本机能力'}</span>{capability.availability&&<AvailabilityBadge state={capability.availability.state}/>}<code>{capability.id}</code></div>{!available&&<div className="notice warning">{capability.availability?.message||'所属服务暂不可用，恢复后可继续执行。'} 已有记录仍可查看，当前填写内容会保留。</div>}
    <form onSubmit={e=>{e.preventDefault();submit();}}>
      <FieldValidity.Provider value={reportField}><JsonForms key={formRevision} middleware={middleware} schema={inputSchema} uischema={uischema} data={data} renderers={renderers} cells={vanillaCells} ajv={ajv} readonly={busy||confirm} validationMode={showErrors?'ValidateAndShow':'ValidateAndHide'} config={{showUnfocusedDescription:true,hideRequiredAsterisk:false}} onChange={({data:next,errors})=>{
        // JSON Forms debounces this notification. An earlier notification must
        // never replace the synchronous reducer snapshot with stale input.
        if(next!==live.current.data)return;
        setData(next||{});setInvalid(Boolean(errors?.length));onInputChange?.(next||{});
      }}/></FieldValidity.Provider>
      <ErrorNotice error={error}/>
      {showErrors&&invalid&&<p className="field-error">请先补全表单中的必填项，并检查字段格式。</p>}
      {fieldIssueCount>0&&<p className="field-error">请先修正 JSON 或文件问题，并等待文件读取完成。</p>}
      {confirm?<div className="confirm-panel"><div className="confirm-title"><Check size={18}/><strong>核对本次操作</strong></div><p>将执行“{capability.title}”，输入内容如下。</p><PrettyData data={previewInput(data)}/><div className="form-actions"><button type="button" className="button primary" disabled={busy||!available||fieldIssueCount>0} onClick={()=>void invoke()}>{busy?<Loading label="正在执行"/>:<><Check size={16}/>确认执行</>}</button><button type="button" className="button secondary" disabled={busy} onClick={()=>setConfirm(false)}>继续编辑</button></div></div>:<div className="form-actions"><button className="button primary" disabled={busy||!available||fieldIssueCount>0}>{busy?<Loading label="正在提交…"/>:<><Play size={15}/>{capability.execution.confirmation?'预览操作':'运行能力'}<ArrowRight size={15}/></>}</button>{hasExamples&&<button type="button" className="button secondary" disabled={busy} onClick={()=>reset(true)}>填入示例</button>}<button type="button" className="icon-button" title="重置表单" aria-label="重置表单" disabled={busy} onClick={()=>reset()}><RotateCcw size={16}/></button></div>}
    </form>
  </div>;
}
