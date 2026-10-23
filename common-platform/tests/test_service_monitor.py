"""Deterministic heartbeat transitions, isolation, deadlines and shutdown."""

import asyncio
from types import SimpleNamespace

import httpx
import pytest

from app.remote_services import RemoteServiceError, RemoteServiceManager
from registration_helpers import complete_admission, async_complete_admission


class FakeClock:
    def __init__(self):
        self.value = 1_800_000_000.0

    def __call__(self):
        return self.value

    def advance(self, seconds):
        self.value += seconds


def saved(tmp_path, handler, count=1, token=None):
    clock = FakeClock()
    manager = RemoteServiceManager(tmp_path, transport=httpx.MockTransport(handler), clock=clock)
    for number in range(count):
        manager.save_connection({"id": f"service-{number}", "name": f"合成服务{number}", "base_url": f"https://service-{number}.example", "timeout_seconds": 180, **({'token': token} if token else {})})
    return manager, clock


def test_unknown_failure_threshold_recovery_staleness_and_enabled_are_independent(tmp_path):
    state = {"ok": False}
    manager, clock = saved(tmp_path, lambda req: httpx.Response(200, json=state))
    assert manager.monitor_status("service-0")["state"] == "unknown"
    assert manager.check_available("service-0") is None
    for number in range(1, 4):
        clock.advance(15)
        response = asyncio.run(manager.health("service-0"))
        assert response["ok"] is False
        status = manager.monitor_status("service-0")
        assert status["state"] == ("offline" if number == 3 else "degraded")
        assert status["consecutive_failures"] == number
        assert status["last_success_at"] is None
    with pytest.raises(RemoteServiceError) as unavailable:
        manager.check_available("service-0")
    assert unavailable.value.code == "remote_offline"
    state["ok"] = True
    clock.advance(15)
    assert asyncio.run(manager.health("service-0"))["ok"]
    status = manager.monitor_status("service-0")
    assert status["state"] == "healthy" and status["consecutive_failures"] == 0
    assert status["last_checked_at"] == status["last_success_at"]
    assert status["interval_seconds"] == 15 and status["stale_after_seconds"] == 45
    assert manager.check_available("service-0") is None
    clock.advance(45)
    assert manager.monitor_status("service-0")["state"] == "healthy"
    clock.advance(0.01)
    with pytest.raises(RemoteServiceError) as stale:
        manager.check_available("service-0")
    assert stale.value.code == "remote_stale"
    item = manager.list_services()["items"][0]
    assert item["monitor"]["state"] == "stale" and item["enabled"] is False


@pytest.mark.parametrize("payload", [{}, {"unrelated": "private-provider-text"}, {"ok": "true"}, {"status": "anything"},
                                    {"status": {}}, {"ok": 1}, [], None])
def test_http_200_requires_semantically_valid_health_and_records_failure(tmp_path, payload):
    manager, clock = saved(tmp_path, lambda req: httpx.Response(200, json=payload))
    with pytest.raises(RemoteServiceError) as error:
        asyncio.run(manager.health("service-0"))
    assert error.value.code in {"remote_invalid_health", "remote_invalid_json"}
    assert "private-provider-text" not in error.value.message
    assert manager.monitor_status("service-0")["state"] == "degraded"
    assert manager.list_services()["items"][0]["last_health"]["ok"] is False


@pytest.mark.parametrize("payload,healthy", [({"status": "ok"}, True), ({"ok": True}, True), ({"status": "healthy"}, True),
                                          ({"ok": False}, False), ({"status": "unhealthy"}, False), ({"ok": True, "status": "failed"}, False)])
def test_semantic_health_compatibility_and_negative_status_wins(tmp_path, payload, healthy):
    manager, _ = saved(tmp_path, lambda req: httpx.Response(200, json=payload))
    response = asyncio.run(manager.health("service-0"))
    assert response["ok"] is healthy and set(response) == {"ok", "message", "latency_ms"}
    assert manager.monitor_status("service-0")["state"] == ("healthy" if healthy else "degraded")


def test_probe_uses_five_second_timeout_and_records_safe_failure(tmp_path):
    seen = []

    def fail(request):
        seen.append(request)
        raise httpx.ReadTimeout("private-provider-token", request=request)

    manager, _ = saved(tmp_path, fail)
    with pytest.raises(RemoteServiceError) as error:
        asyncio.run(manager.health("service-0"))
    assert error.value.code == "remote_timeout"
    assert seen[0].extensions["timeout"]["read"] == 5
    status = manager.monitor_status("service-0")
    assert status["state"] == "degraded" and "private-provider-token" not in str(status)


def test_old_probe_response_cannot_overwrite_updated_connection_revision(tmp_path):
    manager = None

    def replace(request):
        manager.save_connection({"id": "service-0", "timeout_seconds": 181})
        return httpx.Response(200, json={"ok": True})

    manager, _ = saved(tmp_path, replace)
    with pytest.raises(RemoteServiceError) as error:
        asyncio.run(manager.health("service-0"))
    assert error.value.code == "remote_revision_changed"
    status = manager.monitor_status("service-0")
    assert status["state"] == "unknown" and status["consecutive_failures"] == 0 and status["last_checked_at"] is None
    assert manager.list_services()["items"][0]["last_health"] is None


def test_updated_revision_starts_new_probe_instead_of_joining_old_request(tmp_path):
    async def scenario():
        entered, release = asyncio.Event(), asyncio.Event()
        calls = []
        async def handler(request):
            calls.append(request.url.host)
            if request.url.host == 'service-0.example':
                entered.set()
                await release.wait()
                return httpx.Response(200, json={'ok': False})
            return httpx.Response(200, json={'ok': True})
        manager, _ = saved(tmp_path, handler)
        old = asyncio.create_task(manager.health('service-0'))
        await entered.wait()
        manager.save_connection({'id': 'service-0', 'expected_revision': 1, 'base_url': 'https://updated.example'})
        assert manager.monitor_status('service-0')['state'] == 'unknown'
        current = await asyncio.wait_for(manager.health('service-0'), 0.5)
        assert current['ok'] and not old.done()
        release.set()
        with pytest.raises(RemoteServiceError) as error:
            await old
        assert error.value.code == 'remote_revision_changed'
        assert calls == ['service-0.example', 'updated.example']
        status = manager.monitor_status('service-0')
        assert status['state'] == 'healthy' and status['consecutive_failures'] == 0
        assert manager.list_services()['items'][0]['last_health']['ok']
    asyncio.run(scenario())


def test_revision_changed_while_queued_does_not_send_an_obsolete_probe(tmp_path):
    async def scenario():
        entered, release = asyncio.Event(), asyncio.Event()
        calls = []
        async def handler(request):
            calls.append(request.url.host)
            if request.url.host == 'service-0.example':
                entered.set()
                await release.wait()
            return httpx.Response(200, json={'ok': True})
        manager, _ = saved(tmp_path, handler, count=2)
        manager._monitor._semaphore = asyncio.Semaphore(1)
        first = asyncio.create_task(manager.health('service-0'))
        await entered.wait()
        queued = asyncio.create_task(manager.health('service-1'))
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        manager.save_connection({'id': 'service-1', 'expected_revision': 1, 'timeout_seconds': 181})
        release.set()
        await first
        with pytest.raises(RemoteServiceError) as error:
            await queued
        assert error.value.code == 'remote_revision_changed'
        assert calls == ['service-0.example']
        assert manager.monitor_status('service-1')['state'] == 'unknown'
        assert (await manager.health('service-1'))['ok']
        assert calls == ['service-0.example', 'service-1.example']
    asyncio.run(scenario())


def test_stale_offline_state_recovers_on_success_without_republishing(tmp_path):
    state = {'ok': False}
    manager, clock = saved(tmp_path, lambda _: httpx.Response(200, json=state))
    for _ in range(3):
        asyncio.run(manager.health('service-0'))
    assert manager.monitor_status('service-0')['state'] == 'offline'
    clock.advance(46)
    assert manager.monitor_status('service-0')['state'] == 'stale'
    state['ok'] = True
    asyncio.run(manager.health('service-0'))
    current = manager.list_services()['items'][0]
    assert current['monitor']['state'] == 'healthy'
    assert current['monitor']['consecutive_failures'] == 0
    assert not current['enabled'] and current['revision'] == 1


@pytest.mark.parametrize('health_state', ['healthy', 'degraded', 'offline', 'stale'])
def test_publication_revision_carries_health_without_extending_its_age(tmp_path, health_state):
    from test_remote_services import manifest
    response = {'ok': True}
    def handler(request):
        return httpx.Response(200, json=manifest() if request.url.path.endswith('/manifest') else response)
    manager, clock = saved(tmp_path, handler, token='synthetic-monitor-token')
    asyncio.run(manager.discover('service-0'))
    complete_admission(manager, 'service-0')
    asyncio.run(manager.health('service-0'))
    if health_state in ('degraded', 'offline'):
        response['ok'] = False
        for _ in range(3 if health_state == 'offline' else 1):
            clock.advance(1)
            asyncio.run(manager.health('service-0'))
    elif health_state == 'stale':
        clock.advance(46)
    before = manager.monitor_status('service-0')
    assert before['state'] == health_state
    enabled = manager.set_enabled('service-0', True, 1)
    assert enabled['revision'] == enabled['discovered_revision'] == 2
    assert manager.monitor_status('service-0') == before
    disabled = manager.set_enabled('service-0', False, 2)
    assert disabled['revision'] == disabled['discovered_revision'] == 3
    assert manager.monitor_status('service-0') == before
    if health_state in ('offline', 'stale'):
        with pytest.raises(RemoteServiceError) as error:
            manager.check_available('service-0')
        assert error.value.code == 'remote_' + health_state


def test_old_inflight_health_is_discarded_after_publication_revision_change(tmp_path):
    from test_remote_services import manifest
    async def scenario():
        entered, release = asyncio.Event(), asyncio.Event()
        checks = 0
        async def handler(request):
            nonlocal checks
            if request.url.path.endswith('/manifest'):
                return httpx.Response(200, json=manifest())
            checks += 1
            if checks == 2:
                entered.set()
                await release.wait()
                return httpx.Response(200, json={'ok': False})
            return httpx.Response(200, json={'ok': True})
        manager, _ = saved(tmp_path, handler, token='synthetic-monitor-token')
        await manager.discover('service-0')
        await async_complete_admission(manager, 'service-0')
        await manager.health('service-0')
        before = manager.monitor_status('service-0')
        old = asyncio.create_task(manager.health('service-0'))
        await entered.wait()
        manager.set_enabled('service-0', True, 1)
        release.set()
        with pytest.raises(RemoteServiceError) as error:
            await old
        assert error.value.code == 'remote_revision_changed'
        assert manager.monitor_status('service-0') == before
        assert (await manager.health('service-0'))['ok']
        assert checks == 3
    asyncio.run(scenario())


def test_manual_and_background_health_checks_share_one_request(tmp_path):
    state = SimpleNamespace(calls=0)

    async def scenario():
        entered, release = asyncio.Event(), asyncio.Event()

        async def handler(request):
            state.calls += 1
            entered.set()
            await release.wait()
            return httpx.Response(200, json={"ok": True})

        manager, _ = saved(tmp_path, handler)
        first = asyncio.create_task(manager.health("service-0"))
        await entered.wait()
        second = asyncio.create_task(manager.health("service-0"))
        background = asyncio.create_task(manager._monitor.poll_once())
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        release.set()
        a, b, _ = await asyncio.gather(first, second, background)
        assert a == b and state.calls == 1

    asyncio.run(scenario())


def test_polling_is_bounded_to_four_concurrent_requests_and_failures_are_isolated(tmp_path):
    async def scenario():
        first_four, release = asyncio.Event(), asyncio.Event()
        active = peak = started = 0

        async def handler(request):
            nonlocal active, peak, started
            active += 1
            started += 1
            peak = max(peak, active)
            if started == 4:
                first_four.set()
            try:
                await release.wait()
                return httpx.Response(500 if request.url.host == "service-0.example" else 200, json={"ok": True})
            finally:
                active -= 1

        manager, _ = saved(tmp_path, handler, count=9)
        polling = asyncio.create_task(manager._monitor.poll_once())
        await first_four.wait()
        assert started == 4 and peak == 4
        release.set()
        await polling
        assert started == 9 and peak == 4
        assert manager.monitor_status("service-0")["state"] == "degraded"
        assert all(manager.monitor_status(f"service-{n}")["state"] == "healthy" for n in range(1, 9))

    asyncio.run(scenario())


def test_one_unreadable_service_credential_does_not_block_other_heartbeats(tmp_path, monkeypatch):
    manager, _ = saved(tmp_path, lambda req: httpx.Response(200, json={"ok": True}), count=2)
    manager.save_connection({"id": "service-0", "token": "synthetic-unavailable-token"})
    original = manager._tokens.decrypt

    def unavailable(secret):
        if secret is not None:
            raise RemoteServiceError("remote_secret_unavailable", "合成凭据不可用。")
        return original(secret)

    monkeypatch.setattr(manager._tokens, "decrypt", unavailable)
    asyncio.run(manager._monitor.poll_once())
    assert manager.monitor_status("service-0")["state"] == "degraded"
    assert manager.monitor_status("service-1")["state"] == "healthy"


def test_loop_probes_saved_connections_immediately_then_on_interval_and_never_enables_them(tmp_path):
    calls = []
    manager, clock = saved(tmp_path, lambda req: (calls.append(req) or httpx.Response(200, json={"status": "ok"})))
    restarted = RemoteServiceManager(tmp_path, transport=manager._transport, clock=clock)

    async def scenario():
        stop = asyncio.Event()

        async def deterministic_wait(event, seconds):
            assert 14 <= seconds <= 15
            if len(calls) == 3:
                event.set()
            else:
                clock.advance(15)

        restarted._monitor._wait = deterministic_wait
        assert calls == []
        await restarted.monitor_loop(stop)

    asyncio.run(scenario())
    assert len(calls) == 3
    item = restarted.list_services()["items"][0]
    assert item["monitor"]["state"] == "healthy" and not item["enabled"]


def test_stopping_loop_cancels_pending_probe_without_counting_a_service_failure(tmp_path):
    async def scenario():
        entered, stopped = asyncio.Event(), asyncio.Event()

        async def hanging(request):
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                stopped.set()

        manager, _ = saved(tmp_path, hanging)
        stop = asyncio.Event()
        task = asyncio.create_task(manager.monitor_loop(stop))
        await entered.wait()
        # Starting another monitoring loop cannot cause overlapping batches.
        await manager.monitor_loop(stop)
        stop.set()
        await asyncio.wait_for(task, 0.5)
        assert stopped.is_set()
        assert manager.monitor_status("service-0")["state"] == "unknown"
        assert manager._monitor._inflight == {}

    asyncio.run(scenario())
