"""Bind graph atoms to the real dispatch table without calling business code.

Run the kit's structural/host checks before deployment. This startup check has a
different purpose: a handshake may acknowledge only callable local bindings.
"""

from copy import deepcopy
from types import MappingProxyType


class AtomBindings:
    def __init__(self, graph, manifest, handlers):
        """Pin one definition per actual capability and reject missing callables."""
        module = manifest.get("module", manifest)
        if graph.get("graph_version") != "1.0" or (graph["module_id"], graph["module_version"]) != (
            module["id"],
            module["version"],
        ):
            raise ValueError("Atom graph identity differs from the running module")
        capabilities = {item["id"]: item for item in manifest["capabilities"]}
        backends = [atom for atom in graph["atoms"] if atom["kind"] == "backend"]
        bound = {atom["capability"]["id"]: atom for atom in backends}
        if (
            len(bound) != len(backends)
            or set(bound) != set(capabilities)
            or set(handlers) != set(capabilities)
        ):
            raise ValueError("Every capability requires one actual atomic binding")
        for identifier, atom in bound.items():
            if (
                not callable(handlers[identifier])
                or atom["capability"]["version"] != capabilities[identifier]["version"]
            ):
                raise ValueError("Atom handler or version is invalid")
        self._graph = deepcopy(graph)
        self._by_capability = deepcopy(bound)
        self.handlers = MappingProxyType(dict(handlers))

    @property
    def document(self):
        """Return a copy so callers cannot mutate accepted declarations."""
        return deepcopy(self._graph)

    def for_capability(self, identifier):
        """Resolve observation metadata; this lookup grants no execution rights."""
        return deepcopy(self._by_capability[identifier])
