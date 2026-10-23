// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import IdentityManagement from './IdentityManagement';
import { api, ApiError, post } from './api';

vi.mock('./api', async importOriginal => {
  const original = await importOriginal<typeof import('./api')>();
  return {...original,api:vi.fn(),post:vi.fn()};
});
const user={id:'user-a',username:'researcher_a',name:'科研人员甲',role:'researcher',roles:['member','researcher'],organization_id:'org-a',team_ids:['team-a'],authorization_revision:3,enabled:true};
const roles=[{id:'member',name:'基础使用者',implies:[],permissions:[]},{id:'admin',name:'旧演示管理员',implies:['member'],permissions:[]},{id:'researcher',name:'科研人员',implies:['member'],permissions:[]},{id:'reviewer',name:'复核人员',implies:['member'],permissions:[]},{id:'system_admin',name:'系统管理员',implies:[],permissions:[]}];
let currentUser={...user};
beforeEach(()=>{
  vi.clearAllMocks();currentUser={...user};
  vi.mocked(api).mockImplementation(async (path,options)=>{
    if(options?.method==='PATCH')return {...currentUser,...JSON.parse(String(options.body)),authorization_revision:4};
    if(path==='/identity/users')return {items:[currentUser]};
    if(path==='/identity/roles')return {roles};
    if(path==='/identity/organizations')return {items:[{id:'org-a',name:'机构甲',kind:'research',enabled:true},{id:'org-b',name:'机构乙',kind:'research',enabled:true}]};
    if(path==='/identity/teams')return {items:[{id:'team-a',name:'团队甲',organization_id:'org-a',enabled:true},{id:'team-b',name:'团队乙',organization_id:'org-b',enabled:true}]};
    throw new Error('Unexpected test request '+path);
  });
});
afterEach(cleanup);
async function openExisting(){render(<IdentityManagement/>);fireEvent.click(await screen.findByRole('button',{name:/科研人员甲/}));}

describe('身份管理操作',()=>{
  it('保持账号修订号，机构变化清空旧团队，并提交明确多角色',async()=>{
    await openExisting();
    fireEvent.change(screen.getByLabelText(/^所属机构/),{target:{value:'org-b'}});
    expect(screen.queryByLabelText('团队甲')).toBeNull();
    fireEvent.click(screen.getByLabelText('团队乙'));
    fireEvent.click(screen.getByLabelText('复核人员'));
    fireEvent.click(screen.getByRole('button',{name:'保存账号'}));
    await waitFor(()=>expect(api).toHaveBeenCalledWith('/identity/users/user-a',expect.objectContaining({method:'PATCH'})));
    const call=vi.mocked(api).mock.calls.find(([,options])=>options?.method==='PATCH');
    const body=JSON.parse(String(call?.[1]?.body));
    expect(body).toMatchObject({expected_revision:3,organization_id:'org-b',team_ids:['team-b']});
    expect(body.roles).toContain('reviewer');expect(body.roles).toContain('researcher');
    expect(body).not.toHaveProperty('password');
  });

  it('新账号不能分配旧管理员，随机初始密码只显示一次且可清除',async()=>{
    const secret='synthetic-only-password';
    vi.mocked(post).mockResolvedValue({user:{...user,id:'new-user',username:'new_person'},initial_password:secret});
    render(<IdentityManagement/>);await screen.findByRole('button',{name:/科研人员甲/});
    expect(screen.queryByRole('option',{name:'旧演示管理员'})).toBeNull();
    fireEvent.change(screen.getByLabelText(/^账号名/),{target:{value:'new_person'}});
    fireEvent.change(screen.getByLabelText('姓名或显示名'),{target:{value:'新成员'}});
    fireEvent.click(screen.getAllByRole('button',{name:'创建账号'})[1]);
    await waitFor(()=>expect(post).toHaveBeenCalledWith('/identity/users',expect.objectContaining({username:'new_person'})));
    expect(vi.mocked(post).mock.calls[0][1]).not.toHaveProperty('password');
    await screen.findByText(secret);
    fireEvent.click(screen.getByRole('button',{name:'关闭初始密码'}));
    expect(screen.queryByText(secret)).toBeNull();
    expect(localStorage.length).toBe(0);
  });

  it('遇到并发更新时保留输入，明确刷新后使用新修订号',async()=>{
    await openExisting();
    const initialMock=vi.mocked(api).getMockImplementation()!;
    vi.mocked(api).mockImplementation(async (path,options)=>{
      if(options?.method==='PATCH')throw new ApiError('revision_conflict','账号信息已更新',409);
      return initialMock(path,options);
    });
    fireEvent.change(screen.getByLabelText('姓名或显示名'),{target:{value:'未保存的新名字'}});
    fireEvent.click(screen.getByRole('button',{name:'保存账号'}));
    await screen.findByText('账号信息已更新');
    expect((screen.getByLabelText('姓名或显示名') as HTMLInputElement).value).toBe('未保存的新名字');
    currentUser={...user,name:'另一位管理员更新',authorization_revision:8};
    fireEvent.click(screen.getByRole('button',{name:'丢弃修改并读取最新'}));
    await waitFor(()=>expect((screen.getByLabelText('姓名或显示名') as HTMLInputElement).value).toBe('另一位管理员更新'));
    expect(screen.getByText(/修订 8/)).toBeTruthy();
  });

  it('停用与密码重置发送当前修订号，操作不会隐式提交未保存表单',async()=>{
    await openExisting();
    vi.mocked(post).mockResolvedValue({...user,enabled:false,authorization_revision:4});
    fireEvent.click(screen.getByRole('button',{name:'停用账号'}));
    expect(post).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button',{name:'确认停用'}));
    await waitFor(()=>expect(post).toHaveBeenCalledWith('/identity/users/user-a/disable',{expected_revision:3}));
    await screen.findByRole('button',{name:'启用账号'});
    vi.mocked(post).mockResolvedValue({user:{...user,enabled:false,authorization_revision:5},initial_password:'new-test-password'});
    fireEvent.click(screen.getByRole('button',{name:'重置密码'}));
    fireEvent.click(screen.getByRole('button',{name:'确认重置'}));
    await waitFor(()=>expect(post).toHaveBeenCalledWith('/identity/users/user-a/reset-password',{expected_revision:4}));
    await screen.findByText('new-test-password');
  });
});
