import json
from pathlib import Path

import pytest

from hermes_cli.gnothi.contract import new_artifact
from hermes_cli.gnothi.store import OrganismRevisionStore


def _artifact(
    revision_id: str,
    *,
    collected_at: str,
    status: str = "current",
) -> dict:
    artifact = new_artifact(
        revision_id=revision_id,
        generation_id="git:abc",
        generation_scope="stable",
        head_commit="abc",
        collected_at=collected_at,
    )
    artifact["organism_contract"]["status"] = status
    return artifact


def test_publish_writes_revision_and_atomically_updates_current(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    store = OrganismRevisionStore()
    artifact = _artifact("rev-1", collected_at="2026-07-11T00:00:00Z")

    pointer = store.publish(artifact, published_at="2026-07-11T00:01:00Z")

    revision_path = tmp_path / "gnothi_seauton" / "revisions" / "rev-1.json"
    pointer_path = tmp_path / "gnothi_seauton" / "current.json"
    assert json.loads(revision_path.read_text()) == artifact
    assert json.loads(pointer_path.read_text()) == pointer
    assert pointer == {
        "schema": "hades.gnothi_pointer.v1",
        "revision_id": "rev-1",
        "sha256": pointer["sha256"],
        "published_at": "2026-07-11T00:01:00Z",
    }
    assert store.current() == artifact
    assert revision_path.stat().st_mode & 0o777 == 0o600
    assert pointer_path.stat().st_mode & 0o777 == 0o600


def test_publish_is_idempotent_but_refuses_conflicting_revision(tmp_path: Path):
    store = OrganismRevisionStore(root=tmp_path)
    artifact = _artifact("rev-1", collected_at="2026-07-11T00:00:00Z")
    store.publish(artifact, published_at="2026-07-11T00:01:00Z")

    assert store.publish(
        artifact, published_at="2026-07-11T00:01:00Z"
    ) == json.loads((tmp_path / "current.json").read_text())

    conflicting = _artifact("rev-1", collected_at="2026-07-12T00:00:00Z")
    with pytest.raises(ValueError, match="conflicting revision"):
        store.publish(conflicting, published_at="2026-07-12T00:01:00Z")


def test_lists_newest_first_and_finds_previous_healthy(tmp_path: Path):
    store = OrganismRevisionStore(root=tmp_path)
    healthy = _artifact("rev-1", collected_at="2026-07-11T00:00:00Z")
    partial = _artifact(
        "rev-2",
        collected_at="2026-07-12T00:00:00Z",
        status="partial",
    )
    store.publish(healthy, published_at="2026-07-11T00:01:00Z")
    store.publish(partial, published_at="2026-07-12T00:01:00Z")

    assert [
        row["organism_contract"]["revision_id"] for row in store.list_revisions()
    ] == ["rev-2", "rev-1"]
    assert store.previous_healthy() == healthy


def test_rejects_invalid_artifact_before_writing(tmp_path: Path):
    root = tmp_path / "store"
    store = OrganismRevisionStore(root=root)
    artifact = _artifact("rev-1", collected_at="2026-07-11T00:00:00Z")
    artifact["schema"] = "wrong"

    with pytest.raises(ValueError, match="invalid organism artifact"):
        store.publish(artifact)

    assert not root.exists()


def test_rejects_unsafe_revision_id(tmp_path: Path):
    store = OrganismRevisionStore(root=tmp_path)
    artifact = _artifact("../escape", collected_at="2026-07-11T00:00:00Z")

    with pytest.raises(ValueError, match="unsafe revision id"):
        store.publish(artifact)

    assert not (tmp_path.parent / "escape.json").exists()
