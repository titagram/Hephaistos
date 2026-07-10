from pathlib import Path

import yaml


SKILL = Path("skills/software-development/hierarchical-development/SKILL.md")
ORG_RUN_OPERATIONS = Path("docs/hades/org-run-operations.md")
DESCRIPTION = "Use when coordinating delegated or durable Hades OrgRuns."


def frontmatter(text: str) -> dict:
    assert text.startswith("---\n"), "SKILL.md must start with YAML frontmatter"
    _, raw, _ = text.split("---", 2)
    parsed = yaml.safe_load(raw)
    assert isinstance(parsed, dict)
    return parsed


def test_hierarchical_development_skill_has_durable_safety_contract():
    text = SKILL.read_text(encoding="utf-8")
    for required in [
        "ephemeral delegation",
        "durable Hades OrgRun",
        "write scopes",
        "independent reviewer",
        "integration",
        "evidence",
        "Do not accept or invent provider/model",
        "do not upload raw plans",
    ]:
        assert required in text


def test_skill_frontmatter_and_onboarding_contract():
    text = SKILL.read_text(encoding="utf-8")
    description = frontmatter(text)["description"]
    assert description == DESCRIPTION
    assert len(description) <= 60
    assert "Routing is missing or incomplete" in text
    assert "Run `hades delegation setup`" in text
    assert "All three role routes already resolve" in text
    assert "Preserve the configuration" in text
    assert "The user explicitly asks to change role models or limits" in text
    assert "Run `hades delegation configure`" in text
    assert "task contract" in text.lower()
    assert "parent" in text.lower() and "evidence" in text.lower()


def test_skill_enforces_recursive_direct_parent_authority():
    text = SKILL.read_text(encoding="utf-8")
    assert (
        "Only a leaf's direct parent may command that leaf or modify its task contract."
        in text
    )
    assert "root/main agent may inspect or query a leaf for information" in text
    assert "must not command it or change its task contract" in text
    assert "Apply this rule recursively at every level" in text


def test_skill_and_org_run_docs_make_parent_review_the_default():
    skill = SKILL.read_text(encoding="utf-8")
    operations = ORG_RUN_OPERATIONS.read_text(encoding="utf-8")
    escalation = (
        "only when independent review is explicitly requested or the result is "
        "high-risk, disputed, or escalated"
    )
    assert "The direct parent performs normal review" in skill
    assert escalation in skill
    assert (
        "Only a leaf's direct parent may command that leaf or modify its task contract."
        in operations
    )
    assert "root/main agent may inspect or query a leaf for information" in operations
    assert "must not command it or change its task contract" in operations
    assert "Apply this rule recursively at every level" in operations
    assert "The direct parent normally checks each direct child" in operations
    assert escalation in operations
    assert (
        "6. Require evidence, parent review, and integration tests before publication."
        in operations
    )
