// @vitest-environment jsdom
// Exercise the real portable template through synthetic host SDK responses.
import {act, cleanup, fireEvent, render, screen, waitFor} from '@testing-library/react';
import {afterEach, describe, expect, it, vi} from 'vitest';
import {stepComponents} from '../../../templates/workflow-module/flow/steps';
import definition from '../../../templates/workflow-module/flow/flow.json';
import type {ObjectProjection, WorkflowStepProps} from './sdk';

const Picker=stepComponents.Picker;
const objectType=definition.flows[0].provider.object_type;
const note=(number:number):ObjectProjection=>({object_ref:{object_type:objectType,object_id:'note-'+number},
  revision:1,state:'draft',data:{title:'记录 '+number},allowed_actions:['edit']});
function propsFor():WorkflowStepProps{
  return {surface:'page',projection:null,objects:Array.from({length:20},(_,index)=>note(index+1)),
    draft:{title:'未提交标题',body:'未提交正文'},busy:false,actions:[],objectPages:[{object_type:objectType,next_cursor:'opaque-page'}],
    updateDraft:vi.fn(),execute:vi.fn(),refresh:vi.fn(),loadMoreObjects:vi.fn(),detailPage:vi.fn(),openObject:vi.fn(),
    navigate:vi.fn(),uploadFile:vi.fn(),uploadSessions:[],resumeUpload:vi.fn(),abortUpload:vi.fn(),jobControl:vi.fn()};
}
afterEach(()=>cleanup());

describe('native workflow template directory',()=>{
  it.each(['page','conversation'] as const)('loads past twenty records on %s without writing personal drafts',async surface=>{
    const props=propsFor();props.surface=surface;
    let resolve!:()=>void;
    props.loadMoreObjects=vi.fn(()=>new Promise<void>(done=>{resolve=done;}));
    const view=render(<Picker {...props}/>);
    expect(screen.getByText('已加载 20 条记录')).toBeTruthy();
    const button=screen.getByRole('button',{name:'加载更多记录'});
    fireEvent.click(button);fireEvent.click(button);
    expect(props.loadMoreObjects).toHaveBeenCalledExactlyOnceWith(objectType);
    expect((screen.getByRole('button',{name:'正在加载记录…'}) as HTMLButtonElement).disabled).toBe(true);
    expect(screen.getByRole('status').textContent).toContain('读取下一页');
    await act(async()=>{
      view.rerender(<Picker {...props} objects={[...props.objects,note(21)]} objectPages={[{object_type:objectType,next_cursor:null}]}/>);
      resolve();
    });
    expect(screen.getByRole('button',{name:'记录 21'})).toBeTruthy();
    expect(screen.getByText('已加载 21 条记录')).toBeTruthy();
    expect(screen.queryByRole('button',{name:'加载更多记录'})).toBeNull();
    expect(screen.queryByRole('status')).toBeNull();
    expect(screen.getByDisplayValue('未提交标题')).toBeTruthy();
    expect(screen.getByDisplayValue('未提交正文')).toBeTruthy();
    expect(props.updateDraft).not.toHaveBeenCalled();
    expect(props.execute).not.toHaveBeenCalled();
    expect(props.openObject).not.toHaveBeenCalled();
  });

  it('shows a page failure and supports an explicit retry without losing loaded records',async()=>{
    const props=propsFor();props.loadMoreObjects=vi.fn().mockRejectedValueOnce(new Error('目录已变化，请重读后重试。')).mockResolvedValueOnce(undefined);
    render(<Picker {...props}/>);
    fireEvent.click(screen.getByRole('button',{name:'加载更多记录'}));
    expect(await screen.findByRole('alert')).toHaveProperty('textContent','目录已变化，请重读后重试。');
    expect(screen.getByRole('button',{name:'记录 20'})).toBeTruthy();
    fireEvent.click(screen.getByRole('button',{name:'加载更多记录'}));
    await waitFor(()=>expect(props.loadMoreObjects).toHaveBeenCalledTimes(2));
    await waitFor(()=>expect(screen.queryByRole('alert')).toBeNull());
    expect(props.updateDraft).not.toHaveBeenCalled();
  });

  it('respects host busy state and uses only the type declared by this template provider',()=>{
    const props=propsFor();
    const view=render(<Picker {...props} busy/>);
    const button=screen.getByRole('button',{name:'加载更多记录'});
    expect((button as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(button);expect(props.loadMoreObjects).not.toHaveBeenCalled();
    view.rerender(<Picker {...props} objects={[...props.objects,{...note(99),object_ref:{object_type:'other.type',object_id:'other'}}]}
      objectPages={[{object_type:objectType,next_cursor:null},{object_type:'other.type',next_cursor:'other-cursor'}]}/>);
    expect(screen.queryByRole('button',{name:'加载更多记录'})).toBeNull();
    expect(screen.queryByRole('button',{name:'记录 99'})).toBeNull();
    expect(screen.getByText('已加载 20 条记录')).toBeTruthy();
  });
});
