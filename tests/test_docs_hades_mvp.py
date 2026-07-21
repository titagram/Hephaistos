import json
from pathlib import Path

from jsonschema import Draft202012Validator


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
        "no-codebase-diagnosis.md": ["source-free", "bug-intake", "quality-report", "diagnosable_without_source"],
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
        "/api/hades/v1/memory/import-bundles",
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
    assert paths["/api/hades/v1/memory/import-bundles"]["post"]["requestBody"]
    assert spec["components"]["schemas"]["ErrorResponse"]["required"] == ["error"]
    assert spec["components"]["schemas"]["ErrorResponse"]["properties"]["error"]["required"] == ["code", "message"]
    assert spec["paths"]["/api/hades/v1/token/verify"]["post"]["responses"]["401"]["$ref"] == "#/components/responses/Unauthorized"


def test_hades_logbook_docs_and_openapi_make_recovery_contract_explicit():
    backend = (HADES_DOCS / "backend.md").read_text(encoding="utf-8").lower()
    operations = (HADES_DOCS / "operations.md").read_text(encoding="utf-8").lower()
    documented = f"{backend}\n{operations}"

    for topic in [
        "hades backend logbook list",
        "hades backend logbook show <entry-id>",
        "hades backend logbook write",
        "write_project_logbook",
        "no legacy grant",
        "re-registration",
        "dead-letter",
        "degraded sync",
        "re-register",
    ]:
        assert topic in documented, f"missing logbook operational contract: {topic!r}"

    spec = json.loads((HADES_DOCS / "openapi-hades-v1.json").read_text(encoding="utf-8"))
    paths = spec["paths"]
    entries = paths["/api/hades/v1/logbook/entries"]
    entry = paths["/api/hades/v1/logbook/entries/{entry}"]

    assert set(entries) >= {"get", "post"}
    assert entry["get"]["responses"]["200"]["content"]["application/json"]["schema"]["$ref"] == "#/components/schemas/ProjectLogbookEntryResponse"
    assert entries["get"]["responses"]["200"]["content"]["application/json"]["schema"]["$ref"] == "#/components/schemas/ProjectLogbookEntryPage"
    assert entries["post"]["requestBody"]["content"]["application/json"]["schema"]["$ref"] == "#/components/schemas/ProjectLogbookEntryCreateRequest"
    assert entries["post"]["responses"]["201"]["content"]["application/json"]["schema"]["$ref"] == "#/components/schemas/ProjectLogbookEntryResponse"
    assert entries["post"]["responses"]["503"]["content"]["application/json"]["schema"]["$ref"] == "#/components/schemas/LogbookRecordingFailedResponse"

    schemas = spec["components"]["schemas"]
    create_request = schemas["ProjectLogbookEntryCreateRequest"]
    assert create_request["additionalProperties"] is False
    assert create_request["required"] == [
        "project_id", "workspace_binding_id", "event_type", "severity", "summary", "idempotency_key", "references"
    ]
    client_body = {
        "project_id": "proj_1",
        "workspace_binding_id": "wb_1",
        "event_type": "change",
        "severity": "info",
        "summary": "Done",
        "idempotency_key": "0123456789abcdef",
        "references": [{"kind": "commit", "id": "abc123"}],
    }
    assert set(create_request["required"]) <= client_body.keys()
    assert set(client_body) <= create_request["properties"].keys()
    Draft202012Validator({
        "$ref": "#/components/schemas/ProjectLogbookEntryCreateRequest",
        "components": {"schemas": schemas},
    }).validate(client_body)
    assert create_request["properties"]["project_id"]["minLength"] == 1
    assert create_request["properties"]["references"]["maxItems"] == 20
    assert create_request["properties"]["idempotency_key"] == {
        "type": "string", "minLength": 16, "maxLength": 128, "pattern": "^[ -~]+$"
    }
    assert schemas["ProjectLogbookReference"]["properties"]["kind"]["enum"] == [
        "wiki_page", "wiki_revision", "graph_import", "verification_work", "kanban_task",
        "run", "repository", "commit", "file",
    ]
    assert entries["get"]["parameters"][-1]["schema"]["maximum"] == 50
    assert schemas["LogbookRecordingFailedResponse"]["properties"]["error"]["properties"]["code"]["const"] == "logbook_recording_failed"


def test_hades_openapi_refs_and_launch_examples_are_resolved():
    spec = json.loads((HADES_DOCS / "openapi-hades-v1.json").read_text(encoding="utf-8"))
    refs: set[str] = set()

    def collect_refs(value):
        if isinstance(value, dict):
            if "$ref" in value:
                refs.add(value["$ref"])
            for child in value.values():
                collect_refs(child)
        elif isinstance(value, list):
            for child in value:
                collect_refs(child)

    collect_refs(spec)
    for ref in refs:
        current = spec
        for part in ref.removeprefix("#/").split("/"):
            assert isinstance(current, dict), ref
            assert part in current, ref
            current = current[part]

    examples = spec["components"]["examples"]
    required_examples = {
        "UnauthorizedError",
        "ForbiddenError",
        "ValidationError",
        "MemoryProposalConflict",
        "MemoryProposalRefused",
        "MemoryImportBundle",
        "ExpiredJobStatus",
        "TruncatedArtifactUpload",
        "InboxPolling",
    }
    assert required_examples <= set(examples)
    assert examples["TruncatedArtifactUpload"]["value"]["truncated"] is True
    assert examples["InboxPolling"]["value"]["events"][0]["event_type"] == "proposal.reviewed"


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
        "no-codebase-diagnosis.md",
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
