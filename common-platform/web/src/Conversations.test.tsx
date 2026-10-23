// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import Conversations from './Conversations';
import type { UIContext } from './ResultView';
import type { Capability, Conversation, Data, Registry } from './types';

const transport=vi.hoisted(()=>({api:vi.fn(),post:vi.fn()}));
vi.mock('./api',async importOriginal=>({...await importOriginal<typeof import('./api')>(),api:transport.api,post:transport.post}));

type Draft={text:string;model_profile_id:string;active_capability_id:string|null;form_inputs:Record<string,Data>};
type State={revision:number;current_conversation_id:string|null;drafts:Record<string,Draft>};
const timestamp='2026-01-01T10:00:00Z';
const capability:Capability={id:'example.echo',version:'1.0.0',module_id:'example',title:'合成文本办理',description:'界面验收专用合成输入',kind:'command',roles:['member'],execution:{mode:'background',confirmation:false},presentation:{page:{renderer:'text-result'},conversation:{renderer:'text-result'}},input_schema:{type:'object',properties:{text:{type:'string',title:'正文',format:'textarea'},attachment:{type:'object',title:'合成附件',format:'platform-file',properties:{filename:{type:'string'},media_type:{type:'string'},content_base64:{type:'string'}}}},required:['text'],additionalProperties:false},output_schema:{type:'object'}};
const registry:Registry={capabilities:[capability],renderers:[],modules:[{id:'example',name:'合成模块',version:'1.0.0',description:'验收',capabilities:[capability],surfaces:{page_tools:[{id:'echo',title:'文本表单',description:'',kind:'form',capability_ids:['example.echo']}],conversation:{capability_ids:['example.echo'],starter_prompts:[]}}}]};
let conversations:Record<string,Conversation>;
let state:State;
let context:UIContext;
let patches:Data[];
const copy=<T,>(value:T):T=>JSON.parse(JSON.stringify(value));

beforeEach(()=>{
  vi.clearAllMocks();
  Element.prototype.scrollIntoView=vi.fn();
  conversations={older:{id:'older',title:'较早会话',messages:[],created_at:timestamp},newer:{id:'newer',title:'最新创建会话',messages:[],created_at:timestamp}};
  state={revision:1,current_conversation_id:'older',drafts:{older:{text:'服务端保存的待发消息',model_profile_id:'offline',active_capability_id:'example.echo',form_inputs:{'example.echo':{text:'服务端保存的表单内容'}}}}};
  patches=[];
  context={user:{id:'alice',username:'alice',name:'合成用户',role:'member'},modules:registry.modules,capabilities:registry.capabilities,onChanged:vi.fn()};
  transport.api.mockImplementation(async(path:string,options?:RequestInit)=>{
    if(path==='/conversations')return {items:[copy(conversations.newer),...Object.values(conversations).filter(c=>c.id!=='newer').map(copy)]};
    if(path==='/conversations/state'&&options?.method==='PATCH'){
      const payload=JSON.parse(String(options.body));patches.push(payload);
      if(payload.expected_revision!==state.revision)throw Error('unexpected test revision');
      state={...state,revision:state.revision+1,...('current_conversation_id'in payload?{current_conversation_id:payload.current_conversation_id}:{}),drafts:{...state.drafts,...payload.drafts}};
      return copy(state);
    }
    if(path==='/conversations/state')return copy(state);
    const id=path.split('/')[2];
    if(path.startsWith('/conversations/')&&conversations[id])return copy(conversations[id]);
    throw Error('Unexpected API path: '+path);
  });
  transport.post.mockImplementation(async(path:string,payload:Data)=>{
    if(path==='/conversations/older/fork'){
      const messages=conversations.older.messages;
      const end=payload.message_id?messages.findIndex(m=>m.id===payload.message_id)+1:messages.length;
      const child={id:'child',title:'较早会话 · 分支',parent_id:'older',fork_message_id:payload.message_id||messages.at(-1)?.id,messages:copy(messages.slice(0,end)),created_at:timestamp};
      conversations.child=child;return copy(child);
    }
    throw Error('Unexpected POST path: '+path);
  });
});
afterEach(()=>{cleanup();});
const mount=()=>render(<Conversations registry={registry} context={context} profiles={[]}/>);

describe('persistent conversational workspace',()=>{
  it('restores server-selected conversation, editable form and composer after remount',async()=>{
    const first=mount();
    const composer=await screen.findByRole('textbox',{name:'对话消息'});
    await waitFor(()=>expect((composer as HTMLTextAreaElement).value).toBe('服务端保存的待发消息'));
    const field=await screen.findByLabelText(/正文/);
    expect((field as HTMLTextAreaElement).value).toBe('服务端保存的表单内容');
    expect((screen.getByLabelText('本次对话模型') as HTMLSelectElement).value).toBe('offline');
    fireEvent.change(composer,{target:{value:'退出前新输入的草稿'}});
    fireEvent.change(field,{target:{value:'退出前新输入的表单'}});
    await waitFor(()=>expect(state.drafts.older.form_inputs['example.echo'].text).toBe('退出前新输入的表单'),{timeout:2500});
    expect(state.current_conversation_id).toBe('older');
    first.unmount();mount();
    await waitFor(()=>expect((screen.getByRole('textbox',{name:'对话消息'}) as HTMLTextAreaElement).value).toBe('退出前新输入的草稿'));
    expect((await screen.findByLabelText(/正文/) as HTMLTextAreaElement).value).toBe('退出前新输入的表单');
    expect(transport.post).not.toHaveBeenCalled();
  });

  it('keeps ordinary fields but excludes selected file bytes from all durable drafts',async()=>{
    const first=mount();
    const input=await screen.findByLabelText('合成附件');
    const file=new File(['synthetic file'],'sample.txt',{type:'text/plain'});
    Object.defineProperty(file,'arrayBuffer',{value:async()=>new TextEncoder().encode('synthetic file').buffer});
    fireEvent.change(input,{target:{files:[file]}});
    await screen.findByText('已选择：sample.txt');
    fireEvent.change(screen.getByRole('textbox',{name:'对话消息'}),{target:{value:'附件之外的普通草稿'}});
    await waitFor(()=>expect(state.drafts.older.text).toBe('附件之外的普通草稿'),{timeout:2500});
    expect(state.drafts.older.form_inputs['example.echo'].text).toBe('服务端保存的表单内容');
    expect(JSON.stringify(patches)).not.toContain('content_base64');
    expect(JSON.stringify(patches)).not.toContain('sample.txt');
    first.unmount();mount();
    await screen.findByLabelText('合成附件');
    expect(screen.queryByText('已选择：sample.txt')).toBeNull();
    expect(screen.getByText(/消息内表单刷新后恢复原始参数，文件需重新选择/)).toBeTruthy();
  });

  it.each(['immediate','after draft save'])('retains a next-action file through registry refresh and draft persistence (%s read), but not reload',async timing=>{
    const uploadCapability:Capability={...capability,input_schema:{...capability.input_schema,required:['text','attachment']}};
    const uploadRegistry:Registry={...registry,capabilities:[uploadCapability]};
    state.drafts.older={text:'',model_profile_id:'offline',active_capability_id:null,form_inputs:{}};
    conversations.older.messages=[{id:'detail-message',role:'assistant',content:'合成批次已就绪',created_at:timestamp,views:[{type:'task',data:{run_id:'detail-run'}}]}];
    const baseApi=transport.api.getMockImplementation()!;
    transport.api.mockImplementation(async(path:string,options?:RequestInit)=>path==='/runs/detail-run'?{id:'detail-run',capability_id:'example.echo',status:'succeeded',input:{},attempt:1,progress:100,created_at:timestamp,updated_at:timestamp,result:{id:'detail-result',capability_id:'example.echo',invocation_status:'completed',summary:'详情已加载',views:[{type:'workspace',data:{blocks:[{type:'actions',items:[{label:'上传下一步附件',capability_id:'example.echo',input:{text:'合成批次引用'}}]}]}}]}}:baseApi(path,options));
    transport.post.mockResolvedValue({id:'upload-accepted',capability_id:'example.echo',invocation_status:'accepted',run_id:'upload-run',summary:'合成上传已排队',views:[]});
    const first=render(<Conversations registry={uploadRegistry} context={context} profiles={[]}/>);
    fireEvent.click(await screen.findByRole('button',{name:'上传下一步附件'}));
    await screen.findByLabelText('合成附件');
    let resolve!:(value:ArrayBuffer)=>void;
    const pending=new Promise<ArrayBuffer>(done=>{resolve=done;});
    const file=new File(['synthetic png'],'sample.png',{type:'image/png'});
    Object.defineProperty(file,'arrayBuffer',{value:()=>pending});
    fireEvent.change(screen.getByLabelText(/正文/),{target:{value:'立即填写的合成标识'}});
    fireEvent.change(screen.getByLabelText('合成附件'),{target:{files:[file]}});
    await screen.findByText('正在读取文件…');
    if(timing==='after draft save'){
      fireEvent.change(screen.getByRole('textbox',{name:'对话消息'}),{target:{value:'读取文件期间的普通消息草稿'}});
      await waitFor(()=>expect(state.drafts.older.text).toBe('读取文件期间的普通消息草稿'),{timeout:2500});
    }
    first.rerender(<Conversations registry={copy(uploadRegistry)} context={{...context}} profiles={[]}/>);
    await act(async()=>{resolve(new TextEncoder().encode('synthetic png').buffer);await pending;});
    await screen.findByText('已选择：sample.png');
    fireEvent.change(screen.getByRole('textbox',{name:'对话消息'}),{target:{value:'合成上传草稿'}});
    await waitFor(()=>expect(state.drafts.older.text).toBe('合成上传草稿'),{timeout:2500});
    expect(JSON.stringify(patches)).not.toContain('content_base64');
    expect(JSON.stringify(patches)).not.toContain('sample.png');
    expect(state.drafts.older.form_inputs).toEqual({});
    first.rerender(<Conversations registry={copy(uploadRegistry)} context={{...context}} profiles={[]}/>);
    expect(screen.getByText('已选择：sample.png')).toBeTruthy();
    fireEvent.click(screen.getByRole('button',{name:'运行能力'}));
    await waitFor(()=>expect(transport.post).toHaveBeenCalledOnce());
    expect(transport.post).toHaveBeenCalledWith('/invoke',expect.objectContaining({conversation_id:'older',origin:'conversation',input:{text:'立即填写的合成标识',attachment:{filename:'sample.png',media_type:'image/png',content_base64:btoa('synthetic png')}}}));
    first.unmount();render(<Conversations registry={uploadRegistry} context={context} profiles={[]}/>);
    fireEvent.click(await screen.findByRole('button',{name:'上传下一步附件'}));
    await screen.findByLabelText('合成附件');
    expect(screen.queryByText('已选择：sample.png')).toBeNull();
    expect(JSON.stringify(state.drafts)).not.toContain('content_base64');
  });

  it('isolates same-capability message and action prefills across editing, refresh and fork',async()=>{
    state.drafts.older={text:'',model_profile_id:'offline',active_capability_id:null,form_inputs:{'example.echo':{text:'旧版来源不明的草稿'}}};
    conversations.older.messages=[
      {id:'lane-one',role:'assistant',content:'第一条合成流程',created_at:timestamp,views:[{type:'form',data:{capability_id:'example.echo',input:{text:'甲流程对象-001'}}}]},
      {id:'lane-two',role:'assistant',content:'第二条合成流程',created_at:timestamp,views:[{type:'form',data:{capability_id:'example.echo',input:{text:'乙流程对象-002'}}}]},
      {id:'next-action',role:'assistant',content:'另一对象的下一步',created_at:timestamp,views:[{type:'workspace',data:{blocks:[{type:'actions',items:[{label:'独立预填操作',capability_id:'example.echo',input:{text:'丙流程对象-003'}}]}]}}]},
    ];
    const values=()=>screen.getAllByLabelText(/正文/).map(field=>(field as HTMLTextAreaElement).value);
    const first=mount();
    await screen.findByText('第二条合成流程');
    expect(values()).toEqual(['甲流程对象-001','乙流程对象-002']);
    fireEvent.change(screen.getAllByLabelText(/正文/)[0],{target:{value:'仅编辑甲的临时输入'}});
    fireEvent.click(screen.getByRole('button',{name:'独立预填操作'}));
    expect(values()).toEqual(['仅编辑甲的临时输入','乙流程对象-002','丙流程对象-003']);
    fireEvent.change(screen.getAllByLabelText(/正文/)[2],{target:{value:'仅编辑丙的临时输入'}});
    fireEvent.change(screen.getByRole('textbox',{name:'对话消息'}),{target:{value:'只持久化普通消息'}});
    await waitFor(()=>expect(state.drafts.older.text).toBe('只持久化普通消息'),{timeout:2500});
    expect(state.drafts.older.form_inputs).toEqual({'example.echo':{text:'旧版来源不明的草稿'}});
    expect(JSON.stringify(patches)).not.toContain('仅编辑');
    first.unmount();mount();
    await screen.findByText('第二条合成流程');
    expect(values()).toEqual(['甲流程对象-001','乙流程对象-002']);
    fireEvent.click(screen.getByRole('button',{name:'独立预填操作'}));
    expect(values()).toEqual(['甲流程对象-001','乙流程对象-002','丙流程对象-003']);
    fireEvent.click(screen.getByRole('button',{name:'从当前会话创建分支'}));
    await waitFor(()=>expect(state.current_conversation_id).toBe('child'));
    expect(values().slice(0,2)).toEqual(['甲流程对象-001','乙流程对象-002']);
    expect(transport.post.mock.calls.every(([path])=>path!=='/invoke')).toBe(true);
  });

  it('branches from the selected message and opens the parent without replaying an action',async()=>{
    state.drafts.older.active_capability_id=null;
    conversations.older.messages=[{id:'first',role:'assistant',content:'起点结果',created_at:timestamp,views:[]},{id:'later',role:'assistant',content:'之后的讨论',created_at:timestamp,views:[]}];
    mount();await screen.findByText('起点结果');
    fireEvent.click(screen.getAllByRole('button',{name:'从此消息开启分支'})[0]);
    await waitFor(()=>expect(state.current_conversation_id).toBe('child'));
    expect(transport.post).toHaveBeenCalledWith('/conversations/older/fork',{message_id:'first'});
    expect(screen.queryByText('之后的讨论')).toBeNull();
    const parent=await screen.findByRole('button',{name:/返回父会话/});
    fireEvent.click(parent);
    await screen.findByText('之后的讨论');
    await waitFor(()=>expect(state.current_conversation_id).toBe('older'));
    expect(transport.post.mock.calls.every(([path])=>path!=='/invoke')).toBe(true);
  });

  it('requires an action click and form submit, then refreshes a completed background result',async()=>{
    state.drafts.older={text:'',model_profile_id:'offline',active_capability_id:null,form_inputs:{}};
    conversations.older.messages=[{id:'action-message',role:'assistant',content:'可以继续办理',created_at:timestamp,views:[{type:'workspace',data:{blocks:[{type:'actions',items:[{label:'办理下一步',capability_id:'example.echo',input:{text:'上一步预填的合成内容'}}]}]}}]}];
    const baseApi=transport.api.getMockImplementation()!;let polls=0;
    transport.api.mockImplementation(async(path:string,options?:RequestInit)=>{
      if(path==='/runs/test-run'){
        polls++;const done=polls>1;
        return {id:'test-run',capability_id:'example.echo',status:done?'succeeded':'queued',input:{},attempt:1,progress:done?100:0,created_at:timestamp,updated_at:timestamp,...(done?{result:{id:'completed',capability_id:'example.echo',invocation_status:'completed',summary:'后台已完成',views:[{type:'text-result',data:{text:'后台真实轮询得到的合成结果'}}]}}:{})};
      }
      return baseApi(path,options);
    });
    transport.post.mockImplementation(async(path:string,payload:Data)=>{
      if(path!=='/invoke')throw Error('Unexpected POST '+path);
      expect(payload.conversation_id).toBe('older');expect(payload.origin).toBe('conversation');expect(payload.input).toEqual({text:'上一步预填的合成内容'});
      conversations.older.messages.push({id:'task-message',role:'assistant',content:'任务已排队',created_at:timestamp,views:[{type:'task',data:{run_id:'test-run'}}]});
      return {id:'accepted',capability_id:'example.echo',invocation_status:'accepted',run_id:'test-run',run_status:'queued',summary:'任务已排队',views:[]};
    });
    mount();fireEvent.click(await screen.findByRole('button',{name:'办理下一步'}));
    expect((await screen.findByLabelText(/正文/) as HTMLTextAreaElement).value).toBe('上一步预填的合成内容');
    expect(transport.post).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button',{name:'运行能力'}));
    await screen.findByText('后台真实轮询得到的合成结果',{}, {timeout:4000});
    expect(transport.post).toHaveBeenCalledTimes(1);
    expect(vi.mocked(context.onChanged).mock.calls.length).toBeGreaterThanOrEqual(2);
    expect(transport.api.mock.calls.filter(([path])=>path==='/conversations/older').length).toBeGreaterThanOrEqual(3);
  });
});
