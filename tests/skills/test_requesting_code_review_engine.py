from __future__ import annotations

from pathlib import Path


SKILL = (
    Path(__file__).parents[2]
    / "skills"
    / "software-development"
    / "requesting-code-review"
    / "SKILL.md"
)


def _content() -> str:
    return SKILL.read_text(encoding="utf-8")


def test_skill_starts_from_live_parent_authority_before_capture() -> None:
    content = _content()
    start = "hermes-review-engine start --session-id ${HERMES_SESSION_ID}"
    capture = "hermes-review-engine capture-target"
    assert start in content
    assert capture in content
    assert content.index(start) < content.index(capture)
    assert "never create or load a review run" in content.lower()
    assert "fail closed" in content.lower()


def test_skill_uses_exact_prompts_and_configured_logical_routes() -> None:
    content = _content()
    assert "hermes-review-engine build-prompts" in content
    assert 'delegate_task(role="reviewer"' in content
    assert "verbatim" in content.lower()
    assert "configured logical route" in content.lower()
    assert "caller-selected provider" in content.lower()
    assert "caller-selected model" in content.lower()


def test_skill_covers_deterministic_review_lifecycle() -> None:
    content = _content()
    for operation in (
        "build-test",
        "test-efficacy",
        "check-coverage",
        "resolve-anchors",
        "compose-review",
    ):
        assert f"hermes-review-engine {operation}" in content
    assert "missing" in content
    assert "idle" in content
    assert "unopened" in content
    assert "fresh verifier" in content.lower()


def test_skill_defines_effort_rosters_and_reverse_audit_bounds() -> None:
    content = _content().lower()
    assert "low" in content and "medium" in content and "high" in content
    assert "two consecutive dry rounds" in content
    assert "five total rounds" in content


def test_skill_never_mutates_or_publishes_automatically() -> None:
    content = _content().lower()
    assert "do not automatically fix" in content
    assert "do not commit" in content
    assert "do not post" in content
    assert "do not push" in content
    assert "do not merge" in content
    assert "separately requests publication" in content
    assert "github-code-review" in content
