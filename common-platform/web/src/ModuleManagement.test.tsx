// @vitest-environment jsdom
import '@testing-library/jest-dom/vitest';
import {afterEach,beforeEach,describe,expect,it,vi} from 'vitest';
import {act,cleanup,fireEvent,render,screen,waitFor} from '@testing-library/react';
import ModuleManagement from './ModuleManagement';
import {api,ApiError,post} from './api';
import type {User} from './types';

vi.mock('./api',async original=>({...await original<typeof import('./api')>(),api:vi.fn(),post:vi.fn()}));
const admin:User={id:'admin-a',username:'synthetic-admin',name:'合成管理员',role:'member',roles:['member','system_admin'],authorization_revision:1};
const moduleA={id:'synthetic.module',name:'合成模块甲',version:'3.0.0',description:'可管理的合成模块',source:'remote',enabled:true,revision:6,protected:false,service_id:'svc-a',capabilities:[],manifest_protocol_version:'1.2',invoke_protocol_version:'1.1',registration:{access_definition:'registered',flow_ids:['synthetic.flow'],component_ids:['synthetic.interaction'],missing_flow_ids:['synthetic.missing'],unmapped_capability_ids:['synthetic.unmapped']}};
let rows=[structuredClone(moduleA)];
let tick:()=>void=()=>{};
function deferred<T>(){let resolve!:(value:T)=>void;let reject!:(error:unknown)=>void;const promise=new Promise<T>((yes,no)=>{resolve=yes;reject=no;});return {promise,resolve,reject};}
beforeEach(()=>{
  vi.clearAllMocks();rows=[structuredClone(moduleA)];
  vi.mocked(api).mockImplementation(async path=>{if(path==='/modules')return {items:rows};throw new Error('Unexpected request '+path);});
  vi.mocked(post).mockResolvedValue({});
  const setInterval=window.setInterval.bind(window);
  vi.spyOn(window,'setInterval').mockImplementation((handler,timeout,...args)=>{if(timeout===5000)tick=handler as ()=>void;return setInterval(handler,timeout,...args);});
});
afterEach(()=>{cleanup();vi.restoreAllMocks();});
async function open(){render(<ModuleManagement user={admin}/>);await screen.findByRole('heading',{name:'合成模块甲'});}
async function poll(){await act(async()=>tick());}

describe('模块管理的发布状态与已加载定义',()=>{
  it('只展示响应提供的协议与定义概况，分别提示缺失映射和用户授权边界',async()=>{
    await open();const definitions=screen.getByRole('region',{name:'模块注册与安装概况'});
    expect(definitions.textContent).toContain('Manifest 协议：1.2');expect(definitions.textContent).toContain('Invoke 协议：1.1');
    expect(definitions.textContent).toContain('步骤流程定义：1');expect(definitions.textContent).toContain('独立交互组件定义：1');
    expect(screen.getByText(/权限办理视图引用的流程未加载或不属于本模块：synthetic.missing/)).toBeTruthy();
    expect(screen.getByText(/尚未映射权限的能力：synthetic.unmapped/)).toBeTruthy();
    expect(screen.getByText(/以上仅统计已加载定义/)).toBeTruthy();
    expect(screen.getByText(/实际用户授权另行分配/)).toBeTruthy();
    expect(vi.mocked(api).mock.calls.every(([path])=>path==='/modules')).toBe(true);
  });

  it('旧响应没有协议和安装数据时明确未提供，不推断已就绪',async()=>{
    vi.mocked(api).mockResolvedValue({items:[{...moduleA,manifest_protocol_version:undefined,invoke_protocol_version:undefined,registration:undefined}]});
    await open();const definitions=screen.getByRole('region',{name:'模块注册与安装概况'});
    expect(definitions.textContent).toContain('Manifest 协议：未提供');expect(definitions.textContent).toContain('Invoke 协议：未提供');
    expect(screen.getByText(/当前响应未提供定义概况/)).toBeTruthy();
    expect(screen.queryByText(/已就绪/)).toBeNull();
  });

  it('受保护核心模块没有启停操作',async()=>{
    rows=[{...moduleA,source:'core',protected:true}];await open();
    expect(screen.getByText(/平台核心模块维持登录、注册与管理能力/)).toBeTruthy();
    expect(screen.queryByRole('button',{name:'停用模块'})).toBeNull();expect(post).not.toHaveBeenCalled();
  });

  it.each([true,false])('对当前启用状态 %s 使用所见修订办理启停',async enabled=>{
    rows=[{...moduleA,enabled}];await open();
    vi.mocked(post).mockImplementation(async()=>{rows=[{...moduleA,enabled:!enabled,revision:7}];return {};});
    fireEvent.click(screen.getByRole('button',{name:enabled?'停用模块':'启用模块'}));
    await waitFor(()=>expect(post).toHaveBeenCalledWith('/modules/synthetic.module/'+(enabled?'disable':'enable'),{expected_revision:6}));
    await screen.findByRole('button',{name:enabled?'启用模块':'停用模块'});
  });

  it('并发修订冲突保持真实发布状态，成功轮询不吞掉操作错误',async()=>{
    await open();vi.mocked(post).mockRejectedValue(new ApiError('revision_conflict','模块已被另一管理员修改',409));
    fireEvent.click(screen.getByRole('button',{name:'停用模块'}));await screen.findByText('模块已被另一管理员修改');
    await poll();expect(screen.getByText('模块已被另一管理员修改')).toBeTruthy();
    expect(screen.getByRole('button',{name:'停用模块'})).toBeEnabled();expect(screen.queryByText(/模块已停用。已有记录保留/)).toBeNull();
  });

  it('变更成功后读取失败会说明需要核对，不宣称已取得最新列表',async()=>{
    const changed=vi.fn();render(<ModuleManagement user={admin} onRegistryChanged={changed}/>);await screen.findByRole('heading',{name:'合成模块甲'});
    vi.mocked(api).mockRejectedValueOnce(new Error('刷新连接失败'));fireEvent.click(screen.getByRole('button',{name:'停用模块'}));
    await screen.findByText('更新请求已完成，但列表读取失败，请刷新核对当前状态。');
    expect(screen.getByText('刷新连接失败')).toBeTruthy();expect(changed).toHaveBeenCalledTimes(1);
  });

  it('初次读取尚未结束时不叠加心跳读取',async()=>{
    const delayed=deferred<{items:typeof rows}>();vi.mocked(api).mockReturnValueOnce(delayed.promise);
    render(<ModuleManagement user={admin}/>);await poll();await poll();expect(api).toHaveBeenCalledTimes(1);
    await act(async()=>delayed.resolve({items:rows}));await poll();expect(api).toHaveBeenCalledTimes(2);
  });

  it('在途旧轮询被启停操作废弃，旧响应不能回写已停用状态',async()=>{
    await open();const delayed=deferred<{items:typeof rows}>();vi.mocked(api).mockReturnValueOnce(delayed.promise);await poll();
    const changing=deferred<object>();vi.mocked(post).mockReturnValueOnce(changing.promise);
    fireEvent.click(screen.getByRole('button',{name:'停用模块'}));
    await act(async()=>delayed.resolve({items:[{...moduleA,name:'不应出现的旧模块'}]}));
    expect(screen.queryByRole('heading',{name:'不应出现的旧模块'})).toBeNull();
    rows=[{...moduleA,enabled:false,revision:7}];await act(async()=>changing.resolve({}));
    await screen.findByRole('button',{name:'启用模块'});
  });

  it('角色数组授权发生变化时重新判定访问，即使主角色没变化',async()=>{
    const view=render(<ModuleManagement user={{...admin,roles:['member']}}/>);expect(api).not.toHaveBeenCalled();
    view.rerender(<ModuleManagement user={admin}/>);await screen.findByRole('heading',{name:'合成模块甲'});
    view.rerender(<ModuleManagement user={{...admin,roles:['member'],authorization_revision:2}}/>);
    expect(screen.getByText('模块管理由管理员维护')).toBeTruthy();expect(screen.queryByRole('heading',{name:'合成模块甲'})).toBeNull();
  });

  it('切换账号后旧读取及错误不会覆盖新账号模块',async()=>{
    const old=deferred<{items:typeof rows}>();vi.mocked(api).mockReturnValueOnce(old.promise);
    const view=render(<ModuleManagement user={admin}/>);
    rows=[{...moduleA,id:'other-module',name:'新管理员模块'}];view.rerender(<ModuleManagement user={{...admin,id:'admin-b'}}/>);
    await screen.findByRole('heading',{name:'新管理员模块'});
    await act(async()=>old.reject(new Error('旧管理员读取失败')));
    expect(screen.queryByText('旧管理员读取失败')).toBeNull();expect(screen.getByRole('heading',{name:'新管理员模块'})).toBeTruthy();
  });

  it('切换授权版本后旧启停结果不通知新宿主，也不覆盖新状态',async()=>{
    const changed=vi.fn();const view=render(<ModuleManagement user={admin} onRegistryChanged={changed}/>);await screen.findByRole('heading',{name:'合成模块甲'});
    const delayed=deferred<object>();vi.mocked(post).mockReturnValueOnce(delayed.promise);
    fireEvent.click(screen.getByRole('button',{name:'停用模块'}));
    rows=[{...moduleA,name:'新授权模块',revision:9}];view.rerender(<ModuleManagement user={{...admin,authorization_revision:2}} onRegistryChanged={changed}/>);
    await screen.findByRole('heading',{name:'新授权模块'});await act(async()=>delayed.resolve({}));
    expect(changed).not.toHaveBeenCalled();expect(screen.getByRole('heading',{name:'新授权模块'})).toBeTruthy();
    expect(screen.queryByText(/模块已停用。已有记录保留/)).toBeNull();
  });
});
