import { lazy } from 'react';
import type { InteractionComponent, InteractionDefinition } from './sdk';

// Only locally installed, reviewed packs enter this build. API responses cannot import URLs.
const loaders=import.meta.glob<{default:InteractionComponent}>('../../../components/packs/*/Component.tsx');
const definitions=import.meta.glob<Record<string,unknown>>('../../../components/packs/*/interaction.json',{eager:true,import:'default'});
const cache=new Map<string,ReturnType<typeof lazy<InteractionComponent>>>();
export function installedComponent(definition:InteractionDefinition){
  const base=`../../../components/packs/${definition.pack}/`;
  const metadata=definitions[base+'interaction.json'];
  const loader=loaders[base+'Component.tsx'];
  if(!loader||metadata?.id!==definition.id||metadata?.version!==definition.version)throw new Error('组件前端尚未安装，或与服务端版本不一致。请重新构建平台前端。');
  const key=definition.id+'@'+definition.version;
  if(!cache.has(key))cache.set(key,lazy(loader));
  return cache.get(key)!;
}
