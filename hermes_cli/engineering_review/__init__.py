"""Python facade for Hermes' packaged engineering review engine."""

from .bridge import (
    EngineCancelledError,
    EngineEvidenceError,
    EngineExecutionError,
    EngineOutputLimitError,
    EngineProcessError,
    EngineTimeoutError,
    EngineeringReviewBridge,
    bundle_path,
)
from .protocol import (
    CheckStatus,
    EngineCommand,
    EngineDiagnostic,
    EngineProtocolError,
    EngineRequest,
    EngineResponse,
)
from .evidence import (
    REVIEW_PLAN_MARKER,
    REVIEW_RUN_MARKER,
    VERIFIED_FINDINGS_EVIDENCE_MARKER,
    VERIFIER_HANDOFF_INSTRUCTION,
    encode_verified_findings,
    parse_verified_findings,
)
from .authority import (
    ReviewAuthority,
    ReviewAuthorityClient,
    ReviewAuthorityUnavailable,
)

__all__ = [
    "CheckStatus",
    "EngineCancelledError",
    "EngineCommand",
    "EngineDiagnostic",
    "EngineEvidenceError",
    "EngineExecutionError",
    "EngineOutputLimitError",
    "EngineProcessError",
    "EngineProtocolError",
    "EngineRequest",
    "EngineResponse",
    "EngineTimeoutError",
    "EngineeringReviewBridge",
    "ReviewAuthority",
    "ReviewAuthorityClient",
    "ReviewAuthorityUnavailable",
    "REVIEW_PLAN_MARKER",
    "REVIEW_RUN_MARKER",
    "VERIFIED_FINDINGS_EVIDENCE_MARKER",
    "VERIFIER_HANDOFF_INSTRUCTION",
    "bundle_path",
    "encode_verified_findings",
    "parse_verified_findings",
]
