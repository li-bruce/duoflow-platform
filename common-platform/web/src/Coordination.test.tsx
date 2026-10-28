// @vitest-environment jsdom
import '@testing-library/jest-dom/vitest';
import {cleanup,fireEvent,render,screen,waitFor} from '@testing-library/react';
import {afterEach,beforeEach,expect,it,vi} from 'vitest';
import Coordination from './Coordination';
import {api,post} from './api';
import type {User} from './types';
vi.mock('./api',async original=>({...await original<typeof import('./api')>(),api:vi.fn(),post:vi.fn()}));
const admin:User={id:'admin-a',username:'admin',name:'管理员',role:'admin',authorization_revision:1};
const modules=[{id:'demo.source',title:'来源模块',service_id:'source',declaration:{emits:['resource.published'],accepts:[]}},
{id:'demo.reader',title:'接收模块',service_id:'reader',declaration:{emits:[],accepts:[{source_module:'demo.source',kind:'resource.published',mode:'notify'}]}}];
const summary={version:'1.0',config_revision:3,counts:{objects:2,approvals_pending:1,deliveries_pending:1,deliveries_leased:0,deliveries_acked:4},objects:[{module_id:'demo.source',object_ref:{object_type:'demo.source.batch',object_id:'batch-1'},state:'pending',revision:2,scope:{organization_id:'org',project_id:'project'}}],approvals:[{event_id:'request-one',module_id:'demo.source',object_ref:{object_type:'demo.source.batch',object_id:'batch-1'},object_revision:2,status:'pending',revision:1,scope:{organization_id:'org',project_id:'project'}}],backlogs:[{module_id:'demo.reader',pending:1,leased:0}]};
let connected=true;
beforeEach(()=>{vi.clearAllMocks();connected=true;vi.mocked(api).mockImplementation(async path=>{
  if(path==='/coordination/summary')return {state:connected?'connected':'unavailable',summary:connected?summary:null};
  if(path==='/coordination/configuration')return {modules,module_ids:modules.map(x=>x.id),subscriptions:[]};
  return {};
});vi.mocked(post).mockResolvedValue({});});
afterEach(cleanup);
it('展示并行业务状态、审批版本和各模块消息进度',async()=>{
  render(<Coordination user={admin}/>);expect(await screen.findByText('独立协同中心已连接')).toBeVisible();
  expect(screen.getByText('pending')).toBeVisible();expect(screen.getByText('版本 2')).toBeVisible();expect(screen.getByText('待接收 1 · 处理中 0')).toBeVisible();
});
it('审批只发送选择与预期版本，由服务端填实际操作者',async()=>{
  render(<Coordination user={admin}/>);fireEvent.click(await screen.findByRole('button',{name:'同意公开申请'}));
  await waitFor(()=>expect(post).toHaveBeenCalledWith('/coordination/approvals/request-one/decide',{expected_revision:1,decision:'approved'}));
});
it('消息关系同时满足两侧声明才允许添加，保存绑定当前配置修订',async()=>{
  render(<Coordination user={admin}/>);await screen.findByText('独立协同中心已连接');
  fireEvent.click(screen.getByText('配置参与模块与订阅关系'));
  fireEvent.change(screen.getByRole('combobox',{name:'来源模块'}),{target:{value:'demo.source'}});
  fireEvent.change(screen.getByRole('combobox',{name:'接收模块'}),{target:{value:'demo.reader'}});
  fireEvent.change(screen.getByLabelText('机构编号'),{target:{value:'org'}});fireEvent.change(screen.getByLabelText('项目编号'),{target:{value:'project'}});
  fireEvent.change(screen.getByLabelText('处理方式'),{target:{value:'sync'}});expect(screen.getByRole('button',{name:'添加订阅'})).toBeDisabled();
  fireEvent.change(screen.getByLabelText('处理方式'),{target:{value:'notify'}});fireEvent.click(screen.getByRole('button',{name:'添加订阅'}));
  fireEvent.click(screen.getByRole('button',{name:'保存协同配置'}));
  await waitFor(()=>expect(api).toHaveBeenCalledWith('/coordination/configuration',expect.objectContaining({method:'PUT'})));
  const call=vi.mocked(api).mock.calls.find(x=>x[1]?.method==='PUT')!;const body=JSON.parse(String(call[1]?.body));
  expect(body.expected_revision).toBe(3);expect(body.subscriptions[0]).toMatchObject({source_module:'demo.source',target_module:'demo.reader',mode:'notify',scope:{organization_id:'org',project_id:'project'}});expect(body).not.toHaveProperty('token');
});
it('切换到普通成员后不保留管理员审批或模块状态',async()=>{
  const view=render(<Coordination user={admin}/>);await screen.findByText('pending');view.rerender(<Coordination user={{...admin,id:'member',role:'member'}}/>);
  expect(screen.queryByText('pending')).toBeNull();expect(screen.queryByRole('button',{name:'同意公开申请'})).toBeNull();
});
it('中心断开时显示连接状态并停用配置提交',async()=>{
  connected=false;render(<Coordination user={admin}/>);await screen.findByText('协同中心暂未连接');fireEvent.click(screen.getByText('配置参与模块与订阅关系'));
  expect(screen.getByRole('button',{name:'保存协同配置'})).toBeDisabled();expect(screen.queryByRole('button',{name:'同意公开申请'})).toBeNull();
});
it('参与模块尚未接入时不会提交空配置覆盖独立中心',async()=>{
  vi.mocked(api).mockImplementation(async path=>path==='/coordination/summary'?{state:'connected',summary}:{modules:[],module_ids:[],subscriptions:[]});
  render(<Coordination user={admin}/>);await screen.findByText('独立协同中心已连接');fireEvent.click(screen.getByText('配置参与模块与订阅关系'));
  expect(screen.getByRole('button',{name:'保存协同配置'})).toBeDisabled();
});
