import type { ComponentType } from 'react';

export type Data = Record<string, any>;
export type ObjectRef = {object_type:string;object_id:string};
export type ObjectProjection = {object_ref:ObjectRef;revision:number;state:string;data:Data;allowed_actions:string[];jobs?:Data[]};
export type WorkflowAction = {id:string;title:string;available:boolean;capability_id?:string;command_schema?:Data;creates_object?:boolean;target_step?:string};
export type WorkflowStep = {id:string;title:string;description:string;component:string;triggers?:string[];actions:WorkflowAction[]};
/** A descriptive contract for the business service's authoritative state machine.
 * This is not the user's navigation cursor and does not execute transitions.
 */
export type WorkflowStateMachine = {id:string;version:string;initial:string;states:{id:string;title:string;terminal?:boolean}[];transitions:{id:string;from:string;to:string;step_id:string;action_id:string;title?:string;guard_description?:string}[]};
export type WorkflowDefinition = {id:string;version:string;title:string;description:string;pack:string;initial_step:string;steps:WorkflowStep[];page_groups?:{id:string;title:string;steps:string[]}[];state_machine?:WorkflowStateMachine;available:boolean};
export type ObjectPageCursor = {object_type:string;next_cursor:string|null};
export type DetailPage = {object_ref:ObjectRef;revision:number;items:Data[];next_cursor:string|null;total:number};
export type WorkflowInstance = {id:string;flow_id:string;flow_version:string;title:string;cursor:string;drafts:Data;revision:number;object_ref:ObjectRef|null;projection:ObjectProjection|null;objects:ObjectProjection[];object_pages?:ObjectPageCursor[];actions:WorkflowAction[];step_actions?:Record<string,WorkflowAction[]>;status:string;error?:unknown;run_id?:string|null;conversation_id:string|null;forked_from:string|null;updated_at:string};

/** Installed business steps share this interface on the page and in conversation.
 * The host owns routing, credentials, upload transport, revisions and UI persistence.
 * Only the business service owns the authoritative object projection.
 */
export type WorkflowStepProps = {
  surface:'page'|'conversation'; projection:ObjectProjection|null; objects:ObjectProjection[];
  draft:Data; updateDraft:(draft:Data)=>void; busy:boolean; actions:WorkflowAction[];
  /** Acceptance may resolve while a background command is still queued. Do not
   * infer business success or clear submitted input until its committed result
   * is visible; updateDraft is ignored while the host is busy. */
  execute:(actionId:string,command:Data)=>Promise<unknown>; refresh:()=>Promise<void>;
  objectPages:ObjectPageCursor[]; loadMoreObjects:(objectType:string)=>Promise<void>;
  detailPage:(section:string,cursor?:string,limit?:number)=>Promise<DetailPage>;
  openObject:(objectId:string)=>Promise<void>; navigate:(stepId:string)=>void;
  uploadFile:(file:File,onProgress?:(received:number,total:number)=>void)=>Promise<unknown>;
  uploadSessions:Data[];
  resumeUpload:(id:string,file:File,onProgress?:(received:number,total:number)=>void)=>Promise<unknown>;
  abortUpload:(id:string)=>Promise<void>;
  jobControl:(id:string,operation:'status'|'result'|'cancel')=>Promise<unknown>;
};
export type WorkflowStepComponents = Record<string,ComponentType<WorkflowStepProps>>;
