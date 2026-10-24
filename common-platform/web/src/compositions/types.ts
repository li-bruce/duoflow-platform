import type { JsonSchema } from "@jsonforms/core";
import type { Data, View } from "../types";

export type Binding =
  | { source: "input"; path: string[] }
  | { source: "step"; step_id: string; path: string[] }
  | { source: "literal"; value: unknown };
export type CompositionStep = {
  id: string;
  title: string;
  module_id: string;
  flow_id: string;
  flow_version: string;
  bindings: Record<string, Binding>;
};
export type CompositionRole = {
  id: string;
  title: string;
  bindings: {
    module_id: string;
    role_id: string;
    scope: Record<string, string>;
  }[];
};
export type CompositionDocument = {
  composition_version: "1.0";
  id: string;
  version: string;
  title: string;
  description: string;
  input_schema: JsonSchema;
  steps: CompositionStep[];
  roles: CompositionRole[];
};
export type SavedComposition = {
  id: string;
  document: CompositionDocument;
  fingerprint: string;
  shared?: boolean;
  owned?: boolean;
};
export type CompositionRun = {
  id: string;
  definition_id: string;
  title: string;
  status: string;
  revision: number;
  input: Data;
  content_available: boolean;
  error?: { code: string; message: string };
  steps: {
    id: string;
    title: string;
    module_id: string;
    flow_id: string;
    status: string;
    input?: Data;
    output?: Data;
    invocation_id?: string;
    view?: View;
    error?: { code: string; message: string };
  }[];
};

export const statusLabel: Record<string, string> = {
  pending: "等待前一步",
  ready: "准备继续",
  opening: "正在打开",
  dispatching: "正在调用",
  confirming: "等待确认",
  waiting: "等待业务办理",
  waiting_run: "等待任务结果",
  waiting_service: "等待服务恢复",
  succeeded: "已完成",
  blocked: "需要重新检查",
  failed: "执行失败",
  unknown: "需要核对调用结果",
};

/** Return declared fields only. Paths are data selectors, never expressions. */
export function schemaFields(
  schema: JsonSchema,
  prefix: string[] = [],
): { path: string[]; title: string; schema: JsonSchema }[] {
  return Object.entries(schema.properties || {}).flatMap(([key, value]) => {
    const field = value as JsonSchema,
      path = [...prefix, key];
    return [
      { path, title: String(field.title || path.join(" · ")), schema: field },
      ...schemaFields(field, path),
    ];
  });
}
