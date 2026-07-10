"""Interactive setup for Hades delegated-agent model routing.

The command deliberately remains a CLI workflow rather than a model tool: it
changes user configuration, requires one explicit final confirmation, and
persists the complete ``delegation`` subtree atomically.
"""

from __future__ import annotations

import argparse
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Literal, Protocol, Sequence

import yaml

from hermes_cli.delegation_onboarding import (
    ROLE_ORDER,
    ConfiguredModel,
    DelegationRecommendation,
    build_delegation_patch,
    configured_models,
    recommend_role_models,
)
from tools.delegation_routing import load_delegation_routing, resolve_role_profile
from utils import atomic_roundtrip_yaml_update


class PromptIO(Protocol):
    """Minimal injectable terminal interface used by the wizard."""

    def write(self, message: str) -> None: ...

    def choose_model(
        self,
        role: str,
        choices: Sequence[ConfiguredModel],
        default: ConfiguredModel,
    ) -> ConfiguredModel: ...

    def confirm(self, message: str) -> bool: ...


@dataclass(frozen=True)
class WizardResult:
    code: int
    message: str
    next_command: str | None = None
    patch: dict | None = None


class ConsolePrompt:
    """Simple numbered prompt suitable for the classic CLI and tests."""

    def write(self, message: str) -> None:
        print(message)

    def choose_model(
        self,
        role: str,
        choices: Sequence[ConfiguredModel],
        default: ConfiguredModel,
    ) -> ConfiguredModel:
        self.write(f"\nModel for {role}:")
        default_index = choices.index(default)
        for index, model in enumerate(choices, 1):
            marker = " (default)" if index - 1 == default_index else ""
            self.write(f"  {index}. {model.provider}/{model.model}{marker}")
        while True:
            raw = input(f"Select [default {default_index + 1}]: ").strip()
            if not raw:
                return default
            try:
                selected = int(raw) - 1
            except ValueError:
                selected = -1
            if 0 <= selected < len(choices):
                return choices[selected]
            self.write(f"Enter a number from 1 to {len(choices)}.")

    def confirm(self, message: str) -> bool:
        return input(f"{message} [y/N]: ").strip().lower() in {"y", "yes"}


def build_parser(subparsers, *, cmd_delegation) -> None:
    parser = subparsers.add_parser(
        "delegation",
        help="Configure Hades subagent routing",
    )
    sub = parser.add_subparsers(dest="delegation_action", required=True)
    for action in ("setup", "configure"):
        child = sub.add_parser(
            action,
            help=(
                "Create routing when none exists"
                if action == "setup"
                else "Review or replace existing routing"
            ),
        )
        child.set_defaults(func=cmd_delegation)


def _active_config_path() -> Path:
    from hermes_cli.config import get_config_path

    return get_config_path()


def _load_config(path: Path) -> dict:
    if not path.exists():
        return {}
    loaded = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(loaded, dict):
        raise ValueError("config.yaml root must be a mapping")
    return loaded


def _complete_routing(config: dict):
    """Return parsed routing only when all runtime roles resolve."""
    raw = config.get("delegation")
    if not isinstance(raw, dict):
        return None
    try:
        routing = load_delegation_routing(config)
    except ValueError:
        return None
    if all(resolve_role_profile(routing, role) is not None for role in ROLE_ORDER):
        return routing
    return None


def _run_model_setup() -> bool:
    """Run the existing model setup flow without duplicating credential UX."""
    from hermes_cli.main import cmd_model

    cmd_model(argparse.Namespace(refresh=False))
    return True


def _model_key(model: ConfiguredModel) -> tuple[str, str]:
    return model.provider, model.model


def _defaults_for_roles(models, recommendations, current_routing):
    by_key = {_model_key(model): model for model in models}
    defaults = {}
    for role in ROLE_ORDER:
        current = (
            resolve_role_profile(current_routing, role)
            if current_routing is not None
            else None
        )
        defaults[role] = by_key.get(
            (current.provider, current.model) if current else ("", ""),
            by_key[(recommendations[role].provider, recommendations[role].model)],
        )
    return defaults


def _selected_recommendations(models, defaults, recommendations, prompt: PromptIO):
    selected = {}
    for role in ROLE_ORDER:
        suggestion = recommendations[role]
        prompt.write(
            f"{role}: recommended {suggestion.provider}/{suggestion.model} — "
            f"{suggestion.reason} (confidence: {suggestion.confidence})"
        )
        chosen = prompt.choose_model(role, models, defaults[role])
        if chosen not in models:
            raise ValueError(f"prompt returned an unconfigured model for {role}")
        if _model_key(chosen) == (suggestion.provider, suggestion.model):
            selected[role] = suggestion
        else:
            selected[role] = DelegationRecommendation(
                chosen.provider,
                chosen.model,
                "user-selected configured model",
                "low",
            )
    return selected


def run_delegation_wizard(
    mode: Literal["setup", "configure"],
    *,
    inventory_loader: Callable[[], list[ConfiguredModel]] = configured_models,
    model_setup: Callable[[], bool] = _run_model_setup,
    prompt: PromptIO | None = None,
    config_path: Path | None = None,
) -> WizardResult:
    """Configure role routing from authenticated models with one final write."""
    if mode not in {"setup", "configure"}:
        return WizardResult(2, f"unsupported delegation action: {mode}")
    prompt = prompt or ConsolePrompt()
    path = Path(config_path) if config_path is not None else _active_config_path()
    try:
        config = _load_config(path)
    except (OSError, ValueError, yaml.YAMLError) as exc:
        return WizardResult(1, f"Cannot read active config: {exc}")

    current_routing = _complete_routing(config)
    if mode == "setup" and current_routing is not None:
        return WizardResult(
            2,
            "Delegation routing is already configured; setup made no changes.",
            next_command="hades delegation configure",
        )

    models = inventory_loader()
    if not models:
        prompt.write("No configured models are available; starting Hades model setup.")
        if not model_setup():
            return WizardResult(1, "Model setup did not complete.", next_command="hades model")
        models = inventory_loader()
    if not models:
        return WizardResult(
            1,
            "No authenticated model is configured.",
            next_command="hades model",
        )

    recommendations = recommend_role_models(models)
    defaults = _defaults_for_roles(models, recommendations, current_routing)
    try:
        selected = _selected_recommendations(models, defaults, recommendations, prompt)
        patch = build_delegation_patch(selected)
    except ValueError as exc:
        return WizardResult(1, f"Cannot build delegation routing: {exc}")

    prompt.write(
        "Capacity ceilings:\n"
        f"  max_spawn_depth: {patch['max_spawn_depth']}\n"
        f"  max_concurrent_children: {patch['max_concurrent_children']}\n"
        f"  max_async_children: {patch['max_async_children']}"
    )
    preview = yaml.safe_dump({"delegation": patch}, sort_keys=False).rstrip()
    prompt.write(f"Final configuration:\n{preview}")
    if not prompt.confirm("Write this delegation configuration?"):
        return WizardResult(1, "Delegation configuration was not changed.", patch=patch)

    try:
        atomic_roundtrip_yaml_update(path, "delegation", patch)
        if os.name != "nt":
            os.chmod(path, 0o600)
    except OSError as exc:
        return WizardResult(1, f"Cannot save delegation configuration: {exc}")
    return WizardResult(0, "Delegation routing configured.", patch=patch)


def delegation_command(args: argparse.Namespace) -> int:
    result = run_delegation_wizard(getattr(args, "delegation_action", ""))
    print(result.message)
    if result.next_command:
        print(f"Next: {result.next_command}")
    return result.code
