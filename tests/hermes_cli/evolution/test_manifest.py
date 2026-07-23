"""Contracts for content-addressed evolution generation manifests."""

from __future__ import annotations

import hashlib
from copy import deepcopy
from pathlib import Path

import pytest

from hermes_cli.evolution.contract import EvolutionContractError
from hermes_cli.evolution.manifest import (
    generation_id_for,
    identity_payload,
    validate_manifest,
)


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _manifest(*, payload: bytes = b"echo safe\n") -> dict[str, object]:
    digest = _digest(payload)
    return {
        "schema_version": 1,
        "parent_generation_id": "a" * 64,
        "source_suggestion_id": "suggestion-1",
        "blueprint_digest": "b" * 64,
        "stable_base": {
            "release": "0.17.0",
            "repository_commit": "c" * 40,
            "compatibility_version": "1",
            "configuration_fingerprint": "d" * 64,
        },
        "compatibility_range": ">=1,<2",
        "components": [
            {
                "class": "script",
                "logical_id": "hello",
                "path": "bin/hello.sh",
                "digest": digest,
                "source": "https://example.test/hello",
                "author": "Hermes Maintainer",
                "license": "MIT",
                "provenance": "upstream-release",
                "capabilities": ["hello"],
                "lockfiles": [
                    {
                        "path": "locks/hello.lock",
                        "digest": _digest(b"hello==1\n"),
                    }
                ],
            }
        ],
        "dependency_constraints": ["hello>=1,<2"],
        "resolved_versions": {"hello": "1.0"},
        "credential_references": ["hello_service_token"],
        "service_prerequisites": ["hello-service"],
        "capabilities": ["hello"],
        "invariants": ["hello-is-safe"],
        "verification_commands": ["hello --verify"],
        "canary_policy": {"side_effects": "none"},
        "resource_ceilings": {"cpu_seconds": 10},
        "expected_organism_diff": "adds hello command",
        "build_environment": {"builder": "hermes", "version": "1"},
        "builder_version": "1",
        "rollback_plan": "remove hello component",
        "incompatibility_reasons": [],
        "created_at": "2026-07-23T12:34:56.123456Z",
        "attestations": {"mutable": "later"},
    }


def _stage(root: Path, manifest: dict[str, object]) -> None:
    component = manifest["components"][0]  # type: ignore[index]
    path = root / component["path"]  # type: ignore[index]
    path.parent.mkdir(parents=True)
    path.write_bytes(b"echo safe\n")
    lockfile = root / component["lockfiles"][0]["path"]  # type: ignore[index]
    lockfile.parent.mkdir(parents=True)
    lockfile.write_bytes(b"hello==1\n")


def test_identity_excludes_only_mutable_top_level_fields() -> None:
    manifest = _manifest()
    changed = deepcopy(manifest)
    changed["generation_id"] = "f" * 64
    changed["created_at"] = "2027-01-01T00:00:00.000000Z"
    changed["attestations"] = {"mutable": "changed"}

    assert identity_payload(manifest) == identity_payload(changed)
    assert generation_id_for(manifest) == generation_id_for(changed)

    changed["rollback_plan"] = "different plan"
    assert generation_id_for(manifest) != generation_id_for(changed)


@pytest.mark.parametrize(
    ("mutate", "code"),
    [
        (lambda m: m["components"][0].__setitem__("path", "/tmp/a"), "invalid_relative_posix_path"),  # type: ignore[index]
        (lambda m: m["components"][0].__setitem__("source", "file:///tmp/a"), "invalid_manifest"),  # type: ignore[index]
        (lambda m: m.__setitem__("api_key", "sk_live_not_allowed"), "invalid_manifest"),
        (lambda m: m["components"][0].pop("license"), "invalid_manifest"),  # type: ignore[index]
        (lambda m: m["components"].append(deepcopy(m["components"][0])), "invalid_manifest"),  # type: ignore[index]
        (lambda m: m["components"][0].__setitem__("class", "wheel"), "invalid_manifest"),  # type: ignore[index]
        (lambda m: m.__setitem__("schema_version", 2), "invalid_manifest"),
    ],
)
def test_manifest_rejects_unsafe_or_incomplete_identity_fields(mutate, code: str) -> None:
    manifest = _manifest()
    mutate(manifest)

    with pytest.raises(EvolutionContractError) as raised:
        validate_manifest(manifest)

    assert raised.value.code == code


def test_manifest_rehashes_declared_component_and_lockfile_bytes(tmp_path: Path) -> None:
    manifest = _manifest()
    stage = tmp_path / "stage"
    _stage(stage, manifest)
    validate_manifest(manifest, stage)

    (stage / "bin/hello.sh").write_bytes(b"tampered\n")
    with pytest.raises(EvolutionContractError, match="invalid_manifest"):
        validate_manifest(manifest, stage)


def test_manifest_rejects_a_symlinked_parent_of_declared_bytes(tmp_path: Path) -> None:
    manifest = _manifest()
    source = tmp_path / "source"
    source.mkdir()
    (source / "hello.sh").write_bytes(b"echo safe\n")
    (tmp_path / "bin").symlink_to(source, target_is_directory=True)
    (tmp_path / "locks").mkdir()
    (tmp_path / "locks/hello.lock").write_bytes(b"hello==1\n")

    with pytest.raises(EvolutionContractError, match="invalid_manifest"):
        validate_manifest(manifest, tmp_path)


def test_manifest_rejects_generation_id_not_matching_identity() -> None:
    manifest = _manifest()
    manifest["generation_id"] = "0" * 64

    with pytest.raises(EvolutionContractError, match="invalid_manifest"):
        validate_manifest(manifest)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda m: m["build_environment"].__setitem__("workspace", "/private/tmp"),
        lambda m: m["components"][0].__setitem__("source", "https://user@example.test/a"),  # type: ignore[index]
        lambda m: m.__setitem__("credential_references", ["service/token"]),
        lambda m: m.__setitem__("created_at", "2026-07-23T12:34:56Z"),
    ],
)
def test_manifest_rejects_noncanonical_privacy_sensitive_nested_fields(mutate) -> None:
    manifest = _manifest()
    mutate(manifest)

    with pytest.raises(EvolutionContractError, match="invalid_manifest"):
        validate_manifest(manifest)


@pytest.mark.parametrize(
    "payload",
    [
        "/private/tmp/private.txt",
        r"C:\Users\alice\private.txt",
        "relative/private.txt",
        {"nested": {"path": "/private/tmp/private.txt"}},
        {"nested": {"text": "x" * (1024 * 1024)}},
        {"nested": {"material": "aB3dE5gH7jK9mN2pQ4rS6tV8"}},
    ],
)
def test_manifest_rejects_local_paths_and_unbounded_text_in_nested_payloads(
    payload: object,
) -> None:
    manifest = _manifest()
    manifest["build_environment"]["payload"] = payload  # type: ignore[index]

    with pytest.raises(EvolutionContractError, match="invalid_manifest"):
        validate_manifest(manifest)


@pytest.mark.parametrize(
    "sensitive_key",
    ["prompt", "system_prompt_body", "transcript", "raw-output", "raw_output"],
)
def test_manifest_rejects_sensitive_evidence_keys_at_any_depth(
    sensitive_key: str,
) -> None:
    manifest = _manifest()
    manifest["build_environment"]["nested"] = {sensitive_key: "not identity"}  # type: ignore[index]

    with pytest.raises(EvolutionContractError, match="invalid_manifest"):
        validate_manifest(manifest)


def test_manifest_accepts_uppercase_symbolic_credential_reference() -> None:
    manifest = _manifest()
    manifest["credential_references"] = ["OPENAI_API_KEY"]

    validate_manifest(manifest)


def test_manifest_rejects_opaque_credential_material_as_a_reference() -> None:
    manifest = _manifest()
    manifest["credential_references"] = ["aB3dE5gH7jK9mN2pQ4rS6tV8"]

    with pytest.raises(EvolutionContractError, match="invalid_manifest"):
        validate_manifest(manifest)


@pytest.mark.parametrize("kind", ["extra-dir", "fifo", "manifest"])
def test_staging_inventory_is_exact_and_has_no_manifest(tmp_path: Path, kind: str) -> None:
    manifest = _manifest()
    _stage(tmp_path, manifest)
    if kind == "extra-dir":
        (tmp_path / "unexpected").mkdir()
    elif kind == "fifo":
        __import__("os").mkfifo(tmp_path / "pipe")
    else:
        (tmp_path / "manifest.json").write_text("{}")

    with pytest.raises(EvolutionContractError, match="invalid_manifest"):
        validate_manifest(manifest, tmp_path)
