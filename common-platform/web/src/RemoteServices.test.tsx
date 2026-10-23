// @vitest-environment jsdom
import '@testing-library/jest-dom/vitest';
import {afterEach,beforeEach,describe,expect,it,vi} from 'vitest';
import {act,cleanup,fireEvent,render,screen,waitFor} from '@testing-library/react';
import RemoteServices from './RemoteServices';
import {api,ApiError,post} from './api';
import type {User} from './types';

vi.mock('./api',async original=>({...await original<typeof import('./api')>(),api:vi.fn(),post:vi.fn()}));
const admin:User={id:'admin-a',username:'synthetic-admin',name:'合成管理员',role:'member',roles:['member','system_admin'],authorization_revision:1};
const capability={id:'synthetic.query',version:'1.0.0',title:'合成查询',description:'仅用于管理测试',roles:['member'],input_schema:{type:'object'},output_schema:{type:'object'}};
const manifest={protocol_version:'1.2',module:{id:'synthetic',name:'合成模块',version:'2.0.0',description:'合成模块声明'},capabilities:[capability],surfaces:{page_tools:[{id:'main',title:'合成表单',description:'固定页',kind:'form',capability_ids:['synthetic.query']}],conversation:{capability_ids:['synthetic.query'],starter_prompts:[]}}};
const service={id:'svc-a',name:'服务甲',base_url:'http://127.0.0.1:8030',timeout_seconds:60,token_configured:true,enabled:false,revision:4,discovered_revision:4 as number|null,manifest,admission:{version:'1.0',revision:4,passed:true,checked_at:Date.now()/1000,checks:[{id:'manifest',title:'能力声明与输入输出',passed:true,code:'ok'}]}};
type Row = Omit<typeof service,'admission'> & {admission:typeof service.admission|null};
let rows:Row[]=[structuredClone(service)];
let tick:()=>void=()=>{};
function deferred<T>(){let resolve!:(value:T)=>void;let reject!:(error:unknown)=>void;const promise=new Promise<T>((yes,no)=>{resolve=yes;reject=no;});return {promise,resolve,reject};}
beforeEach(()=>{
  vi.clearAllMocks();rows=[structuredClone(service)];
  vi.mocked(api).mockImplementation(async path=>{if(path==='/remote-services')return {items:rows};throw new Error('Unexpected request '+path);});
  vi.mocked(post).mockResolvedValue(service);
  const setInterval=window.setInterval.bind(window);
  vi.spyOn(window,'setInterval').mockImplementation((handler,timeout,...args)=>{if(timeout===5000)tick=handler as ()=>void;return setInterval(handler,timeout,...args);});
});
afterEach(()=>{cleanup();vi.restoreAllMocks();});
async function open(){render(<RemoteServices user={admin}/>);await screen.findByDisplayValue('服务甲');}
async function poll(){await act(async()=>tick());}

describe('远程连接管理的修订与异步边界',()=>{
  it.each([['1.2','1.1'],['1.1','1.0'],['1.0','1.0']])('按 Manifest %s 展示 Invoke %s，并区分业务版本和安装授权',async(protocol,invoke)=>{
    rows=[{...service,manifest:{...manifest,protocol_version:protocol}}];await open();
    const versions=screen.getByRole('region',{name:'远程协议版本'});
    expect(versions.textContent).toContain('Manifest 协议：'+protocol);
    expect(versions.textContent).toContain('Invoke 协议：'+invoke);
    expect(screen.getByText(/模块业务版本、连接修订与传输协议分别管理/)).toBeTruthy();
    expect(screen.getByText(/权限定义描述规则，实际用户授权另行分配/)).toBeTruthy();
    expect(screen.getByText(/接入检查确认协议与已安装依赖/)).toBeTruthy();
  });

  it('轮询更新服务器修订时保留编辑值和预期修订，409后仍保留输入',async()=>{
    await open();fireEvent.change(screen.getByLabelText('服务名称'),{target:{value:'我的未保存名称'}});
    rows=[{...service,name:'另一个管理员的名称',revision:5,discovered_revision:null}];await poll();
    expect(screen.getByLabelText('服务名称')).toHaveValue('我的未保存名称');
    expect(screen.getByText(/正在编辑连接修订 4，服务器已更新到修订 5/)).toBeTruthy();
    vi.mocked(post).mockRejectedValue(new ApiError('remote_revision_conflict','连接已由另一管理员修改',409));
    fireEvent.click(screen.getByRole('button',{name:'保存连接'}));
    await screen.findByText('连接已由另一管理员修改');
    expect(post).toHaveBeenCalledWith('/remote-services',expect.objectContaining({id:'svc-a',name:'我的未保存名称',expected_revision:4}));
    expect(screen.getByLabelText('服务名称')).toHaveValue('我的未保存名称');
    await poll();expect(screen.getByText('连接已由另一管理员修改')).toBeTruthy();
    fireEvent.click(screen.getByRole('button',{name:'放弃编辑并加载最新连接'}));
    expect(screen.getByLabelText('服务名称')).toHaveValue('另一个管理员的名称');
    vi.mocked(post).mockResolvedValue(rows[0]);fireEvent.click(screen.getByRole('button',{name:'保存连接'}));
    await waitFor(()=>expect(post).toHaveBeenLastCalledWith('/remote-services',expect.objectContaining({expected_revision:5})));
  });

  it('手动刷新期间开始编辑，不会被刷新前捕获的干净状态覆盖',async()=>{
    await open();const delayed=deferred<{items:typeof rows}>();vi.mocked(api).mockReturnValueOnce(delayed.promise);
    fireEvent.click(screen.getByRole('button',{name:'刷新列表'}));
    fireEvent.change(screen.getByLabelText('服务名称'),{target:{value:'刷新期间输入'}});
    await act(async()=>delayed.resolve({items:[{...service,name:'刷新返回名称',revision:6}]}));
    expect(screen.getByLabelText('服务名称')).toHaveValue('刷新期间输入');
    fireEvent.click(screen.getByRole('button',{name:'保存连接'}));
    await waitFor(()=>expect(post).toHaveBeenCalledWith('/remote-services',expect.objectContaining({expected_revision:4,name:'刷新期间输入'})));
  });

  it('手动刷新期间切换服务，晚到列表不会把编辑器切回原服务',async()=>{
    rows=[service,{...service,id:'svc-b',name:'服务乙',base_url:'http://127.0.0.1:8031'}];await open();
    const delayed=deferred<{items:typeof rows}>();vi.mocked(api).mockReturnValueOnce(delayed.promise);
    fireEvent.click(screen.getByRole('button',{name:'刷新列表'}));
    fireEvent.click(screen.getByRole('button',{name:/服务乙/}));
    await act(async()=>delayed.resolve({items:rows}));
    expect(screen.getByLabelText('服务名称')).toHaveValue('服务乙');
  });

  it.each([true,false])('发布状态 %s 的切换均携带界面所见修订',async enabled=>{
    rows=[{...service,enabled}];await open();
    vi.mocked(post).mockResolvedValue({...service,enabled:!enabled});
    fireEvent.click(screen.getByRole('button',{name:enabled?'停用服务':'启用发布'}));
    await waitFor(()=>expect(post).toHaveBeenCalledWith('/remote-services/svc-a/'+(enabled?'disable':'enable'),{expected_revision:4}));
  });

  it('发现后先阻止发布，全部接入检查通过后才允许发布',async()=>{
    rows=[{...service,admission:null}];await open();
    expect(screen.getByRole('button',{name:'启用发布'})).toBeDisabled();
    expect(screen.getByText(/能力发现和健康检测不能代替接入检查/)).toBeTruthy();
    vi.mocked(post).mockResolvedValue(service);
    fireEvent.click(screen.getByRole('button',{name:'检查模块接入'}));
    await waitFor(()=>expect(post).toHaveBeenCalledWith('/remote-services/svc-a/admission',{expected_revision:4}));
    await waitFor(()=>expect(screen.getByRole('button',{name:'启用发布'})).toBeEnabled());
  });

  it('接入检查失败显示具体项目并继续阻止发布',async()=>{
    rows=[{...service,admission:null}];await open();
    vi.mocked(post).mockResolvedValue({...service,admission:{...service.admission,passed:false,
      checks:[{id:'authentication',title:'接口身份验证',passed:false,code:'remote_admission_interface'}]}});
    fireEvent.click(screen.getByRole('button',{name:'检查模块接入'}));
    await screen.findByText('接口身份验证');
    expect(screen.getByRole('button',{name:'启用发布'})).toBeDisabled();
    expect(screen.getByText('remote_admission_interface')).toBeTruthy();
    expect(post).toHaveBeenCalledTimes(1);
  });

  it('新建连接不伪造预期修订，也不回填保存前的令牌',async()=>{
    await open();fireEvent.click(screen.getByRole('button',{name:'新建服务'}));
    fireEvent.change(screen.getByLabelText('服务名称'),{target:{value:'新合成服务'}});
    fireEvent.change(screen.getByLabelText(/Bearer Token/),{target:{value:'synthetic-only-token'}});
    vi.mocked(post).mockResolvedValue({...service,id:'svc-new',name:'新合成服务'});
    fireEvent.click(screen.getByRole('button',{name:'保存连接'}));
    await screen.findByText(/配置已保存/);
    const body=vi.mocked(post).mock.calls[0][1] as Record<string,unknown>;
    expect(body).not.toHaveProperty('expected_revision');expect(body).not.toHaveProperty('id');
    expect(screen.getByLabelText(/Bearer Token/)).toHaveValue('');
  });

  it('保存开始即废弃先前的轮询，旧列表不能逆转已保存状态',async()=>{
    await open();const stale=deferred<{items:typeof rows}>();vi.mocked(api).mockReturnValueOnce(stale.promise);await poll();
    const saving=deferred<typeof service>();vi.mocked(post).mockReturnValueOnce(saving.promise);
    fireEvent.change(screen.getByLabelText('服务名称'),{target:{value:'保存后的名称'}});
    fireEvent.click(screen.getByRole('button',{name:'保存连接'}));
    await act(async()=>stale.resolve({items:[{...service,name:'旧轮询结果'}]}));
    await act(async()=>saving.resolve({...service,name:'保存后的名称',revision:5,discovered_revision:5}));
    expect(screen.getByLabelText('服务名称')).toHaveValue('保存后的名称');
    expect(screen.queryByRole('button',{name:/旧轮询结果/})).toBeNull();
  });

  it('主角色不变而管理员角色被撤销时停止读取，并拒绝晚到数据',async()=>{
    const delayed=deferred<{items:typeof rows}>();vi.mocked(api).mockReturnValueOnce(delayed.promise);
    const view=render(<RemoteServices user={admin}/>);
    view.rerender(<RemoteServices user={{...admin,roles:['member'],authorization_revision:2}}/>);
    expect(screen.getByText('服务接入由管理员维护')).toBeTruthy();
    await act(async()=>delayed.resolve({items:rows}));
    expect(screen.queryByText('服务甲')).toBeNull();expect(api).toHaveBeenCalledTimes(1);
  });

  it('主角色不变而获得管理员角色时加载连接',async()=>{
    const view=render(<RemoteServices user={{...admin,roles:['member']}}/>);expect(api).not.toHaveBeenCalled();
    view.rerender(<RemoteServices user={admin}/>);await screen.findByDisplayValue('服务甲');
  });

  it('切换管理员账号后忽略旧保存结果和回调，保留新账号的连接',async()=>{
    const changed=vi.fn();const view=render(<RemoteServices user={admin} onRegistryChanged={changed}/>);await screen.findByDisplayValue('服务甲');
    const delayed=deferred<typeof service>();vi.mocked(post).mockReturnValueOnce(delayed.promise);
    fireEvent.click(screen.getByRole('button',{name:'保存连接'}));
    rows=[{...service,id:'other-service',name:'新管理员连接'}];
    view.rerender(<RemoteServices user={{...admin,id:'admin-b'}} onRegistryChanged={changed}/>);
    await screen.findByDisplayValue('新管理员连接');
    await act(async()=>delayed.resolve({...service,name:'旧保存结果'}));
    expect(screen.getByLabelText('服务名称')).toHaveValue('新管理员连接');expect(changed).not.toHaveBeenCalled();
  });

  it('卸载后发现成功不再通知旧宿主刷新目录',async()=>{
    const changed=vi.fn();const view=render(<RemoteServices user={admin} onRegistryChanged={changed}/>);await screen.findByDisplayValue('服务甲');
    const delayed=deferred<typeof service>();vi.mocked(post).mockReturnValueOnce(delayed.promise);
    fireEvent.click(screen.getByRole('button',{name:'重新发现能力'}));view.unmount();
    await act(async()=>delayed.resolve(service));expect(changed).not.toHaveBeenCalled();
  });
});
