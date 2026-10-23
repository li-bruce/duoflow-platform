// @vitest-environment jsdom
import { fireEvent, render, screen, waitFor, cleanup } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { InteractionProps, InteractionInstance } from './sdk';
import InteractionHost from './InteractionHost';

const mocked=vi.hoisted(()=>({api:vi.fn(),post:vi.fn()}));
vi.mock('../api',async original=>({...await original<typeof import('../api')>(),api:mocked.api,post:mocked.post}));
vi.mock('./catalog',()=>({installedComponent:()=>function TestComponent(props:InteractionProps){return <><label>合成文本<input aria-label="合成文本" value={String(props.state.message||'')} disabled={props.busy} onChange={e=>props.updateState({message:e.target.value})}/></label><button disabled={props.busy} onClick={()=>void props.invokeAction('echo')}>合成提交</button><span>{props.surface}</span>{props.result&&<p>{props.result.summary}</p>}</>;}}));
let record:InteractionInstance;
beforeEach(()=>{
  record={id:'instance-a',component_id:'test.echo',component_version:'1.0.0',title:'合成组件',input:{},state:{message:'已保存文本'},revision:1,status:'editing',result:null,actions:[],conversation_id:null,forked_from:null,updated_at:''};
  mocked.api.mockReset();mocked.post.mockReset();
  mocked.api.mockImplementation(async(path:string,options?:RequestInit)=>{
    if(path.endsWith('/catalog'))return {items:[{id:'test.echo',version:'1.0.0'}]};
    if(options?.method==='PATCH'){const body=JSON.parse(String(options.body));record={...record,state:body.state,revision:record.revision+1};}
    return structuredClone(record);
  });
  mocked.post.mockImplementation(async()=>{record={...record,status:'completed',revision:record.revision+1,result:{id:'result',capability_id:'test.echo',invocation_status:'completed',summary:'服务确认完成'}};return {instance:structuredClone(record),result:record.result};});
});
afterEach(()=>{cleanup();vi.restoreAllMocks();});

describe('shared component host',()=>{
  it('restores data without executing an action',async()=>{
    render(<InteractionHost instanceId="instance-a"/>);
    expect(await screen.findByDisplayValue('已保存文本')).toBeTruthy();
    expect(mocked.post).not.toHaveBeenCalled();
  });
  it('flushes edited state before invoking and renders the server result',async()=>{
    render(<InteractionHost instanceId="instance-a" surface="conversation"/>);
    fireEvent.change(await screen.findByLabelText('合成文本'),{target:{value:'新选择'}});
    fireEvent.click(screen.getByText('合成提交'));
    expect(await screen.findByText('服务确认完成')).toBeTruthy();
    expect(record.state).toEqual({message:'新选择'});
    expect(mocked.post.mock.calls[0][1].expected_revision).toBe(2);
    expect(screen.getByText('conversation')).toBeTruthy();
  });
  it('saves continuously edited drafts and blocks actions after a conflict',async()=>{
    mocked.api.mockImplementation(async(path:string,options?:RequestInit)=>{
      if(path.endsWith('/catalog'))return {items:[{id:'test.echo',version:'1.0.0'}]};
      if(options?.method==='PATCH')throw new Error('合成修订冲突');
      return structuredClone(record);
    });
    render(<InteractionHost instanceId="instance-a"/>);
    fireEvent.change(await screen.findByLabelText('合成文本'),{target:{value:'保留未保存内容'}});
    await waitFor(()=>expect(screen.getAllByText('合成修订冲突').length).toBeGreaterThan(0));
    expect((screen.getByText('合成提交') as HTMLButtonElement).disabled).toBe(true);
    expect(mocked.post).not.toHaveBeenCalled();
  });
});
