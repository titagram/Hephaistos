import pytest

from tools.delegation_routing import (
    DelegationProfile,
    load_delegation_routing,
    resolve_role_profile,
)


def test_empty_config_keeps_legacy_delegation():
    routing = load_delegation_routing({})
    assert routing.profiles == {}
    assert resolve_role_profile(routing, "leaf") is None


def test_allow_list_routes_to_named_profile():
    routing = load_delegation_routing({
        "delegation": {
            "profiles": {
                "fast_leaf": {
                    "provider": "openrouter",
                    "model": "model-x",
                    "max_iterations": 12,
                    "child_timeout_seconds": 90,
                },
            },
            "role_routes": {"leaf": "fast_leaf"},
        },
    })
    assert resolve_role_profile(routing, "leaf") == DelegationProfile(
        provider="openrouter", model="model-x", reasoning_effort=None,
        max_iterations=12, child_timeout_seconds=90,
    )


def test_reviewer_routes_to_named_profile():
    routing = load_delegation_routing({
        "delegation": {
            "profiles": {
                "careful_review": {
                    "provider": "openrouter",
                    "model": "review-model",
                    "reasoning_effort": "high",
                    "max_iterations": 20,
                    "child_timeout_seconds": 120,
                },
            },
            "role_routes": {"reviewer": "careful_review"},
        },
    })
    assert resolve_role_profile(routing, "reviewer") == DelegationProfile(
        provider="openrouter", model="review-model", reasoning_effort="high",
        max_iterations=20, child_timeout_seconds=120,
    )


@pytest.mark.parametrize("config", [
    {"delegation": {"role_routes": {"admin": "x"}}},
    {"delegation": {"profiles": {"x": {"provider": "", "model": "m", "max_iterations": 1, "child_timeout_seconds": 1}}, "role_routes": {"leaf": "x"}}},
    {"delegation": {"profiles": {"x": {"provider": "p", "model": "m", "api_key": "secret", "max_iterations": 1, "child_timeout_seconds": 1}}, "role_routes": {"leaf": "x"}}},
])
def test_routing_rejects_unsafe_or_invalid_config(config):
    with pytest.raises(ValueError):
        load_delegation_routing(config)
