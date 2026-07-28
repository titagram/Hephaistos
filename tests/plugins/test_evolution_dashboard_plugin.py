"""HTTP contract tests for the bundled Evolution dashboard adapter."""

from __future__ import annotations

import importlib.util
import json
import sys
from copy import deepcopy
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


def _load_production_plugin():
    """Load the adapter as the server does, without its test-only root hook."""
    plugin_file = PLUGIN_DIR / "plugin_api.py"
    name = f"hermes_dashboard_plugin_evolution_production_test_{id(plugin_file)}"
    spec = importlib.util.spec_from_file_location(name, plugin_file)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
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


@pytest.fixture
def production_plugin(tmp_path, monkeypatch):
    """A real server root comes from Hermes storage, never a request field."""
    home = tmp_path / ".hermes"
    home.mkdir(mode=0o700)
    monkeypatch.setenv("HERMES_HOME", str(home))
    module = _load_production_plugin()
    assert module._local_root is None
    yield module
    module.shutdown_for_tests()
    sys.modules.pop(module.__name__, None)


@pytest.fixture
def production_client(production_plugin):
    app = FastAPI()
    app.include_router(production_plugin.router, prefix="/api/plugins/evolution")
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


@pytest.mark.parametrize(
    ("path", "payload"),
    [
        ("organism-rebuild", {"force": False, "collectors": []}),
        ("observer-scan", {}),
    ],
)
def test_server_owned_root_reuses_one_job_manager_for_submission_and_polling(
    production_client, production_plugin, path, payload
):
    """Dropping the production manager after submission must fail this poll contract."""
    initialized = production_client.post("/api/plugins/evolution/initialize")
    assert initialized.status_code == 200, initialized.text
    context = _mutation_context(production_client)

    submitted = production_client.post(
        f"/api/plugins/evolution/jobs/{path}", json={**context, **payload}
    )

    assert submitted.status_code == 202, submitted.text
    job_id = submitted.json()["job_id"]
    assert production_plugin._manager is not None
    assert production_plugin._manager_root == production_plugin._root()
    polled = production_client.get(f"/api/plugins/evolution/jobs/{job_id}")
    assert polled.status_code == 200, polled.text
    assert polled.json()["job_id"] == job_id


def test_job_conflicts_return_a_stable_conflict_response(client, plugin, monkeypatch):
    """Rewrapping a live-job conflict as an unavailable server failure must fail."""
    from hermes_cli.evolution.dashboard_jobs import EvolutionJobConflict

    assert client.post("/api/plugins/evolution/initialize").status_code == 200
    context = _mutation_context(client)

    def conflict(*args, **kwargs):
        raise EvolutionJobConflict("job_already_active")

    monkeypatch.setattr(plugin.EvolutionJobManager, "submit_rebuild", conflict)
    response = client.post(
        "/api/plugins/evolution/jobs/organism-rebuild",
        json={**context, "force": False, "collectors": []},
    )

    assert response.status_code == 409
    assert response.json() == {"code": "job_already_active"}


def _valid_telos_document(organism_id: str) -> dict[str, object]:
    item = {"id": "bounded", "statement": "Operate safely.", "tags": ["safe"], "priority": 3}
    return {
        "schema_version": 1,
        "organism_id": organism_id,
        "parent_digest": None,
        "purpose": "Assist safely and reliably.",
        "desired_traits": [item],
        "capability_directions": [item],
        "priorities": [item],
        "tradeoffs": [],
        "prohibitions": [item],
        "proactivity_policy": item,
        "success_indicators": [item],
    }


@pytest.mark.parametrize("untrusted_key", ["actor_ref", "ActorRef", "actor-ref", "session_ref", "command_name", "source_path", "url"])
def test_telos_draft_rejects_untrusted_document_keys_at_every_depth(client, untrusted_key):
    """Permitting host-control metadata in a Telos document must fail at the HTTP boundary."""
    assert client.post("/api/plugins/evolution/initialize").status_code == 200
    context = _mutation_context(client)
    root_document = _valid_telos_document(context["organism_id"])
    nested_document = deepcopy(root_document)
    nested_document["desired_traits"][0][untrusted_key] = "host-metadata"

    for document in ({**root_document, untrusted_key: "host-metadata"}, nested_document):
        response = client.post(
            "/api/plugins/evolution/telos/drafts",
            json={**context, "document": document},
        )
        assert response.status_code == 422
        assert response.json() == {"code": "invalid_request"}


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
    assert draft.status_code == 422
    assert draft.json() == {"code": "invalid_request"}

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
