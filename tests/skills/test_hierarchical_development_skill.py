from pathlib import Path


def test_hierarchical_development_skill_has_durable_safety_contract():
    path = Path("skills/software-development/hierarchical-development/SKILL.md")
    text = path.read_text(encoding="utf-8")
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
