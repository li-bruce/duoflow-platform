import type { JsonSchema } from "@jsonforms/core";

/** Public boundaries resolved by the platform from the admitted module contracts. */
export type PublicFlow = {
  id: string;
  version: string;
  title: string;
  description: string;
  entry: {
    kind: "capability" | "workflow";
    id: string;
    version: string;
    completed_states?: string[];
  };
  input_schema: JsonSchema;
  output_schema: JsonSchema;
  node_ids: string[];
  required_permissions: string[];
  data_access: { object_id: string; operations: string[] }[];
  capabilities: {
    id: string;
    version: string;
    kind: string;
    permission?: string;
    roles: string[];
  }[];
  role_templates: {
    id: string;
    title: string;
    permissions: string[];
    required_scope: string[];
  }[];
};

export type BusinessModule = {
  module_id: string;
  title: string;
  description: string;
  version: string;
  source: "local" | "remote";
  service_id?: string;
  contract_digest: string;
  flows: PublicFlow[];
};
