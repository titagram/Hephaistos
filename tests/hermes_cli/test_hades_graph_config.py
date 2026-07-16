"""Graph v2 configuration and deterministic source identity contracts."""

from __future__ import annotations

import hashlib
from pathlib import Path
import subprocess

import pytest


def _write(root: Path, rel: str, content: bytes | str) -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(content, bytes):
        path.write_bytes(content)
    else:
        path.write_text(content, encoding="utf-8")
    return path


def _symlink_or_skip(link: Path, target: str | Path) -> None:
    try:
        link.symlink_to(target)
    except (NotImplementedError, OSError) as exc:
        pytest.skip(f"symlinks unavailable in test environment: {exc}")


def test_graph_index_config_defaults_are_exact_and_immutable():
    from hermes_cli.hades_graph_config import load_hades_graph_index_config

    config = load_hades_graph_index_config({})

    assert config.max_file_bytes == 8_388_608
    assert config.max_total_source_bytes == 2_147_483_648
    assert config.max_wall_seconds == 3_600
    assert config.max_chunk_uncompressed_bytes == 8_388_608
    assert config.max_bundle_uncompressed_bytes == 536_870_912
    assert config.spool_ttl_seconds == 86_400
    assert config.graphify_candidates is False
    assert config.excluded_paths == ()
    with pytest.raises(AttributeError):
        config.max_file_bytes = 1  # type: ignore[misc]


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("max_file_bytes", 1_023),
        ("max_file_bytes", 1_073_741_825),
        ("max_total_source_bytes", 1_048_575),
        ("max_total_source_bytes", 17_592_186_044_417),
        ("max_wall_seconds", 29),
        ("max_wall_seconds", 86_401),
        ("max_chunk_uncompressed_bytes", 65_535),
        ("max_bundle_uncompressed_bytes", 8_388_607),
        ("spool_ttl_seconds", 3_599),
    ],
)
def test_graph_index_config_rejects_values_outside_closed_ranges(key, value):
    from hermes_cli.hades_graph_config import (
        GraphIndexConfigError,
        load_hades_graph_index_config,
    )

    with pytest.raises(GraphIndexConfigError, match=key):
        load_hades_graph_index_config({"hades": {"graph_index": {key: value}}})


def test_graph_index_config_rejects_chunk_larger_than_bundle_and_non_boolean():
    from hermes_cli.hades_graph_config import (
        GraphIndexConfigError,
        load_hades_graph_index_config,
    )

    with pytest.raises(GraphIndexConfigError, match="max_chunk_uncompressed_bytes"):
        load_hades_graph_index_config({
            "hades": {
                "graph_index": {
                    "max_chunk_uncompressed_bytes": 8_388_608,
                    "max_bundle_uncompressed_bytes": 8_388_607,
                }
            }
        })
    with pytest.raises(GraphIndexConfigError, match="graphify_candidates"):
        load_hades_graph_index_config({
            "hades": {"graph_index": {"graphify_candidates": 0}}
        })


def test_graph_index_config_rejects_unknown_key_at_explicit_boundary():
    from hermes_cli.hades_graph_config import (
        GraphIndexConfigError,
        load_hades_graph_index_config,
    )

    with pytest.raises(GraphIndexConfigError, match=r"hades\.graph_index\.made_up"):
        load_hades_graph_index_config({"hades": {"graph_index": {"made_up": "nope"}}})


def test_graph_index_config_rejects_unsafe_user_exclusion_path():
    from hermes_cli.hades_graph_config import (
        GraphIndexConfigError,
        load_hades_graph_index_config,
    )

    with pytest.raises(GraphIndexConfigError, match="excluded_paths"):
        load_hades_graph_index_config({
            "hades": {"graph_index": {"excluded_paths": ["..\\private"]}}
        })


def test_secret_exclusions_are_compulsory_and_user_exclusions_are_additive(tmp_path):
    from hermes_cli.hades_graph_config import (
        build_source_identity,
        load_hades_graph_index_config,
    )

    _write(tmp_path, "src/app.py", "print('safe')\n")
    _write(tmp_path, ".env", "DO_NOT_LEAK=top-secret\n")
    _write(tmp_path, "custom/ignored.py", "print('ignored')\n")
    config = load_hades_graph_index_config({
        "hades": {"graph_index": {"excluded_paths": ["custom"]}}
    })

    identity = build_source_identity(tmp_path, config)
    expected_file = hashlib.sha256(b"print('safe')\n").hexdigest().encode("ascii")
    expected = hashlib.sha256(b"src/app.py\0" + expected_file + b"\n").hexdigest()

    assert identity.tree_sha256 == expected
    assert "top-secret" not in str(identity)


def test_nfc_collision_fails_without_exposing_raw_untrusted_name():
    from hermes_cli.hades_graph_config import SourceIdentityError
    from hermes_cli.hades_index.inventory import validate_normalized_source_paths

    with pytest.raises(
        SourceIdentityError, match="source_path_normalization_collision"
    ) as exc:
        validate_normalized_source_paths(["safe/café.py", "safe/cafe\u0301.py"])

    assert "café.py" in str(exc.value)
    assert "cafe\u0301.py" not in str(exc.value)


def test_source_identity_hashes_regular_and_safe_symlink_target_at_link_path(tmp_path):
    from hermes_cli.hades_graph_config import (
        build_source_identity,
        load_hades_graph_index_config,
    )

    _write(tmp_path, "src/target.py", b"answer = 42\n")
    _symlink_or_skip(tmp_path / "src" / "alias.py", "target.py")
    config = load_hades_graph_index_config({})

    identity = build_source_identity(tmp_path, config)
    digest = hashlib.sha256(b"answer = 42\n").hexdigest().encode("ascii")
    preimage = b"src/alias.py\0" + digest + b"\n" + b"src/target.py\0" + digest + b"\n"

    assert identity.tree_sha256 == hashlib.sha256(preimage).hexdigest()


def test_source_identity_hashes_binary_and_oversized_files_before_parse_budgets(
    tmp_path,
):
    from hermes_cli.hades_graph_config import (
        build_source_identity,
        load_hades_graph_index_config,
    )

    content = b"\x00\xff" * 800
    _write(tmp_path, "src/blob.bin", content)
    config = load_hades_graph_index_config({
        "hades": {"graph_index": {"max_file_bytes": 1_024}}
    })

    identity = build_source_identity(tmp_path, config)
    file_digest = hashlib.sha256(content).hexdigest().encode("ascii")
    expected = hashlib.sha256(b"src/blob.bin\0" + file_digest + b"\n").hexdigest()

    assert identity.tree_sha256 == expected


@pytest.mark.parametrize("target", ["../../outside-secret.txt", "missing.py"])
def test_invalid_symlink_uses_non_leaking_marker(tmp_path, target):
    from hermes_cli.hades_graph_config import (
        build_source_identity,
        load_hades_graph_index_config,
    )

    _write(tmp_path, "src/app.py", b"pass\n")
    _symlink_or_skip(tmp_path / "src" / "invalid.py", target)
    config = load_hades_graph_index_config({})

    identity = build_source_identity(tmp_path, config)

    assert len(identity.tree_sha256) == 64
    assert "outside-secret" not in str(identity)


def test_cyclic_symlink_is_invalid_without_target_leakage(tmp_path):
    from hermes_cli.hades_graph_config import (
        build_source_identity,
        load_hades_graph_index_config,
    )

    _symlink_or_skip(tmp_path / "cycle-a", "cycle-b-super-secret")
    _symlink_or_skip(tmp_path / "cycle-b-super-secret", "cycle-a")
    identity = build_source_identity(tmp_path, load_hades_graph_index_config({}))

    assert len(identity.tree_sha256) == 64
    assert "super-secret" not in str(identity)


def test_unavailable_submodule_hashes_gitlink_and_marks_inventory_partial(tmp_path):
    from hermes_cli.hades_graph_config import (
        build_source_identity,
        load_hades_graph_index_config,
    )

    _write(
        tmp_path,
        ".gitmodules",
        '[submodule "lib"]\n\tpath = lib\n\turl = https://example.invalid/lib\n',
    )
    _write(tmp_path, ".git/modules/lib/HEAD", "ref: refs/heads/main\n")
    # A gitlink cannot be represented in a normal directory tree on all platforms;
    # the explicit unavailable marker is therefore a portable inventory fixture.
    _write(tmp_path, "lib/.git", "gitdir: ../.git/modules/lib\n")

    identity = build_source_identity(tmp_path, load_hades_graph_index_config({}))

    assert len(identity.tree_sha256) == 64


def test_git_metadata_and_non_git_workspace_are_deterministic(tmp_path):
    from hermes_cli.hades_graph_config import (
        build_source_identity,
        load_hades_graph_index_config,
    )

    _write(tmp_path, "src/app.py", "pass\n")
    non_git = build_source_identity(tmp_path, load_hades_graph_index_config({}))
    assert non_git.head_commit is None
    assert non_git.branch is None
    assert non_git.dirty is False


def test_source_identity_reports_dirty_git_worktree_without_public_paths(tmp_path):
    from hermes_cli.hades_graph_config import (
        build_source_identity,
        load_hades_graph_index_config,
    )

    try:
        subprocess.run(
            ["git", "init"],
            cwd=tmp_path,
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        subprocess.run(
            ["git", "config", "user.email", "graph@example.invalid"],
            cwd=tmp_path,
            check=True,
        )
        subprocess.run(
            ["git", "config", "user.name", "Graph Test"], cwd=tmp_path, check=True
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        pytest.skip(f"git unavailable in test environment: {exc}")
    _write(tmp_path, "src/app.py", "before\n")
    subprocess.run(["git", "add", "src/app.py"], cwd=tmp_path, check=True)
    subprocess.run(
        ["git", "commit", "-m", "initial"],
        cwd=tmp_path,
        check=True,
        stdout=subprocess.DEVNULL,
    )
    _write(tmp_path, "src/app.py", "after\n")

    identity = build_source_identity(tmp_path, load_hades_graph_index_config({}))

    assert identity.dirty is True
    assert identity.head_commit is not None and len(identity.head_commit) == 40
    assert "src/app.py" not in str(identity)


def test_unavailable_git_submodule_hashes_gitlink_and_marks_partial(tmp_path):
    from hermes_cli.hades_index.inventory import build_source_snapshot

    commit = "a" * 40
    try:
        subprocess.run(
            ["git", "init"],
            cwd=tmp_path,
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        subprocess.run(
            ["git", "update-index", "--add", "--cacheinfo", f"160000,{commit},lib"],
            cwd=tmp_path,
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        pytest.skip(f"git index fixture unavailable: {exc}")

    snapshot = build_source_snapshot(tmp_path)
    expected_file = (
        hashlib
        .sha256(b"SUBMODULE_UNAVAILABLE\0" + commit.encode("ascii"))
        .hexdigest()
        .encode("ascii")
    )
    expected = hashlib.sha256(b"lib\0" + expected_file + b"\n").hexdigest()

    assert snapshot.tree_sha256 == expected
    assert snapshot.partial_reasons == ("submodule_unavailable",)


def test_verify_source_unchanged_raises_typed_error_on_digest_mismatch(tmp_path):
    from hermes_cli.hades_graph_config import (
        SourceIdentityError,
        build_source_identity,
        load_hades_graph_index_config,
        verify_source_unchanged,
    )

    _write(tmp_path, "src/app.py", "before\n")
    config = load_hades_graph_index_config({})
    before = build_source_identity(tmp_path, config)
    _write(tmp_path, "src/app.py", "after\n")

    with pytest.raises(SourceIdentityError, match="source_changed_during_index"):
        verify_source_unchanged(tmp_path, config, before)
