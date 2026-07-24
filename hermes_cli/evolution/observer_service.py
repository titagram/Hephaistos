"""Observer background scan orchestration and circuit-breaker service with state persistence."""

from __future__ import annotations

import json
import logging
from pathlib import Path

from .observation_contract import ObservationEnvelope
from .observer_policy import compute_opportunity_key, is_opportunity_eligible, score_opportunity
from .organism_home import ensure_organism_directories, secure_file_permissions
from .suggestions import SuggestionRecord, SuggestionRepository
from .telos_store import TelosStore

logger = logging.getLogger(__name__)


class CircuitBreakerOpen(Exception):
    """Raised when the Observer circuit breaker is open due to repeated errors."""


class ObserverService:
    def __init__(self, organism_root: Path | None = None, max_consecutive_errors: int = 5) -> None:
        self.organism_root = ensure_organism_directories(organism_root)
        self.db_path = self.organism_root / "evolution" / "evolution.db"
        self.repository = SuggestionRepository(self.db_path)
        self.telos_store = TelosStore(self.organism_root)
        self.max_consecutive_errors = max_consecutive_errors
        self.state_file = self.organism_root / "observer_state.json"
        self.consecutive_errors = 0
        self.circuit_open = False
        self._load_state()

    def _load_state(self) -> None:
        if not self.state_file.is_file():
            return
        try:
            data = json.loads(self.state_file.read_text(encoding="utf-8"))
            self.consecutive_errors = int(data.get("consecutive_errors", 0))
            self.circuit_open = bool(data.get("circuit_open", False))
        except Exception as e:
            logger.warning(f"Failed to load observer state: {e}")

    def _save_state(self) -> None:
        try:
            data = {
                "consecutive_errors": self.consecutive_errors,
                "circuit_open": self.circuit_open,
            }
            self.state_file.write_text(json.dumps(data, indent=2), encoding="utf-8")
            secure_file_permissions(self.state_file)
        except Exception as e:
            logger.warning(f"Failed to save observer state: {e}")

    def reset_circuit_breaker(self) -> None:
        self.consecutive_errors = 0
        self.circuit_open = False
        self._save_state()

    def record_error(self) -> None:
        self.consecutive_errors += 1
        if self.consecutive_errors >= self.max_consecutive_errors:
            self.circuit_open = True
        self._save_state()

    def ingest_envelope(self, envelope: ObservationEnvelope) -> bool:
        if self.circuit_open:
            raise CircuitBreakerOpen("Observer circuit breaker is open")
        try:
            res = self.repository.insert_observation_envelope(envelope)
            if self.consecutive_errors > 0:
                self.consecutive_errors = 0
                self._save_state()
            return res
        except Exception as e:
            self.record_error()
            raise e

    def scan_and_update_suggestions(self, max_events: int = 1000) -> list[SuggestionRecord]:
        if self.circuit_open:
            raise CircuitBreakerOpen("Observer circuit breaker is open")

        try:
            active_telos = self.telos_store.get_active_revision()
            active_digest = self.telos_store.get_active_digest() or "none"
            if not active_telos:
                return []

            all_envelopes = self.repository.list_all_envelopes()[:max_events]
            if not all_envelopes:
                return []

            # Cluster by opportunity key
            clusters: dict[str, list[ObservationEnvelope]] = {}
            for env in all_envelopes:
                key = compute_opportunity_key(
                    env.organism_id,
                    env.capability_key,
                    env.operation_key,
                    env.outcome_key,
                    env.constraint_key,
                )
                clusters.setdefault(key, []).append(env)

            updated_suggestions: list[SuggestionRecord] = []

            for key, env_group in clusters.items():
                eligible = is_opportunity_eligible(env_group, active_telos)
                score = score_opportunity(env_group, active_telos)
                initial_state = "eligible" if eligible else "observing"

                first_env = env_group[0]
                reason = f"Evidenced gap for {first_env.capability_key} ({first_env.outcome_key})"

                sug = self.repository.upsert_suggestion(
                    opportunity_key=key,
                    initial_state=initial_state,
                    active_telos_digest=active_digest,
                    score=score,
                    envelopes=env_group,
                    summary_reason=reason,
                )

                # Transition state if newly eligible
                if eligible and sug.state == "observing":
                    self.repository.update_suggestion_state(sug.suggestion_id, "eligible", "eligibility_passed")
                    sug = self.repository.get_suggestion_by_id(sug.suggestion_id)  # type: ignore[assignment]

                if sug is not None:
                    updated_suggestions.append(sug)

            if self.consecutive_errors > 0:
                self.consecutive_errors = 0
                self._save_state()

            return updated_suggestions
        except Exception as e:
            self.record_error()
            raise e
