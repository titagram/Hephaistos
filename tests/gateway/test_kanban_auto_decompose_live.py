"""Tests for live auto-decompose settings resolution (issue #49638).

The gateway dispatcher used to capture ``kanban.auto_decompose`` once at boot,
so a user who flipped it to ``false`` to STOP runaway auto-decompose (which had
created and launched tasks they didn't intend) found the flag had no effect
without a full gateway restart. ``_resolve_auto_decompose_settings`` is now
called every tick, reading the current config.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from gateway import kanban_watchers as watchers
from gateway.kanban_watchers import GatewayKanbanWatchersMixin, _resolve_auto_decompose_settings
from hermes_cli import kanban_db as kb


def test_enabled_by_default_when_key_absent():
    enabled, per_tick = _resolve_auto_decompose_settings(lambda: {"kanban": {}})
    assert enabled is True
    assert per_tick == 3


def test_disabled_when_flag_false():
    enabled, per_tick = _resolve_auto_decompose_settings(
        lambda: {"kanban": {"auto_decompose": False}}
    )
    assert enabled is False


def test_per_tick_respected_and_clamped():
    enabled, per_tick = _resolve_auto_decompose_settings(
        lambda: {"kanban": {"auto_decompose": True, "auto_decompose_per_tick": 7}}
    )
    assert (enabled, per_tick) == (True, 7)

    # 0 is treated as "unset" by the `or 3` fallback → default 3 (a 0 per-tick
    # cap would disable progress, so falling back to the default is the safe read).
    _, per_tick_zero = _resolve_auto_decompose_settings(
        lambda: {"kanban": {"auto_decompose_per_tick": 0}}
    )
    assert per_tick_zero == 3

    # A genuine negative value clamps up to 1.
    _, per_tick_neg = _resolve_auto_decompose_settings(
        lambda: {"kanban": {"auto_decompose_per_tick": -5}}
    )
    assert per_tick_neg == 1


def test_malformed_per_tick_falls_back_to_default():
    _, per_tick = _resolve_auto_decompose_settings(
        lambda: {"kanban": {"auto_decompose_per_tick": "lots"}}
    )
    assert per_tick == 3


def test_config_read_error_fails_safe_disabled():
    """A transient config read failure must DISABLE auto-decompose, never
    silently fall back to the default-on behaviour the user turned off."""

    def _boom():
        raise RuntimeError("config read failed")

    enabled, per_tick = _resolve_auto_decompose_settings(_boom)
    assert enabled is False
    assert per_tick == 3


def test_non_dict_config_fails_safe():
    enabled, _ = _resolve_auto_decompose_settings(lambda: None)
    assert enabled is True  # no kanban key → default-on (not an error path)
    enabled2, _ = _resolve_auto_decompose_settings(lambda: ["not", "a", "dict"])
    assert enabled2 is True


def test_live_toggle_takes_effect_between_calls():
    """Simulate a user flipping the flag while the dispatcher runs: a later
    resolution reflects the new value without any restart."""
    state = {"kanban": {"auto_decompose": True}}
    assert _resolve_auto_decompose_settings(lambda: state)[0] is True
    # User edits config.yaml mid-run.
    state["kanban"]["auto_decompose"] = False
    assert _resolve_auto_decompose_settings(lambda: state)[0] is False


def test_gateway_dispatch_tick_is_local_without_backend_entry_points(tmp_path, monkeypatch):
    """The gateway dispatcher never composes backend sync or remote admission."""

    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.delenv("HERMES_KANBAN_DISPATCH_IN_GATEWAY", raising=False)
    kb.init_db()
    with kb.connect_closing() as conn:
        kb.create_task(conn, title="local", assignee="default")

    monkeypatch.setattr(
        "hermes_cli.kanban_backend.maybe_run_kanban_sync",
        lambda **_kwargs: pytest.fail("backend sync called"),
    )
    monkeypatch.setattr(
        "hermes_cli.hades_kanban_sync.make_remote_admission",
        lambda *_args, **_kwargs: pytest.fail("remote admission called"),
    )
    monkeypatch.setattr("hermes_cli.config.load_config", lambda: {
        "kanban": {"dispatch_in_gateway": True, "auto_decompose": False},
    })
    monkeypatch.setattr(watchers, "_acquire_singleton_lock", lambda _path: (None, "unavailable"))
    monkeypatch.setattr("hermes_cli.profiles.profile_exists", lambda _name: True)
    monkeypatch.setattr("hermes_cli.kanban_db._default_spawn", lambda *_args, **_kwargs: 12345)
    real_dispatch_once = kb.dispatch_once
    dispatch_kwargs = []

    def _dispatch_once(*args, **kwargs):
        dispatch_kwargs.append(kwargs)
        return real_dispatch_once(*args, **kwargs)

    monkeypatch.setattr(kb, "dispatch_once", _dispatch_once)

    class _Runner(GatewayKanbanWatchersMixin):
        _running = True

    runner = _Runner()
    sleep_calls = 0

    async def _sleep(_seconds):
        nonlocal sleep_calls
        sleep_calls += 1
        if sleep_calls >= 2:
            runner._running = False

    monkeypatch.setattr(watchers.asyncio, "sleep", _sleep)
    asyncio.run(runner._kanban_dispatcher_watcher())

    assert dispatch_kwargs
    assert all("admission_fn" not in kwargs for kwargs in dispatch_kwargs)


def test_gateway_dispatch_runs_legacy_link_locally(tmp_path, monkeypatch):
    """A legacy link does not defer a gateway worker launch."""

    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.delenv("HERMES_KANBAN_DISPATCH_IN_GATEWAY", raising=False)
    kb.init_db()
    with kb.connect_closing() as conn:
        task_id = kb.create_task(conn, title="remote", assignee="default")
        kb.upsert_remote_link(
            conn,
            task_id=task_id,
            project_id="project",
            workspace_binding_id="binding",
            remote_work_item_id="work-item",
        )

    monkeypatch.setattr(
        "hermes_cli.kanban_backend.maybe_run_kanban_sync",
        lambda **_kwargs: pytest.fail("backend sync called"),
    )
    monkeypatch.setattr(
        "hermes_cli.hades_kanban_sync.make_remote_admission",
        lambda *_args, **_kwargs: pytest.fail("remote admission called"),
    )
    monkeypatch.setattr("hermes_cli.config.load_config", lambda: {
        "kanban": {"dispatch_in_gateway": True, "auto_decompose": False},
    })
    monkeypatch.setattr(watchers, "_acquire_singleton_lock", lambda _path: (None, "unavailable"))
    monkeypatch.setattr("hermes_cli.profiles.profile_exists", lambda _name: True)
    monkeypatch.setattr("hermes_cli.kanban_db._default_spawn", lambda *_args, **_kwargs: 12345)

    class _Runner(GatewayKanbanWatchersMixin):
        _running = True

    runner = _Runner()
    sleeps = 0

    async def _sleep(_seconds):
        nonlocal sleeps
        sleeps += 1
        if sleeps >= 2:
            runner._running = False

    monkeypatch.setattr(watchers.asyncio, "sleep", _sleep)
    asyncio.run(runner._kanban_dispatcher_watcher())

    with kb.connect_closing() as conn:
        assert kb.get_task(conn, task_id).status == "running"
