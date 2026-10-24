"""Server-only observation queue; delivery cannot change business outcomes."""

from copy import deepcopy
import json
from queue import Queue, Full
import re
from threading import Thread
from urllib.parse import urlsplit
from urllib.request import Request, build_opener, HTTPRedirectHandler


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


class ObservationClient:
    def __init__(self, callback_url, graph, *, timeout=1, send=None):
        """Use administrator configuration for the destination, never request input."""
        url = urlsplit(callback_url)
        if (
            url.scheme not in {"http", "https"}
            or not url.netloc
            or url.username
            or url.password
            or url.query
            or url.fragment
            or url.path != "/api/v1/runtime-observations/events"
        ):
            raise ValueError("Invalid configured observation endpoint")
        if not 0 < timeout <= 2:
            raise ValueError("Observation timeout must be at most two seconds")
        self.url, self.graph, self.timeout = callback_url, deepcopy(graph), timeout
        self.send = send or self._send
        self.queue = Queue(maxsize=64)
        self.worker = Thread(target=self._deliver, daemon=True, name="duoflow-observations")
        self.worker.start()

    def _send(self, ticket, batch):
        request = Request(
            self.url,
            data=json.dumps(batch).encode(),
            headers={"Content-Type": "application/json", "X-DuoFlow-Observation": ticket},
            method="POST",
        )
        with build_opener(_NoRedirect).open(request, timeout=self.timeout) as response:
            return response.status == 200

    def _deliver(self):
        """Bound retries; do not serialize credentials or report transport errors as business errors."""
        while True:
            ticket, batch, lost = self.queue.get()
            try:
                for _ in range(2):
                    try:
                        if self.send(ticket, batch):
                            break
                    except Exception:
                        pass
                else:
                    lost()
            finally:
                self.queue.task_done()

    def session(self, *, ticket, request_id, atom_id):
        """Create a call-local reporter; absent tickets produce no network traffic."""
        return ObservationSession(self, ticket, request_id, atom_id)

    def probe(self, challenge):
        """Actively confirm the platform callback using its one-time challenge."""
        if (
            not isinstance(challenge, dict)
            or set(challenge) != {"ticket", "nonce"}
            or not re.fullmatch(r"[0-9a-f]{64}", str(challenge["ticket"]))
            or not re.fullmatch(r"[0-9a-f]{32}", str(challenge["nonce"]))
        ):
            return False
        url = self.url.rsplit("/", 1)[0] + "/probe"
        body = {"runtime_version": "1.0", "nonce": challenge["nonce"]}
        try:
            request = Request(
                url,
                data=json.dumps(body).encode(),
                headers={
                    "Content-Type": "application/json",
                    "X-DuoFlow-Observation": challenge["ticket"],
                },
                method="POST",
            )
            with build_opener(_NoRedirect).open(request, timeout=self.timeout) as response:
                raw = response.read(1025)
                if len(raw) > 1024:
                    return False
                result = json.loads(raw)
                return response.status == 200 and result == {
                    "accepted": True,
                    "nonce": challenge["nonce"],
                }
        except Exception:
            return False

    def flush(self):
        """Wait for the bounded queue during tests or controlled shutdown."""
        self.queue.join()


class ObservationSession:
    def __init__(self, client, ticket, request_id, atom_id):
        self.client, self.ticket, self.request_id, self.atom_id = (
            client,
            ticket,
            request_id,
            atom_id,
        )
        self.sequence, self.dropped = 0, 0

    def _lost(self):
        self.dropped = min(256, self.dropped + 1)

    def _emit(self, phase, state=None):
        if (
            not isinstance(self.ticket, str)
            or not re.fullmatch(r"[0-9a-f]{64}", self.ticket)
            or not re.fullmatch(r"[0-9a-f]{32}", self.request_id)
        ):
            return
        if self.sequence >= 256:
            self._lost()
            return
        self.sequence += 1
        event = {
            "sequence": self.sequence,
            "atom_id": self.atom_id,
            "call_id": self.request_id,
            "parent_call_id": None,
            "phase": phase,
        }
        if state is not None:
            event["state"] = deepcopy(state)
        batch = {
            "runtime_version": "1.0",
            "request_id": self.request_id,
            "graph_version": self.client.graph["version"],
            "dropped": self.dropped,
            "events": [event],
        }
        try:
            self.client.queue.put_nowait((self.ticket, batch, self._lost))
        except Full:
            self._lost()

    def mark(self, phase):
        """Report the actual invocation boundary, without affecting dispatch."""
        if phase in {"started", "succeeded", "failed"}:
            self._emit(phase)

    def state(self, machine_id, projection):
        """Report a committed or freshly read projection, omitting business data."""
        if (
            not isinstance(projection, dict)
            or not projection.get("object_ref")
            or type(projection.get("revision")) is not int
            or not isinstance(projection.get("state"), str)
        ):
            return
        self._emit(
            "state",
            {
                "machine_id": machine_id,
                "value": projection["state"],
                "revision": projection["revision"],
                "object_ref": projection["object_ref"],
            },
        )
