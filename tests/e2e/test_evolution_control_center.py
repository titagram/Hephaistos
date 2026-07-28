"""Real local lifecycle coverage for the Evolution dashboard plugin.

This is deliberately an end-to-end Python test rather than a service-unit
fixture.  It discovers the bundled dashboard plugin as the web server does,
mounts its router, and uses the server-owned global organism directory while
two independent profile homes are active in turn.
"""

from __future__ import annotations

import importlib.util
import json
import sys
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


@dataclass(frozen=True)
class _ControlCenterApp:
    client: TestClient
    module: Any
    default_root: Path
    organism_root: Path
    profile_alpha: Path
    profile_beta: Path


class _DeterministicCollector:
    """A real Gnothi collector with fixed, public fixture facts."""

    def __init__(self, name: str) -> None:
        self.name = name

    def probe_fingerprint(self, _context: Any) -> str:
        return f"sha256:fixture-{self.name}"

    def collect(self, context: Any) -> Any:
        from hermes_cli.gnothi.collectors.base import CollectorResult

        node_id = f"fixture:{self.name}"
        nodes = [
            {
                "id": node_id,
                "kind": "module",
                "label": f"Fixture {self.name}",
                "owner": {"class": "core", "id": "hermes"},
                "generation_scope": context.generation_scope,
                "state": {"available": True},
                "evidence_refs": [f"evidence:{self.name}"],
                "properties": {"collector": self.name},
                "verified_at": "2026-07-28T10:00:00Z",
            }
        ]
        edges: list[dict[str, object]] = []
        if self.name == "capabilities":
            nodes[0]["id"] = "capability:fixture-camera"
            nodes[0]["kind"] = "capability"
            nodes[0]["label"] = "Fixture camera capability"
            edges.append(
                {
                    "id": "edge:fixture-camera-source",
                    "kind": "requires",
                    "from": "capability:fixture-camera",
                    "to": "fixture:source",
                    "evidence_refs": ["evidence:capabilities"],
                    "properties": {"collector": self.name},
                }
            )
        return CollectorResult(
            name=self.name,
            status="current",
            nodes=nodes,
            edges=edges,
            evidence=[{"id": f"evidence:{self.name}", "kind": "fixture"}],
            fingerprint=f"sha256:fixture-{self.name}",
            verified_at="2026-07-28T10:00:00Z",
        )


def _deterministic_collectors() -> list[_DeterministicCollector]:
    from hermes_cli.gnothi.builder import COLLECTOR_ORDER

    return [_DeterministicCollector(name) for name in COLLECTOR_ORDER]


@pytest.fixture
def control_center(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[_ControlCenterApp]:
    """Mount the discovered router against one global root and two profiles.

    The Hades backend constructor sentinel makes any accidental remote path a
    hard test failure instead of allowing a configured local environment to
    hide the regression.
    """

    default_root = tmp_path / ".hermes"
    default_root.mkdir(mode=0o700)
    profile_alpha = default_root / "profiles" / "alpha"
    profile_beta = default_root / "profiles" / "beta"
    profile_alpha.mkdir(parents=True, mode=0o700)
    profile_beta.mkdir(parents=True, mode=0o700)
    monkeypatch.setenv("HERMES_HOME", str(profile_alpha))
    monkeypatch.delenv("HADES_HOME", raising=False)

    from hermes_cli.hades_backend_client import HadesBackendClient

    def fail_if_remote_client_constructed(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("Evolution control-center E2E must not construct HadesBackendClient")

    monkeypatch.setattr(HadesBackendClient, "__init__", fail_if_remote_client_constructed)

    # Use the production discovery path, then import exactly the router it
    # declares.  The isolated app avoids the unrelated dashboard routes while
    # preserving the same plugin contract and import mechanism.
    from hermes_cli.web_server import _discover_dashboard_plugins

    discovered = next(
        plugin
        for plugin in _discover_dashboard_plugins()
        if plugin["name"] == "evolution"
    )
    api_path = Path(discovered["_dir"]) / str(discovered["_api_file"])
    module_name = f"evolution_control_center_e2e_{uuid.uuid4().hex}"
    spec = importlib.util.spec_from_file_location(module_name, api_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)

    # The job manager calls the production builder.  Only its collector input
    # is replaced, so the persisted artifact, graph, and revision store are
    # the real Gnothi implementation with deterministic facts.
    import hermes_cli.evolution.dashboard_jobs as dashboard_jobs
    from hermes_cli.gnothi.builder import build_organism_revision

    def deterministic_rebuild(*args: object, **kwargs: object) -> dict[str, Any]:
        kwargs["collectors"] = _deterministic_collectors()
        kwargs["now"] = "2026-07-28T10:00:00Z"
        return build_organism_revision(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(dashboard_jobs, "build_organism_revision", deterministic_rebuild)

    app = FastAPI()
    app.include_router(module.router, prefix="/api/plugins/evolution")
    organism_root = default_root / "organism"
    try:
        with TestClient(app) as client:
            assert module._root() == organism_root
            yield _ControlCenterApp(
                client=client,
                module=module,
                default_root=default_root,
                organism_root=organism_root,
                profile_alpha=profile_alpha,
                profile_beta=profile_beta,
            )
    finally:
        module.shutdown_for_tests()
        sys.modules.pop(module_name, None)


def _mutation_context(client: TestClient) -> dict[str, str]:
    response = client.get("/api/plugins/evolution/mutation-context")
    assert response.status_code == 200, response.text
    return response.json()


def _poll_terminal_job(client: TestClient, job_id: str) -> dict[str, Any]:
    """Poll the public job endpoint until its durable record is terminal."""

    deadline = time.monotonic() + 5
    last: dict[str, Any] | None = None
    while time.monotonic() < deadline:
        response = client.get(f"/api/plugins/evolution/jobs/{job_id}")
        assert response.status_code == 200, response.text
        last = response.json()
        if last["state"] in {"completed", "failed", "cancelled", "unknown"}:
            return last
        time.sleep(0.01)
    pytest.fail(f"evolution job did not reach a terminal state: {last}")


def _telos_document(
    organism_id: str,
    *,
    parent_digest: str | None,
    purpose: str,
) -> dict[str, object]:
    camera = {
        "id": "camera-direction",
        "statement": "Improve local camera capability.",
        "tags": ["camera", "local"],
        "priority": 4,
    }
    return {
        "schema_version": 1,
        "organism_id": organism_id,
        "parent_digest": parent_digest,
        "purpose": purpose,
        "desired_traits": [
            {"id": "safe-trait", "statement": "Operate safely.", "tags": ["safe"], "priority": 3}
        ],
        "capability_directions": [camera],
        "priorities": [
            {"id": "safe-priority", "statement": "Prioritize safety.", "tags": ["safe"], "priority": 3}
        ],
        "tradeoffs": [],
        "prohibitions": [
            {"id": "safe-prohibition", "statement": "Avoid unsafe work.", "tags": ["safe"], "priority": 3}
        ],
        "proactivity_policy": {
            "id": "bounded-proactivity",
            "statement": "Remain bounded.",
            "tags": ["bounded"],
            "priority": 3,
        },
        "success_indicators": [
            {"id": "safe-success", "statement": "Complete safely.", "tags": ["safe"], "priority": 3}
        ],
    }


def _activate_from_real_host(
    organism_root: Path, organism_id: str, digest: str
) -> None:
    """Bootstrap/change the active pointer through the real host authority path."""

    from hermes_cli.evolution.host_transition import (
        perform_telos_transition,
        prepare_telos_pending_request,
    )
    from hermes_cli.evolution.ledger import EvolutionLedger
    from hermes_cli.evolution.telos_approval import HostApprovalContext
    from hermes_cli.evolution.telos_store import TelosStore

    prepared = prepare_telos_pending_request(
        digest=digest,
        action="activate",
        surface="e2e-host",
        actor_ref="e2e-operator",
        session_ref="e2e-session",
        organism_root=organism_root,
    )
    assert prepared["status"] == "ok"
    request_id = prepared["request_id"]
    prompt = prepared["prompt_fields"]
    assert isinstance(request_id, str) and isinstance(prompt, dict)
    context = HostApprovalContext(
        surface="e2e-host",
        actor_ref="e2e-operator",
        session_ref="e2e-session",
        request_id=request_id,
        telos_digest=digest,
        action="activate",
        nonce=str(prompt["display_nonce"]),
        context_digest=str(prompt["expected_host_context_digest"]),
    )
    ledger = EvolutionLedger(organism_root / "evolution" / "evolution.db")
    try:
        result = perform_telos_transition(
            ledger, TelosStore(organism_root), context, "approved"
        )
    finally:
        ledger.connection.close()
    assert result.status == "approved"
    assert TelosStore(organism_root).get_active_digest() == digest


def _authorization_and_later_stage_counts(organism_root: Path) -> tuple[int, int, int, int]:
    from hermes_cli.evolution.ledger import EvolutionLedger

    ledger = EvolutionLedger(organism_root / "evolution" / "evolution.db")
    try:
        connection = ledger.connection
        return tuple(
            int(connection.execute(query).fetchone()[0])
            for query in (
                "SELECT COUNT(*) FROM authorization_requests",
                "SELECT COUNT(*) FROM authorization_grants",
                "SELECT COUNT(*) FROM authorization_consumptions",
                """
                SELECT COUNT(*) FROM lifecycle_events
                WHERE event_type LIKE '%build%' OR event_type LIKE '%promotion%'
                """,
            )
        )
    finally:
        ledger.connection.close()


def test_local_control_center_lifecycle_is_global_and_governed(
    control_center: _ControlCenterApp, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A local operator can govern one global organism without remote access."""

    client = control_center.client
    root = control_center.organism_root

    # An absent read must be a probe only; initialization is the explicit
    # mutation that creates the global organism.
    assert not root.exists()
    missing = client.get("/api/plugins/evolution/snapshot")
    assert missing.status_code == 200, missing.text
    assert missing.json()["state"] == "missing"
    assert not root.exists()

    initialized = client.post("/api/plugins/evolution/initialize", json={})
    assert initialized.status_code == 200, initialized.text
    assert (root / "identity.json").is_file()
    context = _mutation_context(client)

    # Rebuild through the durable job API and inspect its real Gnothi graph.
    submitted = client.post(
        "/api/plugins/evolution/jobs/organism-rebuild",
        json={**context, "force": False, "collectors": []},
    )
    assert submitted.status_code == 202, submitted.text
    rebuild = _poll_terminal_job(client, submitted.json()["job_id"])
    assert rebuild["state"] == "completed", rebuild
    assert rebuild["result"]["kind"] == "organism_rebuild"
    gnothi_digest = rebuild["result"]["revision_digest"]

    graph = client.get(
        "/api/plugins/evolution/graph",
        params={"root_id": "capability:fixture-camera", "depth": 1},
    )
    assert graph.status_code == 200, graph.text
    graph_body = graph.json()
    assert {node["id"] for node in graph_body["nodes"]} == {
        "capability:fixture-camera",
        "fixture:source",
    }
    assert graph_body["edges"] == [
        {
            "id": "edge:fixture-camera-source",
            "kind": "requires",
            "from": "capability:fixture-camera",
            "to": "fixture:source",
            "evidence_refs": ["evidence:capabilities"],
        }
    ]

    # Create the initial local Telos through the real host authority bridge,
    # then prove a dashboard draft stays inert until its exact confirmation.
    from hermes_cli.evolution.telos_contract import telos_revision_from_dict
    from hermes_cli.evolution.telos_store import TelosStore

    initial_revision = telos_revision_from_dict(
        _telos_document(context["organism_id"], parent_digest=None, purpose="Initial Telos")
    )
    store = TelosStore(root)
    store.save_revision(initial_revision)
    _activate_from_real_host(root, context["organism_id"], initial_revision.canonical_digest)
    baseline_digest = initial_revision.canonical_digest

    target_draft = client.post(
        "/api/plugins/evolution/telos/drafts",
        json={
            **_mutation_context(client),
            "document": _telos_document(
                context["organism_id"],
                parent_digest=baseline_digest,
                purpose="Target Telos",
            ),
        },
    )
    assert target_draft.status_code == 200, target_draft.text
    target_digest = target_draft.json()["digest"]
    assert store.get_active_digest() == baseline_digest

    alternate_draft = client.post(
        "/api/plugins/evolution/telos/drafts",
        json={
            **_mutation_context(client),
            "document": _telos_document(
                context["organism_id"],
                parent_digest=baseline_digest,
                purpose="Alternate Telos",
            ),
        },
    )
    assert alternate_draft.status_code == 200, alternate_draft.text
    alternate_digest = alternate_draft.json()["digest"]

    stale_context = _mutation_context(client)
    stale_prepared = client.post(
        "/api/plugins/evolution/telos/transitions/prepare",
        json={
            **stale_context,
            "current_digest": baseline_digest,
            "target_digest": target_digest,
            "action": "activate",
        },
    )
    assert stale_prepared.status_code == 200, stale_prepared.text
    stale_confirmation = stale_prepared.json()

    # A different, real host approval changes the expected current state.
    # The stale browser confirmation must not be able to move the pointer.
    _activate_from_real_host(root, context["organism_id"], alternate_digest)
    pointer_before_stale_confirmation = (root / "telos" / "active.json").read_bytes()
    stale_confirmation_response = client.post(
        "/api/plugins/evolution/telos/transitions/confirm",
        json={
            **stale_context,
            "confirmation_id": stale_confirmation["confirmation_id"],
            "current_digest": baseline_digest,
            "target_digest": target_digest,
            "action": "activate",
            "phrase": stale_confirmation["required_phrase"],
        },
    )
    assert stale_confirmation_response.status_code == 409
    assert stale_confirmation_response.json()["code"] == "snapshot_changed"
    assert (root / "telos" / "active.json").read_bytes() == pointer_before_stale_confirmation
    assert store.get_active_digest() == alternate_digest

    valid_context = _mutation_context(client)
    valid_prepared = client.post(
        "/api/plugins/evolution/telos/transitions/prepare",
        json={
            **valid_context,
            "current_digest": alternate_digest,
            "target_digest": target_digest,
            "action": "activate",
        },
    )
    assert valid_prepared.status_code == 200, valid_prepared.text
    valid_confirmation = valid_prepared.json()
    confirmed = client.post(
        "/api/plugins/evolution/telos/transitions/confirm",
        json={
            **valid_context,
            "confirmation_id": valid_confirmation["confirmation_id"],
            "current_digest": alternate_digest,
            "target_digest": target_digest,
            "action": "activate",
            "phrase": valid_confirmation["required_phrase"],
        },
    )
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json() == {"status": "approved"}
    assert store.get_active_digest() == target_digest

    # Seed sanitized cross-profile facts through the real Observer service,
    # then run its fixed job and create one immutable blueprint twice.
    from hermes_cli.evolution.global_config import load_global_config, save_global_config

    autopoiesis = load_global_config()["autopoiesis"]
    autopoiesis["enabled"] = True
    save_global_config(autopoiesis)
    observer_enabled = client.post(
        "/api/plugins/evolution/observer",
        json={**_mutation_context(client), "enabled": True},
    )
    assert observer_enabled.status_code == 200, observer_enabled.text

    from hermes_cli.evolution.observation_contract import ObservationEnvelope
    from hermes_cli.evolution.observer_service import ObserverService

    observer = ObserverService(root)
    for event_id, profile_ref, session_ref, evidence_ref in (
        ("11111111-1111-1111-1111-111111111111", "profilealpha", "sessalpha", "a" * 64),
        ("22222222-2222-2222-2222-222222222222", "profilebeta", "sessbeta", "b" * 64),
    ):
        assert observer.ingest_envelope(
            ObservationEnvelope(
                schema_version=1,
                event_id=event_id,
                organism_id=context["organism_id"],
                occurred_at="2026-07-28T10:05:00.000000Z",
                signal_type="capability_absence",
                provenance="explicit_user",
                source_profile_ref=profile_ref,
                source_project_ref=None,
                source_session_ref=session_ref,
                generation_id=gnothi_digest,
                gnothi_revision_digest=gnothi_digest,
                telos_digest=target_digest,
                capability_key="camera",
                operation_key="capture.image",
                outcome_key="not_available",
                constraint_key="local_only",
                severity="high",
                task_impact="high",
                retry_count=0,
                latency_bucket=None,
                explicit_user_intent=True,
                recovered=False,
                evidence_refs=(evidence_ref,),
                redaction_status="verified_redacted",
            )
        )

    scan = client.post(
        "/api/plugins/evolution/jobs/observer-scan", json=_mutation_context(client)
    )
    assert scan.status_code == 202, scan.text
    scanned = _poll_terminal_job(client, scan.json()["job_id"])
    assert scanned["state"] == "completed", scanned
    assert scanned["result"] == {"kind": "observer_scan", "updated_suggestions": 1}

    pipeline = client.get("/api/plugins/evolution/pipeline")
    assert pipeline.status_code == 200, pipeline.text
    eligible = next(
        suggestion
        for suggestion in pipeline.json()["suggestions"]
        if suggestion["state"] == "eligible"
    )
    first_blueprint = client.post(
        f"/api/plugins/evolution/suggestions/{eligible['suggestion_id']}/blueprint",
        json={
            **_mutation_context(client),
            "expected_suggestion_digest": eligible["suggestion_digest"],
        },
    )
    assert first_blueprint.status_code == 200, first_blueprint.text
    second_blueprint = client.post(
        f"/api/plugins/evolution/suggestions/{eligible['suggestion_id']}/blueprint",
        json={
            **_mutation_context(client),
            "expected_suggestion_digest": eligible["suggestion_digest"],
        },
    )
    assert second_blueprint.status_code == 200, second_blueprint.text
    assert first_blueprint.json()["status"] == "created"
    assert second_blueprint.json()["status"] == "existing"
    assert {
        key: second_blueprint.json()[key]
        for key in ("blueprint_id", "canonical_digest")
    } == {
        key: first_blueprint.json()[key]
        for key in ("blueprint_id", "canonical_digest")
    }

    # Research is an always-on public handoff.  It is a read/projection, not
    # an authorization, build, promotion, or suggestion-state transition.
    counts_before_research = _authorization_and_later_stage_counts(root)
    public_pipeline = client.get("/api/plugins/evolution/pipeline")
    assert public_pipeline.status_code == 200, public_pipeline.text
    public_body = public_pipeline.json()
    public_suggestion = next(
        suggestion
        for suggestion in public_body["suggestions"]
        if suggestion["suggestion_id"] == eligible["suggestion_id"]
    )
    assert next(stage for stage in public_body["stages"] if stage["id"] == "research") == {
        "id": "research",
        "available": True,
    }
    research_brief = {
        "topic": public_suggestion["public_research_topic"],
        "score": public_suggestion["score"],
        "telos_alignment": public_suggestion["telos_alignment"],
        "observed_sessions": public_suggestion["distinct_session_count"],
        "observation_count": public_suggestion["observation_count"],
        "public_only": True,
    }
    brief_json = json.dumps(research_brief, sort_keys=True)
    assert public_suggestion["summary"] not in brief_json
    assert all(
        private not in brief_json
        for private in (
            "profilealpha",
            "profilebeta",
            "sessalpha",
            "sessbeta",
            "evidence_refs",
            "source_profile_ref",
            "source_session_ref",
            "path",
            "log",
        )
    )
    assert _authorization_and_later_stage_counts(root) == counts_before_research
    after_research = client.get("/api/plugins/evolution/pipeline")
    assert after_research.status_code == 200, after_research.text
    assert next(
        suggestion["state"]
        for suggestion in after_research.json()["suggestions"]
        if suggestion["suggestion_id"] == eligible["suggestion_id"]
    ) == "eligible"

    # A profile switch changes the active profile home but never the global
    # organism, its Gnothi/Telos identity, or immutable blueprint history.
    alpha_snapshot = client.get("/api/plugins/evolution/snapshot").json()
    alpha_telos = client.get("/api/plugins/evolution/telos").json()
    alpha_pipeline = client.get("/api/plugins/evolution/pipeline").json()
    monkeypatch.setenv("HERMES_HOME", str(control_center.profile_beta))
    beta_snapshot = client.get("/api/plugins/evolution/snapshot")
    beta_telos = client.get("/api/plugins/evolution/telos")
    beta_pipeline = client.get("/api/plugins/evolution/pipeline")
    assert beta_snapshot.status_code == 200, beta_snapshot.text
    assert beta_telos.status_code == 200, beta_telos.text
    assert beta_pipeline.status_code == 200, beta_pipeline.text
    assert beta_snapshot.json()["organism"] == alpha_snapshot["organism"]
    assert beta_snapshot.json()["gnothi"]["revision_id"] == alpha_snapshot["gnothi"]["revision_id"]
    assert beta_snapshot.json()["gnothi"]["revision_digest"] == alpha_snapshot["gnothi"]["revision_digest"]
    assert beta_telos.json()["active_digest"] == alpha_telos["active_digest"] == target_digest
    assert beta_pipeline.json()["blueprints"] == alpha_pipeline["blueprints"]
