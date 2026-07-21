from pathlib import Path

import yaml


SKILL = Path("skills/hades-logbook/SKILL.md")
DESCRIPTION = "Record factual outcomes in the Hades project logbook."


def _frontmatter(text: str) -> dict:
    assert text.startswith("---\n")
    _, raw, _ = text.split("---", 2)
    parsed = yaml.safe_load(raw)
    assert isinstance(parsed, dict)
    return parsed


def test_hades_logbook_skill_frontmatter_is_installable():
    description = _frontmatter(SKILL.read_text(encoding="utf-8"))["description"]

    assert description == DESCRIPTION
    assert len(description) <= 60


def test_hades_logbook_skill_describes_reference_and_rendering_contracts():
    text = " ".join(SKILL.read_text(encoding="utf-8").split())

    assert "do not prove that the commit or file exists" in text
    assert "resource-ID references" in text
    assert "summary is plain text displayed literally" in text
    assert "narrative accepts Markdown" in text
