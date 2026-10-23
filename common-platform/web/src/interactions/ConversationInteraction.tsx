import { useState } from 'react';
import { CopilotKitContext, CopilotKitCoreReact } from '@copilotkit/react-core/v2/context';
import { useRenderTool, useRenderToolCall } from '@copilotkit/react-core/v2/headless';
import { z } from 'zod';
import InteractionHost from './InteractionHost';

const referenceSchema=z.object({instance_id:z.string()});
function RegisteredView({instanceId,onChanged}:{instanceId:string;onChanged?:()=>void}){
  useRenderTool({name:'platform.interaction',parameters:referenceSchema,render:({parameters})=>parameters.instance_id?<InteractionHost instanceId={parameters.instance_id} surface="conversation" onChanged={onChanged}/>:null},[onChanged]);
  const render=useRenderToolCall();
  return render({toolCall:{id:instanceId,type:'function',function:{name:'platform.interaction',arguments:JSON.stringify({instance_id:instanceId})}},toolMessage:{id:'render-'+instanceId,role:'tool',toolCallId:instanceId,content:'The registered interaction is available; business execution is managed by the platform.'}});
}
/** CopilotKit is the tool-rendering adapter only. Login, models, actions and persistence stay in the host. */
export default function ConversationInteraction(props:{instanceId:string;onChanged?:()=>void}){
  // This is an offline renderer registry, not a second agent/runtime connection.
  // The full CopilotKitProvider requires an actual runtime or cloud connection
  // in production. Its public context/core API supports our rendering-only use.
  const [context]=useState(()=>({copilotkit:new CopilotKitCoreReact({}),executingToolCallIds:new Set<string>()}));
  return <CopilotKitContext.Provider value={context}><RegisteredView {...props}/></CopilotKitContext.Provider>;
}
