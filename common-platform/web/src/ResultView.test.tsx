// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import Workbench from './Workbench';
import ResultView from './ResultView';
import type { UIContext } from './ResultView';
import type { Capability, Data, Registry, Result } from './types';

const transport=vi.hoisted(()=>({api:vi.fn(),post:vi.fn()}));
vi.mock('./api',async original=>({...await original<typeof import('./api')>(),api:transport.api,post:transport.post}));

const timestamp='2026-01-01T10:00:00Z';
const cap=(id:string,title:string):Capability=>({id,version:'1.0.0',module_id:'example',title,description:'合成连续后台验收',kind:'command',roles:['member'],execution:{mode:'background',confirmation:false},input_schema:{type:'object',properties:{text:{type:'string',title:'合成正文',default:'合成初始内容'}},required:['text']},output_schema:{type:'object'},presentation:{page:{renderer:'workspace'},conversation:{renderer:'workspace'}}});
const first=cap('example.start','首项操作'),next=cap('example.next','后续操作');
const registry:Registry={capabilities:[first,next],renderers:[],modules:[{id:'example',version:'1.0.0',name:'合成流程',description:'两项连续后台操作',source:'local',capabilities:[first,next],surfaces:{page_tools:[{id:'entry',title:'合成页面',description:'结果内继续办理',kind:'form',capability_ids:[first.id]}],conversation:{capability_ids:[first.id,next.id],starter_prompts:[]}}}]};
const context=():UIContext=>({user:{id:'synthetic-user',name:'合成使用者',username:'synthetic-user',role:'member'},modules:registry.modules,capabilities:registry.capabilities,onChanged:vi.fn()});
function queued(id:string,capabilityId:string,view=true):Result{return {id:'invocation-'+id,capability_id:capabilityId,invocation_status:'accepted',run_id:id,run_status:'queued',summary:'合成任务已排队',views:view?[{type:'task',data:{run_id:id}}]:[]};}

beforeEach(()=>{vi.clearAllMocks();});
afterEach(()=>{cleanup();vi.useRealTimers();});

describe('result task identity',()=>{
  it('replaces the first task after a next-action submit and polls the new run to completion',async()=>{
    vi.useFakeTimers({toFake:['setInterval','clearInterval']});
    const calls:Record<string,number>={};
    transport.api.mockImplementation(async(path:string)=>{
      if(!path.startsWith('/runs/'))throw Error('Unexpected request '+path);
      const id=path.slice('/runs/'.length);calls[id]=(calls[id]||0)+1;
      const done=calls[id]>1;
      const result:Result=id==='first-run'?{id:'first-result',capability_id:first.id,invocation_status:'completed',summary:'第一项已完成',views:[{type:'workspace',data:{blocks:[{type:'text',text:'第一项独有结果'},{type:'actions',title:'后续动作区',items:[{label:'办理下一步',capability_id:next.id,input:{text:'第一项提供的合成预填'}}]}]}}]}:{id:'second-result',capability_id:next.id,invocation_status:'completed',summary:'第二项已完成',views:[{type:'text-result',data:{text:'第二项独有结果'}}]};
      return {id,capability_id:id==='first-run'?first.id:next.id,status:done?'succeeded':'queued',input:{},attempt:1,progress:done?100:0,created_at:timestamp,updated_at:timestamp,...(done?{result}:{})};
    });
    transport.post.mockImplementation(async(path:string,body:Data)=>{
      expect(path).toBe('/invoke');
      if(body.capability_id===first.id)return queued('first-run',first.id);
      expect(body.capability_id).toBe(next.id);
      expect(body.input).toEqual({text:'第一项提供的合成预填'});
      return queued('second-run',next.id);
    });
    render(<Workbench registry={registry} context={context()}/>);
    fireEvent.click(screen.getByRole('button',{name:'运行能力'}));
    await waitFor(()=>expect(calls['first-run']).toBe(1));
    await act(async()=>{await vi.advanceTimersByTimeAsync(1700);});
    await screen.findByText('第一项独有结果');
    fireEvent.click(screen.getByRole('button',{name:'办理下一步'}));
    const actionRegion=screen.getByRole('region',{name:'后续动作区'});
    expect((within(actionRegion).getByLabelText(/合成正文/) as HTMLInputElement).value).toBe('第一项提供的合成预填');
    fireEvent.click(within(actionRegion).getByRole('button',{name:'运行能力'}));
    await waitFor(()=>expect(calls['second-run']).toBe(1));
    expect(screen.queryByText('第一项独有结果')).toBeNull();
    await act(async()=>{await vi.advanceTimersByTimeAsync(1700);});
    await screen.findByText('第二项独有结果');
    expect(calls).toEqual({'first-run':2,'second-run':2});
    expect(transport.post).toHaveBeenCalledTimes(2);
  });

  it('rebinds a fallback run_id without a task view when the result prop changes',async()=>{
    transport.api.mockImplementation(async(path:string)=>({id:path.split('/').at(-1),capability_id:first.id,status:'succeeded',input:{},attempt:1,progress:100,created_at:timestamp,updated_at:timestamp,result:{id:path,capability_id:first.id,invocation_status:'completed',summary:'合成已完成',views:[{type:'text-result',data:{text:'当前任务结果 '+path}}]}}));
    const ui=context();
    const element=render(<ResultView result={queued('old-fallback',first.id,false)} context={ui}/>);
    await screen.findByText('当前任务结果 /runs/old-fallback');
    element.rerender(<ResultView result={queued('new-fallback',first.id,false)} context={ui}/>);
    await screen.findByText('当前任务结果 /runs/new-fallback');
    expect(screen.queryByText('当前任务结果 /runs/old-fallback')).toBeNull();
    expect(transport.api).toHaveBeenCalledWith('/runs/new-fallback');
  });
});
