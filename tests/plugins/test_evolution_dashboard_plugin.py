"""HTTP contract tests for the bundled Evolution dashboard adapter."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


REPO_ROOT = Path(__file__).resolve().parents[2]
PLUGIN_DIR = REPO_ROOT / "plugins" / "evolution" / "dashboard"


def _load_plugin(root: Path):
    plugin_file = PLUGIN_DIR / "plugin_api.py"
    assert plugin_file.exists(), f"plugin file missing: {plugin_file}"
    name = f"hermes_dashboard_plugin_evolution_test_{id(root)}"
    spec = importlib.util.spec_from_file_location(name, plugin_file)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    module.set_local_root_for_tests(root)
    return module


@pytest.fixture
def plugin(tmp_path, monkeypatch):
    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    module = _load_plugin(tmp_path / "organism")
    yield module
    module.shutdown_for_tests()
    sys.modules.pop(module.__name__, None)


@pytest.fixture
def client(plugin):
    app = FastAPI()
    app.include_router(plugin.router, prefix="/api/plugins/evolution")
    with TestClient(app) as test_client:
        yield test_client


def _mutation_context(client: TestClient) -> dict[str, str]:
    response = client.get("/api/plugins/evolution/mutation-context")
    assert response.status_code == 200, response.text
    return response.json()


def test_manifest_is_discoverable_as_evolution_tab():
    manifest = json.loads((PLUGIN_DIR / "manifest.json").read_text())

    assert manifest == {
        "name": "evolution",
        "label": "Evolution",
        "description": "Local Gnothi Seauton and Autopoiesis control center",
        "icon": "Activity",
        "version": "1.0.0",
        "tab": {"path": "/evolution", "position": "after:plugins"},
        "entry": "dist/index.js",
        "css": "dist/style.css",
        "api": "plugin_api.py",
    }

    from hermes_cli.web_server import _discover_dashboard_plugins

    discovered = next(item for item in _discover_dashboard_plugins() if item["name"] == "evolution")
    assert discovered["tab"] == {"path": "/evolution", "position": "after:plugins"}
    assert discovered["has_api"] is True


def test_snapshot_is_non_mutating_when_local_root_is_absent(client, plugin):
    assert not plugin.local_root_for_tests().exists()

    response = client.get("/api/plugins/evolution/snapshot")

    assert response.status_code == 200
    assert response.json()["state"] == "missing"
    assert not plugin.local_root_for_tests().exists()


def test_mutation_context_hides_identity_until_initialized(client):
    missing = client.get("/api/plugins/evolution/mutation-context")
    assert missing.status_code == 400
    assert missing.json() == {"code": "organism_missing"}

    initialized = client.post("/api/plugins/evolution/initialize")
    assert initialized.status_code == 200, initialized.text
    context = _mutation_context(client)

    assert len(context["organism_id"]) == 36
    assert len(context["expected_snapshot_digest"]) == 64


def test_read_routes_enforce_bounded_query_contracts(client):
    assert client.get("/api/plugins/evolution/graph", params={"depth": 5}).status_code == 422
    assert client.get("/api/plugins/evolution/graph", params={"limit": 201}).status_code == 422
    assert client.get("/api/plugins/evolution/revisions", params={"limit": 51}).status_code == 422
    assert client.get("/api/plugins/evolution/telos", params={"history_limit": 51}).status_code == 422
    assert client.get("/api/plugins/evolution/pipeline", params={"limit": 51}).status_code == 422
    assert client.get("/api/plugins/evolution/audit", params={"limit": 101}).status_code == 422

    for route in ("graph", "revisions", "telos", "pipeline", "audit"):
        response = client.get(f"/api/plugins/evolution/{route}")
        assert response.status_code == 200, response.text


def test_jobs_mutations_and_polling_use_digest_bound_context(client):
    assert client.post("/api/plugins/evolution/initialize").status_code == 200
    context = _mutation_context(client)
    body = {**context, "force": False, "collectors": []}

    rebuild = client.post("/api/plugins/evolution/jobs/organism-rebuild", json=body)
    assert rebuild.status_code == 202, rebuild.text
    job_id = rebuild.json()["job_id"]
    assert client.get(f"/api/plugins/evolution/jobs/{job_id}").status_code == 200

    stale = client.post(
        "/api/plugins/evolution/jobs/observer-scan",
        json={**context, "expected_snapshot_digest": "0" * 64},
    )
    assert stale.status_code == 409
    assert stale.json() == {"code": "snapshot_changed"}


def test_mutation_models_forbid_unbounded_or_untrusted_fields(client):
    assert client.post("/api/plugins/evolution/initialize").status_code == 200
    context = _mutation_context(client)

    unsafe = client.post(
        "/api/plugins/evolution/jobs/organism-rebuild",
        json={**context, "force": False, "collectors": [], "path": "/tmp/pwn"},
    )
    assert unsafe.status_code == 422
    assert unsafe.json() == {"code": "invalid_request"}

    invalid_collector = client.post(
        "/api/plugins/evolution/jobs/organism-rebuild",
        json={**context, "force": False, "collectors": ["../../command"]},
    )
    assert invalid_collector.status_code == 422
    assert invalid_collector.json() == {"code": "invalid_request"}


def test_router_exposes_all_governed_mutation_endpoints(client):
    assert client.post("/api/plugins/evolution/initialize").status_code == 200
    context = _mutation_context(client)

    observer = client.post("/api/plugins/evolution/observer", json={**context, "enabled": False})
    assert observer.status_code == 200, observer.text

    draft = client.post(
        "/api/plugins/evolution/telos/drafts",
        json={**context, "document": {}},
    )
    assert draft.status_code == 400
    assert draft.json() == {"code": "invalid_telos_draft"}

    prepare = client.post(
        "/api/plugins/evolution/telos/transitions/prepare",
        json={
            **context,
            "current_digest": "a" * 64,
            "target_digest": "b" * 64,
            "action": "activate",
        },
    )
    assert prepare.status_code in {400, 409}
    assert prepare.json()["code"] in {"telos_unavailable", "telos_current_changed", "snapshot_changed"}

    confirm = client.post(
        "/api/plugins/evolution/telos/transitions/confirm",
        json={
            **context,
            "confirmation_id": "00000000-0000-4000-8000-000000000000",
            "current_digest": "a" * 64,
            "target_digest": "b" * 64,
            "action": "activate",
            "phrase": "ACTIVATE",
        },
    )
    assert confirm.status_code == 409
    assert confirm.json() == {"code": "confirmation_not_found"}

    blueprint = client.post(
        "/api/plugins/evolution/suggestions/not-a-uuid/blueprint",
        json={**context, "expected_suggestion_digest": "a" * 64},
    )
    assert blueprint.status_code == 422
    assert blueprint.json() == {"code": "invalid_request"}


def test_errors_are_sanitized_and_route_module_has_no_remote_or_shell_boundary(client):
    missing = client.get("/api/plugins/evolution/jobs/00000000-0000-4000-8000-000000000000")
    assert missing.status_code == 404
    assert missing.json() == {"code": "job_not_found"}

    source = (PLUGIN_DIR / "plugin_api.py").read_text()
    for forbidden in ("hades_backend", "BackendClient", "subprocess", "shell=True", "os.system"):
        assert forbidden not in source
