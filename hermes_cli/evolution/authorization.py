"""Immutable, digest-bound authorization records for local evolution."""

from __future__ import annotations

import json
import re
import unicodedata
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from types import MappingProxyType
from typing import Literal

from .contract import canonical_json_bytes, content_digest, require_digest
from .ledger import EvolutionLedger, LifecycleEvent
from .state_machine import GrantKind


_REQUEST_DIGEST_DOMAIN = "hades-evolution-authorization-request-v1"
_SCOPE_DIGEST_DOMAIN = "hades-evolution-authorization-scope-v1"
_KINDS = frozenset({"research", "build", "promotion"})
_COMPONENT_CLASSES = frozenset({"skill", "script", "plugin", "mcp"})
_TOKEN_PATTERN = re.compile(r"[a-z][a-z0-9._-]{0,63}\Z", re.ASCII)
_HOST_PATTERN = re.compile(
    r"(?=.{1,253}\Z)"
    r"(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)*"
    r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\Z",
    re.ASCII,
)
_HEX_SECRET_PATTERN = re.compile(r"[0-9a-f]{24,}\Z", re.ASCII)
_CREDENTIAL_PREFIXES = (
    "github_pat_",
    "github-pat-",
    "glpat-",
    "sk_live_",
    "sk-live-",
    "sk_test_",
    "sk-test-",
    "sk_proj_",
    "sk-proj-",
    "pk_live_",
    "pk-live-",
    "pk_test_",
    "pk-test-",
    "xoxb_",
    "xoxb-",
    "xoxp_",
    "xoxp-",
    "ghp_",
    "ghp-",
    "hf_",
    "ya29.",
    "ya29_",
    "ya29-",
    "akia-",
    "akia",
    "pk_",
    "pk-",
    "sk_",
    "sk-",
)
_FILE_SUFFIXES = frozenset(
    {
        "crt",
        "env",
        "json",
        "key",
        "pem",
        "py",
        "sh",
        "toml",
        "txt",
        "yaml",
        "yml",
        "zip",
    }
)
_MAX_ITEMS = 32
_MAX_TTL_SECONDS = 86400


class AuthorizationError(RuntimeError):
    """A fail-closed authorization failure with a stable code."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class AuthorizationRequest:
    request_id: str
    attempt_id: str
    kind: GrantKind
    subject_digest: str
    scope: Mapping[str, object]
    ttl_seconds: int
    expires_at: str
    created_at: str

    def canonical_payload(self) -> dict[str, object]:
        return {
            "request_id": self.request_id,
            "attempt_id": self.attempt_id,
            "kind": self.kind,
            "subject_digest": self.subject_digest,
            "scope": _plain(self.scope),
            "ttl_seconds": self.ttl_seconds,
            "expires_at": self.expires_at,
            "created_at": self.created_at,
        }


@dataclass(frozen=True)
class AuthorizationDecision:
    decision_id: str
    request_id: str
    decision: Literal["approved", "denied"]
    decided_by: str
    confirmation_digest: str | None
    created_at: str


@dataclass(frozen=True)
class AuthorizationGrant:
    grant_id: str
    request_id: str
    attempt_id: str
    kind: GrantKind
    subject_digest: str
    scope: Mapping[str, object]
    expires_at: str
    approved_by: str
    confirmation_digest: str
    created_at: str
    consumed_at: str | None = None


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _expires_at(created_at: str, ttl_seconds: int) -> str:
    value = datetime.strptime(
        created_at, "%Y-%m-%dT%H:%M:%S.%fZ"
    ).replace(tzinfo=UTC)
    return (value + timedelta(seconds=ttl_seconds)).strftime(
        "%Y-%m-%dT%H:%M:%S.%fZ"
    )


def _plain(value: object) -> object:
    if isinstance(value, Mapping):
        return {key: _plain(child) for key, child in value.items()}
    if isinstance(value, tuple):
        return [_plain(child) for child in value]
    return value


def _syntax_safe_symbolic(
    value: object, *, code: str, limit: int = 64
) -> str:
    if not isinstance(value, str):
        raise AuthorizationError(code)
    normalized = unicodedata.normalize("NFC", value)
    suffix = value.rpartition(".")[2] if "." in value else ""
    if (
        normalized != value
        or not 1 <= len(value) <= limit
        or _TOKEN_PATTERN.fullmatch(value) is None
        or suffix in _FILE_SUFFIXES
    ):
        raise AuthorizationError(code)
    return value


def _compact_material(value: str) -> str:
    return value.translate(str.maketrans("", "", "._-"))


def _looks_like_credential_material(value: str) -> bool:
    compact = _compact_material(value)
    if _HEX_SECRET_PATTERN.fullmatch(compact) is not None:
        return True

    unique = len(set(compact))
    has_alpha = any(character.isalpha() for character in compact)
    has_digit = any(character.isdigit() for character in compact)
    has_separator = any(separator in value for separator in "._-")
    if (
        not has_separator
        and len(compact) >= 32
        and unique >= 10
        and has_alpha
        and has_digit
    ):
        return True
    if not has_separator and len(compact) >= 40 and unique >= 14:
        return True

    for prefix in _CREDENTIAL_PREFIXES:
        if not value.startswith(prefix):
            continue
        payload = _compact_material(value[len(prefix) :])
        payload_unique = len(set(payload))
        return len(payload) >= 16 and (
            (
                payload_unique >= 8
                and any(character.isalpha() for character in payload)
                and any(character.isdigit() for character in payload)
            )
            or (len(payload) >= 24 and payload_unique >= 12)
        )
    return False


def _privacy_safe_symbolic(
    value: object, *, code: str, limit: int = 64
) -> str:
    symbolic = _syntax_safe_symbolic(value, code=code, limit=limit)
    if _looks_like_credential_material(symbolic):
        raise AuthorizationError(code)
    return symbolic


def _canonical_token(value: object) -> str:
    return _privacy_safe_symbolic(value, code="invalid_scope")


def _canonical_sequence(
    value: object,
    *,
    nonempty: bool,
    allowed: frozenset[str] | None = None,
) -> tuple[str, ...]:
    if (
        isinstance(value, (str, bytes, bytearray))
        or not isinstance(value, Sequence)
        or len(value) > _MAX_ITEMS
        or (nonempty and len(value) == 0)
    ):
        raise AuthorizationError("invalid_scope")
    result = tuple(_canonical_token(item) for item in value)
    if len(set(result)) != len(result):
        raise AuthorizationError("invalid_scope")
    if allowed is not None and not set(result) <= allowed:
        raise AuthorizationError("invalid_scope")
    return result


def _domains(value: object) -> tuple[str, ...]:
    if (
        isinstance(value, (str, bytes, bytearray))
        or not isinstance(value, Sequence)
        or len(value) > _MAX_ITEMS
    ):
        raise AuthorizationError("invalid_scope")
    result: list[str] = []
    for domain in value:
        if (
            not isinstance(domain, str)
            or unicodedata.normalize("NFC", domain) != domain
            or _HOST_PATTERN.fullmatch(domain) is None
            or any(
                _looks_like_credential_material(label)
                for label in domain.split(".")
            )
        ):
            raise AuthorizationError("invalid_scope")
        result.append(domain)
    if len(set(result)) != len(result):
        raise AuthorizationError("invalid_scope")
    return tuple(result)


def _positive_integer(value: object, *, maximum: int = 2**31 - 1) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or value < 1
        or value > maximum
    ):
        raise AuthorizationError("invalid_scope")
    return value


def _policy_mapping(value: object) -> Mapping[str, object]:
    if not isinstance(value, Mapping) or not 1 <= len(value) <= 16:
        raise AuthorizationError("invalid_scope")
    result: dict[str, object] = {}
    for key, fact in value.items():
        canonical_key = _canonical_token(key)
        if isinstance(fact, bool):
            normalized: object = fact
        elif isinstance(fact, int) and 0 <= fact <= 2**31 - 1:
            normalized = fact
        elif isinstance(fact, str):
            normalized = _canonical_token(fact)
        else:
            raise AuthorizationError("invalid_scope")
        result[canonical_key] = normalized
    return MappingProxyType(result)


def _resource_limits(value: object) -> Mapping[str, int]:
    if not isinstance(value, Mapping) or not 1 <= len(value) <= 16:
        raise AuthorizationError("invalid_scope")
    return MappingProxyType(
        {
            _canonical_token(key): _positive_integer(limit)
            for key, limit in value.items()
        }
    )


def _exact_keys(
    scope: object, expected: frozenset[str]
) -> Mapping[str, object]:
    if not isinstance(scope, Mapping) or set(scope) != expected:
        raise AuthorizationError("invalid_scope")
    return scope


def _validate_scope(
    kind: GrantKind, scope: Mapping[str, object]
) -> Mapping[str, object]:
    if kind == "research":
        source = _exact_keys(
            scope,
            frozenset(
                {"source_classes", "domains", "operations", "duration"}
            ),
        )
        operations = source["operations"]
        if not isinstance(operations, (list, tuple)) or list(operations) != [
            "search",
            "retrieve",
        ]:
            raise AuthorizationError("invalid_scope")
        return MappingProxyType(
            {
                "source_classes": _canonical_sequence(
                    source["source_classes"], nonempty=True
                ),
                "domains": _domains(source["domains"]),
                "operations": ("search", "retrieve"),
                "duration": _positive_integer(
                    source["duration"], maximum=_MAX_TTL_SECONDS
                ),
            }
        )
    if kind == "build":
        source = _exact_keys(
            scope,
            frozenset(
                {
                    "component_classes",
                    "source_families",
                    "dependency_families",
                    "workspace_class",
                    "isolation_policy",
                    "side_effects",
                    "resource_limits",
                }
            ),
        )
        workspace_class = _canonical_token(source["workspace_class"])
        if workspace_class != "candidate-only":
            raise AuthorizationError("invalid_scope")
        return MappingProxyType(
            {
                "component_classes": _canonical_sequence(
                    source["component_classes"],
                    nonempty=True,
                    allowed=_COMPONENT_CLASSES,
                ),
                "source_families": _canonical_sequence(
                    source["source_families"], nonempty=True
                ),
                "dependency_families": _canonical_sequence(
                    source["dependency_families"], nonempty=False
                ),
                "workspace_class": workspace_class,
                "isolation_policy": _policy_mapping(
                    source["isolation_policy"]
                ),
                "side_effects": _canonical_sequence(
                    source["side_effects"], nonempty=False
                ),
                "resource_limits": _resource_limits(
                    source["resource_limits"]
                ),
            }
        )
    if kind == "promotion":
        source = _exact_keys(
            scope,
            frozenset(
                {
                    "generation_id",
                    "report_digest",
                    "expected_active_id",
                    "expected_lifecycle_sequence",
                    "operation",
                }
            ),
        )
        try:
            generation_id = require_digest(source["generation_id"])
            report_digest = require_digest(source["report_digest"])
            expected_active_id = require_digest(source["expected_active_id"])
        except ValueError:
            raise AuthorizationError("invalid_scope") from None
        sequence = source["expected_lifecycle_sequence"]
        if (
            isinstance(sequence, bool)
            or not isinstance(sequence, int)
            or sequence < 0
            or sequence > 2**63 - 1
            or source["operation"] != "switch_active"
        ):
            raise AuthorizationError("invalid_scope")
        return MappingProxyType(
            {
                "generation_id": generation_id,
                "report_digest": report_digest,
                "expected_active_id": expected_active_id,
                "expected_lifecycle_sequence": sequence,
                "operation": "switch_active",
            }
        )
    raise AuthorizationError("invalid_grant_kind")


def _kind(value: object) -> GrantKind:
    if not isinstance(value, str) or value not in _KINDS:
        raise AuthorizationError("invalid_grant_kind")
    return value  # type: ignore[return-value]


def _digest(value: object, *, code: str) -> str:
    try:
        return require_digest(value)
    except ValueError:
        raise AuthorizationError(code) from None


def _lookup_uuid(value: object, *, code: str) -> str:
    if not isinstance(value, str):
        raise AuthorizationError(code)
    try:
        parsed = uuid.UUID(value)
    except (ValueError, AttributeError):
        raise AuthorizationError(code) from None
    if str(parsed) != value:
        raise AuthorizationError(code)
    return value


def _attempt_identity(value: object) -> str:
    if (
        not isinstance(value, str)
        or value != value.strip()
        or not 1 <= len(value) <= 256
        or any(not character.isprintable() for character in value)
    ):
        raise AuthorizationError("invalid_attempt_id")
    return value


def _scope_digest(scope: Mapping[str, object]) -> str:
    return content_digest(_plain(scope), domain=_SCOPE_DIGEST_DOMAIN)


def _event(
    *,
    attempt_id: str,
    event_type: str,
    actor: str,
    input_digests: tuple[str, ...],
    authorization_id: str,
    reason_code: str,
    created_at: str,
) -> LifecycleEvent:
    return LifecycleEvent(
        event_id=str(uuid.uuid4()),
        attempt_id=attempt_id,
        generation_id=None,
        event_type=event_type,
        prior_state=None,
        next_state=None,
        actor=actor,
        input_digests=input_digests,
        authorization_id=authorization_id,
        reason_code=reason_code,
        reason_summary=reason_code,
        created_at=created_at,
    )


def create_authorization_request(
    ledger: EvolutionLedger,
    *,
    attempt_id: str,
    kind: GrantKind,
    subject_digest: str,
    scope: Mapping[str, object],
    ttl_seconds: int,
) -> AuthorizationRequest:
    selected_kind = _kind(kind)
    selected_subject = _digest(subject_digest, code="invalid_subject_digest")
    selected_scope = _validate_scope(selected_kind, scope)
    ttl = _positive_integer(ttl_seconds, maximum=_MAX_TTL_SECONDS)
    if selected_kind == "promotion" and (
        selected_scope["generation_id"] != selected_subject
    ):
        raise AuthorizationError("invalid_scope")
    created_at = _now()
    request = AuthorizationRequest(
        request_id=str(uuid.uuid4()),
        attempt_id=_attempt_identity(attempt_id),
        kind=selected_kind,
        subject_digest=selected_subject,
        scope=selected_scope,
        ttl_seconds=ttl,
        expires_at=_expires_at(created_at, ttl),
        created_at=created_at,
    )
    request_digest = content_digest(
        request.canonical_payload(), domain=_REQUEST_DIGEST_DOMAIN
    )
    scope_json = canonical_json_bytes(_plain(selected_scope)).decode("utf-8")
    with ledger.transaction() as connection:
        connection.execute(
            """
            INSERT INTO authorization_requests(
                request_id, attempt_id, grant_kind, subject_digest, request_digest,
                scope_json, ttl_seconds, expires_at, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                request.request_id,
                request.attempt_id,
                request.kind,
                request.subject_digest,
                request_digest,
                scope_json,
                request.ttl_seconds,
                request.expires_at,
                request.created_at,
            ),
        )
        ledger._append(
            connection,
            _event(
                attempt_id=request.attempt_id,
                event_type="authorization_requested",
                actor="operator",
                input_digests=(request.subject_digest, request_digest),
                authorization_id=request.request_id,
                reason_code="authorization_requested",
                created_at=created_at,
            ),
        )
    return request


def _request_from_row(row: object) -> AuthorizationRequest:
    try:
        kind = _kind(row["grant_kind"])
        scope = _validate_scope(kind, json.loads(row["scope_json"]))
        return AuthorizationRequest(
            request_id=row["request_id"],
            attempt_id=row["attempt_id"],
            kind=kind,
            subject_digest=_digest(
                row["subject_digest"], code="invalid_subject_digest"
            ),
            scope=scope,
            ttl_seconds=row["ttl_seconds"],
            expires_at=row["expires_at"],
            created_at=row["created_at"],
        )
    except (KeyError, TypeError, json.JSONDecodeError, AuthorizationError):
        raise AuthorizationError("request_unavailable") from None


def issue_grant(
    ledger: EvolutionLedger,
    *,
    request_id: str,
    approved_by: str,
    confirmation_digest: str,
) -> AuthorizationGrant:
    request_key = _lookup_uuid(
        request_id, code="request_unavailable"
    )
    approver = _privacy_safe_symbolic(
        approved_by, code="invalid_approver"
    )
    confirmation = _digest(
        confirmation_digest, code="confirmation_mismatch"
    )
    with ledger.transaction() as connection:
        created_at = _now()
        row = connection.execute(
            """
            SELECT request.*
            FROM authorization_requests AS request
            LEFT JOIN authorization_decisions AS decision
                ON decision.request_id = request.request_id
            WHERE request.request_id = ?
              AND request.expires_at > ?
              AND decision.request_id IS NULL
            """,
            (request_key, created_at),
        ).fetchone()
        if row is None:
            raise AuthorizationError("request_unavailable")
        request = _request_from_row(row)
        expected = content_digest(
            request.canonical_payload(), domain=_REQUEST_DIGEST_DOMAIN
        )
        if confirmation != expected:
            raise AuthorizationError("confirmation_mismatch")
        decision = AuthorizationDecision(
            decision_id=str(uuid.uuid4()),
            request_id=request.request_id,
            decision="approved",
            decided_by=approver,
            confirmation_digest=confirmation,
            created_at=created_at,
        )
        grant = AuthorizationGrant(
            grant_id=str(uuid.uuid4()),
            request_id=request.request_id,
            attempt_id=request.attempt_id,
            kind=request.kind,
            subject_digest=request.subject_digest,
            scope=request.scope,
            expires_at=request.expires_at,
            approved_by=approver,
            confirmation_digest=confirmation,
            created_at=created_at,
        )
        connection.execute(
            """
            INSERT INTO authorization_decisions(
                decision_id, request_id, decision, decided_by,
                confirmation_digest, created_at
            ) VALUES (?, ?, 'approved', ?, ?, ?)
            """,
            (
                decision.decision_id,
                decision.request_id,
                decision.decided_by,
                decision.confirmation_digest,
                decision.created_at,
            ),
        )
        connection.execute(
            """
            INSERT INTO authorization_grants(
                grant_id, authorization_id, request_id, attempt_id, grant_kind,
                subject_digest, scope_json, expires_at, approved_by,
                confirmation_digest, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                grant.grant_id,
                grant.grant_id,
                grant.request_id,
                grant.attempt_id,
                grant.kind,
                grant.subject_digest,
                canonical_json_bytes(_plain(grant.scope)).decode("utf-8"),
                grant.expires_at,
                grant.approved_by,
                grant.confirmation_digest,
                grant.created_at,
            ),
        )
        ledger._append(
            connection,
            _event(
                attempt_id=grant.attempt_id,
                event_type="authorization_granted",
                actor=approver,
                input_digests=(
                    grant.subject_digest,
                    _scope_digest(grant.scope),
                    confirmation,
                ),
                authorization_id=grant.grant_id,
                reason_code="authorization_granted",
                created_at=created_at,
            ),
        )
    return grant


def deny_authorization_request(
    ledger: EvolutionLedger,
    *,
    request_id: str,
    decided_by: str,
) -> AuthorizationDecision:
    request_key = _lookup_uuid(
        request_id, code="request_unavailable"
    )
    decider = _privacy_safe_symbolic(decided_by, code="invalid_approver")
    with ledger.transaction() as connection:
        created_at = _now()
        row = connection.execute(
            """
            SELECT request.*
            FROM authorization_requests AS request
            LEFT JOIN authorization_decisions AS decision
                ON decision.request_id = request.request_id
            WHERE request.request_id = ?
              AND request.expires_at > ?
              AND decision.request_id IS NULL
            """,
            (request_key, created_at),
        ).fetchone()
        if row is None:
            raise AuthorizationError("request_unavailable")
        request = _request_from_row(row)
        decision = AuthorizationDecision(
            decision_id=str(uuid.uuid4()),
            request_id=request.request_id,
            decision="denied",
            decided_by=decider,
            confirmation_digest=None,
            created_at=created_at,
        )
        connection.execute(
            """
            INSERT INTO authorization_decisions(
                decision_id, request_id, decision, decided_by,
                confirmation_digest, created_at
            ) VALUES (?, ?, 'denied', ?, NULL, ?)
            """,
            (
                decision.decision_id,
                decision.request_id,
                decision.decided_by,
                decision.created_at,
            ),
        )
        ledger._append(
            connection,
            _event(
                attempt_id=request.attempt_id,
                event_type="authorization_denied",
                actor=decider,
                input_digests=(
                    request.subject_digest,
                    _scope_digest(request.scope),
                ),
                authorization_id=request.request_id,
                reason_code="authorization_denied",
                created_at=created_at,
            ),
        )
    return decision


def _grant_from_row(row: object) -> AuthorizationGrant:
    try:
        kind = _kind(row["grant_kind"])
        return AuthorizationGrant(
            grant_id=row["grant_id"],
            request_id=row["request_id"],
            attempt_id=row["attempt_id"],
            kind=kind,
            subject_digest=_digest(
                row["subject_digest"], code="invalid_subject_digest"
            ),
            scope=_validate_scope(kind, json.loads(row["scope_json"])),
            expires_at=row["expires_at"],
            approved_by=row["approved_by"],
            confirmation_digest=row["confirmation_digest"],
            created_at=row["created_at"],
            consumed_at=row["consumed_at"],
        )
    except (KeyError, TypeError, json.JSONDecodeError, AuthorizationError):
        raise AuthorizationError("grant_unavailable") from None


def _contains(
    kind: GrantKind,
    granted: Mapping[str, object],
    required: Mapping[str, object],
) -> bool:
    if kind == "promotion":
        return granted == required
    if kind == "research":
        return (
            set(required["source_classes"]) <= set(granted["source_classes"])
            and set(required["domains"]) <= set(granted["domains"])
            and required["operations"] == granted["operations"]
            and required["duration"] <= granted["duration"]
        )
    return (
        set(required["component_classes"])
        <= set(granted["component_classes"])
        and set(required["source_families"])
        <= set(granted["source_families"])
        and set(required["dependency_families"])
        <= set(granted["dependency_families"])
        and required["workspace_class"] == granted["workspace_class"]
        and required["isolation_policy"] == granted["isolation_policy"]
        and set(required["side_effects"]) <= set(granted["side_effects"])
        and set(required["resource_limits"])
        <= set(granted["resource_limits"])
        and all(
            required["resource_limits"][key]
            <= granted["resource_limits"][key]
            for key in required["resource_limits"]
        )
    )


def consume_grant(
    ledger: EvolutionLedger,
    *,
    grant_id: str,
    expected_kind: GrantKind,
    expected_subject_digest: str,
    required_scope: Mapping[str, object],
) -> AuthorizationGrant:
    grant_key = _lookup_uuid(grant_id, code="grant_unavailable")
    selected_kind = _kind(expected_kind)
    selected_subject = _digest(
        expected_subject_digest, code="invalid_subject_digest"
    )
    selected_scope = _validate_scope(selected_kind, required_scope)
    with ledger.transaction() as connection:
        consumed_at = _now()
        row = connection.execute(
            """
            SELECT grant.*, consumption.consumed_at
            FROM authorization_grants AS grant
            JOIN authorization_decisions AS decision
                ON decision.request_id = grant.request_id
               AND decision.decision = 'approved'
            LEFT JOIN authorization_consumptions AS consumption
                ON consumption.grant_id = grant.grant_id
            WHERE grant.grant_id = ?
              AND grant.grant_kind = ?
              AND grant.subject_digest = ?
              AND grant.expires_at > ?
              AND consumption.grant_id IS NULL
            """,
            (
                grant_key,
                selected_kind,
                selected_subject,
                consumed_at,
            ),
        ).fetchone()
        if row is None:
            raise AuthorizationError("grant_unavailable")
        grant = _grant_from_row(row)
        if not _contains(selected_kind, grant.scope, selected_scope):
            raise AuthorizationError("grant_unavailable")
        consumption_id = str(uuid.uuid4())
        inserted = connection.execute(
            """
            INSERT INTO authorization_consumptions(
                consumption_id, grant_id, consumed_at
            )
            SELECT ?, grant.grant_id, ?
            FROM authorization_grants AS grant
            JOIN authorization_decisions AS decision
                ON decision.request_id = grant.request_id
               AND decision.decision = 'approved'
            WHERE grant.grant_id = ?
              AND grant.grant_kind = ?
              AND grant.subject_digest = ?
              AND grant.expires_at > ?
              AND NOT EXISTS (
                  SELECT 1
                  FROM authorization_consumptions AS consumed
                  WHERE consumed.grant_id = grant.grant_id
              )
            """,
            (
                consumption_id,
                consumed_at,
                grant_key,
                selected_kind,
                selected_subject,
                consumed_at,
            ),
        ).rowcount
        if inserted != 1:
            raise AuthorizationError("grant_unavailable")
        ledger._append(
            connection,
            _event(
                attempt_id=grant.attempt_id,
                event_type="authorization_consumed",
                actor="host",
                input_digests=(
                    grant.subject_digest,
                    _scope_digest(selected_scope),
                ),
                authorization_id=grant.grant_id,
                reason_code="authorization_consumed",
                created_at=consumed_at,
            ),
        )
    return replace(grant, consumed_at=consumed_at)
