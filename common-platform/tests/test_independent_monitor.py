"""Real isolated collector routes and process-independent platform aggregation."""
import asyncio
from copy import deepcopy
import json

import httpx
from fastapi.testclient import TestClient
import pytest

from app.monitor_client import AggregateMonitorClient
from app.remote_services import RemoteServiceManager
from monitoring_service.main import create_app
from monitoring_service.protocol import Report, Targets
from monitoring_service.settings import provision, unseal


class Clock:
    def __init__(self): self.value = 1_800_000_000.0
    def __call__(self): return self.value
    def advance(self, value): self.value += value


@pytest.fixture
def monitor(tmp_path):
    platform, directory = tmp_path/"platform", tmp_path/"collector"
    provision(platform, directory, 8032)
    clock = Clock()
    calls = []
    def healthy(request):
        calls.append(request)
        return httpx.Response(200, json={"status":"ok"})
    app = create_app(directory, start_worker=False, clock=clock, transport=httpx.MockTransport(healthy))
    store = app.state.monitor
    client = TestClient(app)
    headers = {"Authorization":"Bearer "+store.control_token}
    command = {"monitor_version":"1.0", "owner_id":store.bootstrap["owner_id"], "expected_revision":0,
               "targets":[{"id":"demo-service", "revision":1, "base_url":"http://127.0.0.1:8210", "token":"synthetic-probe-token"}]}
    assert client.put("/api/v1/monitor/targets", headers=headers, json=command).status_code == 200
    credentials = store.reporting_credentials("demo-service")
    return platform, app, store, client, headers, command, credentials, clock, calls


def report(instance="one", **changes):
    return {"monitor_version":"1.0", "instance_id":instance, "boot_id":"a"*32, "sequence":1,
            "status":"ready", "uptime_seconds":10.0,
            "metrics":{"cpu_percent":20.0, "rss_bytes":1024, "inflight_requests":2,
                       "queue_depth":3,"requests_total":10,"errors_total":1}, **changes}


def send(fixture, payload, token=None):
    _, _, _, client, _, _, credentials, _, _ = fixture
    return client.post("/api/v1/monitor/reports/demo-service", json=payload,
                       headers={"Authorization":"Bearer "+(token or credentials["token"])})


def test_latest_values_aggregate_expire_and_survive_collector_restart(monitor):
    platform, app, store, client, headers, command, credentials, clock, calls = monitor
    assert send(monitor, report()).status_code == 200
    second = report("two"); second["metrics"]["cpu_percent"] = 40.0
    assert send(monitor, second).status_code == 200
    value = client.get("/api/v1/monitor/snapshot", headers=headers).json()
    load = value["items"][0]["load"]
    assert load["fresh_instances"] == 2 and load["metrics"]["rss_bytes"] == 2048
    assert load["metrics"]["cpu_percent"] == 30.0 and load["metrics"]["queue_depth"] == 6
    asyncio.run(store.monitor.poll_once())
    assert store.snapshot()["summary"]["healthy"] == 1
    restarted = create_app(store.directory, clock=clock, start_worker=False).state.monitor
    assert restarted.snapshot()["items"][0]["load"]["fresh_instances"] == 2
    assert restarted.snapshot()["items"][0]["monitor"]["state"] == "unknown"
    clock.advance(46)
    load = store.snapshot()["items"][0]["load"]
    assert load["state"] == "stale" and all(v is None for v in load["metrics"].values())


def test_report_auth_is_separate_from_control_and_service_scope(monitor):
    _, _, store, client, headers, _, credentials, _, _ = monitor
    assert send(monitor, report(), store.control_token).status_code == 401
    assert client.get("/api/v1/monitor/snapshot", headers={"Authorization":"Bearer "+credentials["token"]}).status_code == 401
    assert client.put("/api/v1/monitor/targets", json={}).status_code == 401
    assert store.snapshot()["counters"]["accepted_reports"] == 0


@pytest.mark.parametrize("change", [
    {"secret":"MUST_NOT_ECHO"}, {"sequence":True}, {"metrics":{"cpu_percent":-1.0}},
    {"metrics":{"cpu_percent":101.0}}, {"metrics":{"rss_bytes":True}},
    {"metrics":{"token":"MUST_NOT_ECHO"}}, {"status":"business-approved"},
    {"instance_id":"../private"}, {"boot_id":"bad"},
])
def test_untrusted_report_contract_refused_without_echo_or_write(monitor, change):
    response = send(monitor, report(**change))
    assert response.status_code == 422 and "MUST_NOT_ECHO" not in response.text
    assert monitor[2].snapshot()["counters"]["accepted_reports"] == 0


def test_payload_limit_and_monotonic_idempotency_rate_and_boot_identity(monitor):
    _, _, store, client, _, _, credentials, clock, _ = monitor
    response = client.post("/api/v1/monitor/reports/demo-service", content=b" "*8193,
                          headers={"Authorization":"Bearer "+credentials["token"]})
    assert response.status_code == 413
    value = report()
    assert send(monitor, value).status_code == 200
    assert send(monitor, value).json()["duplicate"]
    assert send(monitor, report(sequence=2)).status_code == 429
    clock.advance(1)
    assert send(monitor, report(sequence=2)).status_code == 200
    assert send(monitor, value).status_code == 409
    clock.advance(1)
    assert send(monitor, report(boot_id="b"*32)).status_code == 200
    clock.advance(1)
    assert send(monitor, report(sequence=3)).status_code == 409
    assert store.snapshot()["counters"]["accepted_reports"] == 3


def test_instance_bound_partial_metrics_and_target_updates(monitor):
    _, _, store, client, headers, command, credentials, clock, _ = monitor
    for n in range(16):
        payload = report(str(n))
        if n == 0: payload["metrics"] = {}
        assert send(monitor, payload).status_code == 200
    assert send(monitor, report("overflow")).status_code == 429
    assert store.snapshot()["items"][0]["load"]["metrics"]["rss_bytes"] is None
    changed = deepcopy(command); changed["expected_revision"] = 1; changed["targets"][0].update(revision=2,token="new-synthetic-probe-token")
    assert client.put("/api/v1/monitor/targets", headers=headers,json=changed).status_code == 200
    assert send(monitor, report()).status_code == 401
    assert store.snapshot()["items"][0]["load"]["state"] == "unknown"
    assert client.put("/api/v1/monitor/targets", headers=headers,json=command).status_code == 409


@pytest.mark.asyncio
async def test_platform_only_reads_aggregate_deduplicates_and_does_not_probe_modules(monitor):
    platform, app, store, _, _, _, _, clock, calls = monitor
    manager = RemoteServiceManager(platform)
    manager.save_connection({"id":"demo-service", "name":"Synthetic", "base_url":"http://127.0.0.1:8210", "token":"synthetic-probe-token"})
    aggregate = AggregateMonitorClient(platform, manager, clock=clock, transport=httpx.ASGITransport(app))
    manager._monitor = aggregate
    await asyncio.gather(*(aggregate.refresh() for _ in range(100)))
    assert aggregate.summary()["state"] == "connected"
    assert aggregate._request_count == 1 and calls == []
    for _ in range(100):
        manager.list_services()
    assert aggregate._request_count == 1 and calls == []
    await store.monitor.poll_once()
    clock.advance(5)
    await aggregate.refresh()
    assert manager.monitor_status("demo-service")["state"] == "healthy"
    assert len(calls) == 1 and aggregate._request_count == 2
    # Unavailable collector does not manufacture 100 module-offline failures.
    aggregate.transport = httpx.MockTransport(lambda _: httpx.Response(503, text="SECRET_PROVIDER_MESSAGE"))
    clock.advance(5)
    await aggregate.refresh()
    assert aggregate.summary()["state"] == "unavailable"
    assert manager.monitor_status("demo-service")["state"] == "unknown"
    assert manager.check_available("demo-service") is None
    assert "SECRET_PROVIDER_MESSAGE" not in str(aggregate.summary())


@pytest.mark.asyncio
async def test_platform_roster_sync_revision_unknown_and_no_local_fallback(monitor):
    platform, app, store, _, _, _, _, clock, calls = monitor
    manager = RemoteServiceManager(platform)
    manager.save_connection({"id":"second-service","name":"Second","base_url":"http://127.0.0.1:8211"})
    aggregate = AggregateMonitorClient(platform, manager, clock=clock, transport=httpx.ASGITransport(app))
    manager._monitor = aggregate
    await aggregate.refresh()
    assert aggregate.summary()["state"] == "connected" and calls == []
    assert aggregate._request_count == 3 and store.snapshot()["items"][0]["id"] == "second-service"
    assert (await manager.health("second-service"))["ok"]
    assert len(calls) == 1
    manager.save_connection({"id":"second-service","name":"Renamed"})
    assert manager.monitor_status("second-service")["state"] == "unknown"


def test_owner_duplicate_target_and_private_storage(monitor):
    _, _, store, client, headers, command, _, _, _ = monitor
    changed = deepcopy(command);changed["owner_id"] = "0"*32
    assert client.put("/api/v1/monitor/targets",headers=headers,json=changed).status_code == 403
    changed = deepcopy(command);changed["targets"] *= 2
    assert client.put("/api/v1/monitor/targets",headers=headers,json=changed).status_code == 422
    public = client.get("/api/v1/monitor/snapshot",headers=headers).text
    assert "synthetic-probe-token" not in public and store.control_token not in public
    assert b"synthetic-probe-token" not in store.path.read_bytes()


def test_duplicate_does_not_renew_freshness_and_removal_frees_all_state(monitor):
    _, _, store, client, headers, command, _, clock, _ = monitor
    value=report();assert send(monitor,value).status_code==200
    clock.advance(46);assert send(monitor,value).json()["duplicate"]
    assert store.snapshot()["items"][0]["load"]["state"]=="stale"
    changed=deepcopy(command);changed.update(expected_revision=1,targets=[])
    assert client.put('/api/v1/monitor/targets',headers=headers,json=changed).status_code==200
    assert store.snapshot()["items"]==[] and not store.monitor._states
    with store.database() as db:assert db.execute('SELECT count(*) FROM reports').fetchone()[0]==0


@pytest.mark.asyncio
@pytest.mark.parametrize('mutation',["extra-secret","wrong-owner","bad-count","bad-message","duplicate-id","old-snapshot"])
async def test_main_api_refuses_untrusted_summaries_without_retaining_remote_strings(monitor,mutation):
    platform, _, store, _, _, _, _, clock, _=monitor
    manager=RemoteServiceManager(platform)
    value=store.snapshot()
    if mutation=='extra-secret':value['token']='NEVER_FORWARD'
    if mutation=='wrong-owner':value['owner_id']='0'*32
    if mutation=='bad-count':value['summary']['healthy']=1
    if mutation=='bad-message':value['items'][0]['monitor']['message']='NEVER_FORWARD'
    if mutation=='duplicate-id':value['items']*=2
    if mutation=='old-snapshot':value['generated_at']-=21
    aggregate=AggregateMonitorClient(platform,manager,clock=clock,transport=httpx.MockTransport(lambda _:httpx.Response(200,json=value)))
    await aggregate.refresh()
    assert aggregate.summary()['state']=='unavailable' and 'NEVER_FORWARD' not in str(aggregate.summary())


@pytest.mark.asyncio
async def test_empty_roster_starts_and_manual_health_does_not_accept_old_revision(monitor):
    platform, app, store, _, _, _, _, clock, _=monitor
    manager=RemoteServiceManager(platform)
    aggregate=AggregateMonitorClient(platform,manager,clock=clock,transport=httpx.ASGITransport(app))
    manager._monitor=aggregate
    await aggregate.refresh()
    assert aggregate.summary()['state']=='connected' and aggregate.summary()['summary']['total']==0
    manager.save_connection({'id':'demo-service','name':'Demo','base_url':'http://127.0.0.1:8210'})
    async def change_target(service_id):
        manager.save_connection({'id':'demo-service','name':'Changed'})
        return {'ok':True,'message':'PRIVATE','latency_ms':1}
    store.monitor.probe=change_target
    from app.remote_services import RemoteServiceError
    with pytest.raises(RemoteServiceError) as error:await manager.health('demo-service')
    assert error.value.code=='remote_revision_conflict'


def test_control_failure_rolls_back_sqlite_and_in_memory_health(monitor):
    _, _, store, client, headers, command, _, clock, _=monitor
    initial=deepcopy(command);initial['expected_revision']=1
    initial['targets'].append({'id':'second-service','revision':2,'base_url':'http://127.0.0.1:8211','token':'another-synthetic-probe'})
    assert client.put('/api/v1/monitor/targets',headers=headers,json=initial).status_code==200
    asyncio.run(store.monitor.poll_once())
    prior=store.snapshot()
    changed=deepcopy(initial);changed['expected_revision']=2
    changed['targets'][0]['revision']=2;changed['targets'][1]['revision']=1
    assert client.put('/api/v1/monitor/targets',headers=headers,json=changed).status_code==409
    after=store.snapshot()
    assert after['items']==prior['items'] and after['revision']==prior['revision']


def test_generated_monitor_schemas_match_actual_models():
    from pathlib import Path
    from scripts.build_monitor_contract import outputs
    root=Path(__file__).parents[1]
    for name,expected in outputs().items():
        assert (root/'contracts/monitoring/v1'/name).read_text(encoding='utf-8')==expected
