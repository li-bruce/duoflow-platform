// @vitest-environment jsdom
// Synthetic host tests: these do not replace real service or browser acceptance.
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { ApiError } from '../api';
import type { Data, WorkflowDefinition, WorkflowInstance, WorkflowStepProps } from './sdk';
import FlowHost from './FlowHost';

const mocked = vi.hoisted(() => ({ api: vi.fn(), post: vi.fn(), file: null as File | null, latest: null as WorkflowStepProps | null, changes: vi.fn() }));
vi.mock('../api', async original => ({ ...await original<typeof import('../api')>(), api: mocked.api, post: mocked.post }));
vi.mock('./catalog', () => ({
  installedStep: (_definition: WorkflowDefinition, name: string) => name === 'DraftStep' ? DraftStep : OtherStep,
}));

function DraftStep(props: WorkflowStepProps) {
  mocked.latest = props;
  return <div data-testid="shared-draft-step"><span>{props.surface}</span><label>个人输入<input aria-label="个人输入" value={String(props.draft.value || '')} disabled={props.busy} onChange={event => props.updateDraft({ value: event.target.value })} /></label><button disabled={props.busy} onClick={() => void props.execute('save', { value: 'explicit-command' }).catch(() => {})}>执行合成动作</button><button disabled={props.busy} onClick={() => mocked.file && void props.uploadFile(mocked.file).catch(() => {})}>上传合成文件</button>{props.uploadSessions.map(upload => <button key={upload.upload_id} disabled={props.busy} onClick={() => mocked.file && void props.resumeUpload(upload.upload_id, mocked.file).catch(() => {})}>恢复 {upload.upload_id}</button>)}<span>业务状态 {props.projection?.state}</span>{props.projection?.jobs?.map(job => <span key={job.job_id}>任务 {job.state}</span>)}</div>;
}
function OtherStep(props: WorkflowStepProps) { return <div data-testid="other-step">其他步骤 {props.surface}<input aria-label="第二组件输入" value={props.draft.value||''} disabled={props.busy} onChange={e=>props.updateDraft({value:e.target.value})}/><button disabled={props.busy} onClick={()=>void props.execute('save',{value:'second-command'}).catch(()=>{})}>执行第二组件</button></div>; }

const definition: WorkflowDefinition = {
  id: 'test.workflow', version: '1.0.0', title: '合成工作台', description: '仅测试通用宿主', pack: 'test', initial_step: 'draft', available: true,
  steps: [
    { id: 'draft', title: '编辑步骤', description: '保存个人输入', component: 'DraftStep', actions: [{ id: 'save', title: '保存', available: true }] },
    { id: 'other', title: '其他步骤', description: '另一独立组件', component: 'OtherStep', actions: [] },
  ],
};
let record: WorkflowInstance;
let events: string[];

const copy = () => structuredClone(record);
function deferred<T>(){let resolve!:(value:T)=>void;const promise=new Promise<T>(done=>{resolve=done;});return {promise,resolve};}
function syntheticFile(bytes: Uint8Array, name: string): File {
  // jsdom does not implement Blob.arrayBuffer. Keep real bounded byte slices
  // while stubbing only this browser File primitive for host transport tests.
  return { name, size: bytes.byteLength, type: 'application/octet-stream',
    slice: (start?: number, end?: number) => ({ arrayBuffer: async () => bytes.slice(start, end).buffer }) as Blob,
  } as File;
}
beforeEach(() => {
  delete definition.page_groups;
  record = { id: 'flow-a', flow_id: definition.id, flow_version: definition.version, title: definition.title, cursor: 'draft', drafts: { draft: { value: '保存的个人草稿' } }, revision: 1, object_ref: { object_type: 'synthetic', object_id: 'object-a' }, projection: { object_ref: { object_type: 'synthetic', object_id: 'object-a' }, revision: 7, state: 'draft', data: {}, allowed_actions: ['save'], jobs: [] }, objects: [], actions: [{ id: 'save', title: '保存', available: true }], status: 'editing', error: null, run_id: null, conversation_id: null, forked_from: null, updated_at: '' };
  events = [];
  mocked.api.mockReset(); mocked.post.mockReset(); mocked.changes.mockReset(); mocked.file = null; mocked.latest = null;
  mocked.api.mockImplementation(async (path: string, options?: RequestInit) => {
    events.push(`${options?.method || 'GET'} ${path}`);
    if (path === '/flows/catalog') return { items: [definition] };
    if (options?.method === 'PATCH') {
      const body = JSON.parse(String(options.body));
      expect(body.expected_revision).toBe(record.revision);
      record = { ...record, cursor: body.cursor, drafts: body.drafts, revision: record.revision + 1 };
    }
    return copy();
  });
  mocked.post.mockImplementation(async (path: string) => {
    events.push(`POST ${path}`);
    record = { ...record, status: 'completed', revision: record.revision + 1, projection: { ...record.projection!, state: 'saved', revision: record.projection!.revision + 1 } };
    return { instance: copy(), result: { data: { accepted: true } } };
  });
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.useRealTimers(); });

describe('workflow host with synthetic adapter responses', () => {
  it.each(['page','conversation'] as const)('rechecks idle %s authorization at 15 seconds and clears revoked state', async surface => {
    vi.useFakeTimers();
    const view=render(<FlowHost instanceId="flow-a" surface={surface}/>);
    await act(async()=>{});
    expect(screen.getByDisplayValue('保存的个人草稿')).toBeTruthy();
    const original=mocked.api.getMockImplementation()!;
    mocked.api.mockImplementation((path:string,options?:RequestInit)=>!options?.method&&path==='/flows/instances/flow-a'?Promise.reject(new ApiError('authorization_changed','合成授权撤销',403)):original(path,options));
    await act(async()=>{await vi.advanceTimersByTimeAsync(14999);});
    expect(screen.getByText('业务状态 draft')).toBeTruthy();
    await act(async()=>{await vi.advanceTimersByTimeAsync(1);});
    expect(screen.queryByLabelText('个人输入')).toBeNull();
    expect(screen.queryByText('业务状态 draft')).toBeNull();
    expect(screen.getByText(/已清除当前业务内容/)).toBeTruthy();
    view.unmount();
    await act(async()=>{});
    expect(mocked.api.mock.calls.filter(([,options])=>options?.method==='PATCH')).toHaveLength(0);
  });

  it('preserves an unsaved draft on read-only idle checks and network errors', async()=>{
    vi.useFakeTimers();render(<FlowHost instanceId="flow-a"/>);await act(async()=>{});
    const original=mocked.api.getMockImplementation()!;
    mocked.api.mockImplementation((path:string,options?:RequestInit)=>options?.method==='PATCH'?Promise.reject(new ApiError('network_error','合成离线',0)):original(path,options));
    fireEvent.change(screen.getByLabelText('个人输入'),{target:{value:'不能被后台读取覆盖'}});
    await act(async()=>{await vi.advanceTimersByTimeAsync(15000);});
    expect(screen.getByDisplayValue('不能被后台读取覆盖')).toBeTruthy();
    expect(mocked.api.mock.calls.filter(([path,options])=>path==='/flows/instances/flow-a'&&!options?.method)).toHaveLength(2);
    mocked.api.mockRejectedValue(new ApiError('network_error','读取仍离线',0));
    await act(async()=>{await vi.advanceTimersByTimeAsync(15000);});
    expect(screen.getByDisplayValue('不能被后台读取覆盖')).toBeTruthy();
    expect(screen.getByText('业务状态 draft')).toBeTruthy();
  });

  it('does not poll over an admitted draft save and resumes once it settles', async()=>{
    vi.useFakeTimers();render(<FlowHost instanceId="flow-a"/>);await act(async()=>{});
    const original=mocked.api.getMockImplementation()!,gate=deferred<WorkflowInstance>();let saved:WorkflowInstance|undefined;
    mocked.api.mockImplementation(async(path:string,options?:RequestInit)=>{if(options?.method==='PATCH'){saved=await original(path,options);return gate.promise;}return original(path,options);});
    fireEvent.change(screen.getByLabelText('个人输入'),{target:{value:'保存尚未返回'}});
    await act(async()=>{await vi.advanceTimersByTimeAsync(15000);});
    expect(mocked.api.mock.calls.filter(([path,options])=>path==='/flows/instances/flow-a'&&!options?.method)).toHaveLength(1);
    await act(async()=>{gate.resolve(saved!);await gate.promise;await vi.advanceTimersByTimeAsync(15000);});
    expect(mocked.api.mock.calls.filter(([path,options])=>path==='/flows/instances/flow-a'&&!options?.method)).toHaveLength(2);
    expect(screen.getByDisplayValue('保存尚未返回')).toBeTruthy();
  });

  it.each(['save','command'] as const)('clears both object and draft immediately after a revoked %s', async operation=>{
    vi.useFakeTimers();const view=render(<FlowHost instanceId="flow-a"/>);await act(async()=>{});
    const original=mocked.api.getMockImplementation()!;
    if(operation==='save')mocked.api.mockImplementation((path:string,options?:RequestInit)=>options?.method==='PATCH'?Promise.reject(new ApiError('forbidden','无权保存',403)):original(path,options));
    else mocked.post.mockRejectedValue(new ApiError('authorization_changed','无权办理',403));
    fireEvent.change(screen.getByLabelText('个人输入'),{target:{value:'撤权后须移除的草稿'}});
    if(operation==='command')fireEvent.click(screen.getByRole('button',{name:'执行合成动作'}));
    await act(async()=>{await vi.advanceTimersByTimeAsync(300);});
    expect(screen.queryByDisplayValue('撤权后须移除的草稿')).toBeNull();
    expect(screen.queryByText('业务状态 draft')).toBeNull();
    const writes=mocked.api.mock.calls.filter(([,options])=>options?.method==='PATCH').length;
    view.unmount();await act(async()=>{});
    expect(mocked.api.mock.calls.filter(([,options])=>options?.method==='PATCH')).toHaveLength(writes);
  });

  it('clears unavailable definitions and does not flush discarded drafts on unmount', async()=>{
    vi.useFakeTimers();const view=render(<FlowHost instanceId="flow-a"/>);await act(async()=>{});
    fireEvent.change(screen.getByLabelText('个人输入'),{target:{value:'隐藏流程草稿'}});
    const original=mocked.api.getMockImplementation()!;
    mocked.api.mockImplementation((path:string,options?:RequestInit)=>path==='/flows/catalog'?Promise.resolve({items:[]}):original(path,options));
    fireEvent.click(screen.getByRole('button',{name:'重新读取'}));await act(async()=>{});
    expect(screen.queryByLabelText('个人输入')).toBeNull();
    expect(screen.getByText('流程定义不可用，请重新打开。')).toBeTruthy();
    view.unmount();await act(async()=>{});
    expect(mocked.api.mock.calls.filter(([,options])=>options?.method==='PATCH')).toHaveLength(0);
  });

  it('cannot restore a revoked view with an older delayed refresh', async()=>{
    render(<FlowHost instanceId="flow-a"/>);await screen.findByLabelText('个人输入');
    const gate=deferred<WorkflowInstance>(),saved=copy(),props=mocked.latest!;
    mocked.api.mockImplementationOnce(()=>gate.promise).mockRejectedValueOnce(new ApiError('forbidden','范围已撤销',403));
    let late!:Promise<void>;
    act(()=>{late=props.refresh();});
    await act(async()=>{await props.refresh().catch(()=>{});});
    await act(async()=>{gate.resolve(saved);await late;});
    expect(screen.queryByLabelText('个人输入')).toBeNull();
    expect(screen.queryByText('业务状态 draft')).toBeNull();
  });

  it('ignores a delayed refresh after a newer command response has been applied', async () => {
    render(<FlowHost instanceId="flow-a"/>);
    await screen.findByLabelText('个人输入');
    const old=copy(),gate=deferred<WorkflowInstance>();
    mocked.api.mockImplementationOnce(()=>gate.promise);
    let reading!:Promise<void>;
    act(()=>{reading=mocked.latest!.refresh();});
    fireEvent.click(screen.getByRole('button',{name:'执行合成动作'}));
    expect(await screen.findByText('业务状态 saved')).toBeTruthy();
    await act(async()=>{gate.resolve(old);await reading;});
    expect(screen.getByText('业务状态 saved')).toBeTruthy();
    expect(screen.getByText(/草稿修订 2/)).toBeTruthy();
    expect(mocked.post).toHaveBeenCalledTimes(1);
  });

  it.each(['ui','business'] as const)('never rolls back the same instance %s revision', async field => {
    render(<FlowHost instanceId="flow-a"/>);
    await screen.findByLabelText('个人输入');
    record={...record,revision:3,projection:{...record.projection!,revision:9,state:'newest'}};
    await act(async()=>{await mocked.latest!.refresh();});
    const old={...copy(),revision:field==='ui'?2:3,projection:{...record.projection!,revision:field==='business'?8:9,state:'obsolete'}};
    mocked.api.mockResolvedValueOnce(old);
    await act(async()=>{await mocked.latest!.refresh();});
    expect(screen.getByText('业务状态 newest')).toBeTruthy();
    expect(screen.getByText(/草稿修订 3/)).toBeTruthy();
    expect(screen.getByText(/业务修订 9/)).toBeTruthy();
  });

  it('publishes only the latest read when two refreshes finish in reverse order', async () => {
    render(<FlowHost instanceId="flow-a"/>);
    await screen.findByLabelText('个人输入');
    const first=deferred<WorkflowInstance>(),second=deferred<WorkflowInstance>(),old=copy();
    mocked.api.mockImplementationOnce(()=>first.promise).mockImplementationOnce(()=>second.promise);
    let earlier!:Promise<void>,later!:Promise<void>;
    act(()=>{earlier=mocked.latest!.refresh();later=mocked.latest!.refresh();});
    const fresh={...copy(),projection:{...record.projection!,revision:8,state:'latest-read'}};
    await act(async()=>{second.resolve(fresh);await later;first.resolve(old);await earlier;});
    expect(screen.getByText('业务状态 latest-read')).toBeTruthy();
  });

  it('does not publish the old initial reload after switching instance ids', async () => {
    const first=deferred<WorkflowInstance>(),old=copy(),original=mocked.api.getMockImplementation()!;
    mocked.api.mockImplementation((path:string,options?:RequestInit)=>path==='/flows/instances/flow-a'?first.promise:original(path,options));
    const view=render(<FlowHost instanceId="flow-a"/>);
    record={...record,id:'flow-b',title:'新实例工作台',drafts:{draft:{value:'新实例草稿'}}};
    view.rerender(<FlowHost instanceId="flow-b"/>);
    expect(await screen.findByDisplayValue('新实例草稿')).toBeTruthy();
    await act(async()=>{first.resolve(old);await first.promise;});
    expect(screen.getByDisplayValue('新实例草稿')).toBeTruthy();
    expect(screen.getByRole('heading',{name:'新实例工作台'})).toBeTruthy();
    expect(screen.queryByDisplayValue('保存的个人草稿')).toBeNull();
    expect(mocked.post).not.toHaveBeenCalled();
  });

  it('locks both composed editors and stale callbacks throughout an explicit reload', async () => {
    definition.page_groups=[{id:'workspace',title:'并排工作区',steps:['draft','other']}];
    record.step_actions={draft:record.actions,other:record.actions};
    render(<FlowHost instanceId="flow-a"/>);
    await screen.findByLabelText('个人输入');
    const staleProps=mocked.latest!,gate=deferred<WorkflowInstance>(),original=mocked.api.getMockImplementation()!;
    mocked.api.mockImplementation((path:string,options?:RequestInit)=>path==='/flows/instances/flow-a'&&!options?.method?gate.promise:original(path,options));
    fireEvent.click(screen.getByRole('button',{name:'重新读取'}));
    expect((screen.getByLabelText('个人输入') as HTMLInputElement).disabled).toBe(true);
    expect((screen.getByLabelText('第二组件输入') as HTMLInputElement).disabled).toBe(true);
    for(const name of ['重新读取','在对话中继续','执行合成动作','执行第二组件','并排工作区'])
      expect((screen.getByRole('button',{name}) as HTMLButtonElement).disabled).toBe(true);
    act(()=>{staleProps.updateDraft({value:'读取过程中不应接受'});});
    await expect(staleProps.execute('save',{value:'also-blocked'})).rejects.toThrow('当前有操作尚未结束');
    await act(async()=>{gate.resolve(copy());await gate.promise;});
    expect(screen.getByDisplayValue('保存的个人草稿')).toBeTruthy();
    expect(screen.queryByDisplayValue('读取过程中不应接受')).toBeNull();
    fireEvent.change(screen.getByLabelText('个人输入'),{target:{value:'读取后的第一份'}});
    fireEvent.change(screen.getByLabelText('第二组件输入'),{target:{value:'读取后的第二份'}});
    fireEvent.click(screen.getByRole('button',{name:'执行第二组件'}));
    await screen.findByText('业务状态 saved');
    expect(record.drafts).toEqual({draft:{value:'读取后的第一份'},other:{value:'读取后的第二份'}});
    expect(mocked.post).toHaveBeenCalledTimes(1);
  });

  it('waits for an admitted autosave before reloading and restores its saved draft', async () => {
    const gate=deferred<WorkflowInstance>(),original=mocked.api.getMockImplementation()!;
    let saved:WorkflowInstance|undefined;
    mocked.api.mockImplementation(async(path:string,options?:RequestInit)=>{
      if(options?.method==='PATCH'){saved=await original(path,options);return gate.promise;}
      return original(path,options);
    });
    render(<FlowHost instanceId="flow-a"/>);
    fireEvent.change(await screen.findByLabelText('个人输入'),{target:{value:'保存中的个人草稿'}});
    await waitFor(()=>expect(saved).toBeDefined());
    const reads=()=>mocked.api.mock.calls.filter(([path,options])=>path==='/flows/instances/flow-a'&&!options?.method).length;
    expect(reads()).toBe(1);
    fireEvent.click(screen.getByRole('button',{name:'重新读取'}));
    expect((screen.getByLabelText('个人输入') as HTMLInputElement).disabled).toBe(true);
    expect(reads()).toBe(1);
    await act(async()=>{gate.resolve(saved!);await gate.promise;});
    await waitFor(()=>expect((screen.getByLabelText('个人输入') as HTMLInputElement).disabled).toBe(false));
    expect(reads()).toBe(2);
    expect(screen.getByDisplayValue('保存中的个人草稿')).toBeTruthy();
    expect(screen.getByText(/草稿修订 2/)).toBeTruthy();
  });

  it('rejects a late directory page after reloading its directory snapshot', async () => {
    record.object_pages=[{object_type:'synthetic',next_cursor:'first-cursor'}];
    render(<FlowHost instanceId="flow-a"/>);
    await screen.findByLabelText('个人输入');
    const gate=deferred<{objects:Data[];next_cursor:null}>();
    mocked.post.mockImplementationOnce(()=>gate.promise);
    const request=mocked.latest!.loadMoreObjects('synthetic').catch(error=>error);
    fireEvent.click(screen.getByRole('button',{name:'重新读取'}));
    await waitFor(()=>expect((screen.getByRole('button',{name:'重新读取'}) as HTMLButtonElement).disabled).toBe(false));
    let failure:unknown;
    await act(async()=>{gate.resolve({objects:[{object_ref:{object_type:'synthetic',object_id:'late'}}],next_cursor:null});failure=await request;});
    expect(failure).toBeInstanceOf(Error);
    expect(String(failure)).toContain('业务目录已更新');
    expect(mocked.latest!.objects).toEqual([]);
  });

  it.each(['current','response'] as const)('rejects a detail page when the %s business revision changes', async changed => {
    render(<FlowHost instanceId="flow-a"/>);
    await screen.findByLabelText('个人输入');
    const before=copy(),gate=deferred<Data>();
    mocked.post.mockImplementationOnce(()=>gate.promise);
    const request=mocked.latest!.detailPage('history').catch(error=>error);
    if(changed==='current'){
      record={...record,projection:{...record.projection!,revision:8,state:'fresh-detail'}};
      await act(async()=>{await mocked.latest!.refresh();});
    }
    let failure:unknown;
    await act(async()=>{gate.resolve({object_ref:before.object_ref,revision:changed==='response'?8:7,items:[{value:'obsolete'}],next_cursor:null,total:1});failure=await request;});
    expect(failure).toBeInstanceOf(Error);
    expect(String(failure)).toContain('业务对象或修订已变化');
    expect(mocked.post).toHaveBeenCalledTimes(1);
  });

  it('composes registered page components, isolates their drafts and moves the cursor before a non-current action', async () => {
    definition.page_groups=[{id:'workspace',title:'合成详情工作区',steps:['draft','other']}];
    record.step_actions={draft:record.actions,other:record.actions};
    render(<FlowHost instanceId="flow-a" surface="page"/>);
    fireEvent.change(await screen.findByLabelText('个人输入'),{target:{value:'第一组件草稿'}});
    fireEvent.change(screen.getByLabelText('第二组件输入'),{target:{value:'第二组件草稿'}});
    fireEvent.click(screen.getByRole('button',{name:'执行第二组件'}));
    expect(await screen.findByText('业务状态 saved')).toBeTruthy();
    expect(record.cursor).toBe('other');
    expect(record.drafts).toEqual({draft:{value:'第一组件草稿'},other:{value:'第二组件草稿'}});
    expect(mocked.post).toHaveBeenCalledTimes(1);
    expect(mocked.post.mock.calls[0][1]).toMatchObject({command:{value:'second-command'},expected_revision:3,expected_object_revision:7});
    expect(screen.getByTestId('shared-draft-step')).toBeTruthy();
    expect(screen.getByTestId('other-step')).toBeTruthy();
  });

  it('keeps conversation to the current shared component even when a multi-component page layout is installed', async () => {
    definition.page_groups=[{id:'workspace',title:'合成详情工作区',steps:['draft','other']}];
    render(<FlowHost instanceId="flow-a" surface="conversation"/>);
    expect(await screen.findByTestId('shared-draft-step')).toBeTruthy();
    expect(screen.queryByTestId('other-step')).toBeNull();
    expect(screen.queryByRole('navigation')).toBeNull();
    expect(mocked.post).not.toHaveBeenCalled();
  });
  it.each(['page', 'conversation'] as const)('restores %s using the same registered component and renders only the current step', async surface => {
    render(<FlowHost instanceId="flow-a" surface={surface} />);
    expect(await screen.findByDisplayValue('保存的个人草稿')).toBeTruthy();
    expect(screen.getByTestId('shared-draft-step')).toBeTruthy();
    expect(screen.queryByTestId('other-step')).toBeNull();
    expect(screen.getByText(surface)).toBeTruthy();
    expect(mocked.post).not.toHaveBeenCalled();
    expect(mocked.api.mock.calls.filter(([, options]) => options?.method === 'PATCH')).toHaveLength(0);
    if (surface === 'conversation') {
      expect(screen.queryByRole('navigation')).toBeNull();
      fireEvent.change(screen.getByLabelText('当前步骤'), { target: { value: 'other' } });
    } else fireEvent.click(screen.getByRole('button', { name: '其他步骤' }));
    expect(await screen.findByTestId('other-step')).toBeTruthy();
    expect(screen.queryByTestId('shared-draft-step')).toBeNull();
    expect(mocked.post).not.toHaveBeenCalled();
  });

  it('flushes a pending draft before action and sends distinct current UI and business revisions', async () => {
    render(<FlowHost instanceId="flow-a" surface="conversation" onChanged={mocked.changes} />);
    fireEvent.change(await screen.findByLabelText('个人输入'), { target: { value: '刚刚修改的草稿' } });
    fireEvent.click(screen.getByRole('button', { name: '执行合成动作' }));
    expect(await screen.findByText('业务状态 saved')).toBeTruthy();
    expect(record.drafts.draft).toEqual({ value: '刚刚修改的草稿' });
    expect(mocked.post).toHaveBeenCalledTimes(1);
    expect(mocked.post.mock.calls[0][1]).toMatchObject({ command: { value: 'explicit-command' }, expected_revision: 2, expected_object_revision: 7 });
    expect(events.indexOf('PATCH /flows/instances/flow-a')).toBeLessThan(events.indexOf('POST /flows/instances/flow-a/actions/save'));
    expect(mocked.changes).toHaveBeenCalledTimes(1);
  });

  it.each(['queued', 'running'])('polls a %s platform run without repeating the command', async status => {
    mocked.post.mockImplementation(async () => {
      record = { ...record, status, revision: 2, run_id: 'synthetic-run' };
      return { instance: copy() };
    });
    render(<FlowHost instanceId="flow-a" />);
    fireEvent.click(await screen.findByRole('button', { name: '执行合成动作' }));
    await waitFor(() => expect((screen.getByRole('button', { name: '执行合成动作' }) as HTMLButtonElement).disabled).toBe(true));
    record = { ...record, status: 'completed', revision: 3, projection: { ...record.projection!, state: 'saved' } };
    expect(await screen.findByText('业务状态 saved', {}, { timeout: 2500 })).toBeTruthy();
    expect(mocked.post).toHaveBeenCalledTimes(1);
  });

  it('accepts a slow background receipt without overlapping automatic polls or replaying commands', async () => {
    vi.useFakeTimers();
    record.status='queued';record.run_id='slow-run';
    render(<FlowHost instanceId="flow-a"/>);
    await act(async()=>{});
    const gate=deferred<WorkflowInstance>(),original=mocked.api.getMockImplementation()!;
    let reads=0;
    mocked.api.mockImplementation((path:string,options?:RequestInit)=>{
      if(path==='/flows/instances/flow-a'&&!options?.method){reads++;return gate.promise;}
      return original(path,options);
    });
    await act(async()=>{await vi.advanceTimersByTimeAsync(5600);});
    expect(reads).toBe(1);
    record={...record,status:'completed',revision:2,projection:{...record.projection!,state:'slow-saved',revision:8}};
    await act(async()=>{gate.resolve(copy());await gate.promise;});
    expect(screen.getByText('业务状态 slow-saved')).toBeTruthy();
    expect(screen.getByText('已取得后台回执，业务状态已更新')).toBeTruthy();
    expect(mocked.post).not.toHaveBeenCalled();
  });

  it.each(['queued', 'running'])('polls an independent %s business job after the platform run is already complete', async state => {
    record.status = 'completed';
    record.projection!.jobs = [{ job_id: 'business-job', state, progress: { completed: 0, total: 1 } }];
    render(<FlowHost instanceId="flow-a" />);
    expect(await screen.findByText(`任务 ${state}`)).toBeTruthy();
    record.projection!.jobs = [{ job_id: 'business-job', state: 'done', progress: { completed: 1, total: 1 } }];
    expect(await screen.findByText('任务 done', {}, { timeout: 2500 })).toBeTruthy();
    expect(mocked.post).not.toHaveBeenCalled();
  });

  it('retries a failed platform run by its existing id without resubmitting an action or new draft', async () => {
    record.status = 'failed'; record.run_id = 'original-run';
    mocked.post.mockImplementation(async (path: string) => {
      expect(path).toBe('/runs/original-run/retry');
      record.status = 'queued';
      return { id: 'original-run', status: 'queued' };
    });
    render(<FlowHost instanceId="flow-a" />);
    fireEvent.change(await screen.findByLabelText('个人输入'), { target: { value: '后续个人草稿' } });
    fireEvent.click(screen.getByRole('button', { name: '重试原请求' }));
    expect(await screen.findByText('正在重试原请求，使用原有输入与幂等标识')).toBeTruthy();
    expect(record.drafts.draft.value).toBe('后续个人草稿');
    expect(mocked.post).toHaveBeenCalledExactlyOnceWith('/runs/original-run/retry');
    expect((screen.getByRole('button', { name: '执行合成动作' }) as HTMLButtonElement).disabled).toBe(true);
  });

  it('uploads raw one-MiB chunks and uses the new business revision after upload creation', async () => {
    const bytes = new Uint8Array(2 * 1024 * 1024 + 13).fill(65);
    const file = syntheticFile(bytes, 'synthetic.bin');
    mocked.file = file;
    const hash = 'dec91b0943e536230122a327f3baf975b3e88b6f9a540526a26e70db9b878ecf';
    const upload = { upload_id: 'upload-real-id', filename: file.name, size: file.size, sha256: hash, chunk_size: 1024 * 1024, uploaded_chunks: [], offset: 0, state: 'uploading' };
    mocked.post.mockImplementation(async (path: string, body: Data) => {
      if (path.endsWith('/uploads')) {
        expect(body.expected_revision).toBe(7);
        expect(body.sha256).toBe(hash);
        record.projection!.revision = 8;
        return { upload };
      }
      if (path.endsWith('/complete')) {
        expect(body.expected_revision).toBe(8);
        record.projection!.revision = 9;
        return { file: { file_id: 'created-file' } };
      }
      throw new Error(`Unexpected POST ${path}`);
    });
    render(<FlowHost instanceId="flow-a" />);
    fireEvent.click(await screen.findByRole('button', { name: '上传合成文件' }));
    expect(await screen.findByText('文件已上传并关联', {}, { timeout: 4000 })).toBeTruthy();
    const chunks = mocked.api.mock.calls.filter(([, options]) => options?.method === 'PUT');
    expect(chunks.map(([path]) => path)).toEqual([0, 1, 2].map(index => `/flows/instances/flow-a/uploads/upload-real-id/chunks/${index}`));
    expect(chunks.map(([, options]) => (options.body as Uint8Array).byteLength)).toEqual([1024 * 1024, 1024 * 1024, 13]);
    for (const [, options] of chunks) {
      expect(options.body).toBeInstanceOf(Uint8Array);
      expect(options.headers['Content-Type']).toBe('application/octet-stream');
      expect(options.headers['X-Chunk-SHA256']).toMatch(/^[0-9a-f]{64}$/);
    }
    expect(mocked.post.mock.calls.some(([path]) => path.includes('/actions/'))).toBe(false);
  });

  it('resumes with upload_id, queries its real chunks, and never creates another session', async () => {
    const bytes = new Uint8Array(1024 * 1024 + 7).fill(66);
    mocked.file = syntheticFile(bytes, 'resume.bin');
    const upload = { upload_id: 'existing-upload', filename: 'resume.bin', size: bytes.length, sha256: '6f99fa4145b10b7ed1c9bfcc8aaba7f905b715d3c417e2c07e866939a2f204ac', chunk_size: 1024 * 1024, uploaded_chunks: [0], offset: 1024 * 1024, state: 'uploading' };
    record.projection!.data.uploads = [upload];
    const previousApi = mocked.api.getMockImplementation()!;
    mocked.api.mockImplementation(async (path: string, options?: RequestInit) => path === '/flows/instances/flow-a/uploads/existing-upload' ? { upload } : previousApi(path, options));
    mocked.post.mockImplementation(async (path: string) => {
      expect(path).toBe('/flows/instances/flow-a/uploads/existing-upload/complete');
      return { file: { file_id: 'resumed-file' } };
    });
    render(<FlowHost instanceId="flow-a" />);
    fireEvent.click(await screen.findByRole('button', { name: '恢复 existing-upload' }));
    expect(await screen.findByText('文件已上传并关联', {}, { timeout: 4000 })).toBeTruthy();
    expect(mocked.api.mock.calls.filter(([, options]) => options?.method === 'PUT').map(([path]) => path)).toEqual(['/flows/instances/flow-a/uploads/existing-upload/chunks/1']);
    expect(mocked.post).toHaveBeenCalledTimes(1);
  });

  it('keeps unsaved input and disables actions after a personal draft revision conflict', async () => {
    const previousApi = mocked.api.getMockImplementation()!;
    mocked.api.mockImplementation(async (path: string, options?: RequestInit) => {
      if (options?.method === 'PATCH') throw new ApiError('revision_conflict', '合成草稿冲突', 409);
      return previousApi(path, options);
    });
    render(<FlowHost instanceId="flow-a" />);
    fireEvent.change(await screen.findByLabelText('个人输入'), { target: { value: '保留这份未保存草稿' } });
    expect(await screen.findByText('合成草稿冲突')).toBeTruthy();
    expect(screen.getByDisplayValue('保留这份未保存草稿')).toBeTruthy();
    expect((screen.getByRole('button', { name: '执行合成动作' }) as HTMLButtonElement).disabled).toBe(true);
    expect(mocked.post).not.toHaveBeenCalled();
  });

  it('keeps saved personal input and disables actions after a business revision conflict until explicit reload', async () => {
    mocked.post.mockRejectedValue(new ApiError('revision_conflict', '合成业务修订冲突', 409));
    render(<FlowHost instanceId="flow-a" />);
    fireEvent.change(await screen.findByLabelText('个人输入'), { target: { value: '个人输入保持不变' } });
    fireEvent.click(screen.getByRole('button', { name: '执行合成动作' }));
    expect(await screen.findByText('合成业务修订冲突')).toBeTruthy();
    expect(screen.getByDisplayValue('个人输入保持不变')).toBeTruthy();
    expect((screen.getByRole('button', { name: '执行合成动作' }) as HTMLButtonElement).disabled).toBe(true);
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: '重新读取' })); });
    await waitFor(() => expect((screen.getByRole('button', { name: '执行合成动作' }) as HTMLButtonElement).disabled).toBe(false));
    expect(mocked.post).toHaveBeenCalledTimes(1);
  });
});
