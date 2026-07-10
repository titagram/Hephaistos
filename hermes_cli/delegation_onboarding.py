"""Deterministic delegation recommendations from configured model inventory."""

from __future__ import annotations

from dataclasses import dataclass
from math import inf
from typing import Any, Mapping, Sequence

from hermes_cli.inventory import build_models_payload, load_picker_context
from tools.delegation_routing import load_delegation_routing


ROLE_ORDER = ("orchestrator", "leaf", "reviewer")


@dataclass(frozen=True)
class ConfiguredModel:
    provider: str
    model: str
    reasoning: bool = False
    fast: bool = False
    context_length: int = 0
    input_cost: float | None = None
    output_cost: float | None = None


@dataclass(frozen=True)
class DelegationRecommendation:
    provider: str
    model: str
    reason: str
    confidence: str


def _price(value: Any) -> float | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    text = str(value or "").strip().lower()
    if text == "free":
        return 0.0
    if text.startswith("$"):
        text = text[1:].replace(",", "")
    try:
        return float(text) if text else None
    except ValueError:
        return None


def normalize_inventory(payload: Mapping[str, Any]) -> list[ConfiguredModel]:
    """Flatten authenticated provider rows without retaining credentials."""
    result: list[ConfiguredModel] = []
    rows = payload.get("providers", []) if isinstance(payload, Mapping) else []
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, Mapping) or row.get("authenticated") is False:
            continue
        provider = str(row.get("slug") or "").strip()
        capabilities = row.get("capabilities") if isinstance(row.get("capabilities"), Mapping) else {}
        pricing = row.get("pricing") if isinstance(row.get("pricing"), Mapping) else {}
        for raw_model in row.get("models", []) if isinstance(row.get("models"), list) else []:
            model = str(raw_model or "").strip()
            if not provider or not model:
                continue
            caps = capabilities.get(model, {})
            caps = caps if isinstance(caps, Mapping) else {}
            prices = pricing.get(model, {})
            prices = prices if isinstance(prices, Mapping) else {}
            context = caps.get("context_length", row.get("context_length", 0))
            try:
                context_length = max(0, int(context or 0))
            except (TypeError, ValueError):
                context_length = 0
            result.append(ConfiguredModel(
                provider=provider,
                model=model,
                reasoning=caps.get("reasoning") is True,
                fast=caps.get("fast") is True,
                context_length=context_length,
                input_cost=_price(prices.get("input")),
                output_cost=_price(prices.get("output")),
            ))
    return result


def configured_models() -> list[ConfiguredModel]:
    """Load only providers already authenticated in the user's picker context."""
    payload = build_models_payload(
        load_picker_context(), pricing=True, capabilities=True,
    )
    return normalize_inventory(payload)


def _recommendation(model: ConfiguredModel, role: str, *, reused: bool) -> DelegationRecommendation:
    if reused:
        return DelegationRecommendation(
            model.provider, model.model,
            f"only configured model; explicitly reused for {role}", "high",
        )
    if role == "leaf":
        known_price = model.input_cost is not None and model.output_cost is not None
        reason = "lowest-cost compatible worker" if known_price else "stable configured worker selection"
        confidence = "high" if known_price else ("medium" if model.fast else "low")
    else:
        purpose = "agentic" if role == "orchestrator" else "verification"
        if model.reasoning:
            reason, confidence = f"strongest {purpose} reasoning", "high"
        elif model.context_length:
            reason, confidence = f"largest known context for {purpose}", "medium"
        else:
            reason, confidence = f"stable configured model for {purpose}", "low"
    return DelegationRecommendation(model.provider, model.model, reason, confidence)


def recommend_role_models(
    models: Sequence[ConfiguredModel],
) -> dict[str, DelegationRecommendation]:
    """Recommend each role using stable metadata-only scoring."""
    if not models:
        return {}
    strongest = max(models, key=lambda m: (
        m.reasoning, m.context_length,
        -(m.input_cost if m.input_cost is not None else inf),
        m.provider, m.model,
    ))
    cheapest = min(models, key=lambda m: (
        m.input_cost is None or m.output_cost is None,
        (m.input_cost or 0) + (m.output_cost or 0),
        not m.fast, m.provider, m.model,
    ))
    reused = len(models) == 1
    return {
        role: _recommendation(cheapest if role == "leaf" else strongest, role, reused=reused)
        for role in ROLE_ORDER
    }


def build_delegation_patch(
    recommendations: Mapping[str, DelegationRecommendation],
) -> dict[str, Any]:
    """Build and production-validate a credential-free delegation config patch."""
    missing = [role for role in ROLE_ORDER if role not in recommendations]
    if missing:
        raise ValueError(f"missing delegation recommendations: {', '.join(missing)}")
    profiles: dict[str, dict[str, Any]] = {}
    routes: dict[str, str] = {}
    for role in ROLE_ORDER:
        entry = recommendations[role]
        profile_name = f"recommended_{role}"
        profiles[profile_name] = {
            "provider": entry.provider,
            "model": entry.model,
            "max_iterations": 30 if role == "orchestrator" else 15,
            "child_timeout_seconds": 300 if role == "orchestrator" else 180,
        }
        routes[role] = profile_name
    patch = {
        "profiles": profiles,
        "role_routes": routes,
        "capacity_mode": "balanced",
        "max_spawn_depth": 2,
        "max_concurrent_children": 3,
        "max_async_children": 3,
    }
    load_delegation_routing({"delegation": patch})
    return patch
