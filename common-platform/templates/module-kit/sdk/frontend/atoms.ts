/** Stable UI identities shared by page, conversation and the runtime graph.
 * These definitions describe component contracts; authorization and business
 * state transitions remain server responsibilities.
 */
export type AtomSchema = Record<string, unknown>;
export type AtomicHost = {
  kind: "interaction" | "workflow_step";
  id: string;
  version: string;
  step_id?: string;
};
export type AtomicEvent = {
  id: string;
  title: string;
  input_schema: AtomSchema;
  output_schema: AtomSchema;
  effect: string;
  state_effect: string;
  capability?: { id: string; version: string };
};
export type FrontendAtom = {
  id: string;
  kind: "frontend";
  title: string;
  description: string;
  host: AtomicHost;
  input_schema: AtomSchema;
  output_schema: AtomSchema;
  state_schema: AtomSchema;
  initial_state: Record<string, unknown>;
  events: AtomicEvent[];
};
export type AtomNodeMapping = {
  atom_id: string;
  node_id: string;
  role: "handles" | "shows" | "reads" | "writes";
  event_id?: string;
};

/** Bind concrete components once. No downloaded code or implicit name matching. */
export function registerAtomicComponents<T>(
  definitions: FrontendAtom[],
  components: Record<string, T>,
) {
  const table = new Map<string, { definition: FrontendAtom; component: T }>();
  for (const definition of definitions) {
    if (table.has(definition.id) || !Object.hasOwn(components, definition.id)) {
      throw new Error("组件登记重复，或没有对应的实际组件。");
    }
    table.set(definition.id, {
      definition: structuredClone(definition),
      component: components[definition.id],
    });
  }
  if (table.size !== Object.keys(components).length) {
    throw new Error("实际组件中存在未登记项。");
  }
  return {
    get(id: string) {
      const entry = table.get(id);
      if (!entry) throw new Error("组件未登记。");
      return {
        definition: structuredClone(entry.definition),
        component: entry.component,
      };
    },
    list() {
      return [...table.values()].map((entry) =>
        structuredClone(entry.definition),
      );
    },
  };
}

/** A host resolves a definition by its full binding, never by a translated title. */
export function atomForHost(atoms: FrontendAtom[], host: AtomicHost) {
  const matches = atoms.filter(
    (atom) =>
      atom.host.kind === host.kind &&
      atom.host.id === host.id &&
      atom.host.version === host.version &&
      atom.host.step_id === host.step_id,
  );
  if (matches.length > 1) throw new Error("同一宿主重复登记。");
  return matches[0] ?? null;
}
