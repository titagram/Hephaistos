import argparse
import os
from pathlib import Path

import yaml

from hermes_cli.delegation_onboarding import ConfiguredModel
from hermes_cli.hades_delegation_cmd import (
    ConsolePrompt,
    build_parser,
    run_delegation_wizard,
)


VALID_ROUTING = {
    "delegation": {
        "profiles": {
            "recommended_orchestrator": {
                "provider": "openrouter",
                "model": "strong",
                "max_iterations": 30,
                "child_timeout_seconds": 300,
            },
            "recommended_leaf": {
                "provider": "openrouter",
                "model": "cheap",
                "max_iterations": 15,
                "child_timeout_seconds": 180,
            },
            "recommended_reviewer": {
                "provider": "openrouter",
                "model": "strong",
                "max_iterations": 15,
                "child_timeout_seconds": 180,
            },
        },
        "role_routes": {
            "orchestrator": "recommended_orchestrator",
            "leaf": "recommended_leaf",
            "reviewer": "recommended_reviewer",
        },
        "capacity_mode": "balanced",
        "max_spawn_depth": 2,
        "max_concurrent_children": 3,
        "max_async_children": 3,
    }
}


def configured(provider: str, model: str, **kwargs) -> ConfiguredModel:
    return ConfiguredModel(provider=provider, model=model, **kwargs)


def write_config(path: Path, value: dict) -> None:
    path.write_text(yaml.safe_dump(value, sort_keys=False), encoding="utf-8")


class RecordingPrompt:
    def __init__(self, *, accept: bool = True, picks: dict[str, str] | None = None):
        self.accept = accept
        self.picks = picks or {}
        self.defaults: dict[str, str] = {}
        self.messages: list[str] = []
        self.confirmations = 0

    def write(self, message: str) -> None:
        self.messages.append(message)

    def choose_model(self, role, choices, default):
        self.defaults[role] = f"{default.provider}/{default.model}"
        wanted = self.picks.get(role)
        if wanted:
            return next(model for model in choices if f"{model.provider}/{model.model}" == wanted)
        return default

    def confirm(self, message: str) -> bool:
        self.confirmations += 1
        self.messages.append(message)
        return self.accept


class FailingPrompt:
    def __getattr__(self, name):
        raise AssertionError(f"prompt must not be used: {name}")


def test_parser_requires_setup_or_configure():
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command")
    sentinel = object()
    build_parser(subparsers, cmd_delegation=sentinel)

    with __import__("pytest").raises(SystemExit):
        parser.parse_args(["delegation"])
    for action in ("setup", "configure"):
        args = parser.parse_args(["delegation", action])
        assert args.delegation_action == action
        assert args.func is sentinel


def test_setup_refuses_to_replace_valid_routing(tmp_path):
    config_path = tmp_path / "config.yaml"
    write_config(config_path, VALID_ROUTING)
    before = config_path.read_bytes()

    result = run_delegation_wizard(
        "setup",
        inventory_loader=lambda: [configured("openrouter", "other")],
        model_setup=lambda: True,
        prompt=FailingPrompt(),
        config_path=config_path,
    )

    assert result.code == 2
    assert result.next_command == "hades delegation configure"
    assert config_path.read_bytes() == before


def test_empty_inventory_runs_model_setup_then_resumes(tmp_path):
    calls = []
    inventories = iter(([], [configured("openrouter", "m")]))
    prompt = RecordingPrompt()

    result = run_delegation_wizard(
        "setup",
        inventory_loader=lambda: next(inventories),
        model_setup=lambda: calls.append("model") or True,
        prompt=prompt,
        config_path=tmp_path / "config.yaml",
    )

    assert calls == ["model"]
    assert result.code == 0
    assert prompt.confirmations == 1
    assert yaml.safe_load((tmp_path / "config.yaml").read_text())["delegation"] == result.patch


def test_empty_inventory_after_model_setup_is_a_non_mutating_error(tmp_path):
    config_path = tmp_path / "config.yaml"
    result = run_delegation_wizard(
        "setup",
        inventory_loader=lambda: [],
        model_setup=lambda: True,
        prompt=RecordingPrompt(),
        config_path=config_path,
    )
    assert result.code == 1
    assert result.next_command == "hades model"
    assert not config_path.exists()


def test_declining_final_confirmation_does_not_write(tmp_path):
    config_path = tmp_path / "config.yaml"
    config_path.write_text("# keep me\nmodel: existing\n", encoding="utf-8")
    before = config_path.read_bytes()
    prompt = RecordingPrompt(accept=False)

    result = run_delegation_wizard(
        "setup",
        inventory_loader=lambda: [configured("p", "m")],
        model_setup=lambda: True,
        prompt=prompt,
        config_path=config_path,
    )

    assert result.code == 1
    assert prompt.confirmations == 1
    assert config_path.read_bytes() == before


def test_configure_uses_current_role_models_as_defaults(tmp_path):
    config_path = tmp_path / "config.yaml"
    write_config(config_path, VALID_ROUTING)
    prompt = RecordingPrompt(accept=False)
    inventory = [
        configured("openrouter", "strong", reasoning=True),
        configured("openrouter", "cheap", input_cost=1, output_cost=1),
    ]

    result = run_delegation_wizard(
        "configure",
        inventory_loader=lambda: inventory,
        model_setup=lambda: True,
        prompt=prompt,
        config_path=config_path,
    )

    assert result.code == 1
    assert prompt.defaults == {
        "orchestrator": "openrouter/strong",
        "leaf": "openrouter/cheap",
        "reviewer": "openrouter/strong",
    }


def test_wizard_shows_reasons_capacity_and_yaml_then_preserves_comment_and_mode(tmp_path):
    config_path = tmp_path / "config.yaml"
    config_path.write_text("# user comment\nmodel: existing\n", encoding="utf-8")
    os.chmod(config_path, 0o644)
    prompt = RecordingPrompt()

    result = run_delegation_wizard(
        "setup",
        inventory_loader=lambda: [
            configured("p", "strong", reasoning=True),
            configured("p", "cheap", input_cost=1, output_cost=1),
        ],
        model_setup=lambda: True,
        prompt=prompt,
        config_path=config_path,
    )

    output = "\n".join(prompt.messages)
    assert result.code == 0
    assert "strongest agentic reasoning" in output
    assert "lowest-cost compatible worker" in output
    assert "max_spawn_depth: 2" in output
    assert "role_routes:" in output
    assert "# user comment" in config_path.read_text(encoding="utf-8")
    if os.name != "nt":
        assert config_path.stat().st_mode & 0o777 == 0o600


def test_console_prompt_reprompts_for_invalid_choice(monkeypatch):
    replies = iter(("99", "2"))
    monkeypatch.setattr("builtins.input", lambda _: next(replies))
    prompt = ConsolePrompt()
    choices = [configured("p", "a"), configured("p", "b")]
    assert prompt.choose_model("leaf", choices, choices[0]) == choices[1]


def test_wizard_preserves_config_symlink(tmp_path):
    target = tmp_path / "real-config.yaml"
    target.write_text("# linked config\nmodel: existing\n", encoding="utf-8")
    config_path = tmp_path / "config.yaml"
    config_path.symlink_to(target)

    result = run_delegation_wizard(
        "setup",
        inventory_loader=lambda: [configured("p", "m")],
        model_setup=lambda: True,
        prompt=RecordingPrompt(),
        config_path=config_path,
    )

    assert result.code == 0
    assert config_path.is_symlink()
    assert "# linked config" in target.read_text(encoding="utf-8")
    assert yaml.safe_load(target.read_text(encoding="utf-8"))["delegation"] == result.patch
