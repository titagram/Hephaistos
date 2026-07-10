"""Compact, reusable evidence records for delegated work.

Evidence packets intentionally contain conclusions and structured verification
metadata only.  Conversation messages, model reasoning, and transcripts are
not evidence and are rejected at this boundary.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import subprocess
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from agent.redact import redact_sensitive_text


EVIDENCE_SCHEMA = "hermes.delegation.evidence.v1"
MAX_CONCLUSION_CHARS = 2_000
MAX_RESIDUAL_RISK_CHARS = 500
MAX_RESIDUAL_RISKS = 20
MAX_VERIFICATION_RECORDS = 32
MAX_VERIFICATION_RECORD_BYTES = 4_096

_PACKET_FIELDS = frozenset(
    {
        "schema",
        "contract_hash",
        "base_commit",
        "result_ref",
        "diff_hash",
        "covered_files",
        "observed_files",
        "unattributed_files",
        "verification",
        "conclusion",
        "dependency_hashes",
        "residual_risks",
    }
)
_TRAJECTORY_FIELDS = frozenset(
    {
        "message",
        "messages",
        "reasoning",
        "transcript",
        "full_transcript",
        "conversation",
        "conversation_history",
    }
)


@dataclass(frozen=True)
class GitState:
    """Content-addressed repository state captured at a point in time."""

    base_commit: str
    diff_hash: str
    file_hashes: tuple[tuple[str, str], ...]


def _git(args: Sequence[str], *, cwd: str) -> bytes:
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    ).stdout


def capture_git_state(workspace: str | None = None) -> GitState | None:
    """Capture HEAD plus tracked and untracked content without mutating Git.

    ``None`` means the runtime facts are unavailable (for example, the child is
    outside a Git worktree).  Callers must surface that absence rather than
    inventing a commit or diff hash.
    """
    cwd = os.path.abspath(os.path.expanduser(workspace or os.getcwd()))
    try:
        root = _git(("rev-parse", "--show-toplevel"), cwd=cwd).decode().strip()
        head = _git(("rev-parse", "HEAD"), cwd=root).decode().strip()

        files: dict[str, str] = {}
        changed = set(
            os.fsdecode(path)
            for path in _git(("diff", "--name-only", "-z", "HEAD"), cwd=root).split(b"\0")
            if path
        )
        changed.update(
            os.fsdecode(path)
            for path in _git(
                ("ls-files", "--others", "--exclude-standard", "-z"), cwd=root
            ).split(b"\0")
            if path
        )
        for relative in changed:
            absolute = os.path.join(root, relative)
            if os.path.islink(absolute):
                content = os.fsencode(os.readlink(absolute))
                files[relative] = "symlink:" + hashlib.sha256(content).hexdigest()
            elif os.path.isfile(absolute):
                digest = hashlib.sha256()
                with open(absolute, "rb") as handle:
                    for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                        digest.update(chunk)
                files[relative] = "worktree:" + digest.hexdigest()
            else:
                files[relative] = "deleted"

        file_hashes = tuple(sorted(files.items()))
        diff_hash = canonical_json_hash({"head": head, "files": file_hashes})
        return GitState(
            base_commit=head,
            diff_hash=diff_hash,
            file_hashes=file_hashes,
        )
    except (OSError, subprocess.SubprocessError, UnicodeError, ValueError):
        return None


def changed_files(before: GitState, after: GitState) -> tuple[str, ...]:
    """Return paths whose content identity changed between two snapshots."""
    before_files = dict(before.file_hashes)
    after_files = dict(after.file_hashes)
    return tuple(
        sorted(
            path
            for path in before_files.keys() | after_files.keys()
            if before_files.get(path) != after_files.get(path)
        )
    )


def _canonical_json(value: Any) -> str:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise ValueError("evidence must contain only canonical JSON values") from exc


def canonical_json_hash(value: Any) -> str:
    """Return a SHA-256 digest over canonical JSON."""
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _assert_no_trajectory(value: Any) -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            if isinstance(key, str) and key.strip().lower() in _TRAJECTORY_FIELDS:
                raise ValueError(f"trajectory field {key!r} is forbidden in evidence")
            _assert_no_trajectory(item)
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for item in value:
            _assert_no_trajectory(item)


def _assert_no_secrets(value: Any) -> None:
    if isinstance(value, str):
        if redact_sensitive_text(value, force=True) != value:
            raise ValueError("secret-bearing content is forbidden in evidence")
    elif isinstance(value, Mapping):
        for key, item in value.items():
            _assert_no_secrets(key)
            _assert_no_secrets(item)
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for item in value:
            _assert_no_secrets(item)


def _text_tuple(
    values: Sequence[str],
    *,
    field: str,
    sort: bool = False,
    max_items: int | None = None,
    max_chars: int | None = None,
) -> tuple[str, ...]:
    if isinstance(values, (str, bytes)) or not isinstance(values, Sequence):
        raise ValueError(f"{field} must be a sequence of strings")
    if max_items is not None and len(values) > max_items:
        raise ValueError(f"{field} exceeds its {max_items}-item bound")
    normalized: list[str] = []
    for value in values:
        if not isinstance(value, str):
            raise ValueError(f"{field} must contain only strings")
        text = value.strip()
        if not text:
            raise ValueError(f"{field} must not contain blank values")
        if max_chars is not None and len(text) > max_chars:
            text = text[:max_chars]
        normalized.append(text)
    if sort:
        return tuple(sorted(set(normalized)))
    return tuple(normalized)


def _verification_tuple(values: Sequence[Mapping[str, Any]]) -> tuple[dict[str, Any], ...]:
    if isinstance(values, (str, bytes)) or not isinstance(values, Sequence):
        raise ValueError("verification must be a sequence of structured records")
    if len(values) > MAX_VERIFICATION_RECORDS:
        raise ValueError(
            f"verification exceeds its {MAX_VERIFICATION_RECORDS}-record bound"
        )
    records: list[dict[str, Any]] = []
    for value in values:
        if not isinstance(value, Mapping):
            raise ValueError("verification must contain only structured records")
        record = copy.deepcopy(dict(value))
        _assert_no_trajectory(record)
        _assert_no_secrets(record)
        encoded = _canonical_json(record).encode("utf-8")
        if len(encoded) > MAX_VERIFICATION_RECORD_BYTES:
            raise ValueError(
                "verification record exceeds its documented byte bound"
            )
        records.append(record)
    return tuple(records)


@dataclass(frozen=True)
class EvidencePacket:
    schema: str
    contract_hash: str
    base_commit: str
    result_ref: str | None
    diff_hash: str
    covered_files: tuple[str, ...]
    observed_files: tuple[str, ...]
    unattributed_files: tuple[str, ...]
    verification: tuple[dict[str, Any], ...]
    conclusion: str
    dependency_hashes: tuple[str, ...]
    residual_risks: tuple[str, ...]

    def _payload(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "contract_hash": self.contract_hash,
            "base_commit": self.base_commit,
            "result_ref": self.result_ref,
            "diff_hash": self.diff_hash,
            "covered_files": list(self.covered_files),
            "observed_files": list(self.observed_files),
            "unattributed_files": list(self.unattributed_files),
            "verification": copy.deepcopy(list(self.verification)),
            "conclusion": self.conclusion,
            "dependency_hashes": list(self.dependency_hashes),
            "residual_risks": list(self.residual_risks),
        }

    def to_dict(self) -> dict[str, Any]:
        """Serialize a validated packet without any child trajectory data."""
        payload = self._payload()
        _validate_payload(payload)
        return payload


def build_evidence_packet(
    *,
    contract_hash: str,
    base_commit: str,
    diff_hash: str,
    covered_files: Sequence[str],
    verification: Sequence[Mapping[str, Any]],
    observed_files: Sequence[str] | None = None,
    unattributed_files: Sequence[str] = (),
    result_ref: str | None = None,
    conclusion: str = "",
    dependency_hashes: Sequence[str] = (),
    residual_risks: Sequence[str] = (),
) -> EvidencePacket:
    """Build a bounded evidence packet from explicit runtime facts.

    Conclusions are capped at :data:`MAX_CONCLUSION_CHARS`.  Structured
    verification records are capped by count and serialized byte size.
    Secret-bearing or trajectory-shaped content is rejected rather than
    redacted, so callers cannot mistake a modified record for original proof.
    """
    for field, value in (
        ("contract_hash", contract_hash),
        ("base_commit", base_commit),
        ("diff_hash", diff_hash),
    ):
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{field} must be a non-empty string")
    if result_ref is not None and (not isinstance(result_ref, str) or not result_ref.strip()):
        raise ValueError("result_ref must be null or a non-empty string")
    if not isinstance(conclusion, str):
        raise ValueError("conclusion must be a string")

    normalized_covered = _text_tuple(covered_files, field="covered_files", sort=True)
    normalized_observed = _text_tuple(
        observed_files if observed_files is not None else covered_files,
        field="observed_files", sort=True,
    )
    normalized_unattributed = _text_tuple(
        unattributed_files, field="unattributed_files", sort=True
    )
    if not set(normalized_covered).issubset(normalized_observed):
        raise ValueError("covered_files must be a subset of observed_files")
    if not set(normalized_unattributed).issubset(normalized_observed):
        raise ValueError("unattributed_files must be a subset of observed_files")
    packet = EvidencePacket(
        schema=EVIDENCE_SCHEMA,
        contract_hash=contract_hash.strip(),
        base_commit=base_commit.strip(),
        result_ref=result_ref.strip() if result_ref is not None else None,
        diff_hash=diff_hash.strip(),
        covered_files=normalized_covered,
        observed_files=normalized_observed,
        unattributed_files=normalized_unattributed,
        verification=_verification_tuple(verification),
        conclusion=conclusion.strip()[:MAX_CONCLUSION_CHARS],
        dependency_hashes=_text_tuple(
            dependency_hashes, field="dependency_hashes", sort=True
        ),
        residual_risks=_text_tuple(
            residual_risks,
            field="residual_risks",
            max_items=MAX_RESIDUAL_RISKS,
            max_chars=MAX_RESIDUAL_RISK_CHARS,
        ),
    )
    validate_evidence_packet(packet)
    return packet


def _validate_payload(payload: Mapping[str, Any]) -> None:
    fields = set(payload)
    if fields != _PACKET_FIELDS:
        unexpected = sorted(fields - _PACKET_FIELDS)
        missing = sorted(_PACKET_FIELDS - fields)
        detail = unexpected or missing
        raise ValueError(f"invalid evidence packet field set: {detail}")
    if payload.get("schema") != EVIDENCE_SCHEMA:
        raise ValueError(f"unsupported evidence schema: {payload.get('schema')!r}")
    _assert_no_trajectory(payload)
    _assert_no_secrets(payload)

    for field in ("contract_hash", "base_commit", "diff_hash"):
        value = payload.get(field)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{field} must be a non-empty string")
    result_ref = payload.get("result_ref")
    if result_ref is not None and (
        not isinstance(result_ref, str) or not result_ref.strip()
    ):
        raise ValueError("result_ref must be null or a non-empty string")
    conclusion = payload.get("conclusion")
    if not isinstance(conclusion, str) or len(conclusion) > MAX_CONCLUSION_CHARS:
        raise ValueError("conclusion exceeds its documented bound")

    covered = _text_tuple(payload.get("covered_files"), field="covered_files", sort=True)
    observed = _text_tuple(payload.get("observed_files"), field="observed_files", sort=True)
    unattributed = _text_tuple(payload.get("unattributed_files"), field="unattributed_files", sort=True)
    dependencies = _text_tuple(
        payload.get("dependency_hashes"), field="dependency_hashes", sort=True
    )
    risks = _text_tuple(
        payload.get("residual_risks"),
        field="residual_risks",
        max_items=MAX_RESIDUAL_RISKS,
        max_chars=MAX_RESIDUAL_RISK_CHARS,
    )
    verification = _verification_tuple(payload.get("verification"))
    if list(covered) != list(payload.get("covered_files")):
        raise ValueError("covered_files must be sorted and unique")
    if list(observed) != list(payload.get("observed_files")):
        raise ValueError("observed_files must be sorted and unique")
    if list(unattributed) != list(payload.get("unattributed_files")):
        raise ValueError("unattributed_files must be sorted and unique")
    if not set(covered).issubset(observed) or not set(unattributed).issubset(observed):
        raise ValueError("file provenance must refer only to observed_files")
    if list(dependencies) != list(payload.get("dependency_hashes")):
        raise ValueError("dependency_hashes must be sorted and unique")
    if list(risks) != list(payload.get("residual_risks")):
        raise ValueError("residual_risks violate their documented bounds")
    if list(verification) != list(payload.get("verification")):
        raise ValueError("verification records are not canonical")


def validate_evidence_packet(packet: EvidencePacket | Mapping[str, Any]) -> bool:
    """Validate packet shape and safety, raising ``ValueError`` on failure."""
    if isinstance(packet, EvidencePacket):
        payload = packet._payload()
    elif isinstance(packet, Mapping):
        payload = copy.deepcopy(dict(packet))
    else:
        raise ValueError("evidence packet must be an EvidencePacket or mapping")
    _validate_payload(payload)
    return True


def evidence_is_stale(
    packet: EvidencePacket | Mapping[str, Any],
    *,
    contract_hash: str,
    base_commit: str,
    diff_hash: str,
    dependency_hashes: Sequence[str],
    result_ref: str | None = None,
    covered_files: Sequence[str] | None = None,
    verification: Sequence[Mapping[str, Any]] | None = None,
) -> bool:
    """Return whether current contract, Git, or dependency facts differ."""
    validate_evidence_packet(packet)
    payload = packet._payload() if isinstance(packet, EvidencePacket) else packet
    current_dependencies = _text_tuple(
        dependency_hashes, field="dependency_hashes", sort=True
    )
    if (
        payload["contract_hash"] != contract_hash
        or payload["base_commit"] != base_commit
        or payload["diff_hash"] != diff_hash
        or payload["result_ref"] != result_ref
        or tuple(payload["dependency_hashes"]) != current_dependencies
    ):
        return True
    if covered_files is not None:
        current_files = _text_tuple(covered_files, field="covered_files", sort=True)
        if tuple(payload["covered_files"]) != current_files:
            return True
    if verification is not None:
        current_verification = _verification_tuple(verification)
        if tuple(payload["verification"]) != current_verification:
            return True
    return False
