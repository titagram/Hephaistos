from __future__ import annotations

import pytest

from hermes_cli.hades_embeddings import (
    ALLOWED_CONTENT_KINDS,
    DisallowedEmbeddingContentError,
    EmbeddingRequest,
    generate_embedding,
)


def test_secret_is_redacted_before_reaching_provider():
    captured: dict[str, str] = {}

    def fake_provider(text: str) -> list[float]:
        captured["text"] = text
        return [0.1, 0.2]

    request = EmbeddingRequest(
        content_kind="memory_entry",
        content="use token=sk-live-abc123 to call the api",
        source_id="mem-1",
    )

    result = generate_embedding(request, provider=fake_provider)

    assert "sk-live-abc123" not in captured["text"]
    assert "sk-live-abc123" not in result.redacted_content
    assert result.status == "embedded"
    assert result.vector == [0.1, 0.2]


def test_raw_file_content_kind_is_rejected():
    assert "raw_file" not in ALLOWED_CONTENT_KINDS

    request = EmbeddingRequest(
        content_kind="raw_file",
        content="<?php echo 'hello'; ?>",
        source_id="file-1",
    )

    with pytest.raises(DisallowedEmbeddingContentError):
        generate_embedding(request, provider=lambda text: [0.0])


def test_missing_provider_skips_semantic_indexing():
    request = EmbeddingRequest(
        content_kind="source_slice",
        content="plain content, no secrets here",
        source_id="slice-1",
    )

    result = generate_embedding(request, provider=None)

    assert result.status == "skipped"
    assert result.reason == "semantic_index_skipped"
    assert result.vector is None
