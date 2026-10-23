import type { ComponentType } from 'react';
import type { Data, Result } from '../types';

export type InteractionAction = {id:string;title:string;capability_id:string;capability_version:string;completes:boolean;available:boolean};
export type InteractionDefinition = {id:string;version:string;title:string;description:string;pack:string;available:boolean;input_schema:Data;state_schema:Data;initial_state:Data;actions:InteractionAction[]};
export type InteractionInstance = {id:string;component_id:string;component_version:string;title:string;input:Data;state:Data;revision:number;status:string;result:Result|null;run_id?:string|null;error?:unknown;actions:InteractionAction[];conversation_id:string|null;forked_from:string|null;updated_at:string};

/** Supplier API. Business UI consumes this context; it never owns credentials or chat state. */
export type InteractionProps = {
  input:Data;
  state:Data;
  result:Result|null;
  surface:'page'|'conversation';
  busy:boolean;
  actions:InteractionAction[];
  updateState:(state:Data)=>void;
  invokeAction:(actionId:string)=>Promise<void>;
};
export type InteractionComponent = ComponentType<InteractionProps>;
