import {useRef, useState} from 'react';
import type {WorkflowStepProps, WorkflowStepComponents} from '@platform/workflow-sdk';
import definition from './flow.json';

/** Both hosts mount these exact components. The SDK owns requests and identity. */
function NoteForm(props:WorkflowStepProps & {event:'create'|'edit'}) {
  const source=props.event==='edit' ? props.projection?.data : null;
  const title=String(props.draft.title ?? source?.title ?? '');
  const body=String(props.draft.body ?? source?.body ?? '');
  const allowed=props.actions.find(action=>action.id===props.event)?.available;
  return <section>
    <label>标题<input aria-label="标题" value={title} disabled={props.busy}
      onChange={event=>props.updateDraft({...props.draft,title:event.target.value})}/></label>
    <label>内容<textarea aria-label="内容" value={body} disabled={props.busy}
      onChange={event=>props.updateDraft({...props.draft,body:event.target.value})}/></label>
    <button type="button" disabled={props.busy||!allowed||!title.trim()}
      onClick={()=>void props.execute(props.event,{title,body})}>{props.event==='create'?'创建记录':'保存记录'}</button>
  </section>;
}

function Picker(props:WorkflowStepProps) {
  const objectType=definition.flows[0].provider.object_type;
  const records=props.objects.filter(item=>item.object_ref.object_type===objectType);
  const hasMore=props.objectPages.some(page=>page.object_type===objectType&&page.next_cursor);
  const [loading,setLoading]=useState(false),[error,setError]=useState('');
  const requested=useRef(false);
  async function loadMore(){
    if(props.busy||requested.current||!hasMore)return;
    requested.current=true;setLoading(true);setError('');
    try{await props.loadMoreObjects(objectType);}
    catch(error){setError(error instanceof Error?error.message:'记录加载失败，请重试。');}
    finally{requested.current=false;setLoading(false);}
  }
  return <section><h3>本人记录</h3>
    {records.map(item=><button type="button" key={item.object_ref.object_id} disabled={props.busy}
      onClick={()=>void props.openObject(item.object_ref.object_id)}>{String(item.data.title)}</button>)}
    <p>已加载 {records.length} 条记录</p>
    {hasMore&&<button type="button" disabled={props.busy||loading} onClick={()=>void loadMore()}>
      {loading?'正在加载记录…':'加载更多记录'}</button>}
    {loading&&<p role="status">正在读取下一页记录…</p>}
    {error&&<p role="alert">{error}</p>}
    {props.projection&&<button type="button" disabled={props.busy} onClick={()=>props.navigate('edit')}>编辑所选记录</button>}
    <h3>创建记录</h3><NoteForm {...props} event="create"/>
  </section>;
}

function Editor(props:WorkflowStepProps) {
  return props.projection ? <section>
    <p>当前对象修订：{props.projection.revision}</p><NoteForm {...props} event="edit"/>
    <button type="button" disabled={props.busy} onClick={()=>props.navigate('pick')}>返回记录列表</button>
  </section> : <p>请先在记录列表选择对象。</p>;
}

export const stepComponents:WorkflowStepComponents={Picker,Editor};
