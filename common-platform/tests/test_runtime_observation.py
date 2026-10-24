"""Observe real call identities without granting access or changing business state."""

from copy import deepcopy
import asyncio
import importlib.util
from pathlib import Path
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
import pytest

from app.errors import PlatformError
from app.runtime_observation import RuntimeObservations, create_observation_router
from test_access_bridge import bridge_case

REQUEST = "a" * 32
ATOM = "example-intake.atom.command"


@pytest.fixture
def observations(bridge_case, tmp_path):
    case = bridge_case
    case.runtime.data_dir = tmp_path
    case.runtime.workflows = SimpleNamespace(definitions={}, transport=None)
    graph = {
        "version": "1.0.0",
        "atoms": [{"id": ATOM, "kind": "backend", "capability": {"id": case.spec["id"]}}],
        "state_machines": [],
    }
    observer = RuntimeObservations(case.runtime, clock=lambda: case.clock[0])
    token = observer.issue(case.actor, case.spec, REQUEST, graph)
    app = FastAPI()
    app.include_router(create_observation_router(observer, lambda: case.actor))

    @app.exception_handler(PlatformError)
    async def handle(request, error):
        return JSONResponse({"error": error.code}, status_code=error.status)

    with TestClient(app) as client:
        yield SimpleNamespace(
            **vars(case), observer=observer, token=token, http=client, graph=graph
        )


def batch(sequence=1, phase="started"):
    return {
        "runtime_version": "1.0",
        "request_id": REQUEST,
        "graph_version": "1.0.0",
        "dropped": 0,
        "events": [
            {
                "sequence": sequence,
                "atom_id": ATOM,
                "call_id": REQUEST,
                "parent_call_id": None,
                "phase": phase,
            }
        ],
    }


def send(case, value=None, token=None):
    return case.http.post(
        "/api/v1/runtime-observations/events",
        headers={"X-DuoFlow-Observation": token or case.token},
        json=value or batch(),
    )


def read(case):
    return case.http.get("/api/v1/runtime-observations/" + REQUEST)


def test_dedup_reordering_gaps_and_restart_recovery(observations):
    case = observations
    assert send(case, batch(3, "succeeded")).status_code == 200
    assert send(case, batch(3, "succeeded")).status_code == 200
    assert read(case).json()["gaps"] == [1, 2]
    assert send(case).status_code == 200
    assert read(case).json()["gaps"] == [2]
    restored = RuntimeObservations(case.runtime, clock=lambda: case.clock[0])
    result = asyncio.run(restored.read(case.actor, REQUEST))
    assert len(result["events"]) == 2 and result["freshness"] == "complete"
    assert case.token not in str(result)


def test_conflicting_batch_rolls_back_every_event(observations):
    case = observations
    assert send(case).status_code == 200
    value = batch(2)
    value["events"].append(batch(1, "failed")["events"][0])
    assert send(case, value).status_code == 409
    assert len(read(case).json()["events"]) == 1


@pytest.mark.parametrize(
    "case_name",
    [
        "ticket",
        "expired",
        "version",
        "atom",
        "sequence",
        "parent",
        "business_data",
        "frontend",
        "state",
    ],
)
def test_unsupported_or_untrusted_event_is_rejected(observations, case_name):
    case, value, token = observations, batch(), None
    if case_name == "ticket":
        token = "f" * 64
    elif case_name == "expired":
        case.clock[0] += 121
    elif case_name == "version":
        value["graph_version"] = "0.0.0"
    elif case_name == "atom":
        value["events"][0]["atom_id"] = "other.atom.command"
    elif case_name == "sequence":
        value["events"][0]["sequence"] = 257
    elif case_name == "parent":
        value["events"][0]["parent_call_id"] = REQUEST
    elif case_name == "business_data":
        value["events"][0]["input"] = {"secret": "synthetic"}
    elif case_name == "frontend":
        value["events"][0]["phase"] = "mounted"
    elif case_name == "state":
        value["events"][0]["phase"] = "state"
    assert send(case, value, token).status_code in {401, 409, 422}


def test_current_authorization_and_connection_rechecked(observations):
    case = observations
    assert send(case).status_code == 200
    case.spec["binding_revision"] += 1
    assert send(case, batch(2)).status_code == 409
    assert read(case).status_code == 409
    case.spec["binding_revision"] -= 1
    case.runtime.access.revoke(case.grant["id"], actor_id="manager")
    assert send(case, batch(2)).status_code == 403
    assert read(case).status_code == 403


def test_missing_events_become_stale_without_advancing_business(observations):
    case = observations
    assert read(case).json()["freshness"] == "empty"
    send(case)
    case.clock[0] += 31
    assert read(case).json()["freshness"] == "stale"
    assert "projection" not in read(case).json()


def test_other_users_cannot_read_history(observations):
    case = observations
    with pytest.raises(PlatformError) as error:
        asyncio.run(case.observer.read({**case.actor, "id": "bob"}, REQUEST))
    assert error.value.status == 403


def test_python_sdk_delivery_failure_does_not_change_business_result():
    path = Path(__file__).resolve().parents[1] / "templates/module-kit/sdk/python/observation.py"
    spec = importlib.util.spec_from_file_location("observation_sdk", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    received = []

    def unavailable(ticket, value):
        received.append(deepcopy(value))
        raise OSError("synthetic offline collector")

    client = module.ObservationClient(
        "http://127.0.0.1:9999/api/v1/runtime-observations/events",
        {"version": "1.0.0"},
        send=unavailable,
    )
    observer = client.session(ticket="a" * 64, request_id=REQUEST, atom_id=ATOM)
    observer.mark("started")
    result = {"value": 3}
    observer.mark("succeeded")
    client.flush()
    assert result == {"value": 3} and observer.dropped == 2 and len(received) == 4
    assert "input" not in str(received)


def test_admission_requires_a_received_callback_and_rejects_changed_connection(observations):
    case = observations
    revision = [1]
    case.runtime.remote_services = SimpleNamespace(get_binding=lambda _: {"revision": revision[0]})
    challenge = case.observer.begin_probe("demo", 1)
    with pytest.raises(PlatformError):
        case.observer.require_probe(challenge)
    response = case.http.post(
        "/api/v1/runtime-observations/probe",
        headers={"X-DuoFlow-Observation": challenge["ticket"]},
        json={"runtime_version": "1.0", "nonce": challenge["nonce"]},
    )
    assert response.json() == {"accepted": True, "nonce": challenge["nonce"]}
    case.observer.require_probe(challenge)
    second = case.observer.begin_probe("demo", 1)
    revision[0] = 2
    with pytest.raises(PlatformError):
        case.observer.accept_probe(
            second["ticket"], {"runtime_version": "1.0", "nonce": second["nonce"]}
        )


def test_formal_state_history_rechecks_object_permission(observations):
    case = observations
    graph = deepcopy(case.graph)
    graph["state_machines"] = [
        {"id": "example-intake.lifecycle", "scope": "business", "states": [{"id": "draft"}]}
    ]
    request = "b" * 32
    token = case.observer.issue(case.actor, case.spec, request, graph)
    value = batch(1, "state")
    value["request_id"] = request
    value["events"][0].update(
        call_id=request,
        state={
            "machine_id": "example-intake.lifecycle",
            "value": "draft",
            "revision": 1,
            "object_ref": {"object_type": "example-intake.record", "object_id": "one"},
        },
    )
    assert case.observer.accept(token, value)["accepted"]
    revoked = [False]

    async def read_object(actor, capability, operation, payload):
        assert payload == {"object_ref": value["events"][0]["state"]["object_ref"]}
        if revoked[0]:
            raise PlatformError("object_forbidden", "对象已撤权", 403)
        return {"projection": {"state": "draft"}}

    case.runtime.workflows = SimpleNamespace(
        definitions={
            "flow": {
                "id": "example-intake.flow",
                "provider": {
                    "object_type": "example-intake.record",
                    "read_capability": case.spec["id"],
                },
            }
        },
        transport=SimpleNamespace(request=read_object),
    )
    assert asyncio.run(case.observer.read(case.actor, request))["events"]
    revoked[0] = True
    with pytest.raises(PlatformError) as error:
        asyncio.run(case.observer.read(case.actor, request))
    assert error.value.code == "object_forbidden"
