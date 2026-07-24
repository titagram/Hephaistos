"""Profile-local error log importer with durable cursor and fail-soft redaction."""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from hermes_constants import get_hermes_home
from .observation_contract import ObservationEnvelope, validate_observation_envelope, ObservationContractError

_EXCEPTION_CLASS_PATTERN = re.compile(r"([A-Z][a-zA-Z0-9_]+Error|[A-Z][a-zA-Z0-9_]+Exception)")


def classify_error_line(line: str) -> tuple[str, str, str]:
    """Extract closed taxonomy keys (capability_key, operation_key, outcome_key) without raw line content."""
    m = _EXCEPTION_CLASS_PATTERN.search(line)
    exception_cls = m.group(1).lower() if m else "generic.error"
    # Clean taxonomy key
    capability_key = "system.runtime"
    operation_key = "error.log"
    outcome_key = exception_cls.replace("error", "").replace("exception", "").strip(".") or "failure"
    return capability_key, operation_key, outcome_key


class ExperienceBridge:
    def __init__(
        self,
        organism_id: str,
        profile_ref: str,
        generation_id: str,
        hermes_home: Path | None = None,
    ) -> None:
        self.organism_id = organism_id
        self.profile_ref = profile_ref
        self.generation_id = generation_id
        self.hermes_home = (hermes_home or get_hermes_home()).resolve()
        self.logs_dir = self.hermes_home / "logs"
        self.cursor_file = self.logs_dir / ".experience_cursor.json"

    def _read_cursor(self) -> int:
        if not self.cursor_file.exists():
            return 0
        try:
            data = json.loads(self.cursor_file.read_text(encoding="utf-8"))
            return int(data.get("offset", 0))
        except Exception:
            return 0

    def _write_cursor(self, offset: int) -> None:
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        data = {"offset": offset, "updated_at": datetime.now(timezone.utc).isoformat()}
        self.cursor_file.write_text(json.dumps(data), encoding="utf-8")

    def import_new_error_events(self, max_lines: int = 500) -> list[ObservationEnvelope]:
        log_file = self.logs_dir / "errors.log"
        if not log_file.is_file():
            return []

        current_offset = self._read_cursor()
        envelopes: list[ObservationEnvelope] = []

        try:
            with log_file.open("r", encoding="utf-8", errors="replace") as f:
                f.seek(current_offset)
                count = 0
                for line in f:
                    if count >= max_lines:
                        break
                    count += 1
                    line_str = line.strip()
                    if not line_str:
                        continue

                    cap_key, op_key, out_key = classify_error_line(line_str)
                    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"

                    envelope = ObservationEnvelope(
                        schema_version=1,
                        event_id=str(uuid.uuid4()),
                        organism_id=self.organism_id,
                        occurred_at=now,
                        signal_type="failure",
                        provenance="legacy_log_import",
                        source_profile_ref=self.profile_ref,
                        source_project_ref=None,
                        source_session_ref=None,
                        generation_id=self.generation_id,
                        gnothi_revision_digest=None,
                        telos_digest=None,
                        capability_key=cap_key,
                        operation_key=op_key,
                        outcome_key=out_key,
                        constraint_key="unconstrained",
                        severity="medium",
                        task_impact="low",
                        retry_count=0,
                        latency_bucket=None,
                        explicit_user_intent=False,
                        recovered=False,
                        evidence_refs=(),
                        redaction_status="verified_redacted",
                    )

                    try:
                        validate_observation_envelope(envelope)
                        envelopes.append(envelope)
                    except ObservationContractError:
                        # Fail-soft: omit invalid envelope
                        pass

                new_offset = f.tell()
                self._write_cursor(new_offset)
        except Exception:
            pass

        return envelopes
