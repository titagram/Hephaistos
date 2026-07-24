"""Suggestions projection repository and SQLite transactional store with versioned migration support."""

from __future__ import annotations

import hashlib
import json
import sqlite3

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

from .observation_contract import ObservationEnvelope, observation_envelope_from_dict, validate_observation_envelope
from .observer_policy import OpportunityScore
from .organism_home import ensure_organism_directories, secure_file_permissions


SUGGESTION_STATES = frozenset({
    "observing",
    "eligible",
    "surfaced",
    "accepted",
    "dismissed",
    "superseded",
    "addressed",
    "draft",
})


@dataclass(frozen=True)
class SuggestionRecord:
    suggestion_id: str
    opportunity_key: str
    state: str
    active_telos_digest: str
    score: float
    user_intent: float
    telos_alignment: float
    impact: float
    recurrence: float
    confidence: float
    reuse: float
    risk: float
    expected_cost: float
    score_policy_version: str
    first_observed_at: str
    last_observed_at: str
    observation_count: int
    distinct_session_count: int
    summary_reason: str
    created_at: str
    updated_at: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "suggestion_id": self.suggestion_id,
            "opportunity_key": self.opportunity_key,
            "state": self.state,
            "active_telos_digest": self.active_telos_digest,
            "score": self.score,
            "user_intent": self.user_intent,
            "telos_alignment": self.telos_alignment,
            "impact": self.impact,
            "recurrence": self.recurrence,
            "confidence": self.confidence,
            "reuse": self.reuse,
            "risk": self.risk,
            "expected_cost": self.expected_cost,
            "score_policy_version": self.score_policy_version,
            "first_observed_at": self.first_observed_at,
            "last_observed_at": self.last_observed_at,
            "observation_count": self.observation_count,
            "distinct_session_count": self.distinct_session_count,
            "summary_reason": self.summary_reason,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }





class SuggestionRepository:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        return conn

    def insert_observation_envelope(self, envelope: ObservationEnvelope) -> bool:
        validate_observation_envelope(envelope)
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT event_id FROM observation_envelopes WHERE event_id = ?", (envelope.event_id,))
        if cursor.fetchone() is not None:
            conn.close()
            return False

        cursor.execute(
            """
            INSERT INTO observation_envelopes (
                event_id, organism_id, occurred_at, signal_type, provenance,
                source_profile_ref, source_project_ref, source_session_ref,
                generation_id, capability_key, operation_key, outcome_key,
                constraint_key, severity, task_impact, retry_count,
                latency_bucket, explicit_user_intent, recovered,
                evidence_refs_json, redaction_status, canonical_envelope_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                envelope.event_id,
                envelope.organism_id,
                envelope.occurred_at,
                envelope.signal_type,
                envelope.provenance,
                envelope.source_profile_ref,
                envelope.source_project_ref,
                envelope.source_session_ref,
                envelope.generation_id,
                envelope.capability_key,
                envelope.operation_key,
                envelope.outcome_key,
                envelope.constraint_key,
                envelope.severity,
                envelope.task_impact,
                envelope.retry_count,
                envelope.latency_bucket,
                1 if envelope.explicit_user_intent else 0,
                1 if envelope.recovered else 0,
                json.dumps(list(envelope.evidence_refs)),
                envelope.redaction_status,
                envelope.to_canonical_json(),
            ),
        )
        conn.commit()
        conn.close()
        return True

    def get_envelopes_for_opportunity(self, capability_key: str, operation_key: str, outcome_key: str, constraint_key: str) -> list[ObservationEnvelope]:
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT canonical_envelope_json FROM observation_envelopes
            WHERE capability_key = ? AND operation_key = ? AND outcome_key = ? AND constraint_key = ?
            ORDER BY occurred_at ASC
            """,
            (capability_key, operation_key, outcome_key, constraint_key),
        )
        rows = cursor.fetchall()
        conn.close()
        return [observation_envelope_from_dict(json.loads(row[0])) for row in rows]

    def list_all_envelopes(self) -> list[ObservationEnvelope]:
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT canonical_envelope_json FROM observation_envelopes ORDER BY occurred_at ASC")
        rows = cursor.fetchall()
        conn.close()
        return [observation_envelope_from_dict(json.loads(row[0])) for row in rows]

    def get_suggestion_by_id(self, suggestion_id: str) -> SuggestionRecord | None:
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM opportunity_suggestions WHERE suggestion_id = ?", (suggestion_id,))
        row = cursor.fetchone()
        conn.close()
        if not row:
            return None
        return self._record_from_row(row)

    def get_suggestion_by_opportunity_key(self, opportunity_key: str) -> SuggestionRecord | None:
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM opportunity_suggestions WHERE opportunity_key = ?", (opportunity_key,))
        row = cursor.fetchone()
        conn.close()
        if not row:
            return None
        return self._record_from_row(row)

    def list_suggestions(self, state: str | None = None) -> list[SuggestionRecord]:
        conn = self._get_connection()
        cursor = conn.cursor()
        if state:
            cursor.execute("SELECT * FROM opportunity_suggestions WHERE state = ? ORDER BY score DESC, created_at ASC", (state,))
        else:
            cursor.execute("SELECT * FROM opportunity_suggestions ORDER BY score DESC, created_at ASC")
        rows = cursor.fetchall()
        conn.close()
        return [self._record_from_row(r) for r in rows]

    def upsert_suggestion(
        self,
        opportunity_key: str,
        initial_state: str,
        active_telos_digest: str,
        score: OpportunityScore,
        envelopes: Sequence[ObservationEnvelope],
        summary_reason: str,
    ) -> SuggestionRecord:
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"
        first_obs = min(e.occurred_at for e in envelopes)
        last_obs = max(e.occurred_at for e in envelopes)
        distinct_sessions = len({e.source_session_ref for e in envelopes if e.source_session_ref})

        existing = self.get_suggestion_by_opportunity_key(opportunity_key)
        conn = self._get_connection()
        cursor = conn.cursor()

        if existing is None:
            h = hashlib.sha256(opportunity_key.encode("utf-8")).hexdigest()[:12]
            suggestion_id = f"sug_{h}"

            cursor.execute(
                """
                INSERT INTO opportunity_suggestions (
                    suggestion_id, opportunity_key, state, active_telos_digest,
                    score, user_intent, telos_alignment, impact, recurrence,
                    confidence, reuse, risk, expected_cost, score_policy_version,
                    first_observed_at, last_observed_at, observation_count,
                    distinct_session_count, summary_reason, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    suggestion_id,
                    opportunity_key,
                    initial_state,
                    active_telos_digest,
                    score.score,
                    score.user_intent,
                    score.telos_alignment,
                    score.impact,
                    score.recurrence,
                    score.confidence,
                    score.reuse,
                    score.risk,
                    score.expected_cost,
                    score.policy_version,
                    first_obs,
                    last_obs,
                    len(envelopes),
                    distinct_sessions,
                    summary_reason,
                    now,
                    now,
                ),
            )

            # Record event
            cursor.execute(
                "INSERT INTO opportunity_suggestion_events VALUES (?, ?, ?, ?, ?, ?)",
                (str(uuid.uuid4()), suggestion_id, "none", initial_state, "created", now),
            )
        else:
            suggestion_id = existing.suggestion_id
            cursor.execute(
                """
                UPDATE opportunity_suggestions SET
                    active_telos_digest = ?,
                    score = ?, user_intent = ?, telos_alignment = ?, impact = ?,
                    recurrence = ?, confidence = ?, reuse = ?, risk = ?,
                    expected_cost = ?, last_observed_at = ?, observation_count = ?,
                    distinct_session_count = ?, summary_reason = ?, updated_at = ?
                WHERE suggestion_id = ?
                """,
                (
                    active_telos_digest,
                    score.score,
                    score.user_intent,
                    score.telos_alignment,
                    score.impact,
                    score.recurrence,
                    score.confidence,
                    score.reuse,
                    score.risk,
                    score.expected_cost,
                    last_obs,
                    len(envelopes),
                    distinct_sessions,
                    summary_reason,
                    now,
                    suggestion_id,
                ),
            )

        conn.commit()
        conn.close()
        return self.get_suggestion_by_id(suggestion_id)  # type: ignore[return-value]

    def update_suggestion_state(self, suggestion_id: str, new_state: str, reason_code: str) -> None:
        if new_state not in SUGGESTION_STATES:
            raise ValueError(f"Invalid suggestion state: {new_state}")
        existing = self.get_suggestion_by_id(suggestion_id)
        if not existing:
            raise ValueError(f"Suggestion not found: {suggestion_id}")

        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute("UPDATE opportunity_suggestions SET state = ?, updated_at = ? WHERE suggestion_id = ?", (new_state, now, suggestion_id))
        cursor.execute(
            "INSERT INTO opportunity_suggestion_events VALUES (?, ?, ?, ?, ?, ?)",
            (str(uuid.uuid4()), suggestion_id, existing.state, new_state, reason_code, now),
        )
        conn.commit()
        conn.close()

    def _record_from_row(self, row: sqlite3.Row) -> SuggestionRecord:
        keys = row.keys()
        return SuggestionRecord(
            suggestion_id=row["suggestion_id"],
            opportunity_key=row["opportunity_key"] if "opportunity_key" in keys and row["opportunity_key"] is not None else "",
            state=row["state"],
            active_telos_digest=row["active_telos_digest"] if "active_telos_digest" in keys and row["active_telos_digest"] is not None else "",
            score=float(row["score"]) if "score" in keys and row["score"] is not None else 0.0,
            user_intent=float(row["user_intent"]) if "user_intent" in keys and row["user_intent"] is not None else 0.0,
            telos_alignment=float(row["telos_alignment"]) if "telos_alignment" in keys and row["telos_alignment"] is not None else 0.0,
            impact=float(row["impact"]) if "impact" in keys and row["impact"] is not None else 0.0,
            recurrence=float(row["recurrence"]) if "recurrence" in keys and row["recurrence"] is not None else 0.0,
            confidence=float(row["confidence"]) if "confidence" in keys and row["confidence"] is not None else 0.0,
            reuse=float(row["reuse"]) if "reuse" in keys and row["reuse"] is not None else 0.0,
            risk=float(row["risk"]) if "risk" in keys and row["risk"] is not None else 0.0,
            expected_cost=float(row["expected_cost"]) if "expected_cost" in keys and row["expected_cost"] is not None else 0.0,
            score_policy_version=row["score_policy_version"] if "score_policy_version" in keys and row["score_policy_version"] is not None else "v2",
            first_observed_at=row["first_observed_at"] if "first_observed_at" in keys and row["first_observed_at"] is not None else "",
            last_observed_at=row["last_observed_at"] if "last_observed_at" in keys and row["last_observed_at"] is not None else "",
            observation_count=int(row["observation_count"]) if "observation_count" in keys and row["observation_count"] is not None else 0,
            distinct_session_count=int(row["distinct_session_count"]) if "distinct_session_count" in keys and row["distinct_session_count"] is not None else 0,
            summary_reason=row["summary_reason"] if "summary_reason" in keys and row["summary_reason"] is not None else "",
            created_at=row["created_at"],
            updated_at=row["updated_at"] if "updated_at" in keys and row["updated_at"] is not None else "",
        )
