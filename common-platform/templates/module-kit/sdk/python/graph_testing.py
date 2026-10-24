"""Assertions over observed results. These helpers never execute business transitions."""


def assert_transition(graph, machine_id, before, event, after):
    """Compare independent before/after projections with a declared state edge.

    Call the real module handler between the two reads. Authorization, data
    effects and revision rules still require separate business assertions.
    """
    machines = [item for item in graph["state_machines"] if item["id"] == machine_id]
    if len(machines) != 1:
        raise AssertionError("Expected one declared state machine: " + machine_id)
    if before.get("object_ref") != after.get("object_ref"):
        raise AssertionError("Before and after refer to different business objects")
    edge = (before.get("state"), event, after.get("state"))
    if not any(
        (item["from"], item["event"], item["to"]) == edge for item in machines[0]["transitions"]
    ):
        raise AssertionError(f"Observed transition is absent from the graph: {edge!r}")


def assert_atom_result(graph, atom_id, inputs, output, validate, *, manifest=None):
    """Use the project's Schema validator on results returned by the real handler."""
    atoms = [item for item in graph["atoms"] if item["id"] == atom_id]
    if len(atoms) != 1:
        raise AssertionError("Expected one declared atom: " + atom_id)
    contract = atoms[0]
    if contract["kind"] == "backend":
        capabilities = (manifest or {}).get("capabilities", [])
        matching = [
            item
            for item in capabilities
            if item["id"] == contract["capability"]["id"]
            and item["version"] == contract["capability"]["version"]
        ]
        if len(matching) != 1:
            raise AssertionError("Backend atom requires its actual manifest capability")
        contract = matching[0]
    validate(contract["input_schema"], inputs)
    validate(contract["output_schema"], output)
