/** Associate actual dispatch functions with stable atom identities.
 * Structural and host validation belongs to module_kit check/admission. This
 * startup binding check never invokes a handler or grants user permissions.
 */
export function createAtomBindings({ graph, manifest, handlers }) {
  const need = (value) => {
    if (!value)
      throw Object.assign(new Error("原子登记与实际函数绑定不一致。"), {
        code: "atomic_binding",
        status: 422,
      });
  };
  need(
    graph?.graph_version === "1.0" &&
      graph.module_id === manifest.module.id &&
      graph.module_version === manifest.module.version,
  );
  const capabilities = new Map(
    manifest.capabilities.map((cap) => [cap.id, cap]),
  );
  const backends = graph.atoms.filter((atom) => atom.kind === "backend");
  const bindings = new Map(
    backends.map((atom) => [atom.capability.id, structuredClone(atom)]),
  );
  need(
    bindings.size === backends.length &&
      bindings.size === capabilities.size &&
      Object.keys(handlers).length === capabilities.size,
  );
  for (const [id, capability] of capabilities) {
    need(
      Object.hasOwn(handlers, id) &&
        typeof handlers[id] === "function" &&
        bindings.get(id)?.capability.version === capability.version,
    );
  }
  const document = structuredClone(graph);
  return Object.freeze({
    get document() {
      return structuredClone(document);
    },
    forCapability(id) {
      need(bindings.has(id));
      return structuredClone(bindings.get(id));
    },
  });
}
