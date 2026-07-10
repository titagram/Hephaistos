from unittest.mock import patch

from hermes_cli.delegation_onboarding import (
    ConfiguredModel,
    build_delegation_patch,
    configured_models,
    normalize_inventory,
    recommend_role_models,
)
from tools.delegation_routing import load_delegation_routing


def configured(provider: str, model: str, **kwargs) -> ConfiguredModel:
    return ConfiguredModel(provider=provider, model=model, **kwargs)


def test_recommendations_use_only_authenticated_rows():
    payload = {"providers": [
        {"slug": "openrouter", "authenticated": True, "models": ["strong", "cheap"], "capabilities": {"strong": {"reasoning": True}, "cheap": {"reasoning": False}}},
        {"slug": "other", "authenticated": False, "models": ["forbidden"]},
    ], "model": "strong", "provider": "openrouter"}
    result = recommend_role_models(normalize_inventory(payload))
    assert {x.model for x in result.values()} <= {"strong", "cheap"}
    assert result["orchestrator"].model == "strong"
    assert result["reviewer"].model == "strong"


def test_single_model_is_explicitly_reused_for_all_roles():
    result = recommend_role_models([configured("p", "only")])
    assert {entry.model for entry in result.values()} == {"only"}
    assert all("only configured model" in entry.reason for entry in result.values())


def test_unknown_pricing_sorts_after_known_pricing_for_leaf():
    models = [
        configured("p", "unknown", fast=True),
        configured("p", "known", input_cost=2, output_cost=3),
    ]
    assert recommend_role_models(models)["leaf"].model == "known"


def test_ties_are_deterministic_and_reasons_only_claim_known_metadata():
    models = [configured("z", "b"), configured("a", "c"), configured("a", "a")]
    first = recommend_role_models(models)
    second = recommend_role_models(list(reversed(models)))
    assert first == second
    assert first["leaf"].model == "a"
    assert "lowest-cost" not in first["leaf"].reason
    assert all(entry.confidence in {"low", "medium", "high"} for entry in first.values())


def test_configured_models_uses_enriched_authenticated_inventory():
    payload = {"providers": [{"slug": "p", "authenticated": True, "models": ["m"]}]}
    with (
        patch("hermes_cli.delegation_onboarding.load_picker_context", return_value=object()),
        patch("hermes_cli.delegation_onboarding.build_models_payload", return_value=payload) as build,
    ):
        assert configured_models() == [configured("p", "m")]
    build.assert_called_once_with(build.call_args.args[0], pricing=True, capabilities=True)


def test_patch_has_exact_shape_contains_no_credentials_and_round_trips():
    recommendations = recommend_role_models([
        configured("p", "strong", reasoning=True),
        configured("p", "cheap", input_cost=1, output_cost=1),
    ])
    result = build_delegation_patch(recommendations)
    assert set(result) == {
        "profiles", "role_routes", "capacity_mode", "max_spawn_depth",
        "max_concurrent_children", "max_async_children",
    }
    assert "api_key" not in repr(result)
    parsed = load_delegation_routing({"delegation": result})
    assert parsed.capacity_mode == result["capacity_mode"]
    assert parsed.max_spawn_depth == result["max_spawn_depth"]
    assert set(parsed.role_routes) == {"orchestrator", "leaf", "reviewer"}
