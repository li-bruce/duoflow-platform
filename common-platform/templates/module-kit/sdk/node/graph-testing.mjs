/** Assertions over actual handler results; no transition is executed here. */
import assert from "node:assert/strict";

export function assertTransition(graph, machineId, before, event, after) {
  const machines = graph.state_machines.filter((item) => item.id === machineId);
  assert.equal(machines.length, 1, `Expected one state machine: ${machineId}`);
  assert.deepEqual(
    before.object_ref,
    after.object_ref,
    "The object changed between reads",
  );
  assert.ok(
    machines[0].transitions.some(
      (edge) =>
        edge.from === before.state &&
        edge.event === event &&
        edge.to === after.state,
    ),
    `Observed transition is absent from the graph: ${before.state} -> ${event} -> ${after.state}`,
  );
}

/** Supply the same Schema validator used by the application or test harness. */
export function assertAtomResult(
  graph,
  atomId,
  inputs,
  output,
  validate,
  manifest = null,
) {
  const atoms = graph.atoms.filter((item) => item.id === atomId);
  assert.equal(atoms.length, 1, `Expected one atom: ${atomId}`);
  let contract = atoms[0];
  if (contract.kind === "backend") {
    const matching = (manifest?.capabilities || []).filter(
      (capability) =>
        capability.id === contract.capability.id &&
        capability.version === contract.capability.version,
    );
    assert.equal(
      matching.length,
      1,
      "Backend atom requires its actual manifest capability",
    );
    contract = matching[0];
  }
  validate(contract.input_schema, inputs);
  validate(contract.output_schema, output);
}
