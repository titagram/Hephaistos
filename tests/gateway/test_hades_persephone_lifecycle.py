from __future__ import annotations

from unittest.mock import Mock
import asyncio
import threading

import pytest

from gateway.run import GatewayRunner


def _runner(fake_receiver: object) -> GatewayRunner:
    runner = object.__new__(GatewayRunner)
    runner._hades_persephone_receiver = None
    runner._hades_persephone_receiver_factory = lambda: fake_receiver
    return runner


@pytest.mark.asyncio
async def test_gateway_starts_and_stops_receiver_once() -> None:
    receiver = Mock(spec=["start", "stop"])
    runner = _runner(receiver)

    await runner._start_hades_persephone_receiver()
    await runner._start_hades_persephone_receiver()

    receiver.start.assert_called_once_with()
    assert runner._hades_persephone_receiver is receiver

    await runner._stop_hades_persephone_receiver()
    await runner._stop_hades_persephone_receiver()

    receiver.stop.assert_called_once_with(timeout=5.0)
    assert runner._hades_persephone_receiver is None


@pytest.mark.asyncio
async def test_gateway_receiver_failure_degrades_without_crashing() -> None:
    receiver = Mock(spec=["start", "stop"])
    receiver.start.side_effect = RuntimeError("secret-token-must-not-leak")
    runner = _runner(receiver)
    recorded: list[tuple[str, str | None]] = []
    runner._record_hades_persephone_lifecycle = (
        lambda state, *, error=None: recorded.append((state, error))
    )

    await runner._start_hades_persephone_receiver()

    assert runner._hades_persephone_receiver is None
    assert recorded == [("failed", "secret-token-must-not-leak")]


@pytest.mark.asyncio
async def test_gateway_capability_gate_does_not_start_receiver() -> None:
    runner = _runner(None)
    recorded: list[tuple[str, str | None]] = []
    runner._record_hades_persephone_lifecycle = (
        lambda state, *, error=None: recorded.append((state, error))
    )

    await runner._start_hades_persephone_receiver()

    assert runner._hades_persephone_receiver is None
    assert recorded == [("disabled_capability", None)]


@pytest.mark.asyncio
async def test_concurrent_starts_create_exactly_one_receiver() -> None:
    receiver = Mock(spec=["start", "stop"])
    calls = 0

    def factory():
        nonlocal calls
        calls += 1
        return receiver

    runner = _runner(receiver)
    runner._hades_persephone_receiver = None
    runner._hades_persephone_receiver_factory = factory

    await asyncio.gather(
        runner._start_hades_persephone_receiver(),
        runner._start_hades_persephone_receiver(),
    )

    assert calls == 1
    receiver.start.assert_called_once_with()


@pytest.mark.asyncio
async def test_stop_during_factory_prevents_late_receiver_publish() -> None:
    entered = threading.Event()
    release = threading.Event()
    receiver = Mock(spec=["start", "stop"])

    def factory():
        entered.set()
        assert release.wait(2)
        return receiver

    runner = _runner(receiver)
    runner._hades_persephone_receiver = None
    runner._hades_persephone_receiver_factory = factory
    start_task = asyncio.create_task(runner._start_hades_persephone_receiver())
    assert await asyncio.to_thread(entered.wait, 2)
    stop_task = asyncio.create_task(runner._stop_hades_persephone_receiver())
    await asyncio.sleep(0)
    release.set()
    await asyncio.gather(start_task, stop_task)

    receiver.start.assert_not_called()
    receiver.stop.assert_called_once()
    assert runner._hades_persephone_receiver is None


@pytest.mark.asyncio
async def test_incomplete_stop_retains_receiver_ownership() -> None:
    receiver = Mock(spec=["start", "stop", "health_snapshot"])
    receiver.stop.return_value = False
    receiver.health_snapshot.return_value = {
        "state": "draining",
        "active": True,
        "failure_count": 0,
    }
    runner = _runner(receiver)
    runner._hades_persephone_receiver = receiver
    recorded = []
    runner._record_hades_persephone_health = recorded.append

    await runner._stop_hades_persephone_receiver()

    assert runner._hades_persephone_receiver is receiver
    assert recorded[-1]["state"] in {"draining", "failed"}


def test_receiver_factory_is_inert_when_backend_disabled(monkeypatch) -> None:
    import hermes_cli.config as config_module

    monkeypatch.setattr(config_module, "load_config", lambda: {"backend": {"enabled": False}})
    runner = object.__new__(GatewayRunner)

    assert runner._create_hades_persephone_receiver() is None


@pytest.mark.asyncio
async def test_fatal_receiver_is_restarted_once_with_generation_guard() -> None:
    failed = Mock(spec=["health_snapshot", "stop"])
    failed.health_snapshot.return_value = {
        "state": "failed", "active": False, "failure_count": 1,
    }
    failed.stop.return_value = True
    replacement = Mock(spec=["start", "stop"])
    runner = _runner(replacement)
    runner._hades_persephone_receiver = failed
    runner._hades_persephone_generation = 4
    runner._hades_persephone_draining = False
    runner._hades_persephone_restart_base_seconds = 0
    runner._hades_persephone_receiver_factory = lambda: replacement
    runner._record_hades_persephone_health = lambda snapshot: None
    runner._record_hades_persephone_lifecycle = lambda state, error=None: None
    runner._hades_persephone_runtime_revision = lambda: (True, "default", ())

    await runner._monitor_hades_persephone_receiver(failed, generation=4)

    failed.stop.assert_called_once()
    replacement.start.assert_called_once()
    assert runner._hades_persephone_receiver is replacement
    assert runner._hades_persephone_generation == 5


@pytest.mark.asyncio
async def test_revision_change_refreshes_routes_without_duplicate_receiver() -> None:
    receiver = Mock(spec=["health_snapshot", "refresh_bindings"])
    receiver.health_snapshot.return_value = {
        "state": "connected", "active": True, "failure_count": 0,
    }
    runner = _runner(receiver)
    runner._hades_persephone_receiver = receiver
    runner._hades_persephone_generation = 1
    runner._hades_persephone_draining = False
    runner._hades_persephone_revision = (True, "old", ())
    runner._hades_persephone_monitor_interval_seconds = 0
    runner._record_hades_persephone_health = lambda snapshot: None
    runner._hades_persephone_runtime_revision = lambda: (True, "new", ())
    receiver.refresh_bindings.side_effect = lambda **kwargs: setattr(
        runner, "_hades_persephone_receiver", None
    )

    await runner._monitor_hades_persephone_receiver(receiver, generation=1)

    receiver.refresh_bindings.assert_called_once_with(queue_capability=True)


def test_restart_streak_persists_across_generations_and_resets_only_when_stable() -> None:
    runner = object.__new__(GatewayRunner)
    runner._hades_persephone_restart_base_seconds = 2
    runner._hades_persephone_restart_max_seconds = 30
    runner._hades_persephone_stable_window_seconds = 60

    assert runner._update_hades_persephone_restart_health("failed", generation=1, now=100) == 2
    assert runner._update_hades_persephone_restart_health("failed", generation=2, now=101) == 4
    assert runner._update_hades_persephone_restart_health("failed", generation=3, now=102) == 8
    assert runner._hades_persephone_restart_streak == 3
    assert runner._hades_persephone_next_retry_at == 110
    for generation in range(4, 20):
        delay = runner._update_hades_persephone_restart_health(
            "failed", generation=generation, now=100 + generation
        )
    assert delay == 30
    assert runner._hades_persephone_next_retry_at == 149

    runner._update_hades_persephone_restart_health("connected", generation=4, now=120)
    runner._update_hades_persephone_restart_health("connected", generation=4, now=179)
    assert runner._hades_persephone_restart_streak == 19
    runner._update_hades_persephone_restart_health("connected", generation=4, now=180)
    assert runner._hades_persephone_restart_streak == 0
    assert runner._hades_persephone_next_retry_at is None


@pytest.mark.asyncio
async def test_service_supervisor_cold_starts_only_after_enabled_binding_revision() -> None:
    receiver = Mock(spec=["start", "stop"])
    runner = _runner(receiver)
    runner._hades_persephone_receiver = None
    runner._hades_persephone_generation = 0
    runner._hades_persephone_draining = False
    runner._hades_persephone_supervisor_interval_seconds = 0
    runner._running = True
    revisions = iter(
        [
            (False, "default", ()),
            (True, "default", ()),
            (True, "default", (("project", "agent", "binding"),)),
        ]
    )

    def revision():
        value = next(revisions)
        if value[2]:
            receiver.start.side_effect = lambda: setattr(runner, "_running", False)
        return value

    runner._hades_persephone_runtime_revision = revision
    runner._hades_persephone_receiver_factory = lambda: receiver
    runner._record_hades_persephone_lifecycle = lambda state, error=None: None

    await runner._hades_persephone_service_supervisor()

    receiver.start.assert_called_once_with()
    assert runner._hades_persephone_receiver is receiver


@pytest.mark.asyncio
async def test_monitor_retries_incomplete_drain_with_positive_slice_and_clears_owner() -> None:
    receiver = Mock(spec=["stop", "health_snapshot"])
    receiver.stop.side_effect = [False, True]
    receiver.health_snapshot.return_value = {
        "state": "draining", "active": True, "failure_count": 1,
    }
    runner = _runner(receiver)
    runner._hades_persephone_receiver = receiver
    runner._hades_persephone_generation = 1
    runner._hades_persephone_draining = False
    runner._hades_persephone_monitor_interval_seconds = 0.005
    runner._hades_persephone_drain_retry_base_seconds = 0.005
    runner._hades_persephone_drain_cleanup_slice_seconds = 0.05
    runner._record_hades_persephone_health = lambda snapshot: None

    await runner._stop_hades_persephone_receiver()
    assert runner._hades_persephone_receiver is receiver
    monitor = asyncio.create_task(
        runner._monitor_hades_persephone_receiver(receiver, generation=1)
    )
    await asyncio.wait_for(monitor, timeout=1)

    assert runner._hades_persephone_receiver is None
    assert receiver.stop.call_args_list[0].kwargs["timeout"] == 5.0
    retry_timeout = receiver.stop.call_args_list[1].kwargs["timeout"]
    assert 0 < retry_timeout <= 0.05


@pytest.mark.asyncio
async def test_repeated_drain_failure_backs_off_without_spinning() -> None:
    receiver = Mock(spec=["stop", "health_snapshot"])
    receiver.stop.return_value = False
    receiver.health_snapshot.return_value = {
        "state": "draining", "active": True, "failure_count": 1,
    }
    runner = _runner(receiver)
    runner._hades_persephone_receiver = receiver
    runner._hades_persephone_generation = 2
    runner._hades_persephone_draining = True
    runner._hades_persephone_shutdown_deadline = __import__("time").monotonic() + 0.2
    runner._hades_persephone_monitor_interval_seconds = 0.005
    runner._hades_persephone_drain_retry_base_seconds = 0.04
    runner._hades_persephone_drain_retry_max_seconds = 0.1
    runner._hades_persephone_drain_cleanup_slice_seconds = 0.01
    runner._record_hades_persephone_health = lambda snapshot: None
    task = asyncio.create_task(
        runner._monitor_hades_persephone_receiver(receiver, generation=2)
    )

    await asyncio.sleep(0.12)
    runner._hades_persephone_receiver = None
    await asyncio.wait_for(task, timeout=1)

    assert 1 <= receiver.stop.call_count <= 3
    assert all(call.kwargs["timeout"] > 0 for call in receiver.stop.call_args_list)
