import json
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
HADES_DOCS = REPO_ROOT / "docs" / "hades"
WEBSITE_DOCS = REPO_ROOT / "website" / "docs"


def test_hades_mvp_user_docs_exist_with_required_topics():
    required = {
        "README.md": ["Hades MVP", "Install", "Backend", "Troubleshooting"],
        "launch.md": ["install", "backend bootstrap", "privacy", "troubleshoot", "source of truth"],
        "installation.md": ["one-liner", "backend bootstrap", "Windows"],
        "backend.md": ["hades backend bootstrap", "hades project link", "shared memory"],
        "operations.md": ["job", "waiting_confirmation", "Persephone"],
        "doctor-troubleshooting.md": ["hades doctor", "cleanup", "degraded"],
        "support-runbook.md": ["Safe Support Bundle", "Recovery Matrix", "Escalate"],
        "developer-flow.md": ["subagent", "model routing", "local-only", "hades backend profiles"],
    }

    for filename, topics in required.items():
        text = (HADES_DOCS / filename).read_text(encoding="utf-8")
        lowered = text.lower()
        for topic in topics:
            assert topic.lower() in lowered, f"{filename} missing {topic!r}"


def test_hades_openapi_contract_covers_client_routes():
    spec = json.loads((HADES_DOCS / "openapi-hades-v1.json").read_text(encoding="utf-8"))
    paths = spec["paths"]

    required_paths = {
        "/api/hades/v1/health",
        "/api/hades/v1/token/verify",
        "/api/hades/v1/agents/register",
        "/api/hades/v1/capabilities",
        "/api/hades/v1/workspaces/bind",
        "/api/hades/v1/workspaces/{workspaceBinding}/unlink",
        "/api/hades/v1/memory/snapshot",
        "/api/hades/v1/memory/proposals",
        "/api/hades/v1/agent/jobs",
        "/api/hades/v1/agent/jobs/{job}/status",
        "/api/hades/v1/agent/jobs/{job}/result",
        "/api/hades/v1/artifacts",
        "/api/hades/v1/doctor/reports",
        "/api/hades/v1/persephone/inbox",
        "/api/hades/v1/persephone/events",
        "/api/hades/v1/persephone/messages",
    }

    assert required_paths.issubset(paths)
    assert paths["/api/hades/v1/agent/jobs/{job}/status"]["post"]["requestBody"]
    assert paths["/api/hades/v1/artifacts"]["post"]["requestBody"]
    assert paths["/api/hades/v1/doctor/reports"]["post"]["requestBody"]
    assert paths["/api/hades/v1/memory/proposals"]["post"]["responses"]["200"]
    assert spec["components"]["schemas"]["ErrorResponse"]["required"] == ["error", "message"]


def test_hades_coordination_skill_exists_with_local_only_guardrails():
    text = (REPO_ROOT / "skills" / "autonomous-ai-agents" / "hades-coordination" / "SKILL.md").read_text(encoding="utf-8")
    lowered = text.lower()

    assert "hades backend profiles --json" in lowered
    assert "local-only" in lowered
    assert "persephone" in lowered
    assert "do not write the resolved model" in lowered


def test_hades_support_runbook_covers_launch_failures_without_secret_collection():
    text = (HADES_DOCS / "support-runbook.md").read_text(encoding="utf-8")
    lowered = text.lower()

    required_topics = [
        "backend unreachable",
        "token expired or revoked",
        "failed bootstrap",
        "workspace already linked",
        "job waiting confirmation",
        "proposal refused or conflicted",
        "artifact too large or truncated",
        "docker permissions",
        "windows path issue",
        "desktop/backend version mismatch",
        "hades doctor --report-backend",
        "do not ask users to send",
    ]

    for topic in required_topics:
        assert topic in lowered
    assert ".env" in text
    assert "raw source files" in lowered


def test_hades_launch_docs_are_public_and_do_not_depend_on_coordination_logs():
    launch_text = (HADES_DOCS / "launch.md").read_text(encoding="utf-8")
    website_text = (WEBSITE_DOCS / "getting-started" / "hades-backend.md").read_text(encoding="utf-8")
    index_text = (WEBSITE_DOCS / "index.mdx").read_text(encoding="utf-8")
    install_text = (WEBSITE_DOCS / "getting-started" / "installation.md").read_text(encoding="utf-8")

    for topic in [
        "hades backend bootstrap",
        "hades backend status --json",
        "hades backend sync",
        "derived agent token",
        "profile secret",
        "model provider choices",
        "do not send",
    ]:
        assert topic in launch_text.lower()

    assert "docs/backend-agent-coordination.md" in launch_text
    assert "maintainer evidence" in launch_text.lower()
    assert "/getting-started/hades-backend" in index_text
    assert "Backend Setup" in install_text
    assert "hades backend bootstrap" in website_text
    assert "The backend does not choose your model" in website_text
