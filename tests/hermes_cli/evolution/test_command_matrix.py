"""Adversarial behavioral matrix for the Project A operator read surface."""

from __future__ import annotations

import io
import json
import os
import stat
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace

import pytest

from hermes_cli.evolution import command as command_module
from hermes_cli.evolution.authorization import (
    consume_grant,
    create_authorization_request,
    deny_authorization_request,
    issue_grant,
)
from hermes_cli.evolution.bootstrap import ensure_evolution_initialized
from hermes_cli.evolution.command import evolution_command
from hermes_cli.evolution.contract import content_digest
from hermes_cli.evolution.ledger import (
    EvolutionLedger,
    EvolutionLedgerError,
    LifecycleEvent,
)


NOW = "2026-07-24T00:00:00.000000Z"
HOSTILE_STRINGS = (
    "/Users/alice/.ssh/id_ed25519",
    r"C:\Users\alice\.ssh\id_ed25519",
    "../private/id_ed25519",
    "file:///Users/alice/.ssh/id_ed25519",
    "file:private-record",
    "C:private-record",
    "github_pat_" + "A" * 30,
    "ghp_" + "A" * 36,
    "glpat-ABCDEFGHIJKLMNOPQRSTUVWXYZ123456",
    "ya29.ABCDEFGHIJKLMNOPQRSTUVWXYZ123456",
    "AKIA" + "A" * 16,
    "ASIA" + "A" * 16,
    "AIza" + "A" * 35,
    "Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.payload.signature",
    "-----BEGIN PRIVATE KEY-----ABCDEF-----END PRIVATE KEY-----",
)


def _run(**values: object) -> tuple[int, dict[str, object], str]:
    arguments = {
        "action": "status",
        "json": True,
        "limit": 100,
        "after": 0,
        "kind": "generation",
        "record_id": "a" * 64,
        **values,
    }
    args = SimpleNamespace(**arguments)
    output = io.StringIO()
    with redirect_stdout(output):
        exit_code = evolution_command(args)
    serialized = output.getvalue().strip()
    return exit_code, json.loads(serialized), serialized


def _insert_attempt(
    ledger: EvolutionLedger,
    attempt_id: str,
    *,
    created_at: str = NOW,
) -> None:
    ledger.connection.execute(
        "INSERT INTO attempts VALUES (?, ?, ?, ?, ?)",
        (attempt_id, "manual", "operator-matrix", "draft", created_at),
    )


def _seed_safe_show_records(
    ledger: EvolutionLedger,
    generation_id: str,
) -> dict[str, tuple[str, dict[str, object]]]:
    _insert_attempt(ledger, "attempt-alpha")
    rows = {
        "suggestion": (
            "suggestion-alpha",
            {
                "suggestion_id": "suggestion-alpha",
                "canonical_digest": "a" * 64,
                "state": "draft",
                "created_at": NOW,
            },
        ),
        "blueprint": (
            "b" * 64,
            {
                "blueprint_id": "blueprint-alpha",
                "canonical_digest": "b" * 64,
                "state": "draft",
                "created_at": NOW,
            },
        ),
        "generation": (
            generation_id,
            {
                "generation_id": generation_id,
                "canonical_digest": generation_id,
                "state": "draft",
                "created_at": NOW,
            },
        ),
        "report": (
            "c" * 64,
            {
                "promotion_report_id": "report-alpha",
                "generation_id": generation_id,
                "report_digest": "c" * 64,
                "state": "draft",
                "created_at": NOW,
            },
        ),
    }
    ledger.connection.execute(
        "INSERT INTO suggestions VALUES (?, ?, ?, ?, ?)",
        ("suggestion-alpha", "attempt-alpha", "a" * 64, "draft", NOW),
    )
    ledger.connection.execute(
        "INSERT INTO blueprints VALUES (?, ?, ?, ?, ?)",
        ("blueprint-alpha", "attempt-alpha", "b" * 64, "draft", NOW),
    )
    ledger.connection.execute(
        "INSERT INTO generations VALUES (?, ?, ?, ?, ?)",
        (generation_id, "attempt-alpha", generation_id, "draft", NOW),
    )
    ledger.connection.execute(
        "INSERT INTO promotion_reports VALUES (?, ?, ?, ?, ?)",
        ("report-alpha", generation_id, "c" * 64, "draft", NOW),
    )
    return rows


def _inventory(path: Path) -> tuple[tuple[object, ...], ...]:
    """Capture names, bytes/link targets, modes, sizes, and mtimes without following links."""

    if not os.path.lexists(path):
        return ()
    records: list[tuple[object, ...]] = []

    def visit(current: Path, relative: str) -> None:
        info = current.lstat()
        mode = stat.S_IMODE(info.st_mode)
        if stat.S_ISREG(info.st_mode):
            kind = "file"
            payload: bytes | None = current.read_bytes()
        elif stat.S_ISLNK(info.st_mode):
            kind = "symlink"
            payload = os.fsencode(os.readlink(current))
        elif stat.S_ISDIR(info.st_mode):
            kind = "directory"
            payload = None
        else:
            kind = "other"
            payload = None
        records.append((relative, kind, payload, mode, info.st_size, info.st_mtime_ns))
        if kind == "directory":
            for child in sorted(current.iterdir(), key=lambda item: item.name):
                child_relative = (
                    child.name if relative == "." else f"{relative}/{child.name}"
                )
                visit(child, child_relative)

    visit(path, ".")
    return tuple(records)


def test_all_show_kinds_have_exact_found_and_missing_command_contracts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("HERMES_HOME", str(home))
    baseline = ensure_evolution_initialized()
    ledger = EvolutionLedger(home / "evolution" / "evolution.db")
    try:
        rows = _seed_safe_show_records(ledger, baseline.generation_id)
    finally:
        ledger.connection.close()

    for kind, (record_id, expected_record) in rows.items():
        exit_code, value, _ = _run(
            action="show",
            kind=kind,
            record_id=record_id,
        )
        assert exit_code == 0
        assert value == {
            "schema_version": 1,
            "status": "found",
            "kind": kind,
            "record": expected_record,
        }

        missing_id = "suggestion-missing" if kind == "suggestion" else "f" * 64
        exit_code, value, _ = _run(
            action="show",
            kind=kind,
            record_id=missing_id,
        )
        assert exit_code == 1
        assert value == {
            "schema_version": 1,
            "status": "missing",
            "kind": kind,
            "record": None,
        }


def test_history_pagination_is_ascending_and_uses_the_last_seen_sequence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("HERMES_HOME", str(home))
    ensure_evolution_initialized()
    ledger = EvolutionLedger(home / "evolution" / "evolution.db")
    try:
        for suffix in ("alpha", "beta"):
            ledger.append_event(
                LifecycleEvent(
                    event_id=f"event-{suffix}",
                    attempt_id=None,
                    generation_id=None,
                    event_type="supervisor_recovery",
                    prior_state=None,
                    next_state=None,
                    actor="supervisor",
                    input_digests=(),
                    authorization_id=None,
                    reason_code="stable_base_only",
                    reason_summary=(
                        "evolution overlays disabled because no pointer was proven"
                    ),
                    created_at=NOW,
                )
            )
    finally:
        ledger.connection.close()

    exit_code, first, _ = _run(action="history", limit=1, after=0)
    assert exit_code == 0
    assert set(first) == {"schema_version", "status", "items", "next_after"}
    assert first["status"] == "ok"
    first_sequence = first["items"][0]["sequence"]
    assert first["next_after"] == first_sequence

    exit_code, second, _ = _run(
        action="history",
        limit=2,
        after=first_sequence,
    )
    assert exit_code == 0
    sequences = [item["sequence"] for item in second["items"]]
    assert sequences == sorted(sequences)
    assert all(sequence > first_sequence for sequence in sequences)
    assert second["next_after"] == sequences[-1]


def test_history_preserves_real_a3_authorization_vocabulary_without_reason_text(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("HERMES_HOME", str(home))
    ensure_evolution_initialized()
    ledger = EvolutionLedger(home / "evolution" / "evolution.db")
    scope = {
        "source_classes": ["documentation"],
        "domains": ["example.com"],
        "operations": ["search", "retrieve"],
        "duration": 60,
    }
    try:
        _insert_attempt(ledger, "attempt-alpha")
        request = create_authorization_request(
            ledger,
            attempt_id="attempt-alpha",
            kind="research",
            subject_digest="a" * 64,
            scope=scope,
            ttl_seconds=120,
        )
        confirmation = content_digest(
            request.canonical_payload(),
            domain="hades-evolution-authorization-request-v1",
        )
        grant = issue_grant(
            ledger,
            request_id=request.request_id,
            approved_by="local-operator",
            confirmation_digest=confirmation,
        )
        consume_grant(
            ledger,
            grant_id=grant.grant_id,
            expected_kind="research",
            expected_subject_digest="a" * 64,
            required_scope={**scope, "duration": 30},
        )
        denied_request = create_authorization_request(
            ledger,
            attempt_id="attempt-alpha",
            kind="research",
            subject_digest="b" * 64,
            scope=scope,
            ttl_seconds=120,
        )
        deny_authorization_request(
            ledger,
            request_id=denied_request.request_id,
            decided_by="local-operator",
        )
    finally:
        ledger.connection.close()

    exit_code, history, serialized = _run(
        action="history",
        limit=100,
        after=0,
    )
    assert exit_code == 0
    by_type = {
        item["event_type"]: item
        for item in history["items"]
        if item["event_type"] and item["event_type"].startswith("authorization_")
    }
    assert set(by_type) == {
        "authorization_requested",
        "authorization_granted",
        "authorization_consumed",
        "authorization_denied",
    }
    for event_type, item in by_type.items():
        assert item["attempt_id"] == "attempt-alpha"
        assert item["reason_code"] == event_type
        assert item["reason_summary"] == "redacted"
        assert item["authorization_id"]
    assert by_type["authorization_requested"]["actor"] == "operator"
    assert by_type["authorization_granted"]["actor"] == "local-operator"
    assert by_type["authorization_consumed"]["actor"] == "host"
    assert by_type["authorization_denied"]["actor"] == "local-operator"
    assert request.request_id in serialized
    assert grant.grant_id in serialized
    assert denied_request.request_id in serialized
    assert "authorization requested" not in serialized
    assert "authorization granted" not in serialized


def test_hostile_schema_valid_history_and_show_strings_never_serialize(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("HERMES_HOME", str(home))
    baseline = ensure_evolution_initialized()
    ledger = EvolutionLedger(home / "evolution" / "evolution.db")
    try:
        _insert_attempt(ledger, "baseline-attempt")
        ledger.connection.execute(
            "INSERT INTO generations VALUES (?, ?, ?, ?, ?)",
            (
                baseline.generation_id,
                "baseline-attempt",
                baseline.generation_id,
                "draft",
                NOW,
            ),
        )
        for index, hostile in enumerate(HOSTILE_STRINGS):
            _insert_attempt(ledger, hostile)
            ledger.append_event(
                LifecycleEvent(
                    event_id=hostile,
                    attempt_id=hostile,
                    generation_id=None,
                    event_type=hostile,
                    prior_state=None,
                    next_state=None,
                    actor=hostile,
                    input_digests=(),
                    authorization_id=hostile,
                    reason_code=hostile,
                    reason_summary=hostile,
                    created_at=NOW,
                )
            )
            suggestion_digest = content_digest(
                {"kind": "suggestion", "index": index},
                domain="a7-hostile-show",
            )
            blueprint_digest = content_digest(
                {"kind": "blueprint", "index": index},
                domain="a7-hostile-show",
            )
            report_digest = content_digest(
                {"kind": "report", "index": index},
                domain="a7-hostile-show",
            )
            ledger.connection.execute(
                "INSERT INTO suggestions VALUES (?, ?, ?, ?, ?)",
                (hostile, hostile, suggestion_digest, "draft", NOW),
            )
            ledger.connection.execute(
                "INSERT INTO blueprints VALUES (?, ?, ?, ?, ?)",
                (hostile, hostile, blueprint_digest, "draft", NOW),
            )
            ledger.connection.execute(
                "INSERT INTO promotion_reports VALUES (?, ?, ?, ?, ?)",
                (
                    hostile,
                    baseline.generation_id,
                    report_digest,
                    "draft",
                    NOW,
                ),
            )
    finally:
        ledger.connection.close()

    exit_code, _, serialized_history = _run(
        action="history",
        limit=100,
        after=0,
    )
    assert exit_code == 0
    for hostile in HOSTILE_STRINGS:
        assert hostile not in serialized_history

    for index, hostile in enumerate(HOSTILE_STRINGS):
        identifiers = {
            "suggestion": hostile,
            "blueprint": content_digest(
                {"kind": "blueprint", "index": index},
                domain="a7-hostile-show",
            ),
            "report": content_digest(
                {"kind": "report", "index": index},
                domain="a7-hostile-show",
            ),
        }
        for kind, record_id in identifiers.items():
            exit_code, value, serialized = _run(
                action="show",
                kind=kind,
                record_id=record_id,
            )
            assert exit_code == 1
            assert value == {
                "schema_version": 1,
                "status": "missing",
                "kind": kind,
                "record": None,
            }
            assert hostile not in serialized


def test_lower_hex_public_identities_are_redacted_or_missing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("HERMES_HOME", str(home))
    baseline = ensure_evolution_initialized()
    secret = "d" * 64
    blueprint_digest = "e" * 64
    report_digest = "f" * 64
    input_digest = "9" * 64
    ledger = EvolutionLedger(home / "evolution" / "evolution.db")
    try:
        _insert_attempt(ledger, secret)
        ledger.connection.execute(
            "INSERT INTO generations VALUES (?, ?, ?, ?, ?)",
            (
                baseline.generation_id,
                secret,
                baseline.generation_id,
                "draft",
                NOW,
            ),
        )
        ledger.connection.execute(
            "INSERT INTO suggestions VALUES (?, ?, ?, ?, ?)",
            (secret, secret, "a" * 64, "draft", NOW),
        )
        ledger.connection.execute(
            "INSERT INTO blueprints VALUES (?, ?, ?, ?, ?)",
            (secret, secret, blueprint_digest, "draft", NOW),
        )
        ledger.connection.execute(
            "INSERT INTO promotion_reports VALUES (?, ?, ?, ?, ?)",
            (
                secret,
                baseline.generation_id,
                report_digest,
                "draft",
                NOW,
            ),
        )
        ledger.append_event(
            LifecycleEvent(
                event_id=secret,
                attempt_id=secret,
                generation_id=baseline.generation_id,
                event_type="state_transition",
                prior_state="draft",
                next_state="research_authorized",
                actor=secret,
                input_digests=(input_digest,),
                authorization_id=secret,
                reason_code="transition",
                reason_summary="private",
                created_at=NOW,
            )
        )
    finally:
        ledger.connection.close()

    exit_code, history, serialized = _run(
        action="history",
        limit=100,
        after=0,
    )
    assert exit_code == 0
    event = history["items"][-1]
    for field in ("event_id", "attempt_id", "actor", "authorization_id"):
        assert event[field] is None
    assert event["generation_id"] == baseline.generation_id
    assert event["input_digests"] == [input_digest]
    assert isinstance(event["event_digest"], str)
    assert len(event["event_digest"]) == 64
    assert secret not in serialized

    for kind, record_id in (
        ("suggestion", secret),
        ("blueprint", blueprint_digest),
        ("report", report_digest),
    ):
        show_exit, show, show_serialized = _run(
            action="show",
            kind=kind,
            record_id=record_id,
        )
        assert show_exit == 1
        assert show == {
            "schema_version": 1,
            "status": "missing",
            "kind": kind,
            "record": None,
        }
        assert secret not in show_serialized


def test_uuid_identities_and_declared_digest_fields_remain_visible(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("HERMES_HOME", str(home))
    baseline = ensure_evolution_initialized()
    identity = "123e4567-e89b-12d3-a456-426614174000"
    input_digest = "9" * 64
    ledger = EvolutionLedger(home / "evolution" / "evolution.db")
    try:
        _insert_attempt(ledger, identity)
        rows = _seed_safe_show_records(ledger, baseline.generation_id)
        ledger.append_event(
            LifecycleEvent(
                event_id=identity,
                attempt_id=identity,
                generation_id=baseline.generation_id,
                event_type="state_transition",
                prior_state="draft",
                next_state="research_authorized",
                actor=identity,
                input_digests=(input_digest,),
                authorization_id=identity,
                reason_code="transition",
                reason_summary="private",
                created_at=NOW,
            )
        )
    finally:
        ledger.connection.close()

    exit_code, history, _ = _run(
        action="history",
        limit=100,
        after=0,
    )
    assert exit_code == 0
    event = history["items"][-1]
    for field in ("event_id", "attempt_id", "actor", "authorization_id"):
        assert event[field] == identity
    assert event["generation_id"] == baseline.generation_id
    assert event["input_digests"] == [input_digest]
    assert isinstance(event["event_digest"], str)
    assert len(event["event_digest"]) == 64

    for kind, (record_id, expected_record) in rows.items():
        show_exit, show, _ = _run(
            action="show",
            kind=kind,
            record_id=record_id,
        )
        assert show_exit == 0
        assert show["record"] == expected_record
        if kind == "report":
            assert show["record"]["report_digest"] == "c" * 64
        else:
            assert show["record"]["canonical_digest"]


def test_generation_and_projected_digests_are_strict_lowercase_hex(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("HERMES_HOME", str(home))
    ensure_evolution_initialized()
    ledger = EvolutionLedger(home / "evolution" / "evolution.db")
    try:
        _insert_attempt(ledger, "attempt-alpha")
        ledger.connection.execute(
            "INSERT INTO generations VALUES (?, ?, ?, ?, ?)",
            ("d" * 64, "attempt-alpha", "z" * 64, "draft", NOW),
        )
        ledger.connection.execute(
            "INSERT INTO suggestions VALUES (?, ?, ?, ?, ?)",
            ("suggestion-alpha", "attempt-alpha", "z" * 64, "draft", NOW),
        )
    finally:
        ledger.connection.close()

    for kind, record_id in (
        ("generation", "d" * 64),
        ("suggestion", "suggestion-alpha"),
    ):
        exit_code, value, _ = _run(
            action="show",
            kind=kind,
            record_id=record_id,
        )
        assert exit_code == 1
        assert value["status"] == "missing"


@pytest.mark.parametrize("failure", ["snapshot", "ledger"])
@pytest.mark.parametrize("action", ["history", "show"])
def test_injected_read_failures_keep_action_specific_envelopes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
    action: str,
) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("HERMES_HOME", str(home))
    ensure_evolution_initialized()

    if failure == "snapshot":

        def fail_snapshot(query):
            raise EvolutionLedgerError("injected_snapshot_failure")

        monkeypatch.setattr(
            command_module,
            "read_evolution_snapshot",
            fail_snapshot,
        )
    else:

        def fail_chain(self):
            raise EvolutionLedgerError("injected_ledger_failure")

        monkeypatch.setattr(EvolutionLedger, "verify_chain", fail_chain)

    exit_code, value, _ = _run(action=action)
    assert exit_code == 1
    if action == "history":
        assert value == {
            "schema_version": 1,
            "status": "blocked",
            "items": [],
            "next_after": None,
        }
    else:
        assert value == {
            "schema_version": 1,
            "status": "missing",
            "kind": "generation",
            "record": None,
        }


@pytest.mark.parametrize("action", ["history", "show"])
def test_broken_event_chain_keeps_action_specific_envelopes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    action: str,
) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("HERMES_HOME", str(home))
    ensure_evolution_initialized()
    database = home / "evolution" / "evolution.db"
    original = database.read_bytes()
    assert original.count(b"baseline designation") == 1
    database.write_bytes(
        original.replace(b"baseline designation", b"tampered designation")
    )

    exit_code, value, _ = _run(action=action)
    assert exit_code == 1
    if action == "history":
        assert value == {
            "schema_version": 1,
            "status": "blocked",
            "items": [],
            "next_after": None,
        }
    else:
        assert value == {
            "schema_version": 1,
            "status": "missing",
            "kind": "generation",
            "record": None,
        }


@pytest.mark.parametrize(
    ("state", "expected_status", "expected_history_status"),
    [
        ("absent", "uninitialized", "uninitialized"),
        ("empty", "uninitialized", "uninitialized"),
        ("lock-only", "uninitialized", "uninitialized"),
        ("foreign", "base_only", "blocked"),
        ("dangling-symlink", "blocked", "blocked"),
        ("file-root", "blocked", "blocked"),
        ("symlink-root", "blocked", "blocked"),
        ("unsafe-mode", "blocked", "blocked"),
        ("unsafe-empty-mode", "blocked", "blocked"),
        ("lock-symlink", "blocked", "blocked"),
        ("lock-directory", "blocked", "blocked"),
        ("lock-mode", "blocked", "blocked"),
        ("lock-hardlink", "blocked", "blocked"),
    ],
)
def test_status_history_show_never_mutate_any_root_shape(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    state: str,
    expected_status: str,
    expected_history_status: str,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    root = home / "evolution"
    if state == "empty":
        root.mkdir(mode=0o700)
    elif state == "lock-only":
        root.mkdir(mode=0o700)
        (root / ".lifecycle.lock").write_bytes(b"lock")
        (root / ".lifecycle.lock").chmod(0o600)
    elif state == "foreign":
        root.mkdir(mode=0o700)
        (root / "foreign.marker").write_bytes(b"foreign")
    elif state == "dangling-symlink":
        root.symlink_to(home / "missing-target")
    elif state == "file-root":
        root.write_bytes(b"unsafe-root")
    elif state == "symlink-root":
        target = home / "target"
        target.mkdir(mode=0o700)
        (target / "retained.bin").write_bytes(b"retained")
        root.symlink_to(target)
    elif state == "unsafe-mode":
        root.mkdir(mode=0o755)
        (root / "foreign.marker").write_bytes(b"foreign")
    elif state == "unsafe-empty-mode":
        root.mkdir(mode=0o755)
    elif state == "lock-symlink":
        root.mkdir(mode=0o700)
        (root / ".lifecycle.lock").symlink_to(root / "missing-lock")
    elif state == "lock-directory":
        root.mkdir(mode=0o700)
        (root / ".lifecycle.lock").mkdir(mode=0o700)
    elif state == "lock-mode":
        root.mkdir(mode=0o700)
        (root / ".lifecycle.lock").write_bytes(b"lock")
        (root / ".lifecycle.lock").chmod(0o644)
    elif state == "lock-hardlink":
        root.mkdir(mode=0o700)
        source = home / "retained-lock-link"
        source.write_bytes(b"lock")
        source.chmod(0o600)
        os.link(source, root / ".lifecycle.lock")

    monkeypatch.setenv("HERMES_HOME", str(home))
    before = _inventory(home)

    status_exit, status, _ = _run(action="status")
    assert status_exit == 0
    assert set(status) == {
        "schema_version",
        "status",
        "initialized",
        "overlay_enabled",
        "active_generation_id",
        "last_known_good_generation_id",
        "diagnostics",
    }
    assert status["status"] == expected_status
    assert _inventory(home) == before

    history_exit, history, _ = _run(action="history", limit=1, after=0)
    assert history_exit == (0 if expected_history_status == "uninitialized" else 1)
    assert history == {
        "schema_version": 1,
        "status": expected_history_status,
        "items": [],
        "next_after": None,
    }
    assert _inventory(home) == before

    show_exit, show, _ = _run(
        action="show",
        kind="generation",
        record_id="a" * 64,
    )
    assert show_exit == 1
    assert show == {
        "schema_version": 1,
        "status": "missing",
        "kind": "generation",
        "record": None,
    }
    assert _inventory(home) == before


def test_wrong_owner_empty_root_is_blocked_without_mutation_when_portable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if not hasattr(os, "geteuid") or not hasattr(os, "chown"):
        pytest.skip("owner validation is not exposed on this platform")
    home = tmp_path / "home"
    root = home / "evolution"
    root.mkdir(parents=True, mode=0o700)
    try:
        os.chown(root, os.geteuid() + 1, -1)
    except PermissionError:
        pytest.skip("the current process cannot create a foreign-owned fixture")
    monkeypatch.setenv("HERMES_HOME", str(home))
    before = _inventory(home)

    status_exit, status, _ = _run(action="status")
    assert status_exit == 0
    assert status["status"] == "blocked"
    assert _inventory(home) == before

    history_exit, history, _ = _run(action="history", limit=1, after=0)
    assert history_exit == 1
    assert history["status"] == "blocked"
    assert _inventory(home) == before

    show_exit, show, _ = _run(
        action="show",
        kind="generation",
        record_id="a" * 64,
    )
    assert show_exit == 1
    assert show["status"] == "missing"
    assert _inventory(home) == before


@pytest.mark.parametrize("state", ["unsafe-empty-mode", "lock-symlink"])
def test_init_lock_failures_use_the_canonical_status_envelope(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    state: str,
) -> None:
    home = tmp_path / "home"
    root = home / "evolution"
    root.mkdir(parents=True, mode=0o700)
    home.chmod(0o700)
    root.chmod(0o700)
    if state == "unsafe-empty-mode":
        root.chmod(0o755)
    else:
        (root / ".lifecycle.lock").symlink_to(root / "missing-lock")
    monkeypatch.setenv("HERMES_HOME", str(home))
    before = _inventory(home)

    exit_code, value, _ = _run(action="init")

    assert exit_code == 1
    assert value == {
        "schema_version": 1,
        "status": "blocked",
        "initialized": False,
        "overlay_enabled": False,
        "active_generation_id": None,
        "last_known_good_generation_id": None,
        "diagnostics": ["evolution_unavailable"],
    }
    assert _inventory(home) == before


def test_init_lock_timeout_uses_the_canonical_status_envelope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from hermes_cli.evolution.locking import LifecycleLockTimeout

    def fail_initialization() -> None:
        raise LifecycleLockTimeout("lifecycle_lock_timeout")

    monkeypatch.setattr(
        command_module,
        "ensure_evolution_initialized",
        fail_initialization,
    )

    exit_code, value, _ = _run(action="init")

    assert exit_code == 1
    assert value == {
        "schema_version": 1,
        "status": "blocked",
        "initialized": False,
        "overlay_enabled": False,
        "active_generation_id": None,
        "last_known_good_generation_id": None,
        "diagnostics": ["evolution_unavailable"],
    }
