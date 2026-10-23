import { useState } from 'react';
import { CopilotKitContext, CopilotKitCoreReact } from '@copilotkit/react-core/v2/context';
import { useRenderTool, useRenderToolCall } from '@copilotkit/react-core/v2/headless';
import { z } from 'zod';
import FlowHost from './FlowHost';
const schema=z.object({instance_id:z.string()});
function Registered(props:{instanceId:string;onChanged?:()=>void}){
  useRenderTool({name:'platform.workflow',parameters:schema,render:({parameters})=>parameters.instance_id?<FlowHost instanceId={parameters.instance_id} surface="conversation" onChanged={props.onChanged}/>:null},[props.onChanged]);
  return useRenderToolCall()({toolCall:{id:props.instanceId,type:'function',function:{name:'platform.workflow',arguments:JSON.stringify({instance_id:props.instanceId})}},toolMessage:{id:'render-'+props.instanceId,role:'tool',toolCallId:props.instanceId,content:'Render current workflow step; navigation never executes a business command.'}});
}
export default function ConversationWorkflow(props:{instanceId:string;onChanged?:()=>void}){const [context]=useState(()=>({copilotkit:new CopilotKitCoreReact({}),executingToolCallIds:new Set<string>()}));return <CopilotKitContext.Provider value={context}><Registered {...props}/></CopilotKitContext.Provider>;}
