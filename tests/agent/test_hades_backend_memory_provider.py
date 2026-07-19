from __future__ import annotations

import copy
import json

import pytest


def _create_linked_provider(
    monkeypatch,
    tmp_path,
    *,
    items=None,
    project_id="proj_1",
    workspace_binding_id="wb_1",
):
    from hermes_cli import hades_backend_db as db
    from hermes_cli.hades_backend_runtime import workspace_fingerprint
    from plugins.memory import load_memory_provider

    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))
    workspace = tmp_path / "repo"
    workspace.mkdir()
    monkeypatch.chdir(workspace)

    fp = workspace_fingerprint(workspace, project_id)
    with db.connect_closing() as conn:
        db.save_agent(
            conn,
            agent_id="agent_1",
            project_id=project_id,
            base_url="https://backend.example",
            label="dev",
            token_env_key="HADES_BACKEND_AGENT_TOKEN_TEST",
            capabilities={"memory": True},
        )
        db.upsert_workspace_binding(
            conn,
            project_id=project_id,
            agent_id="agent_1",
            local_project_id="p_1",
            workspace_fingerprint=fp,
            display_path="~/repo",
            repo_root=str(workspace),
            git_remote_display="",
            git_remote_hash="",
            head_commit="",
            backend_workspace_binding_id=workspace_binding_id,
        )
        if items is not None:
            db.replace_memory_cache(
                conn,
                project_id=project_id,
                workspace_binding_id=workspace_binding_id,
                version="v1",
                items=items,
            )

    provider = load_memory_provider("hades_backend")
    assert provider is not None
    provider.initialize("session_1", hermes_home=str(tmp_path / "home"), platform="cli")
    return provider


def _create_v2_graph_provider(monkeypatch, tmp_path):
    from hermes_cli import hades_backend_db as db
    from hermes_cli.hades_backend_sync import _graph_v2_active_cache_key
    from tests.hermes_cli.test_hades_graph_contract import _valid_flow_artifact

    graph = _valid_flow_artifact()
    project = graph["project"]
    projection_version = "c" * 64
    provider = _create_linked_provider(
        monkeypatch,
        tmp_path,
        project_id=project["project_id"],
        workspace_binding_id=project["workspace_binding_id"],
        items=[
            {
                "id": "graph-v2-local",
                "domain": "artifacts",
                "schema": "hades.code_graph.v2",
                "projection_version": projection_version,
                "payload": copy.deepcopy(graph),
            }
        ],
    )
    active = {
        "schema": "hades.code_graph.v2",
        "project_id": project["project_id"],
        "workspace_binding_id": project["workspace_binding_id"],
        "source_identity": graph["source"],
        "artifact_graph_version": graph["graph_contract"]["artifact_graph_version"],
        "projection_version": projection_version,
        "publication_status": "ready",
    }
    with db.connect_closing() as conn:
        db.record_sync_state(
            conn, _graph_v2_active_cache_key(provider._binding), active
        )
    assert provider._active_graph_identity("project") == active
    return provider, graph


def _ready_v2_identity(
    *,
    project_id="proj_1",
    workspace_binding_id="wb_1",
    projection_version=None,
):
    return {
        "schema": "hades.code_graph.v2",
        "project_id": project_id,
        "workspace_binding_id": workspace_binding_id,
        "artifact_graph_version": "a" * 64,
        "projection_version": projection_version or "c" * 64,
        "publication_status": "ready",
    }


def _live_project_topology(*, canonical_start="route:orders.show"):
    return {
        "project_id": "proj_1",
        "workspace_binding_id": "wb_1",
        "schema": "hades.code_graph.v2",
        "projection_version": "c" * 64,
        "coverage": {"records": {"nodes": 2, "edges": 1}},
        "start": canonical_start,
        "nodes": [
            {"id": canonical_start, "kind": "route", "label": "orders.show"},
            {
                "id": "OrderController@show",
                "kind": "method",
                "label": "OrderController@show",
            },
        ],
        "edges": [
            {
                "id": "edge_1",
                "kind": "route_handler",
                "from": canonical_start,
                "to": "OrderController@show",
            }
        ],
    }


def _live_organism_topology(*, schema="hades.organism_graph.v1"):
    return {
        "project_id": "proj_1",
        "workspace_binding_id": "wb_1",
        "schema": schema,
        "start": "capability:graph-search",
        "nodes": [
            {
                "id": "capability:graph-search",
                "kind": "capability",
                "label": "Graph search",
            },
            {
                "id": "runtime:local-cli",
                "kind": "runtime",
                "label": "Local CLI",
            },
        ],
        "edges": [
            {
                "id": "edge:graph-search-requires-cli",
                "kind": "requires",
                "from": "capability:graph-search",
                "to": "runtime:local-cli",
            }
        ],
    }


def _organism_graph_artifact():
    return {
        "schema": "hades.organism_graph.v1",
        "organism_contract": {
            "version": "hades.gnothi_seauton.v1",
            "revision_id": "revision_test_1",
            "generation": {"id": "generation_test_1", "scope": "stable"},
            "source": {"head_commit": "abc123"},
            "collected_at": "2026-07-14T10:00:00Z",
            "status": "current",
            "coverage": {},
        },
        "nodes": [
            {
                "id": "capability:graph-search",
                "kind": "capability",
                "label": "Graph search",
                "owner": {"class": "plugin", "id": "hades_backend"},
                "generation_scope": "stable",
                "state": {"available": True},
                "evidence_refs": ["source:plugins/memory/hades_backend/__init__.py"],
                "properties": {"surface": "model_tool"},
                "verified_at": "2026-07-14T10:00:00Z",
            },
            {
                "id": "runtime:local-cli",
                "kind": "runtime",
                "label": "Local CLI",
                "owner": {"class": "core", "id": "hermes"},
                "generation_scope": "stable",
                "state": {"available": True},
                "evidence_refs": ["source:cli.py"],
                "properties": {"platform": "cli"},
                "verified_at": "2026-07-14T10:00:00Z",
            },
        ],
        "edges": [
            {
                "id": "edge:graph-search-requires-cli",
                "kind": "requires",
                "from": "capability:graph-search",
                "to": "runtime:local-cli",
                "evidence_refs": ["source:plugins/memory/hades_backend/__init__.py"],
                "properties": {"reason": "execution surface"},
            }
        ],
        "redactions": 0,
        "truncated": False,
        "raw_source_included": False,
        "retention_class": "organism_metadata",
    }


def test_hades_backend_memory_provider_prefetches_linked_project_cache(monkeypatch, tmp_path):
    from hermes_cli import hades_backend_db as db
    from hermes_cli.hades_backend_runtime import workspace_fingerprint
    from plugins.memory import load_memory_provider

    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))
    workspace = tmp_path / "repo"
    workspace.mkdir()
    monkeypatch.chdir(workspace)

    fp = workspace_fingerprint(workspace, "proj_1")
    with db.connect_closing() as conn:
        db.save_agent(
            conn,
            agent_id="agent_1",
            project_id="proj_1",
            base_url="https://backend.example",
            label="dev",
            token_env_key="HADES_BACKEND_AGENT_TOKEN_TEST",
            capabilities={"memory": True},
        )
        db.upsert_workspace_binding(
            conn,
            project_id="proj_1",
            agent_id="agent_1",
            local_project_id="p_1",
            workspace_fingerprint=fp,
            display_path="~/repo",
            repo_root=str(workspace),
            git_remote_display="",
            git_remote_hash="",
            head_commit="",
            backend_workspace_binding_id="wb_1",
        )
        db.replace_memory_cache(
            conn,
            project_id="proj_1",
            workspace_binding_id="wb_1",
            version="v1",
            items=[
                {
                    "id": "mem_1",
                    "domain": "project_memory",
                    "summary": "The Laravel API uses /api/hades/v1 routes.",
                    "etag": "e1",
                },
                {
                    "id": "chunk_1",
                    "domain": "source_chunks",
                    "schema": "hades.backend_wiki.file_chunk.v1",
                    "summary": "RAW route dump should not be auto injected.",
                }
            ],
        )

    provider = load_memory_provider("hades_backend")
    assert provider is not None
    provider.initialize("session_1", hermes_home=str(tmp_path / "home"), platform="cli")

    context = provider.prefetch("Which backend routes should I use?", session_id="session_1")

    assert "Shared Hades project memory" in context
    assert "/api/hades/v1" in context
    assert "RAW route dump" not in context
    assert [schema["name"] for schema in provider.get_tool_schemas()] == [
        "hades_backend_project_memory_search",
        "hades_backend_bug_evidence_search",
        "hades_backend_graph_search",
        "hades_backend_graph_traverse",
        "hades_backend_source_slice_fetch",
        "hades_backend_evidence_pack_search",
        "hades_backend_evidence_pack_create",
        "hades_backend_causal_pack_fetch",
        "hades_backend_project_awareness_status",
        "hades_backend_diagnosis_report_create",
        "hades_backend_resolved_bug_promote",
    ]


def test_hades_backend_memory_provider_prefetch_ranks_by_query(monkeypatch, tmp_path):
    provider = _create_linked_provider(
        monkeypatch,
        tmp_path,
        items=[
            {"id": "mem_1", "domain": "project_memory", "summary": "Security billing jobs use route exports."},
            {
                "id": "mem_2",
                "domain": "wiki",
                "summary": "Security activity routes are handled by SecurityActivityCategoryController.",
            },
        ],
    )

    context = provider.prefetch("security activity routes", session_id="session_1")

    assert context.index("Security activity routes") < context.index("Security billing jobs")


def test_hades_backend_memory_provider_prefetch_falls_back_to_wiki_when_broad_search_only_omits_raw_chunks(
    monkeypatch, tmp_path
):
    provider = _create_linked_provider(monkeypatch, tmp_path)
    calls = []

    def search(**kwargs):
        calls.append(kwargs)
        if kwargs["domain"] == "all":
            return {"items": [], "raw_chunks_omitted": 200}, None
        return {
            "items": [
                {
                    "domain": "wiki",
                    "summary": "Entity index",
                    "payload_excerpt": "Workers and departments",
                }
            ],
            "raw_chunks_omitted": 0,
            "version": "wiki-v1",
        }, None

    monkeypatch.setattr(provider, "_backend_memory_search", search)

    context = provider.prefetch("Quali sono le entità principali?")

    assert [call["domain"] for call in calls] == ["all", "wiki"]
    assert "Entity index" in context
    assert "Workers and departments" in context


def test_hades_backend_memory_provider_prefetch_does_not_query_wiki_when_broad_search_is_usable(
    monkeypatch, tmp_path
):
    provider = _create_linked_provider(monkeypatch, tmp_path)
    calls = []

    def search(**kwargs):
        calls.append(kwargs)
        return {
            "items": [{"domain": "project_memory", "summary": "Compact fact"}],
            "raw_chunks_omitted": 0,
        }, None

    monkeypatch.setattr(provider, "_backend_memory_search", search)

    assert "Compact fact" in provider.prefetch("fact")
    assert [call["domain"] for call in calls] == ["all"]


def test_hades_backend_memory_provider_prefetch_answers_explicit_backend_logbook_task_query(
    monkeypatch, tmp_path
):
    provider = _create_linked_provider(monkeypatch, tmp_path)
    calls = []

    def search(**kwargs):
        calls.append(kwargs)
        if kwargs["domain"] == "all":
            return {"items": [], "raw_chunks_omitted": 100}, None
        return {
            "items": [
                {
                    "domain": "wiki",
                    "summary": "Logbook: task #459 introduced payroll reconciliation.",
                }
            ],
            "raw_chunks_omitted": 0,
            "version": "wiki-logbook-v1",
        }, None

    monkeypatch.setattr(provider, "_backend_memory_search", search)

    context = provider.prefetch(
        "Guarda nel logbook del backend cosa si dice in merito al task #459"
    )

    assert [call["domain"] for call in calls] == ["wiki"]
    assert calls[0]["timeout"] == 2.0
    assert "task #459" in context


def test_hades_backend_memory_search_tool_filters_domains_and_raw_chunks(monkeypatch, tmp_path):
    provider = _create_linked_provider(
        monkeypatch,
        tmp_path,
        items=[
            {"id": "mem_1", "domain": "logbook", "summary": "DECIDED: backend memory stays authoritative."},
            {"id": "mem_2", "domain": "wiki", "summary": "Backend routes live under /api/hades/v1."},
            {
                "id": "chunk_1",
                "domain": "source_chunks",
                "schema": "hades.backend_wiki.file_chunk.v1",
                "path": "docs/backend.md",
                "summary": "Exact chunk mentions /api/hades/v1/source.",
            },
        ],
    )

    result = json.loads(
        provider.handle_tool_call(
            "hades_backend_project_memory_search",
            {"query": "backend routes", "domain": "wiki", "limit": 5},
        )
    )

    assert result["status"] == "ok"
    assert result["searched_cache_only"] is True
    assert result["domain"] == "wiki"
    assert result["count"] == 1
    assert result["raw_chunks_omitted"] == 0
    assert result["items"][0]["id"] == "mem_2"
    assert result["items"][0]["domain"] == "wiki"


def test_hades_backend_memory_search_tool_prefers_live_backend(monkeypatch, tmp_path):
    provider = _create_linked_provider(monkeypatch, tmp_path)

    class FakeClient:
        def __init__(self):
            self.calls = []
            self.closed = 0

        def memory_search(self, **payload):
            self.calls.append(payload)
            return {
                "project_id": payload["project_id"],
                "workspace_binding_id": payload["workspace_binding_id"],
                "version": "search_v1",
                "etag": "search_v1",
                "query": payload["query"],
                "domain": payload["domain"],
                "include_raw_chunks": payload["include_raw_chunks"],
                "count": 1,
                "candidate_count": 2,
                "truncated": True,
                "raw_chunks_omitted": 1,
                "freshness": {
                    "workspace_head_commit": "abc123",
                    "index_status": "live_query",
                },
                "server_time": "2026-07-06T12:00:00Z",
                "items": [
                    {
                        "id": "wiki_1",
                        "domain": "wiki",
                        "schema": "devboard.wiki_revision.v1",
                        "source": "wiki_revision",
                        "summary": "Live backend wiki says Hades routes live under /api/hades/v1.",
                        "score": 18,
                        "page_slug": "architecture/hades-memory",
                        "raw_chunk": False,
                    }
                ],
            }

        def close(self):
            self.closed += 1

    fake = FakeClient()
    import plugins.memory.hades_backend as hades_memory

    monkeypatch.setattr(hades_memory.runtime, "client_from_config", lambda *, timeout=None: fake)

    context = provider.prefetch("Hades routes", session_id="session_1")
    result = json.loads(
        provider.handle_tool_call(
            "hades_backend_project_memory_search",
            {"query": "Hades routes", "domain": "wiki", "limit": 5},
        )
    )

    assert "live search search_v1" in context
    assert "/api/hades/v1" in context
    assert result["status"] == "ok"
    assert result["searched_cache_only"] is False
    assert result["backend_version"] == "search_v1"
    assert result["candidate_count"] == 2
    assert result["truncated"] is True
    assert result["raw_chunks_omitted"] == 1
    assert result["freshness"]["index_status"] == "live_query"
    assert result["items"][0]["page_slug"] == "architecture/hades-memory"
    assert fake.calls[0]["limit"] == 8
    assert fake.calls[1]["limit"] == 5
    assert fake.calls[1]["workspace_binding_id"] == "wb_1"
    assert fake.closed == 2


def test_hades_backend_memory_search_tool_exposes_resolved_bug_status(monkeypatch, tmp_path):
    provider = _create_linked_provider(monkeypatch, tmp_path)

    class FakeClient:
        def __init__(self):
            self.calls = []

        def memory_search(self, **payload):
            self.calls.append(payload)
            return {
                "project_id": payload["project_id"],
                "workspace_binding_id": payload["workspace_binding_id"],
                "version": "search_v1",
                "etag": "search_v1",
                "query": payload["query"],
                "domain": payload["domain"],
                "kind": payload["kind"],
                "include_raw_chunks": payload["include_raw_chunks"],
                "count": 2,
                "candidate_count": 2,
                "truncated": False,
                "raw_chunks_omitted": 0,
                "items": [
                    {
                        "id": "mem_bug_1",
                        "domain": "project_memory",
                        "kind": "resolved_bug",
                        "schema": "hades.resolved_bug.v1",
                        "source": "hades_diagnosis_report",
                        "summary": "Resolved bug: active() on null in OrderController.",
                        "score": 42,
                        "raw_chunk": False,
                        "stale": True,
                        "stale_reason": "workspace_head_changed",
                    },
                    {
                        "id": "mem_note_1",
                        "domain": "project_memory",
                        "kind": "agent_note",
                        "summary": "Generic note that should not survive kind filtering.",
                        "score": 10,
                        "raw_chunk": False,
                    }
                ],
            }

        def close(self):
            pass

    fake = FakeClient()
    import plugins.memory.hades_backend as hades_memory

    monkeypatch.setattr(hades_memory.runtime, "client_from_config", lambda *, timeout=None: fake)

    result = json.loads(
        provider.handle_tool_call(
            "hades_backend_project_memory_search",
            {"query": "active null", "domain": "project_memory", "kind": "resolved_bug", "limit": 5},
        )
    )

    assert fake.calls[0]["kind"] == "resolved_bug"
    assert result["kind"] == "resolved_bug"
    assert result["count"] == 1
    assert len(result["items"]) == 1
    assert result["items"][0]["kind"] == "resolved_bug"
    assert result["items"][0]["stale"] is True
    assert result["items"][0]["stale_reason"] == "workspace_head_changed"


def test_hades_backend_memory_search_tool_passes_structured_filters(monkeypatch, tmp_path):
    provider = _create_linked_provider(monkeypatch, tmp_path)

    class FakeClient:
        def __init__(self):
            self.calls = []

        def memory_search(self, **payload):
            self.calls.append(payload)
            return {
                "project_id": payload["project_id"],
                "workspace_binding_id": payload["workspace_binding_id"],
                "version": "search_v1",
                "etag": "search_v1",
                "query": payload["query"],
                "domain": payload["domain"],
                "include_raw_chunks": payload["include_raw_chunks"],
                "count": 2,
                "candidate_count": 2,
                "truncated": False,
                "raw_chunks_omitted": 0,
                "items": [
                    {
                        "id": "mem_bug_1",
                        "domain": "project_memory",
                        "kind": "resolved_bug",
                        "schema": "hades.resolved_bug.v1",
                        "source": "hades_diagnosis_report",
                        "summary": "Resolved bug: active() on null in OrderController.",
                        "payload": {
                            "affected_symbols": ["App\\Http\\Controllers\\OrderController@show"],
                            "path": "app/Http/Controllers/OrderController.php",
                        },
                        "match_fields": ["payload.affected_symbols", "payload.path"],
                        "score": 64,
                        "raw_chunk": False,
                    },
                    {
                        "id": "mem_note_1",
                        "domain": "project_memory",
                        "kind": "agent_note",
                        "schema": "hades.agent_note.v1",
                        "source": "hades_agent",
                        "summary": "OrderController note without diagnosis status.",
                        "score": 10,
                        "raw_chunk": False,
                    },
                ],
            }

        def close(self):
            pass

    fake = FakeClient()
    import plugins.memory.hades_backend as hades_memory

    monkeypatch.setattr(hades_memory.runtime, "client_from_config", lambda *, timeout=None: fake)

    result = json.loads(
        provider.handle_tool_call(
            "hades_backend_project_memory_search",
            {
                "query": "order active null",
                "domain": "project_memory",
                "kind": "resolved_bug",
                "schema": "hades.resolved_bug.v1",
                "source": "hades_diagnosis_report",
                "symbol": "OrderController@show",
                "path": "OrderController.php",
                "limit": 5,
            },
        )
    )

    assert fake.calls[0]["kind"] == "resolved_bug"
    assert fake.calls[0]["schema"] == "hades.resolved_bug.v1"
    assert fake.calls[0]["source"] == "hades_diagnosis_report"
    assert fake.calls[0]["symbol"] == "OrderController@show"
    assert fake.calls[0]["path"] == "OrderController.php"
    assert result["filters"] == {
        "kind": "resolved_bug",
        "schema": "hades.resolved_bug.v1",
        "source": "hades_diagnosis_report",
        "symbol": "OrderController@show",
        "path": "OrderController.php",
    }
    assert result["count"] == 1
    assert result["items"][0]["id"] == "mem_bug_1"
    assert result["items"][0]["match_fields"] == ["payload.affected_symbols", "payload.path"]


def test_hades_backend_memory_search_tool_filters_cache_by_structured_fields(monkeypatch, tmp_path):
    provider = _create_linked_provider(
        monkeypatch,
        tmp_path,
        items=[
            {
                "id": "mem_bug_1",
                "domain": "project_memory",
                "kind": "resolved_bug",
                "schema": "hades.resolved_bug.v1",
                "source": "hades_diagnosis_report",
                "summary": "Resolved bug: active() on null in OrderController.",
                "payload": {
                    "affected_symbols": ["App\\Http\\Controllers\\OrderController@show"],
                    "path": "app/Http/Controllers/OrderController.php",
                },
            },
            {
                "id": "mem_bug_2",
                "domain": "project_memory",
                "kind": "resolved_bug",
                "schema": "hades.resolved_bug.v1",
                "source": "hades_diagnosis_report",
                "summary": "Resolved bug: checkout timeout in PaymentController.",
                "payload": {
                    "affected_symbols": ["App\\Http\\Controllers\\PaymentController@store"],
                    "path": "app/Http/Controllers/PaymentController.php",
                },
            },
        ],
    )

    result = json.loads(
        provider.handle_tool_call(
            "hades_backend_project_memory_search",
            {
                "query": "active null",
                "domain": "project_memory",
                "kind": "resolved_bug",
                "schema": "hades.resolved_bug.v1",
                "symbol": "OrderController@show",
                "path": "OrderController.php",
                "limit": 5,
            },
        )
    )

    assert result["searched_cache_only"] is True
    assert result["filters"]["symbol"] == "OrderController@show"
    assert result["filters"]["path"] == "OrderController.php"
    assert result["count"] == 1
    assert result["items"][0]["id"] == "mem_bug_1"


def test_hades_backend_graph_search_tool_queries_artifacts_live(monkeypatch, tmp_path):
    provider = _create_linked_provider(monkeypatch, tmp_path)
    provider._active_graph_identity = lambda _scope: _ready_v2_identity()

    class FakeClient:
        def __init__(self):
            self.calls = []
            self.closed = 0

        def memory_search(self, **payload):
            self.calls.append(payload)
            return {
                "project_id": payload["project_id"],
                "workspace_binding_id": payload["workspace_binding_id"],
                "version": "graph_search_v1",
                "etag": "graph_search_v1",
                "query": payload["query"],
                "domain": payload["domain"],
                "include_raw_chunks": payload["include_raw_chunks"],
                "count": 1,
                "candidate_count": 1,
                "truncated": False,
                "raw_chunks_omitted": 0,
                "freshness": {"index_status": "live_query"},
                "items": [
                    {
                        "id": "artifact_1",
                        "domain": "artifacts",
                        "schema": "hades.code_graph.v2",
                        "artifact_graph_version": "a" * 64,
                        "projection_version": "c" * 64,
                        "source": "hades.code_graph.v2",
                        "summary": "GET /orders/{order} -> OrderController@show",
                        "score": 21,
                        "raw_chunk": False,
                    }
                ],
            }

        def close(self):
            self.closed += 1

    fake = FakeClient()
    import plugins.memory.hades_backend as hades_memory

    monkeypatch.setattr(
        hades_memory.runtime, "client_from_config", lambda *, timeout=None: fake
    )

    result = json.loads(
        provider.handle_tool_call(
            "hades_backend_graph_search",
            {"query": "OrderController show", "limit": 4},
        )
    )

    assert result["status"] == "ok"
    assert result["tool_domain"] == "graph"
    assert result["domain"] == "artifacts"
    assert result["items"][0]["schema"] == "hades.code_graph.v2"
    assert fake.calls == [
        {
            "project_id": "proj_1",
            "workspace_binding_id": "wb_1",
            "query": "OrderController show",
            "domain": "artifacts",
            "limit": 4,
            "include_raw_chunks": False,
            "schema": "hades.code_graph.v2",
        }
    ]
    assert fake.closed == 1


@pytest.mark.parametrize(
    ("field", "wrong_value"),
    [
        pytest.param("project_id", "proj_other", id="wrong-project"),
        pytest.param("workspace_binding_id", "wb_other", id="wrong-workspace-binding"),
    ],
)
def test_hades_backend_graph_search_rejects_wrong_live_envelope(
    monkeypatch, tmp_path, field, wrong_value
):
    provider = _create_linked_provider(monkeypatch, tmp_path)
    provider._active_graph_identity = lambda _scope: _ready_v2_identity()
    response = {
        "project_id": "proj_1",
        "workspace_binding_id": "wb_1",
        "domain": "artifacts",
        "count": 1,
        "candidate_count": 1,
        "items": [
            {
                "id": "raw-v2",
                "schema": "hades.code_graph.v2",
                "summary": "GET /orders -> OrderController@show",
            }
        ],
    }
    response[field] = wrong_value
    provider._backend_memory_search = lambda **_payload: (response, None)

    result = json.loads(
        provider.handle_tool_call("hades_backend_graph_search", {"query": "orders"})
    )

    assert result["status"] == "backend_invalid_graph"
    assert result["items"] == []
    assert field.replace("_id", "") in result["backend_topology_error"]


def test_hades_backend_graph_search_rejects_wrong_live_projection(
    monkeypatch, tmp_path
):
    provider = _create_linked_provider(monkeypatch, tmp_path)
    provider._active_graph_identity = lambda _scope: _ready_v2_identity()
    provider._backend_memory_search = lambda **_payload: (
        {
            "project_id": "proj_1",
            "workspace_binding_id": "wb_1",
            "domain": "artifacts",
            "count": 1,
            "candidate_count": 1,
            "items": [
                {
                    "id": "raw-v2-wrong-projection",
                    "schema": "hades.code_graph.v2",
                    "projection_version": "d" * 64,
                    "summary": "stale graph projection",
                }
            ],
        },
        None,
    )

    result = json.loads(
        provider.handle_tool_call("hades_backend_graph_search", {"query": "stale"})
    )

    assert result["status"] == "backend_invalid_graph"
    assert result["items"] == []
    assert "projection" in result["backend_topology_error"]


def test_hades_backend_graph_search_rejects_wrong_live_artifact_version(
    monkeypatch, tmp_path
):
    provider = _create_linked_provider(monkeypatch, tmp_path)
    provider._active_graph_identity = lambda _scope: _ready_v2_identity()
    provider._backend_memory_search = lambda **_payload: (
        {
            "project_id": "proj_1",
            "workspace_binding_id": "wb_1",
            "domain": "artifacts",
            "count": 1,
            "candidate_count": 1,
            "items": [
                {
                    "id": "raw-v2-wrong-artifact-version",
                    "schema": "hades.code_graph.v2",
                    "artifact_graph_version": "b" * 64,
                    "summary": "wrong graph artifact version",
                }
            ],
        },
        None,
    )

    result = json.loads(
        provider.handle_tool_call("hades_backend_graph_search", {"query": "wrong"})
    )

    assert result["status"] == "backend_invalid_graph"
    assert result["items"] == []
    assert "artifact" in result["backend_topology_error"]


def test_hades_backend_graph_search_rejects_unhandled_live_v1_items(
    monkeypatch, tmp_path
):
    provider = _create_linked_provider(monkeypatch, tmp_path)

    class FakeClient:
        def memory_search(self, **_payload):
            return {
                "project_id": "proj_1",
                "workspace_binding_id": "wb_1",
                "domain": "artifacts",
                "count": 1,
                "candidate_count": 1,
                "items": [
                    {
                        "id": "legacy-raw",
                        "schema": "hades.code_graph.v1",
                        "summary": "legacy graph result without a handle",
                    }
                ],
            }

        def close(self):
            pass

    import plugins.memory.hades_backend as hades_memory

    monkeypatch.setattr(
        hades_memory.runtime, "client_from_config", lambda *, timeout=None: FakeClient()
    )

    result = json.loads(
        provider.handle_tool_call("hades_backend_graph_search", {"query": "legacy"})
    )

    assert result["status"] == "ok"
    assert result["items"] == []
    assert result["count"] == 0


def test_hades_backend_graph_tools_expose_project_and_organism_scopes(
    monkeypatch, tmp_path
):
    provider = _create_linked_provider(monkeypatch, tmp_path)
    schemas = {schema["name"]: schema for schema in provider.get_tool_schemas()}

    for tool_name in (
        "hades_backend_graph_search",
        "hades_backend_graph_traverse",
    ):
        scope = schemas[tool_name]["parameters"]["properties"]["scope"]
        assert scope["enum"] == ["project", "organism"]
        assert scope["default"] == "project"


def test_hades_backend_graph_search_organism_scope_filters_live_backend(
    monkeypatch, tmp_path
):
    provider = _create_linked_provider(monkeypatch, tmp_path)

    class FakeClient:
        def __init__(self):
            self.calls = []

        def memory_search(self, **payload):
            self.calls.append(payload)
            return {
                "project_id": payload["project_id"],
                "workspace_binding_id": payload["workspace_binding_id"],
                "query": payload["query"],
                "domain": payload["domain"],
                "count": 0,
                "items": [],
            }

        def close(self):
            pass

    fake = FakeClient()
    import plugins.memory.hades_backend as hades_memory

    monkeypatch.setattr(
        hades_memory.runtime, "client_from_config", lambda *, timeout=None: fake
    )

    result = json.loads(
        provider.handle_tool_call(
            "hades_backend_graph_search",
            {"query": "Graph search", "scope": "organism", "limit": 4},
        )
    )

    assert result["status"] == "ok"
    assert result["scope"] == "organism"
    assert fake.calls == [
        {
            "project_id": "proj_1",
            "workspace_binding_id": "wb_1",
            "query": "Graph search",
            "domain": "artifacts",
            "limit": 4,
            "include_raw_chunks": False,
            "schema": "hades.organism_graph.v1",
        }
    ]


def test_hades_backend_graph_scope_rejects_unknown_value_before_backend_call(
    monkeypatch, tmp_path
):
    provider = _create_linked_provider(monkeypatch, tmp_path)

    import plugins.memory.hades_backend as hades_memory

    def unexpected_client(*, timeout=None):
        raise AssertionError("invalid scope reached the backend")

    monkeypatch.setattr(hades_memory.runtime, "client_from_config", unexpected_client)

    search = json.loads(
        provider.handle_tool_call(
            "hades_backend_graph_search",
            {"query": "Graph search", "scope": "everything"},
        )
    )
    traverse = json.loads(
        provider.handle_tool_call(
            "hades_backend_graph_traverse",
            {"start": "capability:graph-search", "scope": "everything"},
        )
    )

    assert "Unsupported graph scope" in search["error"]
    assert search["allowed_scopes"] == ["project", "organism"]
    assert "Unsupported graph scope" in traverse["error"]
    assert traverse["allowed_scopes"] == ["project", "organism"]


@pytest.mark.parametrize(
    ("query", "expected_type", "expected_kind"),
    [
        pytest.param("Example.php", "node", "file", id="node-fields"),
        pytest.param("responds_with", "edge", "responds_with", id="edge-relation-only"),
    ],
)
def test_hades_backend_graph_search_falls_back_to_active_v2_cache(
    monkeypatch, tmp_path, query, expected_type, expected_kind
):
    provider, graph = _create_v2_graph_provider(monkeypatch, tmp_path)

    import plugins.memory.hades_backend as hades_memory

    monkeypatch.setattr(
        hades_memory.runtime,
        "client_from_config",
        lambda *, timeout=None: (_ for _ in ()).throw(RuntimeError("backend offline")),
    )

    result = json.loads(
        provider.handle_tool_call(
            "hades_backend_graph_search", {"query": query, "limit": 20}
        )
    )

    assert result["status"] == "ok"
    assert result["schema"] == "hades.code_graph.v2"
    assert result["artifact_id"] == "graph-v2-local"
    assert result["ranking"] == "local_bm25"
    assert result["backend_live_error"] == "backend offline"
    assert any("bm25" in item["match_fields"] for item in result["items"])
    graph_refs = [item["graph_ref"] for item in result["items"]]
    assert any(
        ref["type"] == expected_type and ref["kind"] == expected_kind
        for ref in graph_refs
    )
    assert result.get("head_commit") == graph["source"]["head_commit"]


@pytest.mark.parametrize(
    ("query", "expected_kind"),
    [
        pytest.param("method", "method", id="fuzzy-name"),
        pytest.param("/jobs", "entrypoint", id="public-path"),
        pytest.param("App\\Example::job", "job", id="qualified-symbol-name"),
    ],
)
def test_hades_backend_graph_traverse_falls_back_to_active_v2_cache(
    monkeypatch, tmp_path, query, expected_kind
):
    provider, graph = _create_v2_graph_provider(monkeypatch, tmp_path)
    expected_node = next(
        node for node in graph["nodes"] if node["kind"] == expected_kind
    )

    import plugins.memory.hades_backend as hades_memory

    monkeypatch.setattr(
        hades_memory.runtime,
        "client_from_config",
        lambda *, timeout=None: (_ for _ in ()).throw(RuntimeError("backend offline")),
    )

    result = json.loads(
        provider.handle_tool_call(
            "hades_backend_graph_traverse",
            {"start": query, "direction": "any", "max_depth": 3, "limit": 20},
        )
    )

    assert result["status"] == "ok"
    assert result["schema"] == "hades.code_graph.v2"
    assert result["searched_cache_only"] is True
    assert result["backend_live_error"] == "backend offline"
    assert expected_node["id"] in {node["id"] for node in result["nodes"]}
    assert result["match_fields"]


@pytest.mark.parametrize(
    ("case", "error_fragment"),
    [
        pytest.param("nodes-not-list", "nodes must be a list", id="nodes-list"),
        pytest.param("node-not-record", "node records", id="node-record"),
        pytest.param("node-id-missing", "node id", id="node-id"),
        pytest.param("node-id-duplicate", "unique", id="node-id-unique"),
        pytest.param("edges-not-list", "edges must be a list", id="edges-list"),
        pytest.param("edge-not-record", "edge records", id="edge-record"),
        pytest.param("edge-kind-missing", "edge kind", id="edge-kind"),
        pytest.param("edge-from-missing", "edge endpoints", id="edge-from"),
        pytest.param("edge-to-missing", "edge endpoints", id="edge-to"),
        pytest.param("edge-dangling", "returned nodes", id="edge-endpoint"),
        pytest.param("coverage-nodes", "coverage node count", id="coverage-nodes"),
        pytest.param("coverage-edges", "coverage edge count", id="coverage-edges"),
        pytest.param("start-missing", "canonical start", id="start-present"),
        pytest.param(
            "start-not-returned", "canonical start", id="start-resolves-to-node"
        ),
    ],
)
def test_hades_backend_graph_traverse_rejects_malformed_live_v2_topology(
    monkeypatch, tmp_path, case, error_fragment
):
    provider = _create_linked_provider(monkeypatch, tmp_path)
    provider._active_graph_identity = lambda _scope: _ready_v2_identity()
    topology = _live_project_topology()

    if case == "nodes-not-list":
        topology["nodes"] = "malformed"
    elif case == "node-not-record":
        topology["nodes"] = ["malformed"]
    elif case == "node-id-missing":
        topology["nodes"][0].pop("id")
    elif case == "node-id-duplicate":
        topology["nodes"].append(copy.deepcopy(topology["nodes"][0]))
        topology["coverage"]["records"]["nodes"] = 3
    elif case == "edges-not-list":
        topology["edges"] = "malformed"
    elif case == "edge-not-record":
        topology["edges"] = ["malformed"]
    elif case == "edge-kind-missing":
        topology["edges"][0].pop("kind")
    elif case == "edge-from-missing":
        topology["edges"][0].pop("from")
    elif case == "edge-to-missing":
        topology["edges"][0].pop("to")
    elif case == "edge-dangling":
        topology["edges"][0]["to"] = "MissingController@show"
    elif case == "coverage-nodes":
        topology["coverage"]["records"]["nodes"] = 99
    elif case == "coverage-edges":
        topology["coverage"]["records"]["edges"] = 99
    elif case == "start-missing":
        topology.pop("start")
    elif case == "start-not-returned":
        topology["start"] = "route:missing"

    provider._backend_graph_traverse = lambda **_payload: (topology, None)

    result = json.loads(
        provider.handle_tool_call(
            "hades_backend_graph_traverse", {"start": "orders.show"}
        )
    )

    assert result["status"] == "backend_invalid_graph"
    assert result["nodes"] == []
    assert result["edges"] == []
    assert error_fragment in result["backend_topology_error"]


def test_hades_backend_graph_search_resolves_organism_vector_topology(
    monkeypatch, tmp_path
):
    provider = _create_linked_provider(monkeypatch, tmp_path)
    provider._backend_memory_search = lambda **_payload: (
        {
            "project_id": "proj_1",
            "workspace_binding_id": "wb_1",
            "domain": "artifacts",
            "count": 1,
            "items": [
                {
                    "id": "organism-vector-hit",
                    "graph_handle": "capability:graph-search",
                    "summary": "Graph search capability",
                }
            ],
        },
        None,
    )
    provider._backend_graph_traverse = lambda **payload: (
        _live_organism_topology(),
        None,
    )

    result = json.loads(
        provider.handle_tool_call(
            "hades_backend_graph_search",
            {"query": "Graph search", "scope": "organism"},
        )
    )

    assert result["status"] == "ok"
    assert result["scope"] == "organism"
    assert result["topology_resolved"] is True
    assert result["topology_resolved_handles"] == ["capability:graph-search"]
    assert {node["id"] for node in result["nodes"]} == {
        "capability:graph-search",
        "runtime:local-cli",
    }


def test_hades_backend_graph_search_rejects_wrong_schema_for_organism_vector(
    monkeypatch, tmp_path
):
    provider = _create_linked_provider(monkeypatch, tmp_path)
    provider._backend_memory_search = lambda **_payload: (
        {
            "project_id": "proj_1",
            "workspace_binding_id": "wb_1",
            "domain": "artifacts",
            "count": 1,
            "items": [{"graph_handle": "capability:graph-search"}],
        },
        None,
    )
    provider._backend_graph_traverse = lambda **_payload: (
        _live_organism_topology(schema="hades.code_graph.v2"),
        None,
    )

    result = json.loads(
        provider.handle_tool_call(
            "hades_backend_graph_search",
            {"query": "Graph search", "scope": "organism"},
        )
    )

    assert result["status"] == "ok"
    assert result["topology_resolved"] is False
    assert "hades.organism_graph.v1" in result["backend_topology_error"]


@pytest.mark.parametrize(
    ("field", "wrong_value"),
    [
        pytest.param("project_id", "proj_other", id="wrong-project"),
        pytest.param("workspace_binding_id", "wb_other", id="wrong-workspace-binding"),
    ],
)
def test_hades_backend_graph_traverse_rejects_wrong_organism_envelope(
    monkeypatch, tmp_path, field, wrong_value
):
    provider = _create_linked_provider(monkeypatch, tmp_path)
    topology = _live_organism_topology()
    topology[field] = wrong_value
    provider._backend_graph_traverse = lambda **_payload: (topology, None)

    result = json.loads(
        provider.handle_tool_call(
            "hades_backend_graph_traverse",
            {"start": "capability:graph-search", "scope": "organism"},
        )
    )

    assert result["status"] == "backend_invalid_graph"
    assert result["nodes"] == []
    assert result["edges"] == []


def test_hades_backend_graph_traverse_rejects_project_schema_for_organism(
    monkeypatch, tmp_path
):
    provider = _create_linked_provider(monkeypatch, tmp_path)
    provider._backend_graph_traverse = lambda **_payload: (
        _live_organism_topology(schema="hades.code_graph.v2"),
        None,
    )

    result = json.loads(
        provider.handle_tool_call(
            "hades_backend_graph_traverse",
            {"start": "capability:graph-search", "scope": "organism"},
        )
    )

    assert result["status"] == "backend_invalid_graph"
    assert "hades.organism_graph.v1" in result["backend_topology_error"]


def test_hades_backend_graph_traverse_rejects_live_v1_topology(monkeypatch, tmp_path):
    provider = _create_linked_provider(monkeypatch, tmp_path)
    provider._active_graph_identity = lambda _scope: {
        "schema": "hades.code_graph.v2",
        "project_id": "proj_1",
        "workspace_binding_id": "wb_1",
        "projection_version": "c" * 64,
        "publication_status": "ready",
    }
    provider._backend_graph_traverse = lambda **payload: (
        {
            "project_id": "proj_1",
            "workspace_binding_id": "wb_1",
            "schema": "hades.code_graph.v1",
            "projection_version": "c" * 64,
            "coverage": {"records": {"nodes": 1, "edges": 0}},
            "start": payload["start"],
            "nodes": [{"id": "legacy", "kind": "route", "label": "legacy"}],
            "edges": [],
        },
        None,
    )

    result = json.loads(
        provider.handle_tool_call("hades_backend_graph_traverse", {"start": "orders"})
    )

    assert result["status"] == "backend_invalid_graph"
    assert result["nodes"] == []
    assert result["edges"] == []
    assert "hades.code_graph.v2" in result["backend_topology_error"]


def test_hades_backend_graph_traverse_accepts_canonical_start_for_fuzzy_query(
    monkeypatch, tmp_path
):
    provider = _create_linked_provider(monkeypatch, tmp_path)
    provider._active_graph_identity = lambda _scope: {
        "schema": "hades.code_graph.v2",
        "project_id": "proj_1",
        "workspace_binding_id": "wb_1",
        "projection_version": "c" * 64,
        "publication_status": "ready",
    }
    timeouts = []

    class FakeClient:
        def __init__(self):
            self.calls = []
            self.closed = 0

        def graph_traverse(self, **payload):
            self.calls.append(payload)
            return {
                "project_id": payload["project_id"],
                "workspace_binding_id": payload["workspace_binding_id"],
                "version": "graph_traversal_1",
                "etag": "graph_traversal_1",
                "artifact_id": "artifact_1",
                "schema": "hades.code_graph.v2",
                "projection_version": "c" * 64,
                "coverage": {"records": {"nodes": 2, "edges": 1}},
                "head_commit": "abc123",
                "start": "route:orders.show",
                "direction": payload["direction"],
                "max_depth": payload["max_depth"],
                "limit": payload["limit"],
                "count": 2,
                "edge_count": 1,
                "truncated": False,
                "match_fields": ["id", "attributes.name"],
                "freshness": {"status": "current", "workspace_head_commit": "abc123"},
                "provenance": {
                    "artifact_id": "artifact_1",
                    "schema": "hades.code_graph.v2",
                },
                "nodes": [
                    {
                        "id": "route:orders.show",
                        "kind": "route",
                        "label": "orders.show",
                    },
                    {
                        "id": "OrderController@show",
                        "kind": "method",
                        "label": "OrderController@show",
                    },
                ],
                "edges": [
                    {
                        "id": "edge_1",
                        "kind": "route_handler",
                        "from": "route:orders.show",
                        "to": "OrderController@show",
                    }
                ],
                "server_time": "2026-07-07T13:00:00Z",
            }

        def close(self):
            self.closed += 1

    fake = FakeClient()
    import plugins.memory.hades_backend as hades_memory

    def client_from_config(*, timeout=None):
        timeouts.append(timeout)
        return fake

    monkeypatch.setattr(hades_memory.runtime, "client_from_config", client_from_config)

    result = json.loads(
        provider.handle_tool_call(
            "hades_backend_graph_traverse",
            {"start": "orders.show", "direction": "out", "max_depth": 2, "limit": 10},
        )
    )

    assert result["status"] == "ok"
    assert result["artifact_id"] == "artifact_1"
    assert result["start"] == "route:orders.show"
    assert result["freshness"]["status"] == "current"
    assert result["nodes"][0]["id"] == "route:orders.show"
    assert result["edges"][0]["kind"] == "route_handler"
    assert fake.calls == [
        {
            "project_id": "proj_1",
            "workspace_binding_id": "wb_1",
            "start": "orders.show",
            "direction": "out",
            "max_depth": 2,
            "limit": 10,
        }
    ]
    assert fake.closed == 1
    assert timeouts == [0.75]


def test_hades_backend_graph_traverse_sends_organism_scope(monkeypatch, tmp_path):
    provider = _create_linked_provider(monkeypatch, tmp_path)

    class FakeClient:
        def __init__(self):
            self.calls = []

        def graph_traverse(self, **payload):
            self.calls.append(payload)
            return _live_organism_topology() | {
                "direction": payload["direction"],
                "max_depth": payload["max_depth"],
                "limit": payload["limit"],
            }

        def close(self):
            pass

    fake = FakeClient()
    import plugins.memory.hades_backend as hades_memory

    monkeypatch.setattr(
        hades_memory.runtime, "client_from_config", lambda *, timeout=None: fake
    )

    result = json.loads(
        provider.handle_tool_call(
            "hades_backend_graph_traverse",
            {
                "start": "capability:graph-search",
                "scope": "organism",
                "direction": "out",
                "max_depth": 1,
                "limit": 10,
            },
        )
    )

    assert result["status"] == "ok"
    assert result["scope"] == "organism"
    assert fake.calls == [
        {
            "project_id": "proj_1",
            "workspace_binding_id": "wb_1",
            "start": "capability:graph-search",
            "direction": "out",
            "max_depth": 1,
            "limit": 10,
            "scope": "organism",
        }
    ]


def test_hades_backend_graph_organism_scope_falls_back_to_current_revision(
    monkeypatch, tmp_path
):
    provider = _create_linked_provider(monkeypatch, tmp_path)

    from hermes_cli.gnothi.store import OrganismRevisionStore

    OrganismRevisionStore().publish(_organism_graph_artifact())

    import plugins.memory.hades_backend as hades_memory

    def unavailable_client(*, timeout=None):
        raise RuntimeError("backend offline")

    monkeypatch.setattr(hades_memory.runtime, "client_from_config", unavailable_client)

    search = json.loads(
        provider.handle_tool_call(
            "hades_backend_graph_search",
            {"query": "Graph search", "scope": "organism", "limit": 5},
        )
    )
    traverse = json.loads(
        provider.handle_tool_call(
            "hades_backend_graph_traverse",
            {
                "start": "capability:graph-search",
                "scope": "organism",
                "direction": "out",
                "max_depth": 1,
                "limit": 10,
            },
        )
    )

    assert search["status"] == "ok"
    assert search["scope"] == "organism"
    assert search["schema"] == "hades.organism_graph.v1"
    assert search["artifact_id"] == "revision_test_1"
    assert search["provenance"]["artifacts"][0]["origin"] == "organism_revision"
    assert any(
        item.get("graph_ref", {}).get("id") == "capability:graph-search"
        for item in search["items"]
    )

    assert traverse["status"] == "ok"
    assert traverse["scope"] == "organism"
    assert traverse["schema"] == "hades.organism_graph.v1"
    assert traverse["artifact_id"] == "revision_test_1"
    assert {node["id"] for node in traverse["nodes"]} == {
        "capability:graph-search",
        "runtime:local-cli",
    }
    assert [edge["kind"] for edge in traverse["edges"]] == ["requires"]


def test_hades_backend_memory_live_search_uses_short_timeout(monkeypatch, tmp_path):
    provider = _create_linked_provider(monkeypatch, tmp_path)
    timeouts = []

    class FakeClient:
        def memory_search(self, **payload):
            return {
                "project_id": payload["project_id"],
                "workspace_binding_id": payload["workspace_binding_id"],
                "version": "search_v1",
                "query": payload["query"],
                "domain": payload["domain"],
                "count": 0,
                "candidate_count": 0,
                "items": [],
            }

        def close(self):
            pass

    import plugins.memory.hades_backend as hades_memory

    def client_from_config(*, timeout=None):
        timeouts.append(timeout)
        return FakeClient()

    monkeypatch.setattr(hades_memory.runtime, "client_from_config", client_from_config)

    provider.handle_tool_call(
        "hades_backend_project_memory_search",
        {"query": "Hades routes"},
    )

    assert timeouts == [0.75]


def test_hades_backend_bug_evidence_search_tool_prefers_live_backend(monkeypatch, tmp_path):
    provider = _create_linked_provider(monkeypatch, tmp_path)

    class FakeClient:
        def __init__(self):
            self.calls = []
            self.closed = 0

        def bug_evidence_search(self, **payload):
            self.calls.append(payload)
            return {
                "project_id": payload["project_id"],
                "workspace_binding_id": payload["workspace_binding_id"],
                "version": "bug_evidence_search_v1",
                "etag": "bug_evidence_search_v1",
                "query": payload["query"],
                "kind": payload["kind"],
                "bug_report_id": payload["bug_report_id"],
                "count": 1,
                "candidate_count": 1,
                "truncated": False,
                "freshness": {
                    "workspace_head_commit": "abc123",
                    "index_status": "live_query",
                },
                "server_time": "2026-07-07T12:00:00Z",
                "items": [
                    {
                        "id": "evidence_1",
                        "bug_report_id": "bug_1",
                        "kind": "stack_trace",
                        "summary": "Call to member function active() on null in SecurityActivityCategoryController.",
                        "source": "laravel.log",
                        "payload": {
                            "frames": [
                                {
                                    "file": "app/Http/Controllers/Taxonomy/SecurityActivityCategoryController.php",
                                    "line": 42,
                                }
                            ]
                        },
                        "sha256": "a" * 64,
                        "redactions": 1,
                        "retention_class": "stack_trace",
                        "occurred_at": "2026-07-07T11:58:00Z",
                        "score": 42,
                        "version": "bug_evidence_1",
                    }
                ],
            }

        def close(self):
            self.closed += 1

    fake = FakeClient()
    import plugins.memory.hades_backend as hades_memory

    monkeypatch.setattr(hades_memory.runtime, "client_from_config", lambda *, timeout=None: fake)

    result = json.loads(
        provider.handle_tool_call(
            "hades_backend_bug_evidence_search",
            {
                "query": "SecurityActivityCategoryController active null",
                "kind": "stack_trace",
                "bug_report_id": "bug_1",
                "limit": 5,
            },
        )
    )

    assert result["status"] == "ok"
    assert result["searched_cache_only"] is False
    assert result["backend_version"] == "bug_evidence_search_v1"
    assert result["kind"] == "stack_trace"
    assert result["bug_report_id"] == "bug_1"
    assert result["freshness"]["index_status"] == "live_query"
    assert result["items"][0]["id"] == "evidence_1"
    assert result["items"][0]["payload"]["frames"][0]["line"] == 42
    assert result["items"][0]["graph_refs"] == [
        {
            "type": "source_frame",
            "path": "app/Http/Controllers/Taxonomy/SecurityActivityCategoryController.php",
            "line": 42,
            "graph_query": "app/Http/Controllers/Taxonomy/SecurityActivityCategoryController.php",
        }
    ]
    assert fake.calls == [
        {
            "project_id": "proj_1",
            "workspace_binding_id": "wb_1",
            "query": "SecurityActivityCategoryController active null",
            "kind": "stack_trace",
            "bug_report_id": "bug_1",
            "limit": 5,
        }
    ]
    assert fake.closed == 1


def test_hades_backend_bug_evidence_search_tool_uses_short_timeout(monkeypatch, tmp_path):
    provider = _create_linked_provider(monkeypatch, tmp_path)
    timeouts = []

    class FakeClient:
        def bug_evidence_search(self, **payload):
            return {
                "project_id": payload["project_id"],
                "workspace_binding_id": payload["workspace_binding_id"],
                "version": "bug_evidence_search_v1",
                "query": payload["query"],
                "count": 0,
                "candidate_count": 0,
                "items": [],
            }

        def close(self):
            pass

    import plugins.memory.hades_backend as hades_memory

    def client_from_config(*, timeout=None):
        timeouts.append(timeout)
        return FakeClient()

    monkeypatch.setattr(hades_memory.runtime, "client_from_config", client_from_config)

    provider.handle_tool_call(
        "hades_backend_bug_evidence_search",
        {"query": "stack trace"},
    )

    assert timeouts == [0.75]


def test_hades_backend_source_slice_fetch_tool_prefers_live_backend(monkeypatch, tmp_path):
    provider = _create_linked_provider(monkeypatch, tmp_path)
    timeouts = []

    class FakeClient:
        def __init__(self):
            self.calls = []
            self.closed = 0

        def source_slices(self, **payload):
            self.calls.append(payload)
            return {
                "project_id": payload["project_id"],
                "workspace_binding_id": payload["workspace_binding_id"],
                "version": "source_slice_search_v1",
                "etag": "source_slice_search_v1",
                "query": payload["query"],
                "path": payload["path"],
                "symbol": payload["symbol"],
                "line": payload["line"],
                "count": 1,
                "candidate_count": 1,
                "truncated": False,
                "freshness": {
                    "workspace_head_commit": "abc123",
                    "index_status": "live_query",
                },
                "server_time": "2026-07-07T12:00:00Z",
                "items": [
                    {
                        "id": "slice_1",
                        "path": "app/Http/Controllers/OrderController.php",
                        "start_line": 41,
                        "end_line": 43,
                        "language": "php",
                        "symbol": "OrderController@show",
                        "head_commit": "abc123",
                        "sha256": "b" * 64,
                        "content_redacted": "41: public function show() {\n42:     return ***;\n43: }",
                        "redactions": 1,
                        "truncated": False,
                        "retention_class": "source_slice",
                        "policy": "manual_review",
                        "updated_at": "2026-07-07T11:59:00Z",
                        "score": 25,
                        "version": "source_slice_1",
                    }
                ],
            }

        def close(self):
            self.closed += 1

    fake = FakeClient()
    import plugins.memory.hades_backend as hades_memory

    def client_from_config(*, timeout=None):
        timeouts.append(timeout)
        return fake

    monkeypatch.setattr(hades_memory.runtime, "client_from_config", client_from_config)

    result = json.loads(
        provider.handle_tool_call(
            "hades_backend_source_slice_fetch",
            {
                "query": "OrderController show",
                "path": "app/Http/Controllers/OrderController.php",
                "symbol": "OrderController@show",
                "line": 42,
                "limit": 3,
            },
        )
    )

    assert result["status"] == "ok"
    assert result["searched_cache_only"] is False
    assert result["backend_version"] == "source_slice_search_v1"
    assert result["freshness"]["index_status"] == "live_query"
    assert result["items"][0]["id"] == "slice_1"
    assert result["items"][0]["start_line"] == 41
    assert result["items"][0]["content_redacted"].endswith("}")
    assert result["items"][0]["redactions"] == 1
    assert fake.calls == [
        {
            "project_id": "proj_1",
            "workspace_binding_id": "wb_1",
            "id": None,
            "query": "OrderController show",
            "path": "app/Http/Controllers/OrderController.php",
            "symbol": "OrderController@show",
            "line": 42,
            "limit": 3,
        }
    ]
    assert fake.closed == 1
    assert timeouts == [1.25]


def test_hades_backend_source_slice_fetch_tool_requires_scope(monkeypatch, tmp_path):
    provider = _create_linked_provider(monkeypatch, tmp_path)

    result = json.loads(provider.handle_tool_call("hades_backend_source_slice_fetch", {}))

    assert result["error"].startswith("Provide at least one")


def test_hades_backend_evidence_pack_search_tool_prefers_live_backend(monkeypatch, tmp_path):
    provider = _create_linked_provider(monkeypatch, tmp_path)
    timeouts = []

    class FakeClient:
        def __init__(self):
            self.calls = []
            self.closed = 0

        def evidence_packs(self, **payload):
            self.calls.append(payload)
            return {
                "project_id": payload["project_id"],
                "workspace_binding_id": payload["workspace_binding_id"],
                "version": "evidence_pack_search_v1",
                "etag": "evidence_pack_search_v1",
                "query": payload["query"],
                "bug_report_id": payload["bug_report_id"],
                "count": 1,
                "candidate_count": 1,
                "truncated": False,
                "freshness": {"status": "current", "workspace_head_commit": "abc123"},
                "server_time": "2026-07-07T12:00:00Z",
                "items": [
                    {
                        "id": "pack_1",
                        "bug_report_id": "bug_1",
                        "title": "Order route evidence pack",
                        "summary": "Stack trace, graph edge, and source slice point to OrderController.",
                        "evidence_refs": [{"type": "bug_evidence", "id": "evidence_1"}],
                        "graph_refs": [{"type": "route_handler", "to": "OrderController@show"}],
                        "source_slice_ids": ["slice_1"],
                        "payload": {"next_verification": "Run focused test"},
                        "sha256": "c" * 64,
                        "redactions": 1,
                        "retention_class": "diagnosis_evidence",
                        "head_commit": "abc123",
                        "updated_at": "2026-07-07T11:59:00Z",
                        "score": 33,
                        "version": "evidence_pack_1",
                    }
                ],
            }

        def close(self):
            self.closed += 1

    fake = FakeClient()
    import plugins.memory.hades_backend as hades_memory

    def client_from_config(*, timeout=None):
        timeouts.append(timeout)
        return fake

    monkeypatch.setattr(hades_memory.runtime, "client_from_config", client_from_config)

    result = json.loads(
        provider.handle_tool_call(
            "hades_backend_evidence_pack_search",
            {"query": "OrderController", "bug_report_id": "bug_1", "limit": 5},
        )
    )

    assert result["status"] == "ok"
    assert result["searched_cache_only"] is False
    assert result["backend_version"] == "evidence_pack_search_v1"
    assert result["freshness"]["workspace_head_commit"] == "abc123"
    assert result["items"][0]["id"] == "pack_1"
    assert result["items"][0]["source_slice_ids"] == ["slice_1"]
    assert result["items"][0]["payload"]["next_verification"] == "Run focused test"
    assert fake.calls == [
        {
            "project_id": "proj_1",
            "workspace_binding_id": "wb_1",
            "id": None,
            "query": "OrderController",
            "bug_report_id": "bug_1",
            "limit": 5,
        }
    ]
    assert fake.closed == 1
    assert timeouts == [0.75]


def test_hades_backend_evidence_pack_search_tool_requires_scope(monkeypatch, tmp_path):
    provider = _create_linked_provider(monkeypatch, tmp_path)

    result = json.loads(provider.handle_tool_call("hades_backend_evidence_pack_search", {}))

    assert result["error"].startswith("Provide at least one")


def test_hades_backend_evidence_pack_create_tool_persists_live_backend(monkeypatch, tmp_path):
    provider = _create_linked_provider(monkeypatch, tmp_path)
    timeouts = []

    class FakeClient:
        def __init__(self):
            self.calls = []
            self.closed = 0

        def create_evidence_pack(self, **payload):
            self.calls.append(payload)
            return {
                "project_id": payload["project_id"],
                "workspace_binding_id": payload["workspace_binding_id"],
                "server_time": "2026-07-07T12:00:00Z",
                "evidence_pack": {
                    "id": "pack_1",
                    "bug_report_id": payload["bug_report_id"],
                    "title": payload["title"],
                    "summary": payload["summary"],
                    "evidence_refs": payload["evidence_refs"],
                    "graph_refs": payload["graph_refs"],
                    "source_slice_ids": payload["source_slice_ids"],
                    "payload": payload["payload"],
                    "head_commit": payload["head_commit"],
                    "redactions": payload["redactions"],
                    "retention_class": "diagnosis_evidence",
                    "sha256": "d" * 64,
                    "updated_at": "2026-07-07T11:59:00Z",
                    "version": "evidence_pack_1",
                },
            }

        def close(self):
            self.closed += 1

    fake = FakeClient()
    import plugins.memory.hades_backend as hades_memory

    def client_from_config(*, timeout=None):
        timeouts.append(timeout)
        return fake

    monkeypatch.setattr(hades_memory.runtime, "client_from_config", client_from_config)

    result = json.loads(
        provider.handle_tool_call(
            "hades_backend_evidence_pack_create",
            {
                "bug_report_id": "bug_1",
                "title": "Order route evidence pack",
                "summary": "Stack trace, graph edge, and source slice point to OrderController.",
                "evidence_refs": [{"type": "bug_evidence", "id": "evidence_1"}],
                "graph_refs": [{"type": "route_handler", "to": "OrderController@show"}],
                "source_slice_ids": ["slice_1"],
                "payload": {
                    "reproduction_steps": ["Open booking form", "Submit without customer"],
                    "expected_behavior": "Booking is rejected with a validation message.",
                    "actual_behavior": "OrderController reaches a nullable relation.",
                    "runtime_context": {"php": "8.3", "queue": "sync"},
                    "deploy_context": {"commit": "abc123"},
                    "minimal_input": {"route": "orders.show", "id": 123},
                    "last_changed_refs": ["symbol:OrderController@show"],
                    "missing_evidence": [],
                    "next_verification": "Run focused test",
                },
                "head_commit": "abc123",
                "redactions": 2,
            },
        )
    )

    assert result["status"] == "ok"
    assert result["evidence_pack"]["id"] == "pack_1"
    assert result["evidence_pack"]["source_slice_ids"] == ["slice_1"]
    assert result["evidence_pack"]["payload"]["next_verification"] == "Run focused test"
    assert fake.calls == [
        {
            "project_id": "proj_1",
            "workspace_binding_id": "wb_1",
            "bug_report_id": "bug_1",
            "title": "Order route evidence pack",
            "summary": "Stack trace, graph edge, and source slice point to OrderController.",
            "evidence_refs": [{"type": "bug_evidence", "id": "evidence_1"}],
            "graph_refs": [{"type": "route_handler", "to": "OrderController@show"}],
            "source_slice_ids": ["slice_1"],
            "payload": {
                "reproduction_steps": ["Open booking form", "Submit without customer"],
                "expected_behavior": "Booking is rejected with a validation message.",
                "actual_behavior": "OrderController reaches a nullable relation.",
                "runtime_context": {"php": "8.3", "queue": "sync"},
                "deploy_context": {"commit": "abc123"},
                "minimal_input": {"route": "orders.show", "id": 123},
                "last_changed_refs": ["symbol:OrderController@show"],
                "missing_evidence": [],
                "next_verification": "Run focused test",
            },
            "head_commit": "abc123",
            "redactions": 2,
        }
    ]
    assert fake.closed == 1
    assert timeouts == [2.0]


def test_hades_backend_evidence_pack_create_tool_requires_title(monkeypatch, tmp_path):
    provider = _create_linked_provider(monkeypatch, tmp_path)

    result = json.loads(
        provider.handle_tool_call(
            "hades_backend_evidence_pack_create",
            {"summary": "Missing title."},
        )
    )

    assert result["error"] == "Missing required parameter: title"


def test_hades_backend_causal_pack_fetch_tool_searches_live_backend(monkeypatch, tmp_path):
    provider = _create_linked_provider(monkeypatch, tmp_path)
    timeouts = []

    class FakeClient:
        def __init__(self):
            self.calls = []
            self.closed = 0

        def causal_packs(self, **payload):
            self.calls.append(payload)
            return {
                "project_id": payload["project_id"],
                "workspace_binding_id": payload["workspace_binding_id"],
                "query": payload["query"],
                "bug_report_id": payload["bug_report_id"],
                "root_cause_id": payload["root_cause_id"],
                "count": 1,
                "server_time": "2026-07-08T10:00:00Z",
                "items": [
                    {
                        "id": "causal_pack_1",
                        "bug_report_id": "bug_1",
                        "bug_id": "booking-form-null-customer",
                        "root_cause_id": "root_customer_required_missing",
                        "bug_class": "validation_gap",
                        "failure_classification": "null_relation",
                        "affected_refs": ["route:bookings.store", "table:customers"],
                        "freshness": {"status": "current", "workspace_head_commit": "abc123"},
                        "awareness": {"status": "ready"},
                        "evidence_refs": [{"type": "evidence_pack", "id": "pack_1"}],
                        "graph_refs": [{"type": "edge", "ref": "route:bookings.store->BookingController@store"}],
                        "source_slice_refs": ["slice_1"],
                        "replay": {"status": "passed", "checked_refs": 3},
                        "status": "valid",
                        "blockers": [],
                        "updated_at": "2026-07-08T09:59:00Z",
                        "version": "causal_pack_1",
                        "score": 42,
                    }
                ],
            }

        def close(self):
            self.closed += 1

    fake = FakeClient()
    import plugins.memory.hades_backend as hades_memory

    def client_from_config(*, timeout=None):
        timeouts.append(timeout)
        return fake

    monkeypatch.setattr(hades_memory.runtime, "client_from_config", client_from_config)

    result = json.loads(
        provider.handle_tool_call(
            "hades_backend_causal_pack_fetch",
            {
                "query": "booking customer validation",
                "bug_report_id": "bug_1",
                "root_cause_id": "root_customer_required_missing",
                "limit": 4,
            },
        )
    )

    assert result["status"] == "ok"
    assert result["searched_cache_only"] is False
    assert result["items"][0]["id"] == "causal_pack_1"
    assert result["items"][0]["root_cause_id"] == "root_customer_required_missing"
    assert result["items"][0]["affected_refs"] == ["route:bookings.store", "table:customers"]
    assert result["items"][0]["freshness"]["workspace_head_commit"] == "abc123"
    assert result["items"][0]["replay"]["status"] == "passed"
    assert fake.calls == [
        {
            "project_id": "proj_1",
            "workspace_binding_id": "wb_1",
            "query": "booking customer validation",
            "bug_report_id": "bug_1",
            "root_cause_id": "root_customer_required_missing",
            "limit": 4,
        }
    ]
    assert fake.closed == 1
    assert timeouts == [0.75]


def test_hades_backend_causal_pack_fetch_tool_fetches_exact_pack_with_replay(monkeypatch, tmp_path):
    provider = _create_linked_provider(monkeypatch, tmp_path)
    timeouts = []

    class FakeClient:
        def __init__(self):
            self.pack_calls = []
            self.replay_calls = []
            self.closed = 0

        def causal_pack(self, causal_pack_id, **payload):
            self.pack_calls.append((causal_pack_id, payload))
            return {
                "project_id": payload["project_id"],
                "workspace_binding_id": payload["workspace_binding_id"],
                "causal_pack": {
                    "id": causal_pack_id,
                    "bug_report_id": "bug_1",
                    "root_cause_id": "root_customer_required_missing",
                    "bug_class": "validation_gap",
                    "affected_refs": ["route:bookings.store"],
                    "evidence_refs": [{"type": "evidence_pack", "id": "pack_1"}],
                    "graph_refs": [{"type": "edge", "ref": "route:bookings.store->BookingController@store"}],
                    "source_slice_refs": ["slice_1"],
                    "status": "valid",
                },
            }

        def replay_causal_pack(self, causal_pack_id, **payload):
            self.replay_calls.append((causal_pack_id, payload))
            return {
                "project_id": payload["project_id"],
                "workspace_binding_id": payload["workspace_binding_id"],
                "replay": {
                    "status": "passed",
                    "checked_refs": 3,
                    "missing_refs": [],
                },
            }

        def close(self):
            self.closed += 1

    fake = FakeClient()
    import plugins.memory.hades_backend as hades_memory

    def client_from_config(*, timeout=None):
        timeouts.append(timeout)
        return fake

    monkeypatch.setattr(hades_memory.runtime, "client_from_config", client_from_config)

    result = json.loads(
        provider.handle_tool_call(
            "hades_backend_causal_pack_fetch",
            {"id": "causal_pack_1", "replay": True},
        )
    )

    assert result["status"] == "ok"
    assert result["count"] == 1
    assert result["items"][0]["id"] == "causal_pack_1"
    assert result["replay"]["status"] == "passed"
    assert fake.pack_calls == [
        (
            "causal_pack_1",
            {
                "project_id": "proj_1",
                "workspace_binding_id": "wb_1",
            },
        )
    ]
    assert fake.replay_calls == fake.pack_calls
    assert fake.closed == 1
    assert timeouts == [0.75]


def test_hades_backend_causal_pack_fetch_tool_requires_scope(monkeypatch, tmp_path):
    provider = _create_linked_provider(monkeypatch, tmp_path)

    result = json.loads(provider.handle_tool_call("hades_backend_causal_pack_fetch", {}))

    assert result["error"].startswith("Provide at least one")


def test_hades_backend_diagnosis_report_create_tool_persists_live_backend(monkeypatch, tmp_path):
    provider = _create_linked_provider(monkeypatch, tmp_path)
    timeouts = []

    class FakeClient:
        def __init__(self):
            self.calls = []
            self.awareness_calls = []
            self.closed = 0

        def project_awareness_status(self, **payload):
            self.awareness_calls.append(payload)
            return {
                "project_id": payload["project_id"],
                "workspace_binding_id": payload["workspace_binding_id"],
                "overall_status": "current",
                "diagnosable_without_source": True,
                "freshness": {
                    "status": "current",
                    "workspace_head_commit": "abc123",
                    "artifact_head_commit": "abc123",
                    "index_status": "live_query",
                },
                "coverage": {"code_graph": {"status": "current", "count": 1}},
                "actions": [],
            }

        def create_diagnosis_report(self, **payload):
            self.calls.append(payload)
            return {
                "project_id": payload["project_id"],
                "workspace_binding_id": payload["workspace_binding_id"],
                "server_time": "2026-07-07T12:00:00Z",
                "diagnosis_report": {
                    "id": "diag_1",
                    "bug_report_id": payload["bug_report_id"],
                    "status": payload["status"],
                    "confidence": payload["confidence"],
                    "root_cause": payload["root_cause"],
                    "mechanism": payload["mechanism"],
                    "evidence_refs": payload["evidence_refs"],
                    "freshness": payload["freshness"],
                    "payload": payload["payload"],
                    "redactions": payload["redactions"],
                    "created_at": "2026-07-07T11:59:00Z",
                    "updated_at": "2026-07-07T11:59:00Z",
                    "version": "diagnosis_report_1",
                },
            }

        def close(self):
            self.closed += 1

    fake = FakeClient()
    import plugins.memory.hades_backend as hades_memory

    def client_from_config(*, timeout=None):
        timeouts.append(timeout)
        return fake

    monkeypatch.setattr(hades_memory.runtime, "client_from_config", client_from_config)

    result = json.loads(
        provider.handle_tool_call(
            "hades_backend_diagnosis_report_create",
            {
                "bug_report_id": "bug_1",
                "status": "final",
                "confidence": "high",
                "root_cause": "OrderController dereferences a missing customer relation.",
                "mechanism": "The show action assumes customer is loaded and calls active().",
                "evidence_refs": [
                    {"type": "bug_evidence", "id": "evidence_1"},
                    {"type": "source_slice", "id": "slice_1"},
                ],
                "freshness": {"status": "current", "workspace_head_commit": "abc123"},
                "awareness": {"diagnosable_without_source": True},
                "causal_pack_refs": ["pack_1"],
                "payload": {"next_verification": "Run OrderControllerTest::test_show_missing_customer"},
                "redactions": 2,
            },
        )
    )

    assert result["status"] == "ok"
    assert result["diagnosis_report"]["id"] == "diag_1"
    assert result["diagnosis_report"]["confidence"] == "high"
    assert result["diagnosis_report"]["root_cause"].startswith("OrderController")
    assert result["diagnosis_report"]["evidence_refs"][1]["id"] == "slice_1"
    assert result["diagnosis_report"]["freshness"]["workspace_head_commit"] == "abc123"
    assert fake.awareness_calls == [
        {
            "project_id": "proj_1",
            "workspace_binding_id": "wb_1",
        }
    ]
    assert fake.calls == [
        {
            "project_id": "proj_1",
            "workspace_binding_id": "wb_1",
            "bug_report_id": "bug_1",
            "status": "final",
            "confidence": "high",
            "root_cause": "OrderController dereferences a missing customer relation.",
            "mechanism": "The show action assumes customer is loaded and calls active().",
            "evidence_refs": [
                {"type": "bug_evidence", "id": "evidence_1"},
                {"type": "source_slice", "id": "slice_1"},
            ],
            "freshness": {
                "status": "current",
                "workspace_head_commit": "abc123",
                "artifact_head_commit": "abc123",
                "index_status": "live_query",
            },
            "causal_pack_refs": ["pack_1"],
            "payload": {
                "causal_pack_refs": ["pack_1"],
                "next_verification": "Run OrderControllerTest::test_show_missing_customer",
            },
            "redactions": 2,
        }
    ]
    assert fake.closed == 2
    assert timeouts == [0.75, 2.0]


def test_hades_backend_diagnosis_report_create_forwards_taxonomy_fields(monkeypatch, tmp_path):
    provider = _create_linked_provider(monkeypatch, tmp_path)

    class FakeClient:
        def project_awareness_status(self, **payload):
            return {
                "project_id": payload["project_id"],
                "workspace_binding_id": payload["workspace_binding_id"],
                "overall_status": "ready",
                "diagnosable_without_source": True,
                "freshness": {"status": "current", "workspace_head_commit": "abc123"},
                "coverage": {"source_slices": {"status": "current", "count": 1}},
                "actions": [],
            }

        def create_diagnosis_report(self, **payload):
            return {
                "diagnosis_report": {
                    "id": "diag_1",
                    "status": payload["status"],
                    "confidence": payload["confidence"],
                    "root_cause": payload["root_cause"],
                    "evidence_refs": payload["evidence_refs"],
                    "freshness": payload["freshness"],
                    "payload": payload["payload"],
                }
            }

        def close(self):
            pass

    import plugins.memory.hades_backend as hades_memory

    monkeypatch.setattr(hades_memory.runtime, "client_from_config", lambda *, timeout=None: FakeClient())

    result = json.loads(
        provider.handle_tool_call(
            "hades_backend_diagnosis_report_create",
            {
                "root_cause": "BookingController does not handle guest aliases.",
                "confidence": "high",
                "evidence_refs": [{"type": "source_slice", "id": "slice_1"}],
                "freshness": {"status": "current"},
                "awareness": {"diagnosable_without_source": True},
                "root_cause_id": "rc.booking.guest_alias_missing",
                "bug_class": "missing_validation",
                "failure_classification": "source_slice_policy_gap",
                "affected_refs": ["route:bookings.store", "class:BookingController"],
                "causal_pack_refs": ["pack_1"],
            },
        )
    )

    payload = result["diagnosis_report"]["payload"]
    assert payload["root_cause_id"] == "rc.booking.guest_alias_missing"
    assert payload["bug_class"] == "missing_validation"
    assert payload["failure_classification"] == "source_slice_policy_gap"
    assert payload["affected_refs"] == ["route:bookings.store", "class:BookingController"]
    assert payload["causal_pack_refs"] == ["pack_1"]


def test_hades_backend_diagnosis_report_create_tool_requires_root_cause(monkeypatch, tmp_path):
    provider = _create_linked_provider(monkeypatch, tmp_path)

    result = json.loads(
        provider.handle_tool_call(
            "hades_backend_diagnosis_report_create",
            {"confidence": "insufficient"},
        )
    )

    assert result["error"] == "Missing required parameter: root_cause"


def test_hades_backend_diagnosis_report_create_tool_blocks_precise_stale_claim(monkeypatch, tmp_path):
    provider = _create_linked_provider(monkeypatch, tmp_path)

    result = json.loads(
        provider.handle_tool_call(
            "hades_backend_diagnosis_report_create",
            {
                "confidence": "high",
                "root_cause": "OrderController dereferences a stale relation.",
                "evidence_refs": [{"type": "bug_evidence", "id": "evidence_1"}],
                "freshness": {"status": "stale"},
            },
        )
    )

    assert result["error"] == "High/medium confidence diagnosis reports require freshness.status=current."
    assert result["freshness_status"] == "stale"


def test_hades_backend_diagnosis_report_create_tool_requires_evidence_for_precise_claim(monkeypatch, tmp_path):
    provider = _create_linked_provider(monkeypatch, tmp_path)

    result = json.loads(
        provider.handle_tool_call(
            "hades_backend_diagnosis_report_create",
            {
                "confidence": "medium",
                "root_cause": "OrderController dereferences a stale relation.",
                "freshness": {"status": "current"},
            },
        )
    )

    assert result["error"] == "High/medium confidence diagnosis reports require evidence_refs."
    assert result["required_for_confidence"] == "medium"


def test_hades_backend_diagnosis_report_create_tool_requires_causal_pack_for_precise_claim(monkeypatch, tmp_path):
    provider = _create_linked_provider(monkeypatch, tmp_path)

    result = json.loads(
        provider.handle_tool_call(
            "hades_backend_diagnosis_report_create",
            {
                "confidence": "high",
                "root_cause": "OrderController dereferences a nullable relation.",
                "evidence_refs": [{"type": "bug_evidence", "id": "evidence_1"}],
                "freshness": {"status": "current"},
                "awareness": {"diagnosable_without_source": True},
            },
        )
    )

    assert result["error"] == "High/medium confidence source-free diagnosis reports require causal_pack_refs."
    assert result["required_for_confidence"] == "high"


def test_hades_backend_diagnosis_report_create_tool_blocks_incomplete_awareness(monkeypatch, tmp_path):
    provider = _create_linked_provider(monkeypatch, tmp_path)

    result = json.loads(
        provider.handle_tool_call(
            "hades_backend_diagnosis_report_create",
            {
                "confidence": "high",
                "root_cause": "OrderController dereferences a nullable relation.",
                "evidence_refs": [{"type": "bug_evidence", "id": "evidence_1"}],
                "freshness": {"status": "current"},
                "awareness": {"diagnosable_without_source": False},
            },
        )
    )

    assert result["error"] == (
        "High/medium confidence diagnosis reports require "
        "awareness.diagnosable_without_source=true."
    )
    assert result["diagnosable_without_source"] is False


def test_hades_backend_diagnosis_report_create_tool_uses_live_awareness_gate(monkeypatch, tmp_path):
    provider = _create_linked_provider(monkeypatch, tmp_path)

    class FakeClient:
        def __init__(self):
            self.create_calls = []

        def project_awareness_status(self, **payload):
            return {
                "project_id": payload["project_id"],
                "workspace_binding_id": payload["workspace_binding_id"],
                "overall_status": "stale",
                "diagnosable_without_source": True,
                "freshness": {
                    "status": "stale",
                    "workspace_head_commit": "new-head",
                    "artifact_head_commit": "old-head",
                    "index_status": "live_query",
                    "stale_reason": "workspace_head_changed",
                },
                "coverage": {"code_graph": {"status": "stale", "count": 1}},
                "actions": ["Run `hades backend sync` before precise diagnosis."],
            }

        def create_diagnosis_report(self, **payload):
            self.create_calls.append(payload)
            raise AssertionError("stale awareness must block before report create")

        def close(self):
            pass

    fake = FakeClient()
    import plugins.memory.hades_backend as hades_memory

    monkeypatch.setattr(hades_memory.runtime, "client_from_config", lambda *, timeout=None: fake)

    result = json.loads(
        provider.handle_tool_call(
            "hades_backend_diagnosis_report_create",
            {
                "confidence": "high",
                "root_cause": "OrderController dereferences a nullable relation.",
                "evidence_refs": [{"type": "bug_evidence", "id": "evidence_1"}],
                "freshness": {"status": "current", "workspace_head_commit": "claimed-current"},
                "awareness": {"diagnosable_without_source": True},
                "causal_pack_refs": ["pack_1"],
            },
        )
    )

    assert result["error"] == "High/medium confidence diagnosis reports require live freshness.status=current."
    assert result["freshness_status"] == "stale"
    assert result["freshness"]["stale_reason"] == "workspace_head_changed"
    assert result["actions"] == ["Run `hades backend sync` before precise diagnosis."]
    assert fake.create_calls == []


def test_hades_backend_resolved_bug_promote_tool_persists_live_backend(monkeypatch, tmp_path):
    provider = _create_linked_provider(monkeypatch, tmp_path)
    timeouts = []

    class FakeClient:
        def __init__(self):
            self.calls = []
            self.closed = 0

        def promote_diagnosis_report(self, diagnosis_report_id, **payload):
            self.calls.append((diagnosis_report_id, payload))
            return {
                "project_id": payload["project_id"],
                "workspace_binding_id": payload["workspace_binding_id"],
                "diagnosis_report_id": diagnosis_report_id,
                "already_promoted": False,
                "server_time": "2026-07-07T12:30:00Z",
                "resolved_bug_memory": {
                    "id": "mem_bug_1",
                    "kind": "resolved_bug",
                    "summary": "Resolved bug: active() on null in OrderController.",
                    "payload": {
                        "schema": "hades.resolved_bug.v1",
                        "root_cause": "OrderController dereferences a missing customer relation.",
                        "verification_status": payload["verification_status"],
                        "affected_symbols": payload["affected_symbols"],
                    },
                    "occurred_at": "2026-07-07T12:29:00Z",
                    "updated_at": "2026-07-07T12:29:00Z",
                    "version": "mem_resolved_bug_1",
                },
            }

        def close(self):
            self.closed += 1

    fake = FakeClient()
    import plugins.memory.hades_backend as hades_memory

    def client_from_config(*, timeout=None):
        timeouts.append(timeout)
        return fake

    monkeypatch.setattr(hades_memory.runtime, "client_from_config", client_from_config)

    result = json.loads(
        provider.handle_tool_call(
            "hades_backend_resolved_bug_promote",
            {
                "diagnosis_report_id": "diag_1",
                "verification_status": "test_passed",
                "fix_commit": "abc123",
                "affected_symbols": ["OrderController@show"],
                "regression_tests": ["OrderControllerTest::test_show_missing_customer"],
                "payload": {"notes": "Focused regression test passed."},
                "redactions": 1,
            },
        )
    )

    assert result["status"] == "ok"
    assert result["resolved_bug_memory"]["id"] == "mem_bug_1"
    assert result["resolved_bug_memory"]["kind"] == "resolved_bug"
    assert result["resolved_bug_memory"]["payload"]["verification_status"] == "test_passed"
    assert fake.calls == [
        (
            "diag_1",
            {
                "project_id": "proj_1",
                "workspace_binding_id": "wb_1",
                "verification_status": "test_passed",
                "fix_commit": "abc123",
                "fix_pr_url": None,
                "affected_symbols": ["OrderController@show"],
                "regression_tests": ["OrderControllerTest::test_show_missing_customer"],
                "payload": {"notes": "Focused regression test passed."},
                "redactions": 1,
            },
        )
    ]
    assert fake.closed == 1
    assert timeouts == [2.0]


def test_hades_backend_resolved_bug_promote_tool_requires_verification(monkeypatch, tmp_path):
    provider = _create_linked_provider(monkeypatch, tmp_path)

    result = json.loads(
        provider.handle_tool_call(
            "hades_backend_resolved_bug_promote",
            {"diagnosis_report_id": "diag_1", "verification_status": "guessed"},
        )
    )

    assert result["error"].startswith("Unsupported resolved bug verification status")


def test_hades_backend_project_awareness_status_tool_reads_live_backend(monkeypatch, tmp_path):
    provider = _create_linked_provider(monkeypatch, tmp_path)

    class FakeClient:
        def __init__(self):
            self.calls = []
            self.closed = 0

        def project_awareness_status(self, **payload):
            self.calls.append(payload)
            return {
                "protocol_version": "v1",
                "project_id": payload["project_id"],
                "workspace_binding_id": payload["workspace_binding_id"],
                "workspace_head_commit": "abc123",
                "overall_status": "partial",
                "diagnosable_without_source": False,
                "freshness": {
                    "status": "current",
                    "workspace_head_commit": "abc123",
                    "artifact_head_commit": "abc123",
                    "index_status": "live_query",
                    "stale_reason": None,
                },
                "coverage": {
                    "memory": {"status": "current", "count": 2},
                    "artifacts": {"status": "current", "count": 1},
                    "bug_evidence": {"status": "missing", "count": 0},
                    "code_graph": {"status": "partial", "count": 1},
                    "source_slices": {"status": "missing", "count": 0},
                },
                "actions": ["Capture typed bug evidence before precise root-cause claims."],
                "server_time": "2026-07-07T12:00:00Z",
            }

        def close(self):
            self.closed += 1

    fake = FakeClient()
    import plugins.memory.hades_backend as hades_memory

    monkeypatch.setattr(hades_memory.runtime, "client_from_config", lambda *, timeout=None: fake)

    result = json.loads(
        provider.handle_tool_call(
            "hades_backend_project_awareness_status",
            {},
        )
    )

    assert result["status"] == "ok"
    assert result["overall_status"] == "partial"
    assert result["diagnosable_without_source"] is False
    assert result["freshness"]["status"] == "current"
    assert result["coverage"]["code_graph"]["status"] == "partial"
    assert "Capture typed bug evidence" in result["actions"][0]
    assert fake.calls == [
        {
            "project_id": "proj_1",
            "workspace_binding_id": "wb_1",
        }
    ]
    assert fake.closed == 1


def test_hades_backend_project_awareness_status_tool_uses_short_timeout(monkeypatch, tmp_path):
    provider = _create_linked_provider(monkeypatch, tmp_path)
    timeouts = []

    class FakeClient:
        def project_awareness_status(self, **payload):
            return {
                "project_id": payload["project_id"],
                "workspace_binding_id": payload["workspace_binding_id"],
                "overall_status": "missing_index",
                "diagnosable_without_source": False,
            }

        def close(self):
            pass

    import plugins.memory.hades_backend as hades_memory

    def client_from_config(*, timeout=None):
        timeouts.append(timeout)
        return FakeClient()

    monkeypatch.setattr(hades_memory.runtime, "client_from_config", client_from_config)

    provider.handle_tool_call("hades_backend_project_awareness_status", {})

    assert timeouts == [0.75]


def test_hades_backend_memory_search_tool_allows_artifacts_domain(monkeypatch, tmp_path):
    provider = _create_linked_provider(monkeypatch, tmp_path)

    class FakeClient:
        def memory_search(self, **payload):
            return {
                "project_id": payload["project_id"],
                "workspace_binding_id": payload["workspace_binding_id"],
                "version": "search_artifacts",
                "query": payload["query"],
                "domain": payload["domain"],
                "count": 1,
                "candidate_count": 1,
                "raw_chunks_omitted": 0,
                "items": [
                    {
                        "id": "artifact_1",
                        "domain": "artifacts",
                        "schema": "hades.git_tree.v1",
                        "source": "hades.git_tree.v1",
                        "summary": "Project index: GET /hades/memory -> MemoryController@index",
                        "score": 16,
                    }
                ],
            }

        def close(self):
            pass

    import plugins.memory.hades_backend as hades_memory

    monkeypatch.setattr(hades_memory.runtime, "client_from_config", lambda *, timeout=None: FakeClient())

    result = json.loads(
        provider.handle_tool_call(
            "hades_backend_project_memory_search",
            {"query": "hades memory route", "domain": "artifacts"},
        )
    )

    assert result["status"] == "ok"
    assert result["domain"] == "artifacts"
    assert result["items"][0]["domain"] == "artifacts"
    assert result["items"][0]["schema"] == "hades.git_tree.v1"


def test_hades_backend_memory_search_tool_can_include_raw_chunks(monkeypatch, tmp_path):
    provider = _create_linked_provider(
        monkeypatch,
        tmp_path,
        items=[
            {
                "id": "chunk_1",
                "domain": "source_chunks",
                "schema": "hades.backend_wiki.file_chunk.v1",
                "path": "docs/backend.md",
                "summary": "Exact chunk mentions taxonomy route extraction.",
            },
        ],
    )

    without_raw = json.loads(
        provider.handle_tool_call(
            "hades_backend_project_memory_search",
            {"query": "taxonomy route", "domain": "source_chunks"},
        )
    )
    with_raw = json.loads(
        provider.handle_tool_call(
            "hades_backend_project_memory_search",
            {
                "query": "taxonomy route",
                "domain": "source_chunks",
                "include_raw_chunks": True,
            },
        )
    )

    assert without_raw["count"] == 0
    assert without_raw["raw_chunks_omitted"] == 1
    assert with_raw["count"] == 1
    assert with_raw["items"][0]["raw_chunk"] is True
    assert with_raw["items"][0]["source"] == "docs/backend.md"


def test_hades_backend_memory_search_ranks_verified_backfill_fact_before_raw_chunk(monkeypatch, tmp_path):
    provider = _create_linked_provider(
        monkeypatch,
        tmp_path,
        items=[
            {
                "id": "chunk_1",
                "domain": "source_chunks",
                "schema": "hades.backend_wiki.file_chunk.v1",
                "path": "graphify-sidecar/carnovali-facts.md",
                "summary": (
                    "taxonomy route taxonomy route taxonomy route "
                    "SecurityActivityCategoryController handled_by extracted chunk"
                ),
            },
            {
                "id": "fact_1",
                "domain": "project_memory",
                "kind": "verified_note_fact",
                "schema": "hades.verified_note_fact.v1",
                "source": "note_backfill_candidate",
                "summary": (
                    "Verified route taxonomy_flock_vocabulary_security_activity_category_show "
                    "is handled by SecurityActivityCategoryController."
                ),
                "payload": {
                    "route": "taxonomy_flock_vocabulary_security_activity_category_show",
                    "handler": "SecurityActivityCategoryController",
                    "workspace_head_commit": "abc123",
                    "index_status": "reviewed_note_fact",
                },
            },
        ],
    )

    result = json.loads(
        provider.handle_tool_call(
            "hades_backend_project_memory_search",
            {
                "query": "taxonomy route SecurityActivityCategoryController",
                "include_raw_chunks": True,
                "limit": 2,
            },
        )
    )

    assert result["status"] == "ok"
    assert result["raw_chunks_omitted"] == 0
    assert [item["id"] for item in result["items"]] == ["fact_1", "chunk_1"]
    assert result["items"][0]["kind"] == "verified_note_fact"
    assert result["items"][0]["raw_chunk"] is False
    assert result["items"][1]["raw_chunk"] is True
    assert result["items"][0]["score"] > result["items"][1]["score"]


def test_hades_backend_memory_search_tool_reports_unmapped_project(monkeypatch, tmp_path):
    from hermes_cli import hades_backend_db as db
    from plugins.memory import load_memory_provider

    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))
    workspace = tmp_path / "unmapped"
    workspace.mkdir()
    monkeypatch.chdir(workspace)
    with db.connect_closing() as conn:
        db.save_agent(
            conn,
            agent_id="agent_1",
            project_id="proj_1",
            base_url="https://backend.example",
            label="dev",
            token_env_key="HADES_BACKEND_AGENT_TOKEN_TEST",
            capabilities={"memory": True},
        )

    provider = load_memory_provider("hades_backend")
    assert provider is not None
    provider.initialize("session_1", hermes_home=str(tmp_path / "home"), platform="cli")

    block = provider.system_prompt_block()
    result = json.loads(
        provider.handle_tool_call(
            "hades_backend_project_memory_search",
            {"query": "routes"},
        )
    )
    bug_result = json.loads(
        provider.handle_tool_call(
            "hades_backend_bug_evidence_search",
            {"query": "stack trace"},
        )
    )
    traversal_result = json.loads(
        provider.handle_tool_call(
            "hades_backend_graph_traverse",
            {"start": "orders.show"},
        )
    )
    awareness_result = json.loads(
        provider.handle_tool_call(
            "hades_backend_project_awareness_status",
            {},
        )
    )
    diagnosis_result = json.loads(
        provider.handle_tool_call(
            "hades_backend_diagnosis_report_create",
            {"confidence": "insufficient", "root_cause": "not determined"},
        )
    )
    promote_result = json.loads(
        provider.handle_tool_call(
            "hades_backend_resolved_bug_promote",
            {"diagnosis_report_id": "diag_1", "verification_status": "manual_review"},
        )
    )

    assert "not linked" in block
    assert result["status"] == "unmapped_project"
    assert result["items"] == []
    assert bug_result["status"] == "unmapped_project"
    assert bug_result["items"] == []
    assert traversal_result["status"] == "unmapped_project"
    assert traversal_result["nodes"] == []
    assert traversal_result["edges"] == []
    assert awareness_result["status"] == "unmapped_project"
    assert diagnosis_result["status"] == "unmapped_project"
    assert promote_result["status"] == "unmapped_project"


def test_hades_backend_memory_write_creates_local_proposal(monkeypatch, tmp_path):
    from hermes_cli import hades_backend_db as db
    from hermes_cli.hades_backend_runtime import workspace_fingerprint
    from plugins.memory import load_memory_provider

    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))
    workspace = tmp_path / "repo"
    workspace.mkdir()
    monkeypatch.chdir(workspace)

    fp = workspace_fingerprint(workspace, "proj_1")
    with db.connect_closing() as conn:
        db.save_agent(
            conn,
            agent_id="agent_1",
            project_id="proj_1",
            base_url="https://backend.example",
            label="dev",
            token_env_key="HADES_BACKEND_AGENT_TOKEN_TEST",
            capabilities={"memory": True},
        )
        db.upsert_workspace_binding(
            conn,
            project_id="proj_1",
            agent_id="agent_1",
            local_project_id="p_1",
            workspace_fingerprint=fp,
            display_path="~/repo",
            repo_root=str(workspace),
            git_remote_display="",
            git_remote_hash="",
            head_commit="",
            backend_workspace_binding_id="wb_1",
        )

    provider = load_memory_provider("hades_backend")
    assert provider is not None
    provider.initialize("session_1", hermes_home=str(tmp_path / "home"), platform="cli")
    provider.on_memory_write("add", "project", "Keep backend responses bounded", metadata={"source": "test"})

    with db.connect_closing() as conn:
        proposals = db.list_memory_proposals(conn)

    assert len(proposals) == 1
    assert proposals[0].action == "create"
    assert proposals[0].intent == "memory_write"
    assert "bounded" in proposals[0].summary


def test_hades_backend_memory_write_preserves_update_identity(monkeypatch, tmp_path):
    from hermes_cli import hades_backend_db as db
    from hermes_cli.hades_backend_runtime import workspace_fingerprint
    from plugins.memory import load_memory_provider

    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))
    workspace = tmp_path / "repo"
    workspace.mkdir()
    monkeypatch.chdir(workspace)

    fp = workspace_fingerprint(workspace, "proj_1")
    with db.connect_closing() as conn:
        db.save_agent(
            conn,
            agent_id="agent_1",
            project_id="proj_1",
            base_url="https://backend.example",
            label="dev",
            token_env_key="HADES_BACKEND_AGENT_TOKEN_TEST",
            capabilities={"memory": True},
        )
        db.upsert_workspace_binding(
            conn,
            project_id="proj_1",
            agent_id="agent_1",
            local_project_id="p_1",
            workspace_fingerprint=fp,
            display_path="~/repo",
            repo_root=str(workspace),
            git_remote_display="",
            git_remote_hash="",
            head_commit="",
            backend_workspace_binding_id="wb_1",
        )

    provider = load_memory_provider("hades_backend")
    assert provider is not None
    provider.initialize("session_1", hermes_home=str(tmp_path / "home"), platform="cli")
    provider.on_memory_write(
        "replace",
        "project",
        "Use bounded backend responses",
        metadata={"old_text": "Use verbose backend responses", "memory_id": "mem_1", "etag": "etag_1"},
    )

    with db.connect_closing() as conn:
        proposals = db.list_memory_proposals(conn)

    assert len(proposals) == 1
    proposal = proposals[0]
    assert proposal.action == "update"
    assert proposal.summary == "Use bounded backend responses"
    assert proposal.provenance["memory_id"] == "mem_1"
    assert proposal.provenance["base_version"] == "etag_1"
    assert proposal.provenance["previous_summary"] == "Use verbose backend responses"


def test_hades_backend_memory_write_creates_delete_proposal(monkeypatch, tmp_path):
    from hermes_cli import hades_backend_db as db
    from hermes_cli.hades_backend_runtime import workspace_fingerprint
    from plugins.memory import load_memory_provider

    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))
    workspace = tmp_path / "repo"
    workspace.mkdir()
    monkeypatch.chdir(workspace)

    fp = workspace_fingerprint(workspace, "proj_1")
    with db.connect_closing() as conn:
        db.save_agent(
            conn,
            agent_id="agent_1",
            project_id="proj_1",
            base_url="https://backend.example",
            label="dev",
            token_env_key="HADES_BACKEND_AGENT_TOKEN_TEST",
            capabilities={"memory": True},
        )
        db.upsert_workspace_binding(
            conn,
            project_id="proj_1",
            agent_id="agent_1",
            local_project_id="p_1",
            workspace_fingerprint=fp,
            display_path="~/repo",
            repo_root=str(workspace),
            git_remote_display="",
            git_remote_hash="",
            head_commit="",
            backend_workspace_binding_id="wb_1",
        )

    provider = load_memory_provider("hades_backend")
    assert provider is not None
    provider.initialize("session_1", hermes_home=str(tmp_path / "home"), platform="cli")
    provider.on_memory_write(
        "remove",
        "project",
        "",
        metadata={"old_text": "Obsolete backend route", "memory_id": "mem_2", "base_version": "v2"},
    )

    with db.connect_closing() as conn:
        proposals = db.list_memory_proposals(conn)

    assert len(proposals) == 1
    proposal = proposals[0]
    assert proposal.action == "delete"
    assert proposal.summary == "Obsolete backend route"
    assert proposal.provenance["memory_id"] == "mem_2"
    assert proposal.provenance["base_version"] == "v2"
    assert proposal.provenance["previous_summary"] == "Obsolete backend route"
