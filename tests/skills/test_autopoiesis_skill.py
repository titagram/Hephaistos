from pathlib import Path


SKILL = Path("skills/autopoiesis/SKILL.md")


def test_autopoiesis_skill_declares_backend_independent_local_mode():
    text = SKILL.read_text(encoding="utf-8")
    local_mode = text.split("## Local Mode", 1)[1].split("## ", 1)[0]

    assert "does not require the Hades backend" in local_mode
    assert "Telos, Observer, suggestions, and their audit ledger remain local" in local_mode
    assert "hades memory setup holographic" in local_mode
    assert "does not replace or synchronize the Autopoiesis ledger" in local_mode
