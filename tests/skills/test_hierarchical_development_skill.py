from pathlib import Path

import yaml


SKILL = Path("skills/software-development/hierarchical-development/SKILL.md")


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
    assert len(description) <= 60
    assert "hades delegation setup" in text
    assert "hades delegation configure" in text
    assert "task contract" in text.lower()
    assert "parent" in text.lower() and "evidence" in text.lower()
