import type { InteractionProps } from '@platform/interaction-sdk';

/** The same component is mounted by both hosts. No login, chat SDK or direct fetch here. */
export default function EchoComponent({state,result,busy,actions,updateState,invokeAction}:InteractionProps){
  const canRun=actions.find(action=>action.id==='echo')?.available;
  return <div>
    <label>消息<input type="text" aria-label="消息" value={String(state.message||'')} disabled={busy} onChange={event=>updateState({...state,message:event.target.value})}/></label>
    <button type="button" className="button primary" disabled={busy||!canRun||!String(state.message||'').trim()} onClick={()=>void invokeAction('echo')}>提交消息</button>
    {result&&<p role="status">{String(result.data?.text||'')}</p>}
  </div>;
}
