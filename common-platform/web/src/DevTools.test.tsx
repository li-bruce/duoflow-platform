// @vitest-environment jsdom
import '@testing-library/jest-dom/vitest';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import DevTools, { StateMachineGraph } from './DevTools';
import { api, ApiError } from './api';
import type { User } from './types';
import type { WorkflowStateMachine } from './workflows/sdk';

vi.mock('./api',async original=>({...await original<typeof import('./api')>(),api:vi.fn()}));
vi.mock('./workflows/FlowHost',()=>({default:({instanceId,surface,onChanged}:{instanceId:string;surface:string;onChanged:()=>void})=><div data-testid="real-flow-host">真实宿主 {instanceId} / {surface}<button onClick={onChanged}>模拟正式宿主完成动作</button></div>}));
const admin:User={id:'debug-admin',username:'debug-admin',name:'开发者',role:'member',roles:['system_admin'],authorization_revision:1};
const machine:WorkflowStateMachine={id:'neutral.record',version:'1.0.0',initial:'draft',states:[{id:'draft',title:'草稿'},{id:'submitted',title:'已提交'},{id:'done',title:'已完成',terminal:true}],transitions:[{id:'submit',from:'draft',to:'submitted',step_id:'edit',action_id:'send',title:'提交',guard_description:'内容完整且本人有提交权'},{id:'finish',from:'submitted',to:'done',step_id:'review',action_id:'accept',title:'确认完成'}]};
const definition={id:'neutral.flow',version:'1.0.0',title:'中立记录流程',description:'同一记录在两端办理',initial_step:'pick',steps:[{id:'pick',title:'选记录',component:'RecordPicker',actions:[]},{id:'edit',title:'编辑记录',component:'RecordEditor',actions:[{id:'send',title:'提交记录',capability_id:'neutral.send'}]}],state_machine:machine};
const registry={generated_at:'2026-10-15T03:00:00Z',modules:[{id:'neutral',name:'中立模块',version:'1.0.0',source:'remote',enabled:true}],summary:{capabilities:1,workflows:1,interactions:1,state_machines:1},capabilities:[{id:'neutral.send',version:'1.0.0',module_id:'neutral',title:'发送记录能力',description:'正式提交记录',kind:'command',execution:{mode:'sync'},presentation:{},input_schema:{type:'object'},output_schema:{type:'object'},available:true,availability:{state:'healthy'},bindings:{workflows:['neutral.flow'],interactions:['neutral.widget']},diagnostics:[]}],workflows:[{id:'neutral.flow',version:'1.0.0',title:'中立记录流程',pack:'not-built-neutral-pack',module_ids:['neutral'],definition,registration:{declaration_loaded:true,pack_installed:true,source_present:true,backend_available:true,state_machine_declared:true},diagnostics:[]}],interactions:[{id:'neutral.widget',version:'1.0.0',title:'中立编辑组件',pack:'not-built-neutral-pack',module_ids:['neutral'],definition:{id:'neutral.widget',actions:[{id:'send',title:'组件提交',capability_id:'neutral.send'}]},registration:{declaration_loaded:true,pack_installed:true,source_present:true,backend_available:true},diagnostics:[{severity:'warning',code:'example_warning',message:'合成断链提醒'}]}]};
const instances=[{id:'instance-a',flow_id:'neutral.flow',title:'本人记录甲',updated_at:'2026-10-15T03:00:00Z'},{id:'instance-b',flow_id:'neutral.flow',title:'本人记录乙',updated_at:'2026-10-15T03:00:00Z'}];
const snapshot=(id='instance-a',state='draft',cursor='edit')=>({instance_id:id,flow_id:'neutral.flow',flow_version:'1.0.0',title:id,observed_at:'2026-10-15T03:00:00Z',cursor,revision:4,object_ref:{object_type:'neutral.record',object_id:id+'-record'},business:{state,revision:2,allowed_actions:state==='draft'?['send']:[],known_state:true},execution:{status:'idle',run_id:null,error_code:null},state_machine:machine,history:[{id:'observation-'+id,observed_at:'2026-10-15T03:00:00Z',cursor,revision:4,business_state:state,business_revision:2,allowed_actions:[],execution_status:'idle',error_code:null}],diagnostics:[],history_note:'宿主观察历史，不等于完整业务事件。'});
function deferred<T>(){let resolve!:(value:T)=>void;let reject!:(failure:unknown)=>void;const promise=new Promise<T>((a,b)=>{resolve=a;reject=b;});return {promise,resolve,reject};}
let ticks:(()=>void)[]=[];
beforeEach(()=>{
  vi.clearAllMocks();vi.mocked(api).mockReset();ticks=[];
  const actualInterval=window.setInterval.bind(window);
  vi.spyOn(window,'setInterval').mockImplementation(((handler:()=>void,timeout:number,...args:unknown[])=>{if(timeout===2000){ticks.push(handler);return 9000+ticks.length;}return actualInterval(handler,timeout,...args);}) as typeof window.setInterval);
  vi.spyOn(window,'clearInterval').mockImplementation(()=>{});
  Element.prototype.scrollIntoView=vi.fn();
  vi.mocked(api).mockImplementation(async path=>{
    if(path==='/devtools/catalog')return structuredClone(registry);
    if(path==='/devtools/instances')return {items:instances};
    if(path.startsWith('/devtools/instances/'))return snapshot(path.split('/').at(-1));
    throw new Error('Unexpected request '+path);
  });
});
afterEach(()=>{cleanup();vi.restoreAllMocks();});
async function open(){const view=render(<DevTools user={admin}/>);await screen.findByRole('button',{name:/中立记录流程/});return view;}
async function observe(id='instance-a'){fireEvent.change(screen.getByRole('combobox',{name:'观察个人流程'}),{target:{value:id}});await screen.findByText(id+'-record',{exact:false});}
async function tick(){await act(async()=>ticks.at(-1)?.());}

describe('通用模块注册调试',()=>{
  it('提供真实声明绑定、前端构建证据与两类组件，不将源码存在误报为已构建',async()=>{
    await open();expect(screen.getByText('not-built-neutral-pack / RecordEditor')).toBeInTheDocument();expect(screen.getByText('neutral.send')).toBeInTheDocument();
    expect(screen.getByText('当前浏览器构建：未包含该组件包')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('tab',{name:/独立前端组件/}));expect(screen.getByText('合成断链提醒')).toBeInTheDocument();expect(screen.getByText('组件提交 →')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('tab',{name:/后端能力/}));expect(screen.getByText('关联流程：neutral.flow')).toBeInTheDocument();expect(screen.getByText('关联组件：neutral.widget')).toBeInTheDocument();
    fireEvent.change(screen.getByRole('textbox',{name:'搜索声明'}),{target:{value:'不存在'}});expect(screen.getByText('当前筛选没有匹配的声明')).toBeInTheDocument();
  });
  it('非管理员不读取目录，身份撤销后不显示或接受旧请求',async()=>{
    const old=deferred<unknown>();vi.mocked(api).mockReturnValueOnce(old.promise);
    const view=render(<DevTools user={admin}/>);view.rerender(<DevTools user={{...admin,roles:['member'],authorization_revision:2}}/>);
    await act(async()=>old.resolve(registry));expect(screen.getByText('开发调试台由管理员使用')).toBeInTheDocument();expect(screen.queryByText('中立记录流程')).toBeNull();
    expect(vi.mocked(api).mock.calls.length).toBe(2);
  });
  it('校验预览只提交JSON且展示服务端关联错误，不安装也不执行代码',async()=>{
    await open();fireEvent.click(screen.getByRole('button',{name:'载入验证编辑器'}));
    vi.mocked(api).mockResolvedValueOnce({valid:false,kind:'workflow',id:'neutral.flow',replacement:{registered:true,changed:true,current_version:'1.0.0',incoming_version:'1.1.0'},diagnostics:[{severity:'error',code:'missing_capability',message:'动作引用能力不存在'}],preview:null});
    fireEvent.click(screen.getByRole('button',{name:'验证声明（只读）'}));await screen.findByText('声明需要修正');expect(screen.getByText('动作引用能力不存在')).toBeInTheDocument();
    const call=vi.mocked(api).mock.calls.find(([path])=>path==='/devtools/validate')!;expect(call[1]?.method).toBe('POST');expect(JSON.parse(String(call[1]?.body))).toEqual({kind:'workflow',definition});
    expect(vi.mocked(api).mock.calls.every(([path])=>path.startsWith('/devtools/'))).toBe(true);
  });
  it('校验通过显示状态机；修改输入后清除旧结果并丢弃在途旧响应',async()=>{
    await open();fireEvent.change(screen.getByRole('combobox',{name:'验证声明类型'}),{target:{value:'state_machine'}});fireEvent.change(screen.getByRole('textbox',{name:'待验证声明 JSON'}),{target:{value:JSON.stringify(machine)}});
    vi.mocked(api).mockResolvedValueOnce({valid:true,kind:'state_machine',id:machine.id,diagnostics:[],replacement:null,preview:machine});fireEvent.click(screen.getByRole('button',{name:'验证声明（只读）'}));await screen.findByText('声明校验通过');
    const delayed=deferred<unknown>();vi.mocked(api).mockReturnValueOnce(delayed.promise);fireEvent.click(screen.getByRole('button',{name:'验证声明（只读）'}));fireEvent.change(screen.getByRole('textbox',{name:'待验证声明 JSON'}),{target:{value:'{}'}});
    await act(async()=>delayed.resolve({valid:true,kind:'state_machine',id:'old',diagnostics:[],replacement:null,preview:machine}));expect(screen.queryByText('声明校验通过')).toBeNull();
  });
  it('无效JSON不会发送，验证请求失败会显示错误且清空结果',async()=>{
    await open();fireEvent.change(screen.getByRole('textbox',{name:'待验证声明 JSON'}),{target:{value:'[]'}});fireEvent.click(screen.getByRole('button',{name:'验证声明（只读）'}));await screen.findByText('请提供单个 JSON 对象。');expect(vi.mocked(api).mock.calls.some(([path])=>path==='/devtools/validate')).toBe(false);
    fireEvent.change(screen.getByRole('textbox',{name:'待验证声明 JSON'}),{target:{value:'{}'}});vi.mocked(api).mockRejectedValueOnce(new Error('验证服务不可用'));fireEvent.click(screen.getByRole('button',{name:'验证声明（只读）'}));await screen.findByText('验证服务不可用');expect(screen.queryByText('声明校验通过')).toBeNull();
  });
});

describe('状态机不是页面导航',()=>{
  it('无声明时明确缺失，不从步骤生成状态机',()=>{render(<StateMachineGraph machine={null}/>);expect(screen.getByText('尚未声明业务状态机')).toBeInTheDocument();expect(screen.queryByRole('button')).toBeNull();});
  it('高亮当前正式状态、用键盘选择节点并查看真实迁移绑定和条件',()=>{
    render(<StateMachineGraph machine={machine} currentState="draft" allowedActions={['send']}/>);
    const current=screen.getByRole('button',{name:'状态 草稿（draft），当前业务状态'});expect(current).toHaveAttribute('aria-pressed','true');expect(screen.getByText('条件说明：内容完整且本人有提交权')).toBeInTheDocument();expect(screen.getByText('服务允许此动作；仍需在正式组件中执行')).toBeInTheDocument();
    const done=screen.getByRole('button',{name:'状态 已完成（done）'});fireEvent.keyDown(done,{key:'Enter'});expect(done).toHaveAttribute('aria-pressed','true');expect(current).toHaveClass('current');expect(screen.getByText('review / accept')).toBeInTheDocument();
  });
  it('服务状态不在声明内时给出诊断，不能虚构节点',()=>{render(<StateMachineGraph machine={machine} currentState="server-only"/>);expect(screen.getByText(/服务当前返回状态「server-only」/)).toBeInTheDocument();expect(screen.getAllByRole('button')).toHaveLength(3);});
});

describe('个人实例实时观察与请求隔离',()=>{
  it('分别呈现业务state、个人cursor和执行status；轮询更新正式状态',async()=>{
    await open();await observe();const runtime=screen.getByRole('region',{name:'实时流程观察'});
    expect(within(runtime).getByText('正式业务状态 · 模块保存')).toBeInTheDocument();expect(within(runtime).getByText('个人导航 · 主框架保存')).toBeInTheDocument();expect(within(runtime).getByText('本次调用 · 执行器保存')).toBeInTheDocument();expect(within(runtime).getByRole('button',{name:'状态 草稿（draft），当前业务状态'})).toBeInTheDocument();
    vi.mocked(api).mockResolvedValueOnce({...snapshot('instance-a','submitted','pick'),execution:{status:'succeeded',run_id:'run-a',error_code:null}});await tick();expect(within(runtime).getByRole('button',{name:'状态 已提交（submitted），当前业务状态'})).toBeInTheDocument();expect(within(runtime).getByText('succeeded')).toBeInTheDocument();expect(within(runtime).getByText(/轮询间隔内的中间变化可能未被记录/)).toBeInTheDocument();
  });
  it('展开正式宿主并在两种布局中使用同一实例，默认不触发业务操作',async()=>{
    await open();await observe();expect(screen.queryByTestId('real-flow-host')).toBeNull();fireEvent.click(screen.getByRole('button',{name:'展开真实操作组件'}));expect(screen.getByTestId('real-flow-host')).toHaveTextContent('instance-a / page');fireEvent.change(screen.getByRole('combobox',{name:'操作形态'}),{target:{value:'conversation'}});expect(screen.getByTestId('real-flow-host')).toHaveTextContent('instance-a / conversation');
    expect(vi.mocked(api).mock.calls.every(([path])=>path.startsWith('/devtools/'))).toBe(true);
  });
  it('请求未完成时不叠加轮询，暂停会取消在途请求并拒绝其结果',async()=>{
    await open();await observe();const pending=deferred<unknown>();vi.mocked(api).mockReturnValueOnce(pending.promise);await tick();const count=vi.mocked(api).mock.calls.length;await tick();expect(api).toHaveBeenCalledTimes(count);
    const signal=vi.mocked(api).mock.calls.at(-1)![1]?.signal;fireEvent.click(screen.getByRole('button',{name:'暂停观察'}));expect(signal?.aborted).toBe(true);await act(async()=>pending.resolve(snapshot('instance-a','done')));expect(screen.getByRole('region',{name:'实时流程观察'})).not.toHaveTextContent('done / 2');expect(screen.getByText(/观察已暂停/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button',{name:'恢复观察'}));await waitFor(()=>expect(api).toHaveBeenCalledTimes(count+1));
  });
  it('切换实例时清除旧对象，旧响应不能覆盖新实例',async()=>{
    await open();await observe();const pending=deferred<unknown>();vi.mocked(api).mockReturnValueOnce(pending.promise);await tick();const oldSignal=vi.mocked(api).mock.calls.at(-1)![1]?.signal;
    await observe('instance-b');expect(oldSignal?.aborted).toBe(true);await act(async()=>pending.resolve({...snapshot('instance-a'),object_ref:{object_type:'secret',object_id:'stale-private'}}));expect(screen.queryByText(/stale-private/)).toBeNull();expect(screen.getByText(/instance-b-record/)).toBeInTheDocument();
  });
  it('重新授权失败清除对象、历史和展开组件，不能继续显示敏感旧投影',async()=>{
    await open();await observe();fireEvent.click(screen.getByRole('button',{name:'展开真实操作组件'}));vi.mocked(api).mockRejectedValueOnce(new ApiError('forbidden','当前对象授权已失效',403));await tick();expect(screen.getByText('当前对象授权已失效')).toBeInTheDocument();expect(screen.queryByText(/instance-a-record/)).toBeNull();expect(screen.queryByText(/最近观察记录/)).toBeNull();expect(screen.queryByTestId('real-flow-host')).toBeNull();
  });
  it('卸载取消在途观察请求；旧请求完成不启动新轮询',async()=>{
    const view=await open();const pending=deferred<unknown>();vi.mocked(api).mockReturnValueOnce(pending.promise);fireEvent.change(screen.getByRole('combobox',{name:'观察个人流程'}),{target:{value:'instance-a'}});const signal=vi.mocked(api).mock.calls.at(-1)![1]?.signal;view.unmount();expect(signal?.aborted).toBe(true);const count=vi.mocked(api).mock.calls.length;await act(async()=>pending.resolve(snapshot()));expect(api).toHaveBeenCalledTimes(count);expect(window.clearInterval).toHaveBeenCalled();
  });
});
