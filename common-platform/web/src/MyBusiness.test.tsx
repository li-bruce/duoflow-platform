// @vitest-environment jsdom
import {afterEach,beforeEach,describe,expect,it,vi} from 'vitest';
import {act,cleanup,fireEvent,render,screen,waitFor,within} from '@testing-library/react';
import MyBusiness from './MyBusiness';
import {api,ApiError,post} from './api';

vi.mock('./api',async importOriginal=>({...await importOriginal<typeof import('./api')>(),api:vi.fn(),post:vi.fn(),requestId:()=> 'synthetic-open-key'}));
vi.mock('./workflows/FlowHost',()=>({default:({instanceId}:{instanceId:string})=><div data-testid="flow-host">恢复实例 {instanceId}</div>}));

const entries=[
  {id:'example.submit',version:'1',title:'提交工作台',description:'填写并提交材料',available:true,experience:{title:'提交者',module_id:'example.module'}},
  {id:'example.review',version:'1',title:'审核工作台',description:'审核当前项目',available:true,experience:{title:'审核者',module_id:'example.module'}},
];
const personal={id:'instance-original',flow_id:'example.submit',title:'进行中的材料',step_title:'填写材料',object_ref:{object_id:'object-existing'},forked_from:null,updated_at:'2026-09-01T00:00:00Z'};
const work=(id:string,title:string)=>({title,project_id:'P',state:'submitted',revision:2,flow_id:'example.submit',object_ref:{object_id:id},actions:[{id:'review',title:'审核'}]});
const drafts={'another-conversation':{text:'另一会话未发出的文字',model_profile_id:'',active_capability_id:null,form_inputs:{}}};

beforeEach(()=>{
  vi.clearAllMocks();location.hash='business';
  vi.mocked(api).mockImplementation(async (path,options)=>{
    if(path==='/business/catalog')return {items:entries};
    if(path==='/business/instances')return {items:[personal]};
    if(path.startsWith('/business/objects?'))return {items:[],next_cursor:null};
    if(path==='/conversations/state')return options?.method==='PATCH'?{revision:5}:{revision:4,drafts};
    throw new Error('Unexpected request '+path);
  });
  vi.mocked(post).mockResolvedValue({instance_id:'instance-original',conversation_id:'conversation-existing'});
});
afterEach(()=>{cleanup();location.hash='';});
function deferred<T>(){let resolve!:(value:T)=>void;let reject!:(reason:unknown)=>void;const promise=new Promise<T>((a,b)=>{resolve=a;reject=b;});return {promise,resolve,reject};}
async function open(){render(<MyBusiness/>);await screen.findByText('进行中的材料');}
function source(index=0){fireEvent.click(screen.getAllByRole('button',{name:'查看可办理业务'})[index]);}

describe('我的业务统一续办',()=>{
  it('恢复页面时传递原实例编号并交给共享FlowHost',async()=>{
    await open();fireEvent.click(screen.getByRole('button',{name:'页面继续'}));
    await waitFor(()=>expect(post).toHaveBeenCalledWith('/business/open',{instance_id:'instance-original',surface:'page',idempotency_key:undefined}));
    expect((await screen.findByTestId('flow-host')).textContent).toContain('instance-original');
    expect(location.hash).toBe('#business?instance=instance-original');
  });

  it('对话续办保留其他会话草稿并在持久化当前会话后进入对话',async()=>{
    await open();fireEvent.click(screen.getByRole('button',{name:'对话继续'}));
    await waitFor(()=>expect(api).toHaveBeenCalledWith('/conversations/state',expect.objectContaining({method:'PATCH'})));
    const write=vi.mocked(api).mock.calls.find(([path,options])=>path==='/conversations/state'&&options?.method==='PATCH');
    expect(JSON.parse(String(write?.[1]?.body))).toEqual({expected_revision:4,current_conversation_id:'conversation-existing',drafts});
    await waitFor(()=>expect(location.hash).toBe('#chat'));
    expect(post).toHaveBeenCalledWith('/business/open',{instance_id:'instance-original',surface:'conversation',idempotency_key:undefined});
  });

  it('对话状态并发冲突时留在业务中心并展示错误',async()=>{
    const original=vi.mocked(api).getMockImplementation()!;
    vi.mocked(api).mockImplementation(async(path,options)=>{
      if(path==='/conversations/state'&&options?.method==='PATCH')throw new ApiError('revision_conflict','另一窗口已切换会话',409);
      return original(path,options);
    });
    await open();fireEvent.click(screen.getByRole('button',{name:'对话继续'}));
    await screen.findByText('另一窗口已切换会话');expect(location.hash).toBe('#business');
  });

  it('模块目录失败时保留个人续办入口，并且不显示上次来源结果',async()=>{
    const original=vi.mocked(api).getMockImplementation()!;
    vi.mocked(api).mockImplementation(async(path,options)=>{
      if(path.startsWith('/business/objects?'))throw new ApiError('workflow_unavailable','该模块暂不可用',503);
      return original(path,options);
    });
    await open();source();await screen.findByText('该模块暂不可用');
    expect(screen.getByRole('button',{name:'页面继续'})).toBeTruthy();
    expect(screen.queryByRole('button',{name:'打开页面'})).toBeNull();
  });

  it('切换办理入口后忽略前一个模块迟到的对象响应',async()=>{
    const late=deferred<{items:ReturnType<typeof work>[];next_cursor:null}>();
    const original=vi.mocked(api).getMockImplementation()!;
    vi.mocked(api).mockImplementation((path,options)=>{
      if(path.includes('flow_id=example.submit'))return late.promise;
      if(path.includes('flow_id=example.review'))return Promise.resolve({items:[work('b','乙入口当前记录')],next_cursor:null});
      return original(path,options);
    });
    await open();source(0);await waitFor(()=>expect(api).toHaveBeenCalledWith('/business/objects?flow_id=example.submit'));
    source(1);await screen.findByText('乙入口当前记录');
    await act(async()=>late.resolve({items:[work('a','甲入口旧记录')],next_cursor:null}));
    expect(screen.queryByText('甲入口旧记录')).toBeNull();expect(screen.getByText('乙入口当前记录')).toBeTruthy();
    const record=screen.getByText('乙入口当前记录').closest('article')!;
    fireEvent.click(within(record).getByRole('button',{name:'在对话中办理'}));
    await waitFor(()=>expect(post).toHaveBeenCalledWith('/business/open',{flow_id:'example.review',object_id:'b',surface:'conversation',idempotency_key:'synthetic-open-key'}));
  });

  it('较新的目录刷新不会被第一次迟到的目录加载覆盖',async()=>{
    const late=deferred<{items:typeof entries}>();let calls=0;
    const original=vi.mocked(api).getMockImplementation()!;
    vi.mocked(api).mockImplementation((path,options)=>{
      if(path==='/business/catalog'){calls++;return calls===1?late.promise:Promise.resolve({items:[{...entries[0],title:'最新入口'}]});}
      return original(path,options);
    });
    render(<MyBusiness/>);fireEvent.click(screen.getByRole('button',{name:'刷新业务'}));await screen.findByRole('heading',{name:'最新入口'});
    await act(async()=>late.resolve({items:entries}));
    expect(screen.queryByRole('heading',{name:'提交工作台'})).toBeNull();expect(screen.getByRole('heading',{name:'最新入口'})).toBeTruthy();
  });

  it('离开页面后收到打开结果不会切换其他页面的会话',async()=>{
    const pending=deferred<{instance_id:string;conversation_id:string}>();vi.mocked(post).mockReturnValue(pending.promise);
    const view=render(<MyBusiness/>);await screen.findByText('进行中的材料');fireEvent.click(screen.getByRole('button',{name:'对话继续'}));
    expect(post).toHaveBeenCalledTimes(1);view.unmount();location.hash='identity';
    await act(async()=>pending.resolve({instance_id:'instance-original',conversation_id:'conversation-existing'}));
    expect(location.hash).toBe('#identity');
    expect(vi.mocked(api).mock.calls.some(([path])=>path==='/conversations/state')).toBe(false);
  });

  it('URL恢复只加载指定个人实例，不触发创建命令',async()=>{
    location.hash='business?instance=instance-saved';await open();
    expect(screen.getByTestId('flow-host').textContent).toContain('instance-saved');expect(post).not.toHaveBeenCalled();
  });
});
