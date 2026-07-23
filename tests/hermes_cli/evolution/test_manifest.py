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


@pytest.mark.parametrize("field", ["digest", "artifact_digest"])
def test_arbitrary_digest_named_fields_do_not_exempt_opaque_material(
    field: str,
) -> None:
    manifest = _manifest()
    manifest["build_environment"][field] = "aB3dE5gH7jK9mN2pQ4rS6tV8"  # type: ignore[index]

    with pytest.raises(EvolutionContractError, match="invalid_manifest"):
        validate_manifest(manifest)


@pytest.mark.parametrize(
    "command",
    [
        "python /Users/alice/private/check.py --verify",
        r"python C:\Users\alice\private\check.py --verify",
        r"check --path=\\server\share",
        "/",
        "~",
        "check --path=/",
        "check --home=~",
        ".",
        "..",
        "//",
        "C:\\",
        "C:/",
        "check --path=.",
        "check --path=..",
        "check --path=//",
        "check --path=C:\\",
        "check --path=C:/",
        "./",
        "../",
        "///",
        ".\\",
        "..\\",
        "~\\",
        "\\\\",
        "check --path=./",
        "check --path=../",
        "check --path=///",
        "check --path=.\\",
        "check --path=..\\",
        "check --path=~\\",
        "check --path=\\\\",
    ],
)
def test_verification_commands_reject_embedded_local_paths(
    command: str,
) -> None:
    manifest = _manifest()
    manifest["verification_commands"] = [command]

    with pytest.raises(EvolutionContractError, match="invalid_manifest"):
        validate_manifest(manifest)


@pytest.mark.parametrize(
    "evidence_key",
    [
        "stdout",
        "stderr",
        "output",
        "raw_output",
        "raw-output",
        "command_stdout",
        "tool_stderr",
        "captured_output",
        "rawoutput",
        "toolOutput",
        "systemPromptBody",
        "tooloutput",
        "systempromptbody",
        "credentialValue",
        "refreshToken",
    ],
)
def test_manifest_rejects_evidence_bearing_keys_anywhere(
    evidence_key: str,
) -> None:
    manifest = _manifest()
    manifest["build_environment"]["nested"] = {  # type: ignore[index]
        evidence_key: "captured evidence"
    }

    with pytest.raises(EvolutionContractError, match="invalid_manifest"):
        validate_manifest(manifest)


def test_manifest_accepts_normal_commands() -> None:
    manifest = _manifest()
    manifest["verification_commands"] = [
        "python -m package.check --verify",
        "hello --profile safe",
        "probe --endpoint https://example.test/health",
        "install --package owner/package@1.2.3",
        "check --path-mode safe --output=json",
        "verify --timeout 30 --memory 512 --retries 3 --jobs 4 --limit 10",
        "check --range=1.0..2.0 --version=3.12.1",
        "probe --endpoint=https://example.test/a/b",
        "install --package=owner/package@1.2.3",
        "check --module=package.submodule --release=2.4.0",
    ]

    validate_manifest(manifest)


@pytest.mark.parametrize("separator_count", [1, 2, 3, 4, 8])
def test_verification_commands_reject_repeated_posix_root_separators(
    separator_count: int,
) -> None:
    root = "/" * separator_count
    for command in (root, f"check --path='{root}'", f"{root}private"):
        manifest = _manifest()
        manifest["verification_commands"] = [command]
        with pytest.raises(EvolutionContractError, match="invalid_manifest"):
            validate_manifest(manifest)


@pytest.mark.parametrize("root", [".", "..", "~"])
@pytest.mark.parametrize("separator", ["/", "//", "\\", "\\\\"])
def test_verification_commands_reject_relative_roots_with_repeated_separators(
    root: str, separator: str
) -> None:
    for token in (root + separator, root + separator + "private"):
        manifest = _manifest()
        manifest["verification_commands"] = [
            f'check --path="{token}"'
        ]
        with pytest.raises(EvolutionContractError, match="invalid_manifest"):
            validate_manifest(manifest)


@pytest.mark.parametrize("separator", ["/", "//", "\\", "\\\\"])
def test_verification_commands_reject_drive_and_unc_roots_and_descendants(
    separator: str,
) -> None:
    tokens = (
        "C:" + separator,
        "C:" + separator + "private",
        "\\\\" + separator + "server" + separator + "share",
    )
    for token in tokens:
        manifest = _manifest()
        manifest["verification_commands"] = [f"--path={token}"]
        with pytest.raises(EvolutionContractError, match="invalid_manifest"):
            validate_manifest(manifest)


def test_verification_command_rejects_opaque_credential_token() -> None:
    manifest = _manifest()
    manifest["verification_commands"] = [
        "probe --authorization Bearer aB3dE5gH7jK9mN2pQ4rS6tV8"
    ]

    with pytest.raises(EvolutionContractError, match="invalid_manifest"):
        validate_manifest(manifest)


@pytest.mark.parametrize(
    ("coordinate", "version"),
    [
        ("hello", "1.0"),
        ("x" * 64, "1.2.3"),
        ("logical-id_2", "2.4.0"),
        ("owner/package", "3.1.4-rc.1+build.7"),
        ("prompt-toolkit", "3.0.51"),
        ("output-parser", "1.4.2"),
        ("transcript-tools", "2.0.0"),
    ],
)
def test_resolved_versions_accept_bounded_coordinates_and_semantic_versions(
    coordinate: str, version: str
) -> None:
    manifest = _manifest()
    manifest["resolved_versions"] = {coordinate: version}

    validate_manifest(manifest)


@pytest.mark.parametrize(
    ("coordinate", "version"),
    [
        ("x" * 65, "1.0.0"),
        ("owner/package/extra", "1.0.0"),
        ("hello", ">=1.0"),
        ("hello", "1..0"),
        ("hello", "v1"),
        ("hello", "1.0 " + "x" * 128),
        ("hello", True),
    ],
)
def test_resolved_versions_reject_invalid_coordinates_or_versions(
    coordinate: str, version: object
) -> None:
    manifest = _manifest()
    manifest["resolved_versions"] = {coordinate: version}

    with pytest.raises(EvolutionContractError, match="invalid_manifest"):
        validate_manifest(manifest)


@pytest.mark.parametrize(
    "side_effects",
    ["none", "read_only", "candidate-only"],
)
def test_canary_policy_allows_only_a_bounded_symbolic_side_effect_policy(
    side_effects: str,
) -> None:
    manifest = _manifest()
    manifest["canary_policy"] = {"side_effects": side_effects}

    validate_manifest(manifest)


@pytest.mark.parametrize(
    "side_effects",
    ["", "contains whitespace", "x" * 65, True, 1, ["none"]],
)
def test_canary_policy_rejects_invalid_side_effect_policy(
    side_effects: object,
) -> None:
    manifest = _manifest()
    manifest["canary_policy"] = {"side_effects": side_effects}

    with pytest.raises(EvolutionContractError, match="invalid_manifest"):
        validate_manifest(manifest)


@pytest.mark.parametrize(
    ("field", "minimum", "maximum"),
    [
        ("cpu_seconds", 1, 86_400),
        ("wall_seconds", 1, 604_800),
        ("memory_bytes", 1, 1 << 50),
        ("disk_bytes", 1, 1 << 50),
        ("network_requests", 1, 1_000_000),
        ("process_count", 1, 4_096),
    ],
)
def test_resource_ceilings_allow_each_explicit_integer_field_in_range(
    field: str, minimum: int, maximum: int
) -> None:
    for value in (minimum, maximum):
        manifest = _manifest()
        manifest["resource_ceilings"] = {field: value}
        validate_manifest(manifest)


@pytest.mark.parametrize(
    ("field", "invalid"),
    [
        ("cpu_seconds", 0),
        ("cpu_seconds", 86_401),
        ("wall_seconds", 604_801),
        ("memory_bytes", (1 << 50) + 1),
        ("disk_bytes", (1 << 50) + 1),
        ("network_requests", 1_000_001),
        ("process_count", 4_097),
        ("cpu_seconds", True),
        ("cpu_seconds", 1.0),
        ("cpu_seconds", "1"),
    ],
)
def test_resource_ceilings_reject_out_of_range_and_non_integer_values(
    field: str, invalid: object
) -> None:
    manifest = _manifest()
    manifest["resource_ceilings"] = {field: invalid}

    with pytest.raises(EvolutionContractError, match="invalid_manifest"):
        validate_manifest(manifest)


def test_build_environment_allows_every_declared_identity_field() -> None:
    manifest = _manifest()
    manifest["build_environment"] = {
        "builder": "hermes-agent",
        "version": "0.17.0",
        "platform": "linux",
        "architecture": "x86_64",
        "python": "3.12.1",
        "environment_digest": "e" * 64,
        "toolchain_digest": "f" * 64,
    }

    validate_manifest(manifest)


@pytest.mark.parametrize("required", ["builder", "version"])
def test_build_environment_requires_builder_and_version(required: str) -> None:
    manifest = _manifest()
    del manifest["build_environment"][required]  # type: ignore[index]

    with pytest.raises(EvolutionContractError, match="invalid_manifest"):
        validate_manifest(manifest)


@pytest.mark.parametrize(
    ("field", "invalid"),
    [
        ("builder", ""),
        ("builder", "contains whitespace"),
        ("builder", "x" * 129),
        ("version", True),
        ("platform", "/"),
        ("architecture", ["x86_64"]),
        ("python", "3.12/venv"),
        ("environment_digest", "sha256"),
        ("toolchain_digest", "f" * 63),
    ],
)
def test_build_environment_rejects_invalid_declared_identity_values(
    field: str, invalid: object
) -> None:
    manifest = _manifest()
    manifest["build_environment"][field] = invalid  # type: ignore[index]

    with pytest.raises(EvolutionContractError):
        validate_manifest(manifest)


@pytest.mark.parametrize(
    ("mapping_name", "unknown_key", "value"),
    [
        ("canary_policy", "sideEffects", "none"),
        ("canary_policy", "side-effects", "none"),
        ("resource_ceilings", "cpuSeconds", 10),
        ("resource_ceilings", "cpu-seconds", 10),
        ("build_environment", "environmentDigest", "e" * 64),
        ("build_environment", "environment-digest", "e" * 64),
        ("build_environment", "tooloutput", "captured"),
        ("build_environment", "systempromptbody", "prompt"),
        ("build_environment", "credentialValue", "reference"),
        ("build_environment", "refreshToken", "reference"),
    ],
)
def test_closed_nested_mappings_reject_every_unknown_key_variant(
    mapping_name: str, unknown_key: str, value: object
) -> None:
    manifest = _manifest()
    manifest[mapping_name][unknown_key] = value  # type: ignore[index]

    with pytest.raises(EvolutionContractError, match="invalid_manifest"):
        validate_manifest(manifest)


@pytest.mark.parametrize("schema_version", [True, 1.0])
def test_schema_version_requires_the_exact_non_boolean_integer_one(
    schema_version: object,
) -> None:
    manifest = _manifest()
    manifest["schema_version"] = schema_version

    with pytest.raises(EvolutionContractError, match="invalid_manifest"):
        validate_manifest(manifest)


def test_root_validation_normalizes_unsupported_dir_fd_operations(
    tmp_path: Path, monkeypatch
) -> None:
    manifest = _manifest()
    _stage(tmp_path, manifest)
    original_open = __import__("os").open

    def unsupported_dir_fd_open(*args, **kwargs):
        if kwargs.get("dir_fd") is not None:
            raise NotImplementedError("dir_fd unavailable")
        return original_open(*args, **kwargs)

    monkeypatch.setattr("hermes_cli.evolution.manifest.os.open", unsupported_dir_fd_open)

    with pytest.raises(EvolutionContractError, match="invalid_manifest"):
        validate_manifest(manifest, tmp_path)


def test_root_validation_normalizes_dir_fd_keyword_type_error(
    tmp_path: Path, monkeypatch
) -> None:
    manifest = _manifest()
    _stage(tmp_path, manifest)
    original_open = __import__("os").open

    def open_without_dir_fd(path, flags, mode=0o777):
        return original_open(path, flags, mode)

    monkeypatch.setattr("hermes_cli.evolution.manifest.os.open", open_without_dir_fd)

    with pytest.raises(EvolutionContractError, match="invalid_manifest"):
        validate_manifest(manifest, tmp_path)


def test_root_validation_does_not_hide_programming_type_errors(
    tmp_path: Path, monkeypatch
) -> None:
    import hermes_cli.evolution.manifest as module

    manifest = _manifest()
    _stage(tmp_path, manifest)

    def programming_error(*args, **kwargs):
        raise TypeError("programming defect involving dir_fd")

    monkeypatch.setattr(module, "_validate_files_at", programming_error)

    with pytest.raises(TypeError, match="programming defect involving dir_fd"):
        validate_manifest(manifest, tmp_path)


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
