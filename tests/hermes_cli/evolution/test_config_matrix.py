"""Exhaustive effective-config contracts for the evolution namespace."""

from __future__ import annotations

import copy
import math
import os
from pathlib import Path
from typing import Any

import pytest

from hermes_cli.config import (
    DEFAULT_CONFIG,
    _normalize_evolution_config,
    load_config,
)


DEFAULT_EVOLUTION = DEFAULT_CONFIG["evolution"]
LEAVES = (
    (("enabled",), False, True),
    (("observer", "enabled"), False, True),
    (("observer", "recurrence_threshold"), 1, 1000),
    (("observer", "scan_interval_seconds"), 1, 86400),
    (("observer", "notice_min_score"), 0, 1),
    (("authorization", "research_ttl_seconds"), 1, 2_592_000),
    (("authorization", "build_ttl_seconds"), 1, 2_592_000),
    (("authorization", "promotion_ttl_seconds"), 1, 2_592_000),
    (("retention", "workspaces"), 0, 1000),
    (("retention", "evidence_days"), 0, 3650),
)


def _at(mapping: dict[str, Any], path: tuple[str, ...]) -> object:
    current: object = mapping
    for name in path:
        assert isinstance(current, dict)
        current = current[name]
    return current


def _with_leaf(path: tuple[str, ...], value: object) -> dict[str, object]:
    evolution = copy.deepcopy(DEFAULT_EVOLUTION)
    current = evolution
    for name in path[:-1]:
        current = current[name]
    current[path[-1]] = value
    return {
        "unrelated": {"preserved": True},
        "evolution": evolution,
    }


def _reset_config_caches() -> None:
    import hermes_cli.config as config_module
    from hermes_cli import managed_scope

    config_module._LOAD_CONFIG_CACHE.clear()
    config_module._RAW_CONFIG_CACHE.clear()
    managed_scope.invalidate_managed_cache()


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (path, boundary)
        for path, minimum, maximum in LEAVES
        for boundary in (minimum, maximum)
    ],
    ids=lambda value: ".".join(value) if isinstance(value, tuple) else repr(value),
)
def test_every_evolution_leaf_accepts_its_inclusive_boundaries(
    path: tuple[str, ...],
    value: object,
) -> None:
    normalized = _normalize_evolution_config(_with_leaf(path, value))
    assert _at(normalized["evolution"], path) == value
    assert normalized["unrelated"] == {"preserved": True}


def _invalid_values(
    path: tuple[str, ...],
    minimum: object,
    maximum: object,
) -> tuple[object, ...]:
    if path in {("enabled",), ("observer", "enabled")}:
        return (0, 1, "true", None, [], {})
    if path == ("observer", "notice_min_score"):
        return (
            -0.001,
            1.001,
            True,
            False,
            "0.5",
            None,
            [],
            {},
            math.nan,
            math.inf,
            -math.inf,
        )
    assert isinstance(minimum, int) and isinstance(maximum, int)
    return (
        minimum - 1,
        maximum + 1,
        True,
        False,
        float(minimum),
        str(minimum),
        None,
        [],
        {},
        math.nan,
        math.inf,
        -math.inf,
    )


INVALID_LEAVES = tuple(
    (path, invalid)
    for path, minimum, maximum in LEAVES
    for invalid in _invalid_values(path, minimum, maximum)
)


@pytest.mark.parametrize(
    ("path", "invalid"),
    INVALID_LEAVES,
    ids=lambda value: ".".join(value) if isinstance(value, tuple) else repr(value),
)
def test_every_evolution_leaf_rejects_wrong_types_bool_as_int_and_bad_ranges(
    path: tuple[str, ...],
    invalid: object,
) -> None:
    normalized = _normalize_evolution_config(_with_leaf(path, invalid))
    assert _at(normalized["evolution"], path) == _at(DEFAULT_EVOLUTION, path)
    assert normalized["unrelated"] == {"preserved": True}


@pytest.mark.parametrize("invalid", [None, "evolution", [], (), 1, True])
def test_invalid_evolution_container_falls_back_without_losing_unrelated_config(
    invalid: object,
) -> None:
    normalized = _normalize_evolution_config({
        "unrelated": {"preserved": True},
        "evolution": invalid,
    })
    assert normalized["evolution"] == DEFAULT_EVOLUTION
    assert normalized["unrelated"] == {"preserved": True}


@pytest.mark.parametrize(
    "section",
    ["observer", "authorization", "retention"],
)
@pytest.mark.parametrize("invalid", [None, "section", [], (), 1, True])
def test_invalid_nested_container_falls_back_independently(
    section: str,
    invalid: object,
) -> None:
    evolution = copy.deepcopy(DEFAULT_EVOLUTION)
    evolution["enabled"] = False
    evolution[section] = invalid
    normalized = _normalize_evolution_config({
        "unrelated": {"preserved": True},
        "evolution": evolution,
    })
    assert normalized["evolution"]["enabled"] is False
    assert normalized["evolution"][section] == DEFAULT_EVOLUTION[section]
    assert normalized["unrelated"] == {"preserved": True}


def test_canonical_load_normalizes_after_managed_overlay_and_never_persists_defaults(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "home"
    managed = tmp_path / "managed"
    home.mkdir()
    managed.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv("HERMES_MANAGED_DIR", str(managed))
    user_bytes = (
        b"unrelated:\n"
        b"  preserved: true\n"
        b"evolution:\n"
        b"  enabled: false\n"
        b"  observer:\n"
        b"    recurrence_threshold: 7\n"
        b"    notice_min_score: 0.8\n"
        b"  retention:\n"
        b"    workspaces: 12\n"
    )
    (home / "config.yaml").write_bytes(user_bytes)
    (managed / "config.yaml").write_text(
        "evolution:\n"
        "  observer:\n"
        "    recurrence_threshold: true\n"
        "    notice_min_score: .inf\n"
        "  authorization:\n"
        "    research_ttl_seconds: 0\n",
        encoding="utf-8",
    )
    _reset_config_caches()

    config = load_config()

    assert config["unrelated"] == {"preserved": True}
    assert config["evolution"]["enabled"] is False
    assert (
        config["evolution"]["observer"]["recurrence_threshold"]
        == DEFAULT_EVOLUTION["observer"]["recurrence_threshold"]
    )
    assert (
        config["evolution"]["observer"]["notice_min_score"]
        == DEFAULT_EVOLUTION["observer"]["notice_min_score"]
    )
    assert (
        config["evolution"]["authorization"]["research_ttl_seconds"]
        == DEFAULT_EVOLUTION["authorization"]["research_ttl_seconds"]
    )
    assert config["evolution"]["retention"]["workspaces"] == 12
    assert _normalize_evolution_config(config) == config
    assert (home / "config.yaml").read_bytes() == user_bytes


def test_loading_missing_evolution_config_returns_defaults_without_writing_them(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    raw = b"unrelated:\n  preserved: true\n"
    (home / "config.yaml").write_bytes(raw)
    _reset_config_caches()

    config = load_config()

    assert config["evolution"] == DEFAULT_EVOLUTION
    assert config["unrelated"] == {"preserved": True}
    assert (home / "config.yaml").read_bytes() == raw


def test_classic_gateway_and_tui_loaders_normalize_their_final_managed_overlay(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "home"
    managed = tmp_path / "managed"
    home.mkdir()
    managed.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv("HERMES_MANAGED_DIR", str(managed))
    (home / "config.yaml").write_text(
        "unrelated:\n"
        "  preserved: true\n"
        "evolution:\n"
        "  enabled: false\n"
        "  observer:\n"
        "    recurrence_threshold: 7\n"
        "  retention:\n"
        "    workspaces: 12\n",
        encoding="utf-8",
    )
    (managed / "config.yaml").write_text(
        "evolution:\n"
        "  observer:\n"
        "    recurrence_threshold: true\n"
        "    notice_min_score: -.inf\n"
        "  authorization:\n"
        "    build_ttl_seconds: false\n",
        encoding="utf-8",
    )
    _reset_config_caches()
    environment_before = {
        key: value
        for key, value in os.environ.items()
        if key.startswith("HERMES_EVOLUTION")
    }

    import cli
    import gateway.run as gateway_run
    import tui_gateway.server as tui_server

    monkeypatch.setattr(cli, "_hermes_home", home)
    monkeypatch.setattr(gateway_run, "_hermes_home", home)
    monkeypatch.setattr(tui_server, "_hermes_home", home)
    monkeypatch.setattr(tui_server, "_cfg_cache", None)
    monkeypatch.setattr(tui_server, "_cfg_mtime", None)
    monkeypatch.setattr(tui_server, "_cfg_path", None)
    monkeypatch.setattr(
        tui_server,
        "get_hermes_home_override",
        lambda: None,
    )

    configs = (
        cli.load_cli_config(),
        gateway_run._load_gateway_config(),
        tui_server._load_cfg(),
    )

    for config in configs:
        assert config["unrelated"] == {"preserved": True}
        evolution = config["evolution"]
        assert evolution["enabled"] is False
        assert (
            evolution["observer"]["recurrence_threshold"]
            == DEFAULT_EVOLUTION["observer"]["recurrence_threshold"]
        )
        assert (
            evolution["observer"]["notice_min_score"]
            == DEFAULT_EVOLUTION["observer"]["notice_min_score"]
        )
        assert (
            evolution["authorization"]["build_ttl_seconds"]
            == DEFAULT_EVOLUTION["authorization"]["build_ttl_seconds"]
        )
        assert evolution["retention"]["workspaces"] == 12
    environment_after = {
        key: value
        for key, value in os.environ.items()
        if key.startswith("HERMES_EVOLUTION")
    }
    assert environment_after == environment_before


def test_evolution_has_no_user_facing_behavioral_environment_variable() -> None:
    root = Path(__file__).resolve().parents[3]
    paths = (
        root / "hermes_cli" / "config.py",
        root / "hermes_cli" / "evolution" / "bootstrap.py",
        root / "hermes_cli" / "evolution" / "command.py",
        root / "cli.py",
        root / "gateway" / "run.py",
        root / "tui_gateway" / "server.py",
    )
    assert all("HERMES_EVOLUTION" not in path.read_text() for path in paths)
