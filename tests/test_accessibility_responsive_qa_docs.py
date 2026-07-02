from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
LOCAL_QA_DOC = REPO_ROOT / "docs" / "hades" / "accessibility-responsive-qa.md"
WEBSITE_QA_DOC = REPO_ROOT / "website" / "docs" / "developer-guide" / "accessibility-responsive-qa.md"


def test_accessibility_responsive_qa_docs_cover_surfaces_and_flows():
    text = LOCAL_QA_DOC.read_text(encoding="utf-8").lower()

    required_topics = [
        "bootstrap installer",
        "desktop app",
        "web dashboard",
        "website docs",
        "install and bootstrap",
        "backend status",
        "settings and keys",
        "project linking",
        "skills and plugins",
        "cron",
        "messaging setup",
        "error recovery",
        "keyboard",
        "screen reader",
        "responsive viewports",
        "error copy",
        "manual evidence template",
    ]

    for topic in required_topics:
        assert topic in text


def test_accessibility_responsive_qa_docs_publish_automated_gates():
    local_text = LOCAL_QA_DOC.read_text(encoding="utf-8")
    website_text = WEBSITE_QA_DOC.read_text(encoding="utf-8")

    commands = [
        "npm run --prefix apps/bootstrap-installer typecheck",
        "npm run --prefix apps/desktop typecheck",
        "npm run --prefix web typecheck",
        "npm run --prefix website typecheck",
        "npm run --prefix website build",
    ]

    for command in commands:
        assert command in local_text
        assert command in website_text

    assert "360" in local_text
    assert "1920" in local_text
    assert "API keys" in local_text
    assert "backend tokens" in local_text
