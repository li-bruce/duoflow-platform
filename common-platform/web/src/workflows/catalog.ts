import { lazy } from 'react';
import type { WorkflowDefinition, WorkflowStepComponents } from './sdk';
const loaders=import.meta.glob<{stepComponents:WorkflowStepComponents}>('../../../flows/packs/*/steps.tsx');
const cache=new Map<string,ReturnType<typeof lazy>>();
export function installedStep(definition:WorkflowDefinition,component:string){
  const key=`${definition.pack}:${component}`;
  if(!cache.has(key)){
    const loader=loaders[`../../../flows/packs/${definition.pack}/steps.tsx`];
    if(!loader)throw new Error('流程组件包尚未构建。');
    cache.set(key,lazy(async()=>{const module=await loader();const step=module.stepComponents[component];if(!step)throw new Error('此步骤组件未安装。');return {default:step};}));
  }
  return cache.get(key)! as ReturnType<typeof lazy<WorkflowStepComponents[string]>>;
}
