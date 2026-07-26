"""JSON command contracts for inert Autopoiesis blueprint proposals."""

from __future__ import annotations

import json
import sqlite3
from argparse import Namespace
from pathlib import Path

import pytest

from hermes_cli.evolution.command import evolution_command
from tests.hermes_cli.evolution.test_proposal_service import (
    _activate_telos,
    _create_eligible_suggestion,
    _enable_autopoiesis,
    _setup_organism,
)


def _ready_command_organism(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
):
    organism_root, organism_id = _setup_organism(tmp_path, monkeypatch)
    _activate_telos(organism_root, organism_id)
    _enable_autopoiesis(tmp_path)
    suggestion = _create_eligible_suggestion(
        organism_root,
        organism_id,
    )
    assert suggestion is not None
    capsys.readouterr()
    return organism_root, suggestion


def _command_json(
    capsys: pytest.CaptureFixture[str],
    **arguments,
) -> tuple[int, dict[str, object]]:
    result = evolution_command(
        Namespace(
            json=True,
            org_root=None,
            **arguments,
        )
    )
    captured = capsys.readouterr()
    assert captured.err == ""
    lines = captured.out.strip().splitlines()
    assert len(lines) == 1
    return result, json.loads(lines[0])


def _create_proposal_via_command(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
):
    _, suggestion = _ready_command_organism(
        tmp_path,
        monkeypatch,
        capsys,
    )
    return _command_json(
        capsys,
        action="propose",
        suggestion_id=suggestion.suggestion_id,
    )


def test_propose_emits_public_created_or_existing_summary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    return_code, result = _create_proposal_via_command(
        tmp_path,
        monkeypatch,
        capsys,
    )

    assert return_code == 0
    assert result["action"] == "propose"
    assert result["status"] in {"created", "existing"}
    assert set(result["proposal"]) == {
        "blueprint_id",
        "attempt_id",
        "canonical_digest",
        "state",
        "created_at",
    }
    assert result["next_action"] == "review_blueprint"


def test_second_propose_is_existing_without_new_public_fields(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _, suggestion = _ready_command_organism(
        tmp_path,
        monkeypatch,
        capsys,
    )
    first_code, first = _command_json(
        capsys,
        action="propose",
        suggestion_id=suggestion.suggestion_id,
    )
    second_code, second = _command_json(
        capsys,
        action="propose",
        suggestion_id=suggestion.suggestion_id,
    )

    assert (first_code, second_code) == (0, 0)
    assert (first["status"], second["status"]) == (
        "created",
        "existing",
    )
    assert second["proposal"] == first["proposal"]


def test_blueprint_show_returns_verified_full_document(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _, proposed = _create_proposal_via_command(
        tmp_path,
        monkeypatch,
        capsys,
    )
    blueprint_id = proposed["proposal"]["blueprint_id"]

    return_code, shown = _command_json(
        capsys,
        action="blueprint_show",
        blueprint_id=blueprint_id,
    )

    assert return_code == 0
    assert shown["action"] == "blueprint_show"
    assert shown["status"] == "found"
    assert shown["blueprint"]["blueprint_id"] == blueprint_id
    document = shown["blueprint"]["document"]
    assert document["schema_version"] == 1
    assert document["origin"] == "observer-v1"
    assert document["suggestion_id"]


def test_blueprint_list_omits_private_observer_snapshot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _create_proposal_via_command(tmp_path, monkeypatch, capsys)

    return_code, listed = _command_json(
        capsys,
        action="blueprint_list",
        limit=20,
    )

    assert return_code == 0
    assert listed["action"] == "blueprint_list"
    assert listed["status"] == "ok"
    assert listed["count"] == 1
    assert set(listed["items"][0]) == {
        "blueprint_id",
        "canonical_digest",
        "state",
        "created_at",
        "suggestion_id",
        "score",
        "score_policy_version",
    }
    assert "summary_reason" not in listed["items"][0]
    assert "evidence_digests" not in listed["items"][0]


def test_blueprint_proposed_history_event_is_publicly_allowlisted(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _create_proposal_via_command(tmp_path, monkeypatch, capsys)

    return_code, history = _command_json(
        capsys,
        action="history",
        limit=100,
        after=0,
    )

    assert return_code == 0
    proposed = [
        item
        for item in history["items"]
        if item["event_type"] == "blueprint_proposed"
    ]
    assert len(proposed) == 1
    assert proposed[0]["reason_code"] == "blueprint_proposed"
    assert proposed[0]["reason_summary"] == "observer proposal created"
    assert proposed[0]["actor"] == "operator"


@pytest.mark.parametrize(
    ("setup_kind", "expected_reason"),
    [
        ("uninitialized", "organism_identity_missing"),
        ("unknown", "suggestion_missing"),
        ("observing", "suggestion_not_eligible"),
        ("no_telos", "no_active_telos"),
    ],
)
def test_propose_gate_failures_are_public_and_stable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    setup_kind: str,
    expected_reason: str,
) -> None:
    if setup_kind == "uninitialized":
        organism_root = tmp_path / "organism"
        profile_root = tmp_path / "profile"
        monkeypatch.setattr(
            "hermes_cli.evolution.command.hermes_constants.get_organism_home",
            lambda: organism_root,
        )
        monkeypatch.setattr(
            "hermes_cli.evolution.command.hermes_constants.get_hermes_home",
            lambda: profile_root,
        )
        suggestion_id = "sug_test"
    else:
        organism_root, organism_id = _setup_organism(
            tmp_path,
            monkeypatch,
        )
        _enable_autopoiesis(tmp_path)
        if setup_kind != "no_telos":
            _activate_telos(organism_root, organism_id)
            suggestion = _create_eligible_suggestion(
                organism_root,
                organism_id,
            )
            assert suggestion is not None
            suggestion_id = suggestion.suggestion_id
            if setup_kind == "observing":
                db_path = (
                    organism_root / "evolution" / "evolution.db"
                )
                conn = sqlite3.connect(
                    f"{db_path.absolute().as_uri()}?mode=rw",
                    uri=True,
                )
                try:
                    conn.execute(
                        """
                        UPDATE opportunity_suggestions
                        SET state = 'observing'
                        WHERE suggestion_id = ?
                        """,
                        (suggestion_id,),
                    )
                    conn.commit()
                finally:
                    conn.close()
        else:
            suggestion_id = "sug_test"
        if setup_kind == "unknown":
            suggestion_id = "sug_unknown"
        capsys.readouterr()

    return_code, result = _command_json(
        capsys,
        action="propose",
        suggestion_id=suggestion_id,
    )

    assert return_code == 1
    assert result == {
        "schema_version": 1,
        "action": "propose",
        "status": "blocked",
        "reason": expected_reason,
        "proposal": None,
        "next_action": None,
    }
    assert not (
        setup_kind == "uninitialized" and organism_root.exists()
    )


def test_blueprint_show_missing_is_missing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _ready_command_organism(tmp_path, monkeypatch, capsys)

    return_code, result = _command_json(
        capsys,
        action="blueprint_show",
        blueprint_id="bp_00000000-0000-0000-0000-000000000000",
    )

    assert return_code == 1
    assert result == {
        "schema_version": 1,
        "action": "blueprint_show",
        "status": "missing",
        "blueprint": None,
    }


def test_blueprint_show_tampered_document_is_blocked_not_missing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    organism_root, suggestion = _ready_command_organism(
        tmp_path,
        monkeypatch,
        capsys,
    )
    _, proposed = _command_json(
        capsys,
        action="propose",
        suggestion_id=suggestion.suggestion_id,
    )
    blueprint_id = proposed["proposal"]["blueprint_id"]
    db_path = organism_root / "evolution" / "evolution.db"
    conn = sqlite3.connect(
        f"{db_path.absolute().as_uri()}?mode=rw",
        uri=True,
    )
    try:
        trigger = conn.execute(
            """
            SELECT sql
            FROM sqlite_master
            WHERE type = 'trigger'
              AND name = 'blueprint_documents_no_update'
            """
        ).fetchone()
        assert trigger is not None
        trigger_sql = str(trigger[0])
        conn.execute("DROP TRIGGER blueprint_documents_no_update")
        conn.execute(
            """
            UPDATE blueprint_documents
            SET canonical_document_json = '{}'
            WHERE blueprint_id = ?
            """,
            (blueprint_id,),
        )
        conn.execute(trigger_sql)
        conn.commit()
    finally:
        conn.close()

    return_code, result = _command_json(
        capsys,
        action="blueprint_show",
        blueprint_id=blueprint_id,
    )

    assert return_code == 1
    assert result["action"] == "blueprint_show"
    assert result["status"] == "blocked"
    assert result["reason"] == "blueprint_incoherent"
    assert result["blueprint"] is None


def test_autopoiesis_skill_teaches_only_the_inert_proposal_flow() -> None:
    skill_path = (
        Path(__file__).resolve().parents[3]
        / "skills"
        / "autopoiesis"
        / "SKILL.md"
    )
    text = skill_path.read_text(encoding="utf-8")
    lowered = text.lower()
    normalized = " ".join(lowered.split())

    for required in (
        "hermes evolution propose <suggestion-id>",
        "hermes evolution blueprint show <blueprint-id>",
        "hermes evolution blueprint list",
        "does not build, install, research, or modify source files",
        "explicit user approval",
        "eligible",
        "telos",
        "inert local draft",
    ):
        assert required in normalized

    for forbidden_command in (
        "hades backend sync",
        "pip install",
        "npm install",
        "curl ",
        "apply_patch",
        "write_file",
        "sed -i",
        "cat >",
    ):
        assert forbidden_command not in lowered
