import pytest

from tools.delegation_capacity import (
    CapacityRequest,
    CapacitySnapshot,
    decide_capacity,
    probe_capacity,
)


def snapshot(**overrides):
    values = {
        "role": "leaf",
        "requested_iterations": 10,
        "depth": 0,
        "max_depth": 3,
        "iterations_remaining": 10,
        "children_remaining": 1,
        "active_processes": 0,
        "max_processes": 4,
        "active_agents": 0,
        "max_active_agents": 3,
        "provider_available": True,
        "memory_pressure": 0.25,
    }
    values.update(overrides)
    return CapacitySnapshot(**values)


@pytest.mark.parametrize(
    ("capacity, expected"),
    [
        (snapshot(memory_pressure=0.95), "queue"),
        (snapshot(provider_available=False), "queue"),
        (snapshot(depth=3, max_depth=3), "degrade_to_leaf"),
        (snapshot(iterations_remaining=0), "replan"),
        (snapshot(children_remaining=0), "replan"),
        (snapshot(active_agents=3, max_active_agents=3), "queue"),
        (snapshot(), "allow"),
    ],
)
def test_capacity_decisions(capacity, expected):
    assert decide_capacity(capacity).action == expected


def test_unknown_probe_metrics_do_not_override_configured_ceilings(monkeypatch):
    monkeypatch.setattr("tools.delegation_capacity._memory_pressure", lambda: None)
    request = CapacityRequest(
        role="leaf",
        requested_iterations=5,
        depth=0,
        max_depth=2,
        budget_snapshot={
            "children_remaining": 0,
            "iterations_remaining": 100,
        },
        provider_available=None,
        active_agents=None,
        max_active_agents=3,
    )

    capacity = probe_capacity(request)

    assert capacity.memory_pressure is None
    assert decide_capacity(capacity).action == "replan"


def test_request_larger_than_remaining_iteration_budget_replans():
    assert (
        decide_capacity(
            snapshot(requested_iterations=11, iterations_remaining=10)
        ).action
        == "replan"
    )
