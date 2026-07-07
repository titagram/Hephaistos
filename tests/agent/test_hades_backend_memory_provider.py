from __future__ import annotations

import json


def _create_linked_provider(monkeypatch, tmp_path, *, items=None):
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
        if items is not None:
            db.replace_memory_cache(
                conn,
                project_id="proj_1",
                workspace_binding_id="wb_1",
                version="v1",
                items=items,
            )

    provider = load_memory_provider("hades_backend")
    assert provider is not None
    provider.initialize("session_1", hermes_home=str(tmp_path / "home"), platform="cli")
    return provider


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
        def memory_search(self, **payload):
            return {
                "project_id": payload["project_id"],
                "workspace_binding_id": payload["workspace_binding_id"],
                "version": "search_v1",
                "etag": "search_v1",
                "query": payload["query"],
                "domain": payload["domain"],
                "include_raw_chunks": payload["include_raw_chunks"],
                "count": 1,
                "candidate_count": 1,
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
            {"query": "active null", "domain": "project_memory", "limit": 5},
        )
    )

    assert result["items"][0]["kind"] == "resolved_bug"
    assert result["items"][0]["stale"] is True
    assert result["items"][0]["stale_reason"] == "workspace_head_changed"


def test_hades_backend_graph_search_tool_queries_artifacts_live(monkeypatch, tmp_path):
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
                        "schema": "hades.php_graph.v1",
                        "source": "hades.php_graph.v1",
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

    monkeypatch.setattr(hades_memory.runtime, "client_from_config", lambda *, timeout=None: fake)

    result = json.loads(
        provider.handle_tool_call(
            "hades_backend_graph_search",
            {"query": "OrderController show", "limit": 4},
        )
    )

    assert result["status"] == "ok"
    assert result["tool_domain"] == "graph"
    assert result["domain"] == "artifacts"
    assert result["items"][0]["schema"] == "hades.php_graph.v1"
    assert fake.calls == [
        {
            "project_id": "proj_1",
            "workspace_binding_id": "wb_1",
            "query": "OrderController show",
            "domain": "artifacts",
            "limit": 4,
            "include_raw_chunks": False,
        }
    ]
    assert fake.closed == 1


def test_hades_backend_graph_traverse_tool_reads_live_backend(monkeypatch, tmp_path):
    provider = _create_linked_provider(monkeypatch, tmp_path)
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
                "schema": "hades.php_graph.v1",
                "head_commit": "abc123",
                "start": payload["start"],
                "direction": payload["direction"],
                "max_depth": payload["max_depth"],
                "limit": payload["limit"],
                "count": 2,
                "edge_count": 1,
                "truncated": False,
                "match_fields": ["id", "attributes.name"],
                "freshness": {"status": "current", "workspace_head_commit": "abc123"},
                "provenance": {"artifact_id": "artifact_1", "schema": "hades.php_graph.v1"},
                "nodes": [
                    {"id": "route:orders.show", "kind": "route", "label": "orders.show"},
                    {"id": "OrderController@show", "kind": "method", "label": "OrderController@show"},
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
    assert timeouts == [2.0]


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

    assert timeouts == [2.0]


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

    assert timeouts == [2.0]


def test_hades_backend_source_slice_fetch_tool_prefers_live_backend(monkeypatch, tmp_path):
    provider = _create_linked_provider(monkeypatch, tmp_path)

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

    monkeypatch.setattr(hades_memory.runtime, "client_from_config", lambda *, timeout=None: fake)

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


def test_hades_backend_source_slice_fetch_tool_requires_scope(monkeypatch, tmp_path):
    provider = _create_linked_provider(monkeypatch, tmp_path)

    result = json.loads(provider.handle_tool_call("hades_backend_source_slice_fetch", {}))

    assert result["error"].startswith("Provide at least one")


def test_hades_backend_diagnosis_report_create_tool_persists_live_backend(monkeypatch, tmp_path):
    provider = _create_linked_provider(monkeypatch, tmp_path)
    timeouts = []

    class FakeClient:
        def __init__(self):
            self.calls = []
            self.closed = 0

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
            "freshness": {"status": "current", "workspace_head_commit": "abc123"},
            "payload": {"next_verification": "Run OrderControllerTest::test_show_missing_customer"},
            "redactions": 2,
        }
    ]
    assert fake.closed == 1
    assert timeouts == [2.0]


def test_hades_backend_diagnosis_report_create_tool_requires_root_cause(monkeypatch, tmp_path):
    provider = _create_linked_provider(monkeypatch, tmp_path)

    result = json.loads(
        provider.handle_tool_call(
            "hades_backend_diagnosis_report_create",
            {"confidence": "insufficient"},
        )
    )

    assert result["error"] == "Missing required parameter: root_cause"


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

    assert timeouts == [2.0]


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
