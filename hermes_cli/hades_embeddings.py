"""Privacy-first embedding generation for Hades semantic retrieval.

Only content already permitted to leave the machine for shared-memory or
backend search purposes (memory entries, bug evidence, redacted source
slices, graph artifact summaries) may be embedded, and only after passing
through the same secret redaction used for source slices
(`hermes_cli.hades_backend_client.redact_secret`). Raw file content must
never enter this path. If no embedding provider is configured, semantic
indexing is skipped rather than attempted or faked.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Callable

from hermes_cli.hades_backend_client import redact_secret

logger = logging.getLogger("hermes_cli.hades_backend")

ALLOWED_CONTENT_KINDS = frozenset(
    {
        "memory_entry",
        "bug_evidence",
        "source_slice",
        "graph_artifact_summary",
    }
)

EmbeddingProvider = Callable[[str], list[float]]


class DisallowedEmbeddingContentError(ValueError):
    """Raised when a content kind outside ALLOWED_CONTENT_KINDS is embedded.

    In particular, raw file content must never reach the embedding path;
    it has to be reduced to a redacted, bounded slice first.
    """


@dataclass(frozen=True)
class EmbeddingRequest:
    content_kind: str
    content: str
    source_id: str


@dataclass(frozen=True)
class EmbeddingResult:
    status: str
    reason: str | None
    source_id: str
    redacted_content: str | None = None
    vector: list[float] | None = None


def generate_embedding(
    request: EmbeddingRequest,
    provider: EmbeddingProvider | None,
) -> EmbeddingResult:
    if request.content_kind not in ALLOWED_CONTENT_KINDS:
        raise DisallowedEmbeddingContentError(
            f"content_kind={request.content_kind!r} is not permitted for "
            "embedding; only already-vetted content "
            f"({sorted(ALLOWED_CONTENT_KINDS)}) may be embedded"
        )

    redacted_content = redact_secret(request.content)

    if provider is None:
        logger.info(
            "hades_backend.embedding.skipped",
            extra={
                "hades_event": "embedding.skipped",
                "hades_reason": "semantic_index_skipped",
                "hades_content_kind": request.content_kind,
                "hades_source_id": request.source_id,
            },
        )
        return EmbeddingResult(
            status="skipped",
            reason="semantic_index_skipped",
            source_id=request.source_id,
        )

    vector = provider(redacted_content)
    return EmbeddingResult(
        status="embedded",
        reason=None,
        source_id=request.source_id,
        redacted_content=redacted_content,
        vector=vector,
    )
