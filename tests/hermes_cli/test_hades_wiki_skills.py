from __future__ import annotations

from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[2]
SKILL_DIR = ROOT / "skills" / "autonomous-ai-agents" / "hades-wiki-verify"
SKILL = SKILL_DIR / "SKILL.md"
PUSH_SKILL = ROOT / "skills" / "autonomous-ai-agents" / "hades-wiki-push" / "SKILL.md"
OPENAI_METADATA = SKILL_DIR / "agents" / "openai.yaml"
DESCRIPTION = "Use when Hades wiki pages need evidence verification."


def _frontmatter(text: str) -> dict:
    assert text.startswith("---\n"), "SKILL.md must start with YAML frontmatter"
    _, raw, _ = text.split("---", 2)
    parsed = yaml.safe_load(raw)
    assert isinstance(parsed, dict)
    return parsed


def test_wiki_verify_skill_metadata_is_bounded_and_discoverable() -> None:
    text = SKILL.read_text(encoding="utf-8")
    metadata = _frontmatter(text)

    assert metadata == {"name": "hades-wiki-verify", "description": DESCRIPTION}
    assert DESCRIPTION.startswith("Use when")
    assert DESCRIPTION.endswith(".")
    assert len(DESCRIPTION) <= 60


def test_wiki_verify_skill_processes_every_current_revision_once() -> None:
    text = SKILL.read_text(encoding="utf-8")

    for command in (
        "hades backend status --json",
        "hades backend sync",
        "hades backend wiki list --status needs_verification --limit 20 --json",
        "hades backend wiki show PAGE_ID --json",
        "hades backend wiki verify PAGE_ID",
        "--expected-revision REVISION_ID",
        "--evidence-file EVIDENCE_FILE",
    ):
        assert command in text

    for invariant in (
        "exactly one page and its current revision at a time",
        "Read the whole `content_markdown`",
        "enumerate every checkable claim",
        "next_cursor",
        "verified/deferred/conflicted",
    ):
        assert invariant in text


def test_wiki_verify_skill_requires_fresh_bounded_evidence() -> None:
    text = SKILL.read_text(encoding="utf-8")

    for guard in (
        "local code first",
        "discovery only",
        "current artifact or file hash",
        "`artifact_ref`",
        "`file_ref`",
        "at most 80",
        "Never fabricate evidence",
        "leave the page `needs_verification`",
        "missing, stale, or conflicting",
        "exact reason",
    ):
        assert guard in text


def test_wiki_verify_skill_never_verifies_truncated_markdown() -> None:
    text = SKILL.read_text(encoding="utf-8")

    for guard in (
        "`content_truncated` is explicitly `false`",
        "missing or `true`",
        "unseen full Markdown",
        "do not call `hades backend wiki verify`",
    ):
        assert guard in text


def test_wiki_verify_skill_maps_each_claim_to_schema_specific_proof() -> None:
    text = SKILL.read_text(encoding="utf-8")

    for contract in (
        "per-material-claim evidence ledger",
        "claim -> evidence object(s) -> why the schema or file content proves the claim",
        "schema-specific proof rules",
        "`hades.git_tree.v1`",
        "inventory, path, and hash",
        "behavior, control flow, or runtime behavior",
        "generic current artifact hash is not blanket semantic proof",
        "zero code-verifiable material claims",
        "unmapped material claim",
    ):
        assert contract in text

    assert "Build final proof only from a current artifact or file hash" not in text


def test_wiki_verify_skill_serializes_bounded_agent_attestations_per_evidence_ref() -> None:
    text = SKILL.read_text(encoding="utf-8")

    for contract in (
        '"claims": [',
        '"claim":',
        '"proof":',
        "between 1 and 8 claims",
        "at most 80 claims total",
        "agent-authored attestation",
        "backend establishes only artifact or file integrity and freshness",
        "exactly `claim` and `proof`",
    ):
        assert contract in text


def test_wiki_verify_skill_requires_manual_verification_capability_grant() -> None:
    text = SKILL.read_text(encoding="utf-8")

    for contract in (
        "`verify_project_wiki`",
        "project administrator",
        "new project-scoped bootstrap token",
        "`hades backend setup`",
        "Never try to upgrade an existing token automatically",
    ):
        assert contract in text


def test_wiki_verify_skill_has_consistent_mapped_evidence_guidance() -> None:
    text = SKILL.read_text(encoding="utf-8")

    for contract in (
        "| Mapped evidence |",
        "Every material claim must be mapped to schema-appropriate proof",
        "graph query or search hit",
        "inspected current graph-artifact entry",
        "encoded structural facts",
    ):
        assert contract in text

    assert "| Safe evidence |" not in text
    assert (
        "graph edge, filename, or stale artifact is a locator, not evidence" not in text
    )


def test_wiki_verify_openai_metadata_invokes_the_skill() -> None:
    metadata = yaml.safe_load(OPENAI_METADATA.read_text(encoding="utf-8"))

    assert metadata == {
        "interface": {
            "display_name": "Verify Hades Wiki",
            "short_description": "Verify wiki claims against project evidence",
            "default_prompt": (
                "Use $hades-wiki-verify to verify pending Hades wiki pages step by step."
            ),
        }
    }
    assert 25 <= len(metadata["interface"]["short_description"]) <= 64


def test_wiki_push_leaves_all_generated_pages_for_separate_verification() -> None:
    text = PUSH_SKILL.read_text(encoding="utf-8")

    assert "All generated pages remain `needs_verification`" in text
    assert "Structural pages may be `verified_from_code`" not in text
