// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import AccessManagement from './AccessManagement';
import { api, ApiError, post } from './api';

vi.mock('./api', async importOriginal=>({...await importOriginal<typeof import('./api')>(),api:vi.fn(),post:vi.fn()}));

const users=[{id:'user-a',username:'alpha',name:'用户甲',enabled:true},{id:'user-b',username:'beta',name:'用户乙',enabled:true}];
const initialPolicies=[{
  module_id:'example.intake',title:'材料模块',version:'1.0',enabled:true,policy_revision:5,
  scope_keys:['project_id','organization_id'],permissions:[{id:'example.intake.read',title:'读取材料'},{id:'example.intake.review',title:'审核材料'}],
  role_templates:[{id:'submitter',title:'提交者',permissions:['example.intake.read'],required_scope:['project_id']},
    {id:'reviewer',title:'审核者',permissions:['example.intake.read','example.intake.review'],required_scope:['project_id','organization_id']}],
},{module_id:'example.product',title:'产品模块',version:'1.0',enabled:true,policy_revision:3,scope_keys:['team_id'],
  permissions:[{id:'example.product.edit',title:'制作产品'}],
  role_templates:[{id:'editor',title:'产品编辑者',permissions:['example.product.edit'],required_scope:['team_id']}],
}];
const grant=(id:string,extra:Record<string,unknown>={})=>({id,subject_id:'user-a',module_id:'example.intake',role_id:'submitter',scope:{project_id:'project-p'},permissions:['example.intake.read'],required_scope:['project_id'],definition_revision:1,expires_at:null,revoked:false,created_at:'2026-01-01',...extra});
let policies=structuredClone(initialPolicies);
let bindings:ReturnType<typeof grant>[]=[];

beforeEach(()=>{
  vi.clearAllMocks();policies=structuredClone(initialPolicies);bindings=[];
  vi.mocked(api).mockImplementation(async path=>{
    if(path==='/access/definitions')return {items:policies};
    if(path==='/identity/users')return {items:users};
    if(path.startsWith('/access/grants?subject_id='))return {items:bindings.filter(item=>item.subject_id===decodeURIComponent(path.split('=')[1]))};
    throw new Error('Unexpected request '+path);
  });
  vi.mocked(post).mockResolvedValue({});
});
afterEach(cleanup);

async function open(){render(<AccessManagement/>);await screen.findByLabelText('授权账号');}
function selectUser(value='user-a'){fireEvent.change(screen.getByLabelText('授权账号'),{target:{value}});}
function setScope(key:string,value:string){fireEvent.change(screen.getByLabelText('范围 '+key),{target:{value}});}
function deferred<T>(){let resolve!:(value:T)=>void;let reject!:(reason:unknown)=>void;const promise=new Promise<T>((a,b)=>{resolve=a;reject=b;});return {promise,resolve,reject};}

describe('模块范围授权管理',()=>{
  it('按模块声明呈现角色和范围并提交完整、带修订号的授权',async()=>{
    await open();selectUser();
    fireEvent.change(screen.getByLabelText('业务角色'),{target:{value:'reviewer'}});
    setScope('project_id',' project-p ');setScope('organization_id','org-receiver');
    fireEvent.change(screen.getByLabelText(/到期时间/),{target:{value:'2099-01-02T03:04'}});
    fireEvent.click(screen.getByRole('button',{name:'添加授权'}));
    await waitFor(()=>expect(post).toHaveBeenCalledWith('/access/grants',{
      subject_id:'user-a',module_id:'example.intake',role_id:'reviewer',
      scope:{project_id:'project-p',organization_id:'org-receiver'},
      expires_at:new Date('2099-01-02T03:04').getTime()/1000,expected_policy_revision:5,
    }));
    await screen.findByText(/授权已保存/);
  });

  it('切换模块或角色时清空先前范围，不把另一模块字段带入授权',async()=>{
    await open();selectUser();setScope('project_id','project-p');setScope('organization_id','org-a');
    fireEvent.change(screen.getByLabelText('业务角色'),{target:{value:'reviewer'}});
    expect((screen.getByLabelText('范围 project_id') as HTMLInputElement).value).toBe('');
    setScope('project_id','project-q');
    fireEvent.change(screen.getByLabelText('业务模块'),{target:{value:'example.product'}});
    expect(screen.queryByLabelText('范围 project_id')).toBeNull();
    expect((screen.getByLabelText('业务角色') as HTMLSelectElement).value).toBe('editor');
    setScope('team_id','team-z');fireEvent.click(screen.getByRole('button',{name:'添加授权'}));
    await waitFor(()=>expect(post).toHaveBeenCalledWith('/access/grants',{
      subject_id:'user-a',module_id:'example.product',role_id:'editor',scope:{team_id:'team-z'},expected_policy_revision:3,
    }));
  });

  it('切换账号立即清除旧列表，较晚返回的旧账号结果不会覆盖当前账号',async()=>{
    const late=deferred<{items:ReturnType<typeof grant>[]}>();
    const original=vi.mocked(api).getMockImplementation()!;
    vi.mocked(api).mockImplementation((path,options)=>path.includes('subject_id=user-a')?late.promise:original(path,options));
    bindings=[grant('grant-b',{subject_id:'user-b',scope:{project_id:'乙的业务范围'}})];
    await open();selectUser();
    await waitFor(()=>expect(api).toHaveBeenCalledWith('/access/grants?subject_id=user-a'));
    selectUser('user-b');await screen.findByText(/乙的业务范围/);
    await act(async()=>late.resolve({items:[grant('grant-a',{scope:{project_id:'甲的旧结果'}})]}));
    expect(screen.queryByText(/甲的旧结果/)).toBeNull();
    expect(screen.getByText(/乙的业务范围/)).toBeTruthy();
  });

  it('较晚返回的旧账号错误不会污染新账号的列表和错误提示',async()=>{
    const late=deferred<{items:ReturnType<typeof grant>[]}>();
    const original=vi.mocked(api).getMockImplementation()!;
    vi.mocked(api).mockImplementation((path,options)=>path.includes('subject_id=user-a')?late.promise:original(path,options));
    await open();selectUser();
    await waitFor(()=>expect(api).toHaveBeenCalledWith('/access/grants?subject_id=user-a'));
    selectUser('user-b');await screen.findByText('此账号暂无模块授权');
    await act(async()=>late.reject(new Error('旧账号读取失败')));
    expect(screen.queryByText('旧账号读取失败')).toBeNull();
  });

  it('策略冲突保留输入，显式刷新后再次提交使用最新策略修订',async()=>{
    vi.mocked(post).mockRejectedValueOnce(new ApiError('access_policy_conflict','权限策略已更新',409)).mockResolvedValue({});
    await open();selectUser();setScope('project_id','project-p');
    fireEvent.click(screen.getByRole('button',{name:'添加授权'}));await screen.findByText('权限策略已更新');
    expect((screen.getByLabelText('范围 project_id') as HTMLInputElement).value).toBe('project-p');
    policies[0].policy_revision=8;
    fireEvent.click(screen.getByRole('button',{name:'刷新授权目录'}));
    await screen.findByText(/策略修订 8/);
    fireEvent.click(screen.getByRole('button',{name:'添加授权'}));
    await waitFor(()=>expect(post).toHaveBeenLastCalledWith('/access/grants',expect.objectContaining({expected_policy_revision:8,scope:{project_id:'project-p'}})));
  });

  it('展示有效、到期、已撤销授权，撤销后重读服务记录',async()=>{
    bindings=[grant('active'),grant('expired',{expires_at:1}),grant('revoked',{revoked:true})];
    vi.mocked(post).mockImplementation(async path=>{
      if(path==='/access/grants/active/revoke'){bindings[0]={...bindings[0],revoked:true};return bindings[0];}
      throw new Error('Unexpected mutation');
    });
    await open();selectUser();
    await screen.findByText('有效');expect(screen.getByText('已到期')).toBeTruthy();expect(screen.getByText('已撤销')).toBeTruthy();
    expect(screen.queryByRole('button',{name:'撤销授权 revoked'})).toBeNull();
    fireEvent.click(screen.getByRole('button',{name:'撤销授权 active'}));
    await waitFor(()=>expect(post).toHaveBeenCalledWith('/access/grants/active/revoke'));
    await screen.findByText(/授权已撤销/);
    await waitFor(()=>expect(screen.queryByRole('button',{name:'撤销授权 active'})).toBeNull());
  });

  it('输入明确范围且到期日有效后才发送，重复点击只产生一次写入',async()=>{
    const pending=deferred<unknown>();vi.mocked(post).mockReturnValue(pending.promise);
    await open();selectUser();setScope('project_id','*');
    fireEvent.submit(screen.getByRole('form',{name:'添加模块授权'}));await screen.findByText(/不能使用 \*/);expect(post).not.toHaveBeenCalled();
    setScope('project_id','project-p');fireEvent.change(screen.getByLabelText(/到期时间/),{target:{value:'2000-01-01T00:00'}});
    fireEvent.submit(screen.getByRole('form',{name:'添加模块授权'}));await screen.findByText('授权到期时间必须晚于当前时间。');expect(post).not.toHaveBeenCalled();
    fireEvent.change(screen.getByLabelText(/到期时间/),{target:{value:''}});
    fireEvent.submit(screen.getByRole('form',{name:'添加模块授权'}));fireEvent.submit(screen.getByRole('form',{name:'添加模块授权'}));
    expect(post).toHaveBeenCalledTimes(1);
    await act(async()=>pending.resolve({}));
  });

  it('目录加载失败显示错误，不能在缺失权限定义时提交授权',async()=>{
    vi.mocked(api).mockRejectedValue(new Error('目录暂不可用'));
    await open();await screen.findByText('目录暂不可用');
    expect(screen.queryByRole('button',{name:'添加授权'})).toBeNull();expect(post).not.toHaveBeenCalled();
  });

  it('离开页面后忽略迟到的授权响应，不更新新页面或触发刷新回调',async()=>{
    const pending=deferred<unknown>();vi.mocked(post).mockReturnValue(pending.promise);const changed=vi.fn();
    const view=render(<AccessManagement onChanged={changed}/>);await screen.findByLabelText('授权账号');selectUser();setScope('project_id','project-p');
    fireEvent.click(screen.getByRole('button',{name:'添加授权'}));expect(post).toHaveBeenCalledTimes(1);view.unmount();
    await act(async()=>pending.resolve({}));expect(changed).not.toHaveBeenCalled();
  });
});
