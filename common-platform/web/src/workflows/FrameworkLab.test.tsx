// @vitest-environment jsdom
import {cleanup,fireEvent,render,screen,waitFor} from '@testing-library/react';
import {afterEach,describe,expect,it,vi} from 'vitest';
import {stepComponents} from '../../../flows/packs/framework-lab/steps';
import type {WorkflowStepProps} from './sdk';

function propsFor(surface:'page'|'conversation'='page'):WorkflowStepProps {
  return {surface,projection:null,objects:[],draft:{},busy:false,actions:[],objectPages:[],
    updateDraft:vi.fn(),execute:vi.fn().mockResolvedValue(undefined),refresh:vi.fn().mockResolvedValue(undefined),
    loadMoreObjects:vi.fn(),detailPage:vi.fn(),openObject:vi.fn().mockResolvedValue(undefined),navigate:vi.fn(),
    uploadFile:vi.fn(),uploadSessions:[],resumeUpload:vi.fn(),abortUpload:vi.fn(),jobControl:vi.fn()};
}
const item = {object_ref:{object_type:'framework-lab.item',object_id:'one'},revision:2,state:'draft',
  data:{title:'共享组件测试',body:'已保存的工作项说明'},allowed_actions:['edit','submit']};
const actions = (...ids:string[])=>ids.map(id=>({id,title:id,available:true}));
afterEach(cleanup);

describe('neutral module uses one shared workflow component set',()=>{
  it.each(['page','conversation'] as const)('creates using the same SDK action on %s',async surface=>{
    const props=propsFor(surface);props.actions=actions('create');props.draft={title:'本人的新工作项',body:'中立说明'};
    const Picker=stepComponents.Picker;
    render(<Picker {...props}/>);
    fireEvent.click(screen.getByRole('button',{name:'创建工作项'}));
    await waitFor(()=>expect(props.execute).toHaveBeenCalledExactlyOnceWith('create',props.draft));
    expect(props.updateDraft).not.toHaveBeenCalled();
  });

  it.each(['page','conversation'] as const)('requires saving draft before submit on %s',async surface=>{
    const props=propsFor(surface);props.actions=actions('edit','submit');props.projection=item;
    props.draft={title:'个人草稿标题',body:'个人草稿说明'};
    const Editor=stepComponents.Editor;const view=render(<Editor {...props}/>);
    expect((screen.getByRole('button',{name:'提交工作项'}) as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(screen.getByRole('button',{name:'保存工作项'}));
    await waitFor(()=>expect(props.execute).toHaveBeenCalledExactlyOnceWith('edit',props.draft));
    await waitFor(()=>expect((screen.getByRole('button',{name:'保存工作项'}) as HTMLButtonElement).disabled).toBe(false));
    view.rerender(<Editor {...props} projection={{...item,revision:3,data:{...item.data,...props.draft}}}/>);
    fireEvent.click(screen.getByRole('button',{name:'提交工作项'}));
    await waitFor(()=>expect(props.execute).toHaveBeenLastCalledWith('submit',{}));
  });

  it('shows service state and feedback, navigation never executes a transition',()=>{
    const props=propsFor();props.projection={...item,state:'returned',data:{...item.data,feedback:'请补充说明'}};
    const Inspector=stepComponents.Inspector;render(<Inspector {...props}/>);
    expect(screen.getByText('已退回')).toBeTruthy();
    expect(screen.getByText('处理反馈：请补充说明')).toBeTruthy();
    fireEvent.click(screen.getByRole('button',{name:'继续填写'}));
    expect(props.navigate).toHaveBeenCalledExactlyOnceWith('edit');
    expect(props.execute).not.toHaveBeenCalled();
    expect((screen.getByRole('button',{name:'模拟完成'}) as HTMLButtonElement).disabled).toBe(true);
  });

  it('calls a declared action with note and handles revision failures',async()=>{
    const props=propsFor('conversation');props.projection={...item,state:'submitted'};
    props.actions=actions('return','complete');props.draft={note:'需要重新修改'};
    props.execute=vi.fn().mockRejectedValue(new Error('工作项已更新，请重新读取后操作。'));
    const Inspector=stepComponents.Inspector;render(<Inspector {...props}/>);
    fireEvent.click(screen.getByRole('button',{name:'模拟退回'}));
    expect(await screen.findByRole('alert')).toHaveProperty('textContent','工作项已更新，请重新读取后操作。');
    expect(props.execute).toHaveBeenCalledExactlyOnceWith('return',{note:'需要重新修改'});
    expect(props.navigate).not.toHaveBeenCalled();
    expect(screen.getByText('已提交')).toBeTruthy();
  });

  it('opens the server object before changing visible step',async()=>{
    const props=propsFor();props.objects=[{...item,state:'completed'}];
    const Picker=stepComponents.Picker;render(<Picker {...props}/>);
    fireEvent.click(screen.getByRole('button',{name:/共享组件测试 · 已完成/}));
    await waitFor(()=>expect(props.openObject).toHaveBeenCalledExactlyOnceWith('one'));
    await waitFor(()=>expect(props.navigate).toHaveBeenCalledExactlyOnceWith('inspect'));
    expect(props.execute).not.toHaveBeenCalled();
  });
});
